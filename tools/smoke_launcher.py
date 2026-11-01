"""Распакованный лаунчер вне checkout, путь с кириллицей, без сетевых обновлений."""

import argparse
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="Лаунчер portable ") as folder:
        with zipfile.ZipFile(args.archive) as archive:
            for entry in archive.infolist():
                if not (Path(folder) / entry.filename).resolve().is_relative_to(Path(folder).resolve()):
                    raise RuntimeError("ZIP traversal")
            archive.extractall(folder)
        app = Path(folder) / "ScreenTranslatorLauncher"
        for name in ("ScreenTranslatorLauncher-console.exe", "ScreenTranslatorLauncher.exe"):
            for option in ("--version", "--smoke-test"):
                result = subprocess.run([str(app / name), option], cwd=app, capture_output=True,
                                        text=True, encoding="utf-8", timeout=45)
                if result.returncode:
                    raise RuntimeError(f"{name} {option}: {result.returncode}\n{result.stdout}\n{result.stderr}")
                if "console" in name and option == "--version" and result.stdout.strip() != "0.1.0":
                    raise RuntimeError("Неверная версия лаунчера")
                if "console" in name and option == "--smoke-test" and "Launcher GUI smoke OK" not in result.stdout:
                    raise RuntimeError("GUI smoke не выполнен")
        print("Launcher frozen smoke OK: оба EXE, Unicode path, GUI, без обновлений")


if __name__ == "__main__":
    main()
