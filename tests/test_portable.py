"""CLI должен работать вне checkout, без GUI, провайдеров и сети."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from screen_translator import __version__
from screen_translator.__main__ import main, show_startup_error


class PortableTests(unittest.TestCase):
    def test_cli_sets_utf8_even_when_inherited_console_is_ascii(self):
        code = ("import sys; sys.stdout.reconfigure(encoding='ascii'); "
                "from screen_translator.__main__ import main; "
                "sys.argv=['screen_translator','--diagnose']; main(); print('Игрок: 中文')")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Игрок: 中文", result.stdout)

    def test_version_and_diagnosis_from_unicode_directory_without_gui(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="Проверка portable ") as folder:
            env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING="utf-8")
            for flag in ("--version", "--diagnose"):
                # Блокируем сеть и импорт GUI; --version не должен загружать даже Tk.
                code = ("import socket,sys,runpy; "
                        "socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network')); "
                        "sys.modules['screen_translator.gui']=None; "
                        f"sys.argv=['screen_translator', '{flag}']; "
                        "runpy.run_module('screen_translator',run_name='__main__')")
                result = subprocess.run([sys.executable, "-c", code], cwd=folder, env=env,
                                        capture_output=True, text=True, encoding="utf-8", timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                if flag == "--version":
                    self.assertEqual(result.stdout.strip(), __version__)
                else:
                    report = json.loads(result.stdout)
                    self.assertEqual(report["version"], __version__)
                    self.assertFalse(report["frozen"])

    def test_startup_import_error_is_reported_with_failure_status(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["screen_translator"]), \
             patch.dict(sys.modules, {"screen_translator.gui": None}), \
             contextlib.redirect_stderr(output):
            self.assertEqual(main(), 1)
        self.assertIn("Tkinter", output.getvalue())

    def test_windowed_startup_error_uses_windows_dialog_without_stderr(self):
        with patch.object(sys, "stderr", None), patch.object(sys, "frozen", True, create=True), \
             patch("ctypes.windll", create=True) as windll:
            show_startup_error("Ошибка 中文")
        self.assertEqual(windll.user32.MessageBoxW.call_args.args[1], "Ошибка 中文")
