import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


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
