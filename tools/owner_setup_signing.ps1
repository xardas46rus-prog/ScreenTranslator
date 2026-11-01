#Requires -Version 5.1
<#
  Локальная инициализация подписи ScreenTranslator (Windows 10 x64).
  НИЧЕГО не загружает в GitHub и не публикует. Приватный ключ остаётся на ПК.
  Запуск из PowerShell в локальном checkout:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\owner_setup_signing.ps1
#>
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')]
    [string]$KeyId = "owner-2026"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Запускайте скрипт на своем компьютере с Windows 10/11."
}
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    throw "Не задан USERPROFILE: не удалось безопасно выбрать каталог ключей."
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$signingDir = Join-Path $env:USERPROFILE "ScreenTranslatorSigning"
$privateKey = Join-Path $signingDir "owner-ed25519.pem"
$publicKey = Join-Path $signingDir "owner-public.json"
$venvDir = Join-Path $repo ".venv-release-tools"
$python = Join-Path $venvDir "Scripts\python.exe"

# Новый каталог — никаких попыток изменить, угадать или перезаписать существующие ключи.
if (Test-Path -LiteralPath $signingDir) {
    throw "Каталог '$signingDir' уже существует. Остановлено без изменений: проверьте существующие ключи вручную по инструкции."
}

Push-Location -LiteralPath $repo
try {
    if (-not (Get-Command "py.exe" -ErrorAction SilentlyContinue)) {
        throw "Не найден py.exe. Установите Python 3.12 x64 для Windows с Python Launcher."
    }
    & py.exe -3.12 -c "import sys,struct; assert sys.platform == 'win32' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8"
    if ($LASTEXITCODE -ne 0) { throw "Требуется Python 3.12 x64." }

    if (-not (Test-Path -LiteralPath $python)) {
        & py.exe -3.12 -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { throw "Не удалось создать локальное окружение Python." }
    }
    & $python -m pip install --disable-pip-version-check -r (Join-Path $repo "requirements-launcher.txt")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить библиотеки для подписи." }
    & $python (Join-Path $repo "tools\prepare_signed_release.py") --help
    if ($LASTEXITCODE -ne 0) { throw "Инструмент подписи не готов." }

    # Создаём отдельный новый каталог вне checkout и запрещаем наследование
    # посторонних разрешений. Если ACL не удаётся установить, ключ НЕ создаётся.
    New-Item -ItemType Directory -Path $signingDir -ErrorAction Stop | Out-Null
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $signingDir /inheritance:r /grant:r "${account}:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось ограничить права каталога. Не запускайте keygen до ручной проверки ACL."
    }

    Write-Host "Каталог ключей защищён: $signingDir"
    Write-Host "Далее пароль будет введён только локально (дважды), он нигде не сохраняется и не отправляется."
    & $python (Join-Path $repo "tools\prepare_signed_release.py") keygen --private-key $privateKey --public-out $publicKey --key-id $KeyId
    if ($LASTEXITCODE -ne 0) {
        throw "Генерация не завершена. Не повторяйте команду без проверки каталога и возможного частично созданного PEM."
    }

    # Дополнительная независимая от Python проверка отпечатка публичного ключа.
    $record = Get-Content -LiteralPath $publicKey -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($record.algorithm -ne "Ed25519" -or $record.key_id -ne $KeyId -or
        $record.public_key_hex -cnotmatch '^[0-9a-f]{64}$') {
        throw "Неверный формат созданной публичной записи."
    }
    $raw = New-Object byte[] 32
    for ($i = 0; $i -lt 32; $i++) {
        $raw[$i] = [Convert]::ToByte($record.public_key_hex.Substring($i * 2, 2), 16)
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $fingerprint = "SHA256:" + [BitConverter]::ToString($sha.ComputeHash($raw)).Replace("-", "").ToLowerInvariant()
    }
    finally { $sha.Dispose() }
    if ($fingerprint -cne $record.fingerprint) {
        throw "Не совпал публичный отпечаток. Не используйте ключ до проверки."
    }

    Write-Host ""
    Write-Host "Локальный зашифрованный PEM создан. НИКУДА ЕГО НЕ ЗАГРУЖАЙТЕ."
    Write-Host "Публичный JSON: $publicKey"
    Write-Host "Публичный отпечаток (сохраните отдельно для сравнения): $fingerprint"
    Write-Host "Следующий этап — ручная сверка публичного JSON и отдельный PR для launcher/trust.py."
    Write-Host "Настоящие обновления всё ещё выключены, пока не будет одобренного подписанного Release."
}
finally { Pop-Location }
