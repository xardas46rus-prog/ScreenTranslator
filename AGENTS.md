# Codex instructions: ScreenTranslator

This repository hosts a Windows 10/11 x64 Python OCR translator: English/Chinese to Russian, Tkinter overlay, fast-chat OCR, Argos and optional Ollama/GigaChat/OpenAI, plus signed GitHub Releases launcher.

## Rules
- Russian UI and documentation; use UTF-8 for English, Russian and Chinese glyphs.
- Do not commit API credentials, DPAPI secrets, local chat logs, .venv, models, installer outputs, signing private keys or user settings.
- The app reads pixels from a user-selected screen region. Never add memory reading, game injection, anticheat bypass or automated in-game actions.
- Cloud API calls require explicit per-provider opt-in and each user's own credentials. Do not embed shared tokens.
- Preserve compatibility with Windows 10 x64, Python 3.12, Tesseract and Argos models on upgrades.
- Prevent OCR overload, duplicate messages, hidden-chat garbage, transliteration errors and missed updates. Use bounded queues, stable deduplication, and test representative chat frames.
- When changing GUI, update dependent files together. Prior failures included missing overlay.clamp_font_size and gui.RELEASE.
- Keep launcher signed update verification, rollback, and model preservation.
- Run compileall and unit tests before PRs. Do not claim the Windows EXE works unless tested on Windows.
- Work in feature branches and open PRs for review; do not publish GitHub Releases without explicit developer approval.

## Test / build
- Translator entry: python -m screen_translator
- Launcher entry: python -m launcher
- Python syntax: python -m compileall -q screen_translator launcher tools
- Translator tests: python -m unittest discover -s tests -p "test_*.py"
- Launcher tests: python -m unittest discover -s tests_launcher -p "test_*.py"
- Windows release: build_exe.bat and build_launcher.bat; confirm OCR, Argos, Ollama and overlay in Windows before distributing.

Before starting: read README.md and inspect current version and tests. If source code is not present, ask to import the project archive rather than invent files.
