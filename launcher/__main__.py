"""Обычный запуск не выполняет сетевых запросов."""

import argparse
from multiprocessing import freeze_support
import sys

from . import __version__


def main():
    freeze_support()
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Лаунчер ScreenTranslator — test only")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--smoke-test", action="store_true", help="Локальная проверка GUI без сети")
    args = parser.parse_args()
    try:
        if args.smoke_test:
            import tempfile
            import tkinter as tk
            from .gui import App
            with tempfile.TemporaryDirectory(prefix="Лаунчер smoke ") as folder:
                root = tk.Tk()
                app = App(root, folder)
                root.update()
                if app.automatic.get() or app.thread is not None:
                    raise RuntimeError("Нежелательная фоновая операция при старте")
                app.close()
            print("Launcher GUI smoke OK — без сети")
        else:
            from .gui import main as gui_main
            gui_main()
        return 0
    except Exception as exc:
        message = f"Не удалось запустить лаунчер: {exc}"
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "ScreenTranslator", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
