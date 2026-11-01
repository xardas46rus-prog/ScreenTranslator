import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from launcher.install import atomic_json
from launcher.flow import UpdateFlow
from launcher.network import Updates
from launcher.update import UpdateError
from tests_launcher.test_auto_updates import ReleaseTransport
from tests_launcher.test_update import Fixture


@unittest.skipUnless(os.environ.get("SCREENTRANSLATOR_GUI_TESTS") == "1", "Нужен дисплей и явное включение GUI")
class LauncherGuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from launcher.gui import App
        self.temp = tempfile.TemporaryDirectory(prefix="Лаунчер GUI ")
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.app = App(self.root, self.temp.name)
        self.root.update()

    def tearDown(self):
        if self.app.thread is not None:
            self.app.cancel.set()
            self.app.thread.join(timeout=12)
        self.app.close()

    def finish(self):
        deadline = time.monotonic() + 4
        while self.app.thread is not None and time.monotonic() < deadline:
            self.root.update(); time.sleep(.01)
        self.assertIsNone(self.app.thread, "Операция GUI не завершена")

    def restart(self, automatic):
        import tkinter as tk
        from launcher.gui import App
        self.app.close()
        atomic_json(Path(self.temp.name) / "preferences.json", {
            "automatic": automatic, "profile": "argos", "directory": str(Path(self.temp.name) / "managed")})
        self.root = tk.Tk()
        self.app = App(self.root, self.temp.name)
        self.root.update()

    def test_default_start_has_no_network_and_check_fails_closed(self):
        self.assertFalse(self.app.automatic.get())
        self.assertIsNone(self.app.thread)
        self.assertIn("ключ", self.app.status.get())
        with patch("launcher.network.GitHub.read") as read:
            self.app.check()
            deadline = time.monotonic() + 3
            while self.app.thread is not None and time.monotonic() < deadline:
                self.root.update(); time.sleep(.01)
            self.assertIsNone(self.app.thread)
            read.assert_not_called()
        self.assertIn("ключ", self.app.status.get())

    def test_no_unsigned_install_and_preferences_outside_app(self):
        self.app.install()
        self.assertIn("Сначала", self.app.status.get())
        self.app.save()
        self.assertTrue((Path(self.temp.name) / "preferences.json").exists())
        self.assertFalse((Path(self.temp.name) / "applications" / "preferences.json").exists())

    def test_cancel_button_targets_current_worker_event(self):
        def operation(manager, cancel):
            if cancel.wait(3):
                raise UpdateError("Операция отменена")
            raise AssertionError("Кнопка не отменила активную операцию")
        original = self.app.cancel
        self.app.work(operation)
        self.assertIsNot(self.app.cancel, original)
        self.app.cancel_button.invoke()
        self.finish()
        self.assertIn("отменена", self.app.status.get())
        self.assertIsNone(self.app.manager.state()["current"])

    def test_opt_in_startup_checks_only_and_without_key_does_not_request(self):
        fixture = Fixture()
        transport = ReleaseTransport(fixture)
        with patch("launcher.gui.TRUSTED_KEYS", fixture.keys), \
             patch("launcher.gui.Updates", side_effect=lambda keys: Updates(keys, transport)), \
             patch("launcher.install.subprocess.Popen") as popen:
            self.restart(False)
            self.assertIsNone(self.app.thread)
            self.assertEqual(transport.calls, [])
            self.restart(True)
            self.finish()
            self.assertEqual(len(transport.calls), 2)  # metadata + manifest, не ZIP
            self.assertIsNotNone(self.app.pending)
            self.assertIn("Ed25519 подтверждена", self.app.available_info.get())
            popen.assert_not_called()
        with patch("launcher.network.GitHub.read") as read:
            self.restart(True)
            self.assertIsNone(self.app.thread)
            read.assert_not_called()

    def test_external_exe_remains_unmanaged_and_bootstrap_never_replaces_it(self):
        folder = Path(self.temp.name) / "external"
        exe = folder / "ScreenTranslator" / "ScreenTranslator.exe"
        exe.parent.mkdir(parents=True); exe.write_bytes(b"unchanged-external-exe")
        with patch("launcher.gui.filedialog.askdirectory", return_value=str(folder)), \
             patch("launcher.gui.messagebox.askyesno", return_value=True), \
             patch("launcher.install.subprocess.Popen") as popen:
            popen.return_value.poll.return_value = 0
            self.app.launch_legacy(); self.finish()
            self.assertIn("не управляется", self.app.external_info.get())
            self.assertIsNone(self.app.manager.state()["current"])
            self.app.launch(); self.finish()
            self.assertEqual(popen.call_count, 2)
            with patch("launcher.network.GitHub.read") as read:
                self.app.bootstrap()
                self.assertIsNone(self.app.thread)
                self.assertIn("пустой каталог", self.app.status.get())
                read.assert_not_called()
        self.assertEqual(exe.read_bytes(), b"unchanged-external-exe")
        self.assertFalse((folder / "launcher-store.json").exists())

    def test_confirmed_first_install_starts_and_already_current_is_not_an_error(self):
        fixture = Fixture(); transport = ReleaseTransport(fixture)
        with patch("launcher.gui.TRUSTED_KEYS", fixture.keys):
            self.app.updates = Updates(fixture.keys, transport)
            self.app.flow = UpdateFlow(self.app.updates)
            self.app.check(); self.finish()
            pending = self.app.pending
            self.app.show_unchecked()  # focus-out без смены каталога не теряет offer
            self.assertIs(self.app.pending, pending)
            with patch("launcher.gui.messagebox.askyesno", return_value=False):
                self.app.install()
            self.assertEqual(len(transport.calls), 2)
            with patch("launcher.gui.messagebox.askyesno", return_value=True), \
                 patch("launcher.install.subprocess.Popen") as popen:
                popen.return_value.poll.return_value = 0
                self.app.install(); self.finish()
                popen.assert_called_once()
            self.assertIn("0.2.1", self.app.installed_info.get())
            self.assertIn("Первая управляемая версия запущена", self.app.status.get())
            self.assertIsNone(self.app.pending)
            self.app.updates = Updates(fixture.keys, transport)
            self.app.flow = UpdateFlow(self.app.updates)
            self.app.check(); self.finish()
            self.assertIn("актуальная версия", self.app.status.get())
            self.assertIsNone(self.app.pending)
