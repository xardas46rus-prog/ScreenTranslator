"""Абсолютная точка входа PyInstaller; не зависит от текущего каталога."""

from multiprocessing import freeze_support

from screen_translator.__main__ import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
