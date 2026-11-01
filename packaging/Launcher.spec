from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis([str(root / "packaging" / "launcher_entry.py")], pathex=[str(root)],
             binaries=[], datas=[], hiddenimports=[], hookspath=[], runtime_hooks=[],
             excludes=["screen_translator", "torch", "argostranslate", "mss", "PIL", "pytesseract"],
             noarchive=False)
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenTranslatorLauncher",
          console=False, debug=False, strip=False, upx=False)
console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScreenTranslatorLauncher-console",
              console=True, debug=False, strip=False, upx=False)
coll = COLLECT(gui, console, a.binaries, a.datas, name="ScreenTranslatorLauncher", strip=False, upx=False)
