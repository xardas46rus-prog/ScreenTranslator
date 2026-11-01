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
- Launcher is an independent offline/test-only prototype. Read docs/LAUNCHER_RU.md before changing update code.
- Never weaken manifest signature, ZIP validation or replay checks. Production launcher/trust.py intentionally has no keys; no test trust override in the shipped CLI/GUI. Test keys are generated only inside tests.
- Keep immutable installed versions and atomic state switching. Never delete unknown files or move user models into replaceable application directories. Rollback must retain the high-water version.
- Launcher dependencies: requirements-launcher.txt; Windows build: build_launcher.bat, dedicated venv/spec without translator/Argos/torch. Release publication and real key enrollment require owner review.
- Translator entry: python -m screen_translator
- Launcher entry: python -m launcher
- Python syntax: python -m compileall -q screen_translator launcher tools
- Translator tests: python -m unittest discover -s tests -p "test_*.py"
- Launcher tests: python -m unittest discover -s tests_launcher -p "test_*.py"
- Windows release: build_exe.bat and build_launcher.bat; confirm OCR, Argos, Ollama and overlay in Windows before distributing.

## Missing original source / старт Codex без архива
Before starting: read README.md, CODEX_CHAT_START_RU.md and inspect current version and tests.
- If original source code is missing and the task is **migration or audit of v3.3.2**, request the project archive. Never fabricate source code and claim it is v3.3.2.
- If the user explicitly requests **development before archive import** (see GitHub issue #2), a new clean-room prototype may be built in a feature branch with tests and a PR. Clearly label it as a prototype, not migrated v3.3.2.
- Do not overwrite imported original source or merge incompatible bootstrap code without inspecting changes and asking for review.
- Work with the repository selected in the Codex environment; never assume that a linked GitHub account alone grants Codex access.
