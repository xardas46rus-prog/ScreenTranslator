#Requires -Version 5.1
<#
  На Windows: после ручного создания ОДОБРЕННОГО Draft Release и загрузки
  неизменённых OCR/Argos ZIP, подготовить и проверить ДВА подписанных манифеста.
  Приватный ключ читается ТОЛЬКО локально. Ничего не публикуется и не загружается.
  Не запускайте до получения реальных asset IDs из Draft Release.
#>
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version,
    [Parameter(Mandatory=$true)][string]$OcrZip,
    [Parameter(Mandatory=$true)][string]$ArgosZip,
    [Parameter(Mandatory=$true)][ValidateRange(1,9223372036854775807)][long]$OcrAssetId,
    [Parameter(Mandatory=$true)][ValidateRange(1,9223372036854775807)][long]$ArgosAssetId,
    [string]$KeyId = "owner-2026"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Скрипт запускается только на ПК владельца Windows." }

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repo ".venv-release-tools\Scripts\python.exe"
$utility = Join-Path $repo "tools\prepare_signed_release.py"
$signingDir = Join-Path $env:USERPROFILE "ScreenTranslatorSigning"
$private = Join-Path $signingDir "owner-ed25519.pem"
$public = Join-Path $signingDir "owner-public.json"
$outDir = Join-Path $env:USERPROFILE ("ScreenTranslatorRelease-" + $Version)

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Сначала запустите tools/owner_setup_signing.ps1 в локальном checkout."
}
foreach ($path in @($private, $public, $OcrZip, $ArgosZip)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Не найден входной файл: $path"
    }
}
if ((Split-Path -Leaf $OcrZip) -cne "ScreenTranslator-$Version-ocr-windows-x64.zip") {
    throw "Имя OCR ZIP должно соответствовать версии и профилю."
}
if ((Split-Path -Leaf $ArgosZip) -cne "ScreenTranslator-$Version-argos-windows-x64.zip") {
    throw "Имя Argos ZIP должно соответствовать версии и профилю."
}
if ($OcrAssetId -eq $ArgosAssetId) { throw "У двух ZIP не могут быть одинаковые asset IDs." }
if (Test-Path -LiteralPath $outDir) {
    throw "Каталог вывода уже существует. Не перезаписываем готовые manifest/signature: $outDir"
}

Write-Host "Сверьте неизменность загруженных в Draft Release ZIP и реальные asset IDs."
Write-Host "Публичный отпечаток должен быть ранее записан вами при keygen."
$fingerprint = Read-Host "Введите РАНЕЕ СОХРАНЁННЫЙ публичный отпечаток SHA256:..."
if ($fingerprint -cnotmatch '^SHA256:[0-9a-f]{64}$') {
    throw "Нужен ранее проверенный SHA256:<64 lowercase hex>; нельзя принимать отпечаток автоматически."
}

New-Item -ItemType Directory -Path $outDir -ErrorAction Stop | Out-Null

function Invoke-Step {
    param([string]$Description, [string[]]$Arguments)
    Write-Host "== $Description"
    & $python $utility @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Операция '$Description' завершилась ошибкой. Выходные файлы не перезаписывать."
    }
}

foreach ($item in @(
    @{profile="ocr";archive=$OcrZip;asset=$OcrAssetId},
    @{profile="argos";archive=$ArgosZip;asset=$ArgosAssetId}
)) {
    $p = $item.profile
    $manifest = Join-Path $outDir "manifest-$p.json"
    $signed = Join-Path $outDir "update-$p.json"
    Invoke-Step "Проверка ZIP и подготовка manifest: $p" @(
        "inspect", "--archive", $item.archive, "--version", $Version,
        "--profile", $p, "--asset-id", [string]$item.asset, "--out", $manifest
    )
    Invoke-Step "Локальная подпись Ed25519: $p" @(
        "sign", "--manifest", $manifest, "--archive", $item.archive,
        "--private-key", $private, "--key-id", $KeyId, "--out", $signed
    )
    Invoke-Step "Проверка подписи, SHA256 и ZIP: $p" @(
        "verify", "--manifest", $signed, "--archive", $item.archive,
        "--public-key", $public, "--fingerprint", $fingerprint
    )
}
Write-Host ""
Write-Host "Оба профиля проверены. Публичные манифесты находятся в: $outDir"
Write-Host "Загрузите в ОДОБРЕННЫЙ Draft Release только update-ocr.json и update-argos.json."
Write-Host "НЕ загружайте PEM, пароли, owner-public.json, неподписанные manifest или модели."
Write-Host "Публикация настоящего Release требует отдельного явного решения владельца."
