# ScreenTranslator — EN / 中文 → RU

Экранный переводчик для Windows 10/11 x64: распознавание выбранной области экрана, автоматический перевод английского и китайского текста на русский, режим оверлея и отдельного окна, быстрый игровой чат.

**Целевая версия исходников:** v3.3.2. Поддержка Argos (локально), Ollama (локально), а также подключаемых OpenAI/GigaChat с персональными API-ключами. Предусмотрен отдельный лаунчер для подписанных обновлений через GitHub Releases.

## Состояние репозитория

Инструкции для Codex и GitHub Actions уже настроены. **Если папок `screen_translator/` и `launcher/` пока нет, исходники ещё не загружены.** Для однократного импорта используйте `ScreenTranslator-Codex-Ready-Import-v3.3.2.zip` и скрипт `UPLOAD_CODEX_TO_GITHUB.bat`, подготовленные в проекте.

Читайте [AGENTS.md](AGENTS.md), [CODEX_START_RU.md](CODEX_START_RU.md) и [CONTRIBUTING.md](CONTRIBUTING.md).

### После импорта исходников

```bat
python -m compileall -q screen_translator launcher tools
python -m unittest discover -s tests -p "test_core.py"
python -m unittest discover -s tests_launcher -p "test_*.py"
```

GUI, OCR через Tesseract, Argos, Ollama и EXE-пакетирование требуют отдельного тестирования на Windows. Автоматическая сборка в GitHub Actions не является подтверждением работы Windows-оверлея.

**Приватность:** никогда не загружайте `.venv`, пользовательские настройки, API-ключи, модели Tesseract/Argos/Ollama и закрытые ключи подписи. Облачные запросы выполняются только после согласия пользователя и с его собственным ключом.

Публичные релизы публикуются отдельно после ручной проверки.
