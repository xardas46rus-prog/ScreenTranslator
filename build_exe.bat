@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not "%~1"=="" if /i not "%~1"=="--argos" (
  echo Использование: build_exe.bat [--argos]
  exit /b 2
)
py -3.12 -c "import sys,struct; assert sys.platform == 'win32' and struct.calcsize('P') == 8, 'Требуется Windows и Python 3.12 x64'"
if errorlevel 1 goto :fail
set "BUILD_ENV=.venv-build-portable"
set "BUILD_REQUIREMENTS=requirements-build.txt"
set "SCREENTRANSLATOR_BUILD_ARGOS=0"
if /i "%~1"=="--argos" (
  set "BUILD_ENV=.venv-build-portable-argos"
  set "BUILD_REQUIREMENTS=requirements-build-argos.lock.txt"
  set "SCREENTRANSLATOR_BUILD_ARGOS=1"
)
echo Подготовка изолированного окружения сборки %BUILD_ENV%...
py -3.12 -m venv "%BUILD_ENV%"
if errorlevel 1 goto :fail
"%BUILD_ENV%\Scripts\python.exe" -m pip install -r "%BUILD_REQUIREMENTS%"
if errorlevel 1 goto :fail
"%BUILD_ENV%\Scripts\python.exe" tools\build_portable.py
if errorlevel 1 goto :fail
echo Готово: dist\ScreenTranslator. Это неподписанный прототип, только для тестирования.
exit /b 0
:fail
echo Сборка остановлена. Проверьте сообщения выше, Python 3.12 x64 и доступ к PyPI.
exit /b 1
