"""Запуск: python -m screen_translator."""

import argparse
import sys

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="ScreenTranslator — чистый стартовый прототип")
    parser.add_argument("--version", action="version", version=__version__)
    parser.parse_args()
    try:
        from .gui import main as gui_main
        gui_main()
    except ImportError as exc:
        print(f"Не удалось загрузить интерфейс Tkinter: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # При отсутствии дисплея сообщение доступно и в Linux-терминале.
        print(f"Не удалось открыть окно ScreenTranslator: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
