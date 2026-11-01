"""Полный offline цикл: никакого настоящего Release или сетевого запроса."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from launcher.flow import UpdateFlow
from launcher.install import Installation
from launcher.network import GitHub, Updates
from launcher.update import API, REPOSITORY, UpdateError, canonical, verify_manifest
from tests_launcher.test_update import Fixture
from tools.prepare_signed_release import (generate_key, inspect_archive, main, private_location,
                                          sign_release, verify_release)


class ReleaseTransport:
    def __init__(self, fixture, release="0.2.1", profile="argos"):
        self.archive = fixture.archive()
        self.envelope = fixture.signed(self.archive, release, profile=profile,
            url=f"https://github.com/{REPOSITORY}/releases/download/v{release}/ScreenTranslator-{release}-{profile}-windows-x64.zip")
        self.manifest = verify_manifest(self.envelope, fixture.keys, profile)
        self.manifest_url = f"https://github.com/{REPOSITORY}/releases/download/v{release}/update-{profile}.json"
        self.release = {"draft": False, "prerelease": False, "tag_name": "v" + release, "assets": [
            {"name": f"update-{profile}.json", "browser_download_url": self.manifest_url},
            {"id": self.manifest.asset_id, "browser_download_url": self.manifest.url, "size": len(self.archive)}]}
        self.calls = []
        self.cancel_download = False

    def read(self, url, limit, cancel, destination=None, exact=False):
        self.calls.append(url)
        if cancel.is_set():
            raise UpdateError("Операция отменена")
        if url == API + "/releases/latest":
            return canonical(self.release)
        if url == self.manifest_url:
            return self.envelope
        if url != self.manifest.url:
            raise AssertionError("Unexpected URL")
        if destination is None:
            raise AssertionError("Download must use staging stream")
        destination.write(self.archive)
        if self.cancel_download:
            cancel.set()
            raise UpdateError("Операция отменена во время загрузки")
        return b""


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Обновление offline ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture()
        self.manager = Installation(self.root / "managed", self.fixture.keys)
        self.addCleanup(patch.stopall)
        patch("socket.socket", side_effect=AssertionError("Real network forbidden in offline test")).start()
        self.external = self.root / "external" / "ScreenTranslator"
        self.external.mkdir(parents=True)
        (self.external / "ScreenTranslator.exe").write_bytes(b"external-exe-untouched")
        self.model = self.root / "ArgosModels" / "model.bin"
        self.model.parent.mkdir(); self.model.write_bytes(b"existing-user-model")
        self.settings = self.root / "user-settings.json"
        self.settings.write_bytes(b"existing-settings")

    def flow(self, release="0.2.1", profile="argos"):
        transport = ReleaseTransport(self.fixture, release, profile)
        return UpdateFlow(Updates(self.fixture.keys, transport)), transport

    def assert_user_files(self):
        self.assertEqual((self.external / "ScreenTranslator.exe").read_bytes(), b"external-exe-untouched")
        self.assertEqual(self.model.read_bytes(), b"existing-user-model")
        self.assertEqual(self.settings.read_bytes(), b"existing-settings")

    def test_signed_release_first_install_update_current_and_rollback(self):
        flow, transport = self.flow()
        with patch("launcher.install.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 0
            # Явно выбранный внешний EXE никогда не становится управляемой версией.
            self.manager.launch(self.external.parent)
            self.assertIsNone(self.manager.state()["current"])
            offer = flow.check(self.manager, Event())
            self.assertFalse(offer.current)
            self.assertIsNone(offer.installed)
            result = flow.install(self.manager, offer.manifest, Event(), launch_first=True)
            self.assertTrue(result.started)
            self.assertEqual(popen.call_count, 2)  # external + первая подписанная установка
            first = self.manager.state()["current"]
            self.assertTrue(self.manager.checked_exe(first).exists())
            next_flow, _ = self.flow("0.2.2")
            offer = next_flow.check(self.manager, Event())
            result = next_flow.install(self.manager, offer.manifest, Event(), launch_first=True)
            self.assertFalse(result.started)  # последующие версии не запускаются скрыто
            self.assertEqual(popen.call_count, 2)
        current_flow, _ = self.flow("0.2.2")
        self.assertTrue(current_flow.check(self.manager, Event()).current)
        self.manager.rollback()
        self.assertEqual(self.manager.state()["current"], first)
        self.assertEqual(self.manager.state()["highwater"], "0.2.2")
        rolled_flow, _ = self.flow("0.2.2")
        self.assertTrue(rolled_flow.check(self.manager, Event()).rolled_back)
        with self.assertRaisesRegex(UpdateError, "Downgrade/replay"):
            rolled_flow.install(self.manager, offer.manifest, Event())
        older_flow, _ = self.flow("0.2.1")
        with self.assertRaisesRegex(UpdateError, "downgrade/replay"):
            older_flow.check(self.manager, Event())
        self.assert_user_files()

    def test_bad_signature_hash_cancel_and_profile_preserve_working_version(self):
        flow, _ = self.flow()
        flow.install(self.manager, flow.check(self.manager, Event()).manifest, Event())
        state = self.manager.state()
        flow, transport = self.flow("0.2.2")
        changed = json.loads(transport.envelope)
        changed["manifest"]["size"] += 1
        transport.envelope = canonical(changed)
        with self.assertRaisesRegex(UpdateError, "Ed25519"):
            flow.check(self.manager, Event())
        self.assertNotIn(transport.manifest.url, transport.calls)
        for cancel_download in (False, True):
            flow, transport = self.flow("0.2.2")
            offer = flow.check(self.manager, Event())
            if cancel_download:
                transport.cancel_download = True
            else:
                transport.archive = b"x" * len(transport.archive)
            with self.assertRaises(UpdateError):
                flow.install(self.manager, offer.manifest, Event())
            self.assertEqual(self.manager.state(), state)
            self.assertTrue(self.manager.checked_exe(state["current"]).exists())
        flow, _ = self.flow("0.2.2", "ocr")
        with self.assertRaises(UpdateError): flow.check(self.manager, Event())
        self.assert_user_files()

    def test_first_launch_failure_keeps_verified_install_and_reports_it(self):
        flow, _ = self.flow()
        offer = flow.check(self.manager, Event())
        with patch("launcher.install.subprocess.Popen", side_effect=OSError("launch failed")):
            result = flow.install(self.manager, offer.manifest, Event(), launch_first=True)
        self.assertFalse(result.started)
        self.assertIsNotNone(result.launch_error)
        self.assertEqual(self.manager.state()["current"]["version"], "0.2.1")
        self.assert_user_files()

    def test_cancellation_after_commit_never_starts_the_executable(self):
        flow, _ = self.flow()
        offer = flow.check(self.manager, Event())
        cancel = Event()
        original = self.manager.install
        def commit(*args):
            result = original(*args)
            cancel.set()
            return result
        with patch.object(self.manager, "install", side_effect=commit), patch("launcher.install.subprocess.Popen") as popen:
            result = flow.install(self.manager, offer.manifest, cancel, launch_first=True)
            popen.assert_not_called()
        self.assertFalse(result.started)
        self.assertIn("отменён", result.launch_error)
        self.assertEqual(self.manager.state()["current"]["version"], "0.2.1")
        self.assert_user_files()

    def test_missing_key_and_http_release_errors_are_actionable(self):
        transport = MagicMock()
        with self.assertRaisesRegex(UpdateError, "ключ"):
            UpdateFlow(Updates({}, transport)).check(self.manager, Event())
        transport.read.assert_not_called()
        for code, text in ((403, "proxy"), (404, "отсутствует")):
            opener = MagicMock()
            opener.open.side_effect = HTTPError(API + "/releases/latest", code, "test", {}, None)
            with self.subTest(code=code), self.assertRaisesRegex(UpdateError, text):
                GitHub(opener).read(API + "/releases/latest", 1024, Event())
        opener = MagicMock()
        opener.open.side_effect = URLError(OSError("Tunnel connection failed: 403 Forbidden"))
        with self.assertRaisesRegex(UpdateError, "Proxy/сеть.*403"):
            GitHub(opener).read(API + "/releases/latest", 1024, Event())


class LocalReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Только тестовые ключи ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / "test-private.pem"
        self.public = self.root / "public.json"
        self.archive = self.root / "app.zip"
        self.archive.write_bytes(Fixture().archive())
        self.password = b"ephemeral-test-password-only"

    def test_local_encrypted_key_inspect_sign_verify_roundtrip_no_zip_changes(self):
        before = self.archive.read_bytes()
        record = generate_key(self.key, self.public, "test-local", self.password)
        self.assertTrue(self.key.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----"))
        payload = inspect_archive(self.archive, "0.2.1", "argos", 123)
        envelope, signed_record = sign_release(payload, self.archive, self.key, "test-local", self.password)
        self.assertEqual(record, signed_record)
        verified = verify_release(envelope, self.archive, record, record["fingerprint"])
        self.assertEqual(verified.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(self.archive.read_bytes(), before)
        with self.assertRaises(UpdateError):
            verify_release(envelope, self.archive, record, "SHA256:" + "0" * 64)
        with self.assertRaises(UpdateError):
            sign_release(payload, self.archive, self.key, "test-local", b"wrong-password")
        self.archive.write_bytes(b"x" * len(before))
        with self.assertRaises(UpdateError):
            sign_release(payload, self.archive, self.key, "test-local", self.password)

    def test_private_key_outside_git_only_and_existing_files_are_not_replaced(self):
        from tools.prepare_signed_release import ROOT
        with patch("tools.prepare_signed_release.Ed25519PrivateKey.generate") as generate:
            with self.assertRaises(UpdateError):
                generate_key(ROOT / "dist" / "private.pem", self.public, "test", self.password)
            generate.assert_not_called()
        git = self.root / "other-checkout"; (git / ".git").mkdir(parents=True)
        (git / ".git" / "HEAD").write_text("ref: refs/heads/main")
        with self.assertRaises(UpdateError): private_location(git / "secret.pem")
        generate_key(self.key, self.public, "test", self.password)
        original = self.key.read_bytes()
        with self.assertRaises(UpdateError): generate_key(self.key, self.public, "test", self.password)
        self.assertEqual(self.key.read_bytes(), original)

    def test_cli_prompts_secret_never_prints_it_and_outputs_no_network(self):
        output, errors = io.StringIO(), io.StringIO()
        password = self.password.decode()
        with patch("getpass.getpass", side_effect=[password, password]), \
             patch("sys.stdin.isatty", return_value=True), patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), \
             patch("socket.socket", side_effect=AssertionError("offline")), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(main(["keygen", "--private-key", str(self.key), "--public-out", str(self.public), "--key-id", "test"]), 0)
            self.assertEqual(main(["inspect", "--archive", str(self.archive), "--version", "0.2.1", "--profile", "argos", "--asset-id", "123", "--out", str(self.root / "manifest.json")]), 0)
        self.assertNotIn(password, output.getvalue() + errors.getvalue())
        self.assertNotIn("BEGIN ENCRYPTED PRIVATE KEY", output.getvalue() + errors.getvalue())

    def test_cli_keygen_in_ci_or_without_terminal_is_rejected_before_password(self):
        args = ["keygen", "--private-key", str(self.key), "--public-out", str(self.public), "--key-id", "test"]
        for ci, terminal in (("true", True), ("false", False)):
            with patch.dict(os.environ, {"GITHUB_ACTIONS": ci}), patch("sys.stdin.isatty", return_value=terminal), \
                 patch("getpass.getpass") as password, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 1)
                password.assert_not_called()
            self.assertFalse(self.key.exists())
