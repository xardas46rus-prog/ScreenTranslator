import base64
import io
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from urllib.error import URLError
import zipfile
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from launcher.update import PREFIX, API, REPOSITORY, UpdateError, canonical, verify_manifest, verify_archive, validate_redirect
from launcher.network import GitHub, Updates, _Redirect
from launcher.install import Installation, atomic_json, zip_entries


class Fixture:
    """Одноразовый ключ генерируется только в тесте; реальных релизов нет."""
    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.keys = {"test": self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}

    def archive(self, entries=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            # Минимальная синтетическая PE-шапка AMD64, не запускаемый двоичный файл.
            executable = b"MZ" + b"\0" * 58 + (64).to_bytes(4, "little") + b"PE\0\0\x64\x86test-only"
            for name, value in (entries or [("ScreenTranslator/ScreenTranslator.exe", executable),
                                            ("ScreenTranslator/_internal/test.dll", b"fixture")]):
                archive.writestr(name, value)
        return buffer.getvalue()

    def signed(self, archive, release="0.2.1", **overrides):
        data = {"schema": 1, "version": release, "channel": "stable", "platform": "windows-x64",
                "profile": "argos", "url": f"https://github.com/{REPOSITORY}/releases/download/v{release}/ScreenTranslator-{release}-argos-windows-x64.zip",
                "asset_id": 123, "size": len(archive), "sha256": hashlib.sha256(archive).hexdigest(), "min_launcher": "0.1.0"}
        data.update(overrides)
        signature = self.private.sign(PREFIX + canonical(data))
        return canonical({"key_id": "test", "manifest": data, "signature": base64.b64encode(signature).decode("ascii")})


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.zip = self.f.archive()
        self.signed = self.f.signed(self.zip)

    def test_canonical_signature_allows_reordered_whitespace_not_tampering(self):
        pretty = json.dumps(json.loads(self.signed), indent=3).encode()
        self.assertEqual(verify_manifest(pretty, self.f.keys, "argos").version, "0.2.1")
        obj = json.loads(self.signed)
        obj["manifest"]["size"] += 1
        with self.assertRaisesRegex(UpdateError, "Ed25519"):
            verify_manifest(canonical(obj), self.f.keys, "argos")

    def test_no_production_key_unknown_key_and_bad_signature_fail_closed(self):
        for keys in ({}, {"other": next(iter(self.f.keys.values()))}, Fixture().keys):
            with self.subTest(keys=list(keys)), self.assertRaises(UpdateError):
                verify_manifest(self.signed, keys, "argos")
        obj = json.loads(self.signed)
        for value in ("not base64!", "AA==", None, [1]):
            obj["signature"] = value
            with self.subTest(value=value), self.assertRaises(UpdateError):
                verify_manifest(canonical(obj), self.f.keys, "argos")

    def test_signed_but_invalid_schema_fields_and_urls_are_rejected(self):
        changes = [dict(schema=True), dict(platform="linux"), dict(channel="beta"), dict(profile="ocr"),
                   dict(version="1.2"), dict(version="01.2.3"), dict(version="1.2.3;cmd"),
                   dict(min_launcher="99.0.0"), dict(asset_id=True), dict(asset_id=0),
                   dict(size=True), dict(size=-1), dict(size=2**40), dict(sha256="x" * 64),
                   dict(url="https://evil.example/a.zip"), dict(url="http://github.com/a"),
                   dict(url="https://github.com/other/repo/releases/download/v0.2.1/a.zip"), dict(extra="run.exe")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(UpdateError):
                verify_manifest(self.f.signed(self.zip, **change), self.f.keys, "argos")

    def test_duplicate_json_and_oversize_fail(self):
        for data in (self.signed.replace(b'"key_id":"test"', b'"key_id":"test","key_id":"test"'),
                     b"x" * 65537, b"[]", b"{", b'"NaN"'):
            with self.subTest(data=data[:30]), self.assertRaises(UpdateError):
                verify_manifest(data, self.f.keys, "argos")

    def test_hash_size_and_cancel_are_checked(self):
        manifest = verify_manifest(self.signed, self.f.keys, "argos")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.zip"
            for data in (self.zip[:-1], self.zip + b"x", b"x" * len(self.zip)):
                path.write_bytes(data)
                with self.assertRaises(UpdateError):
                    verify_archive(path, manifest)
            path.write_bytes(self.zip)
            verify_archive(path, manifest)
            cancel = Event(); cancel.set()
            with self.assertRaisesRegex(UpdateError, "отменена"):
                verify_archive(path, manifest, cancel)

    def test_redirects_only_allow_exact_https_github_cdn(self):
        validate_redirect("https://release-assets.githubusercontent.com/a?sig=x")
        for url in ("http://release-assets.githubusercontent.com/a", "https://evil.example/a", "https://user@release-assets.githubusercontent.com/a", "https://release-assets.githubusercontent.com:444/a", "https://release-assets.githubusercontent.com.evil.example/a"):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                validate_redirect(url)


class NetworkTests(unittest.TestCase):
    def test_disabled_key_prevents_any_request(self):
        transport = unittest.mock.Mock()
        with self.assertRaises(UpdateError):
            Updates({}, transport).check("argos", Event())
        transport.read.assert_not_called()

    def test_offline_timeout_cancel_and_download_size(self):
        for failure in (URLError("offline"), TimeoutError()):
            opener = unittest.mock.Mock()
            opener.open.side_effect = failure
            with self.assertRaises(UpdateError):
                GitHub(opener).read(API + "/releases/latest", 100, Event())
        opener = unittest.mock.MagicMock()
        cancel = Event(); cancel.set()
        with self.assertRaises(UpdateError):
            GitHub(opener).read(API + "/releases/latest", 100, cancel)
        opener.open.assert_not_called()
        for data, limit in ((b"1234", 3), (b"1", 3)):
            response = io.BytesIO(data)
            opener.open.return_value.__enter__.return_value = response
            with self.assertRaises(UpdateError):
                GitHub(opener).read(API + "/releases/latest", limit, Event(), exact=True)

    def test_release_asset_binding_and_throttle(self):
        fixture = Fixture(); archive = fixture.archive(); signed = fixture.signed(archive)
        manifest = verify_manifest(signed, fixture.keys, "argos")
        release = {"draft": False, "prerelease": False, "tag_name": "v0.2.1", "assets": [
            {"name": "update-argos.json", "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v0.2.1/update-argos.json"},
            {"id": 123, "browser_download_url": manifest.url, "size": len(archive)}]}
        for tamper in (False, True):
            transport = unittest.mock.Mock()
            if tamper:
                release["assets"][1]["id"] = 999
            transport.read.side_effect = [canonical(release), signed]
            updates = Updates(fixture.keys, transport)
            if tamper:
                with self.assertRaises(UpdateError): updates.check("argos", Event())
            else:
                self.assertEqual(updates.check("argos", Event()).sha256, manifest.sha256)
            with self.assertRaisesRegex(UpdateError, "30 секунд"):
                updates.check("argos", Event())


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Лаунчер тест ")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.f = Fixture()
        self.archive = self.path / "input.zip"
        self.store = Installation(self.path / "versions-store", self.f.keys, "argos")

    def install(self, release="0.2.1", entries=None):
        archive = self.f.archive(entries)
        self.archive.write_bytes(archive)
        return self.store.install(self.f.signed(archive, release), self.archive, Event())

    def test_install_rollback_replay_and_model_preservation(self):
        model = self.path / "models" / "argosmodel"
        model.parent.mkdir(); model.write_bytes(b"user-model")
        settings = self.path / "settings.json"; settings.write_bytes(b"private-settings")
        self.install(); first = self.store.state()["current"]
        self.install("0.2.2")
        self.store.rollback()
        self.assertEqual(self.store.state()["current"], first)
        self.assertEqual(self.store.state()["highwater"], "0.2.2")
        for release in ("0.2.0", "0.2.1", "0.2.2"):
            with self.subTest(release=release), self.assertRaisesRegex(UpdateError, "Downgrade/replay"):
                self.install(release)
        self.assertEqual(model.read_bytes(), b"user-model")
        self.assertEqual(settings.read_bytes(), b"private-settings")
        self.assertTrue(self.store.checked_exe(first).exists())

    def test_commit_failure_restart_preserves_current_and_all_unknown_files(self):
        self.install()
        state = self.store.state()
        unknown = self.store.root / "do-not-delete.txt"; unknown.write_text("keep")
        with patch("launcher.install.atomic_json", side_effect=OSError("simulated power failure")):
            with self.assertRaises(OSError): self.install("0.2.2")
        restarted = Installation(self.store.root, self.f.keys, "argos")
        self.assertEqual(restarted.state(), state)
        self.assertTrue(restarted.checked_exe(state["current"]).exists())
        self.assertEqual(unknown.read_text(), "keep")

    def test_cancel_corrupt_and_hash_mismatch_leave_current_unchanged(self):
        self.install(); state = self.store.state()
        for data in (b"not zip", self.f.archive()):
            self.archive.write_bytes(data)
            envelope = self.f.signed(data, "0.2.2")
            if data.startswith(b"PK"):
                self.archive.write_bytes(b"x" * len(data))
            with self.assertRaises(UpdateError): self.store.install(envelope, self.archive, Event())
            self.assertEqual(self.store.state(), state)
        cancel = Event(); cancel.set()
        data = self.f.archive(); self.archive.write_bytes(data)
        with self.assertRaises(UpdateError): self.store.install(self.f.signed(data, "0.2.2"), self.archive, cancel)
        self.assertEqual(self.store.state(), state)

    def test_unsafe_archives_rejected_before_commit(self):
        self.install(); state = self.store.state()
        unsafe = ("../escape", "/absolute", "ScreenTranslator/../escape", "ScreenTranslator/C:/x",
                  "ScreenTranslator/_internal/CON.txt", "ScreenTranslator/_internal/a. ",
                  "ScreenTranslator\\evil", "ScreenTranslator/postinstall.bat")
        for path in unsafe:
            with self.subTest(path=path), self.assertRaises(UpdateError):
                self.install("0.2.2", [("ScreenTranslator/ScreenTranslator.exe", b"MZok"), (path, b"bad")])
            self.assertEqual(self.store.state(), state)
        for entries in ([("ScreenTranslator/ScreenTranslator.exe", b"MZ"), ("ScreenTranslator/SCREENTRANSLATOR.EXE", b"MZ")],
                        [("ScreenTranslator/ScreenTranslator.exe", b"not executable")],
                        [("ScreenTranslator/_internal/a", b"missing exe")]):
            with self.assertRaises(UpdateError): self.install("0.2.2", entries)

    def test_symlink_zip_and_bomb_are_rejected(self):
        for symlink in (True, False):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                info = zipfile.ZipInfo("ScreenTranslator/ScreenTranslator.exe")
                if symlink:
                    info.create_system = 3; info.external_attr = (0o120777 << 16)
                else:
                    info.file_size = 5 * 1024**3
                archive.writestr(info, b"MZdata")
            with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
                if not symlink:
                    archive.infolist()[0].file_size = 5 * 1024**3
                with self.assertRaises(UpdateError): zip_entries(archive)

    def test_profiles_unmanaged_directory_and_low_disk_are_safe(self):
        self.install()
        with self.assertRaises(UpdateError): Installation(self.store.root, self.f.keys, "ocr").state()
        legacy = self.path / "existing"; legacy.mkdir(); (legacy / "models").mkdir()
        with self.assertRaises(UpdateError): Installation(legacy, self.f.keys)
        self.assertTrue((legacy / "models").exists())
        state = self.store.state()
        with patch("launcher.install.shutil.disk_usage", return_value=unittest.mock.Mock(free=0)):
            with self.assertRaises(UpdateError): self.install("0.2.2")
        self.assertEqual(self.store.state(), state)

    def test_lock_and_tampered_payload_fail_closed(self):
        self.install()
        with self.store.lock():
            with self.assertRaises(UpdateError): self.store.rollback()
        exe = self.store.checked_exe(self.store.state()["current"])
        exe.write_bytes(b"MZtampered")
        with self.assertRaises(UpdateError): self.store.checked_exe(self.store.state()["current"])

    def test_unknown_payload_file_preserved_but_not_trusted_on_launch(self):
        self.install()
        exe = self.store.checked_exe(self.store.state()["current"])
        unknown = exe.parent / "unknown.dll"
        unknown.write_bytes(b"user-file")
        with self.assertRaisesRegex(UpdateError, "неизвестные"):
            self.store.checked_exe(self.store.state()["current"])
        self.assertEqual(unknown.read_bytes(), b"user-file")

    def test_launch_is_fixed_executable_without_shell_and_running_guard(self):
        self.install()
        with patch("launcher.install.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = None
            self.store.launch()
            self.assertFalse(popen.call_args.kwargs["shell"])
            self.assertEqual(Path(popen.call_args.args[0][0]).name, "ScreenTranslator.exe")
            with self.assertRaises(UpdateError): self.install("0.2.2")
            self.store.stop()
            popen.return_value.terminate.assert_called_once()
