# Сборка только из явных исходников; не добавлять дерево checkout или HOME.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = [], [], []
hiddenimports += ["mss", "mss.windows", "PIL.Image", "pytesseract"]
if os.environ.get("SCREENTRANSLATOR_BUILD_ARGOS") == "1":
    # Динамические импорты и native DLL локального движка; никаких моделей.
    for package in ("argostranslate", "ctranslate2", "sentencepiece", "stanza", "torch", "sacremoses"):
        data, binary, hidden = collect_all(package)
        datas += data
        binaries += binary
        hiddenimports += hidden
    datas += copy_metadata("argostranslate", recursive=True)

a = Analysis(
    [str(root / "packaging" / "portable_entry.py")],
    pathex=[str(root)], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], runtime_hooks=[],
    excludes=["pytest", "IPython", "matplotlib", "tests"], noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenTranslator",
          console=False, debug=False, strip=False, upx=False)
console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenTranslator-console",
              console=True, debug=False, strip=False, upx=False)
coll = COLLECT(gui, console, a.binaries, a.datas, strip=False, upx=False,
               name="ScreenTranslator")
