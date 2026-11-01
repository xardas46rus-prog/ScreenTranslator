"""Windows onedir: сборка, диагностика и ZIP только явного результата PyInstaller."""

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise SystemExit("Сборка EXE требует Windows и Python 3.12 x64; cross-build не поддерживается")
    os.chdir(ROOT)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "packaging/ScreenTranslator.spec"], check=True)
    folder = ROOT / "dist" / "ScreenTranslator"
    console = folder / "ScreenTranslator-console.exe"
    from screen_translator import __version__
    # Рабочий каталог не содержит исходников; не запускаем OCR/сеть/модели.
    version = subprocess.run([str(console), "--version"], cwd=folder,
                             capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    if version.stdout.strip() != __version__:
        raise RuntimeError(f"Неверная версия EXE: {version.stdout!r}")
    diagnosis = subprocess.run([str(console), "--diagnose"], cwd=folder,
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=True)
    report = json.loads(diagnosis.stdout)
    if not report["frozen"] or not all(report["modules"][p] for p in ("tkinter", "mss", "PIL", "pytesseract")):
        raise RuntimeError(f"Неполная сборка: {report}")
    if os.environ.get("SCREENTRANSLATOR_BUILD_ARGOS") == "1" and not report["modules"]["argostranslate"]:
        raise RuntimeError("Argos не включён в профиль --argos")
    shutil.copyfile(ROOT / "docs" / "EXE_BUILD_RU.md", folder / "ПРОЧИТАЙТЕ.md")
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE.txt")
    profile = "argos" if os.environ.get("SCREENTRANSLATOR_BUILD_ARGOS") == "1" else "ocr"
    shutil.make_archive(str(ROOT / "dist" / f"ScreenTranslator-{profile}-prototype-unsigned-test-only"),
                        "zip", root_dir=folder.parent, base_dir=folder.name)
    print(version.stdout.strip())
    print(diagnosis.stdout.strip())


if __name__ == "__main__":
    # Для прямого запуска python tools/build_portable.py, без установки проекта.
    sys.path.insert(0, str(ROOT))
    main()
