[CmdletBinding()]
param(
    [string]$Url = "https://youtube.com/shorts/Z20Py4yQhYQ",
    [string]$WorkRoot = "C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL",
    [string]$VoxArchive = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [int]$Threads = 10,
    [int]$Steps = 16,
    [double]$Cfg = 1.80,
    [double]$OriginalLevel = 0.18,
    [switch]$KeepDiagnostics
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackagePayload = Join-Path $ScriptDir "John_Piper_VoxCPM2_CPU_FINAL.zip"
$RuntimeRoot = "C:\AI-Archive\John-Piper-VoxCPM2-CPU-Package"
$RuntimeZip = Join-Path $RuntimeRoot "John_Piper_VoxCPM2_CPU_FINAL.zip"
$RuntimeDir = Join-Path $RuntimeRoot "runtime"
$InnerLauncher = Join-Path $RuntimeDir "Run-John-Piper-FINAL-CPU-Inner.ps1"
$ExpectedSha256 = "e5247345d451cd00805d9157cf279a19aad6ee2ca9935a0167ee7b38fda9294f"

if (-not (Test-Path -LiteralPath $PackagePayload)) {
    throw "Не найден встроенный пакет: $PackagePayload"
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

# Репозиторий хранит компактный ZIP как Base64-текст. Декодируем атомарно.
$Encoded = (Get-Content -LiteralPath $PackagePayload -Raw).Trim()
try {
    $Bytes = [Convert]::FromBase64String($Encoded)
}
catch {
    throw "Встроенный пакет повреждён: Base64 не декодируется. Выполните git pull заново."
}

$TempZip = "$RuntimeZip.tmp"
[IO.File]::WriteAllBytes($TempZip, $Bytes)
$ActualSha256 = (Get-FileHash -LiteralPath $TempZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) {
    Remove-Item -LiteralPath $TempZip -Force -ErrorAction SilentlyContinue
    throw "Контрольная сумма пакета не совпала: $ActualSha256"
}
Move-Item -LiteralPath $TempZip -Destination $RuntimeZip -Force

if (Test-Path -LiteralPath $RuntimeDir) {
    Remove-Item -LiteralPath $RuntimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Expand-Archive -LiteralPath $RuntimeZip -DestinationPath $RuntimeDir -Force
Get-ChildItem -LiteralPath $RuntimeDir -Recurse -File | Unblock-File

if (-not (Test-Path -LiteralPath $InnerLauncher)) {
    throw "Пакет распакован, но внутренний launcher не найден: $InnerLauncher"
}

$Forward = @{
    Url = $Url
    WorkRoot = $WorkRoot
    VoxArchive = $VoxArchive
    CpuVenv = $CpuVenv
    Threads = $Threads
    Steps = $Steps
    Cfg = $Cfg
    OriginalLevel = $OriginalLevel
}
if ($KeepDiagnostics) {
    $Forward["KeepDiagnostics"] = $true
}

& $InnerLauncher @Forward
if ($LASTEXITCODE -ne 0) {
    throw "John Piper VoxCPM2 CPU production завершился с кодом $LASTEXITCODE."
}
