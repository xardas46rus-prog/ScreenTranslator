"""Запуск: python -m screen_translator."""

import argparse
from multiprocessing import freeze_support
import sys

from . import __version__


def main():
    freeze_support()
    parser = argparse.ArgumentParser(description="ScreenTranslator — чистый стартовый прототип")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--diagnose", action="store_true", help="Локальная диагностика без OCR, моделей и сети")
    parser.add_argument("--smoke-test", action="store_true", help="Проверка GUI и оверлея без OCR и сети; нужен дисплей")
    args = parser.parse_args()
    if args.diagnose:
        from .diagnostics import report
        print(report())
        return 0
    if args.smoke_test:
        from .diagnostics import gui_smoke_test
        gui_smoke_test()
        return 0
    try:
        from .gui import main as gui_main
        gui_main()
    except ImportError as exc:
        show_startup_error(f"Не удалось загрузить интерфейс Tkinter: {exc}")
        return 1
    except Exception as exc:
        # При отсутствии дисплея сообщение доступно и в Linux-терминале.
        show_startup_error(f"Не удалось открыть окно ScreenTranslator: {exc}")
        return 1
    return 0


def show_startup_error(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    elif getattr(sys, "frozen", False):
        # У windowed EXE нет stderr; ошибку нельзя молча потерять.
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "ScreenTranslator — ошибка запуска", 0x10)


if __name__ == "__main__":
    raise SystemExit(main())
