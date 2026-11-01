"""Проверка распакованного ZIP в пути с пробелами/кириллицей, вне исходников."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile


def run(exe, *args):
    result = subprocess.run([str(exe), *args], cwd=exe.parent,
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    if result.returncode:
        raise RuntimeError(f"{exe.name} {args}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--argos", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="Переводчик portable ") as temp:
        with zipfile.ZipFile(args.archive) as archive:
            # Только собственный build-артефакт; не допускаем выход за каталог.
            for entry in archive.infolist():
                target = (Path(temp) / entry.filename).resolve()
                if not target.is_relative_to(Path(temp).resolve()):
                    raise RuntimeError("Небезопасный путь ZIP")
            archive.extractall(temp)
        folder = Path(temp) / "ScreenTranslator"
        console = folder / "ScreenTranslator-console.exe"
        version = run(console, "--version").stdout.strip()
        if version != "0.2.0-prototype":
            raise RuntimeError(f"Неверная версия: {version!r}")
        report = json.loads(run(console, "--diagnose").stdout)
        required = ["tkinter", "mss", "PIL", "pytesseract"]
        if args.argos:
            required.append("argostranslate")
        if not report["frozen"] or not all(report["modules"][name] for name in required):
            raise RuntimeError(f"Неполный portable: {report}")
        run(folder / "ScreenTranslator.exe", "--version")
        print(run(console, "--smoke-test").stdout.strip())
        # У оконного executable нет stdout, но исключение должно давать exit != 0.
        run(folder / "ScreenTranslator.exe", "--smoke-test")
        print(f"Portable smoke OK: {version}, frozen, Unicode path, оба EXE")


if __name__ == "__main__":
    main()
