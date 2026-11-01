"""Минимальная Windows-сборка лаунчера в отдельном окружении."""

import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise SystemExit("Нужны Windows и Python 3.12 x64")
    os.chdir(ROOT)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", "dist/launcher", "--workpath", "build/launcher", "packaging/Launcher.spec"], check=True)
    folder = ROOT / "dist" / "launcher" / "ScreenTranslatorLauncher"
    shutil.copyfile(ROOT / "docs/LAUNCHER_RU.md", folder / "ПРОЧИТАЙТЕ.md")
    shutil.copyfile(ROOT / "LICENSE", folder / "LICENSE.txt")
    archive = ROOT / "dist/launcher/ScreenTranslatorLauncher-prototype-unsigned-test-only"
    shutil.make_archive(str(archive), "zip", root_dir=folder.parent, base_dir=folder.name)
    subprocess.run([sys.executable, "tools/smoke_launcher.py", str(archive) + ".zip"], check=True)


if __name__ == "__main__":
    main()
