@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
py -3.12 -c "import sys,struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8, 'Требуется Windows и Python 3.12 x64'"
if errorlevel 1 goto :fail
echo Сборка отдельного лаунчера: без переводчика, моделей и тяжёлых движков.
py -3.12 -m venv .venv-build-launcher
if errorlevel 1 goto :fail
".venv-build-launcher\Scripts\python.exe" -m pip install -r requirements-launcher-build.txt
if errorlevel 1 goto :fail
".venv-build-launcher\Scripts\python.exe" tools\build_launcher.py
if errorlevel 1 goto :fail
echo Лаунчер собран: dist\launcher. Это unsigned test-only прототип, не Release.
exit /b 0
:fail
echo Сборка лаунчера остановлена. Проверьте Python 3.12 x64 и сообщения выше.
exit /b 1
