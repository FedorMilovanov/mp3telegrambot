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

$ScriptDir = $PSScriptRoot
$RuntimeRoot = "C:\AI-Archive\John-Piper-VoxCPM2-CPU-Package"
$RuntimeZip = Join-Path $RuntimeRoot "John_Piper_VoxCPM2_CPU_FINAL.zip"
$RuntimeDir = Join-Path $RuntimeRoot "runtime"
$InnerLauncher = Join-Path $RuntimeDir "Run-John-Piper-FINAL-CPU-Inner.ps1"
$ExpectedSha256 = "e5247345d451cd00805d9157cf279a19aad6ee2ca9935a0167ee7b38fda9294f"
$ExpectedEncodedLength = 26336

$PartFiles = Get-ChildItem -LiteralPath $ScriptDir -File -Filter "package.part*.b64"
$PartFiles = @($PartFiles | Sort-Object -Property Name)

if ($PartFiles.Count -ne 7) {
    throw "Ожидалось 7 частей production-пакета, найдено: $($PartFiles.Count). Выполните git pull заново."
}

for ($Index = 1; $Index -le 7; $Index++) {
    $ExpectedName = "package.part{0:D2}.b64" -f $Index
    $ActualName = $PartFiles[$Index - 1].Name
    if ($ActualName -ne $ExpectedName) {
        throw "Неверная часть production-пакета: ожидалась $ExpectedName, найдена $ActualName."
    }
}

$Builder = [System.Text.StringBuilder]::new()
foreach ($PartFile in $PartFiles) {
    $Chunk = [System.IO.File]::ReadAllText($PartFile.FullName).Trim()
    if ([string]::IsNullOrWhiteSpace($Chunk)) {
        throw "Пустая часть production-пакета: $($PartFile.Name)"
    }
    $null = $Builder.Append($Chunk)
}
$Encoded = $Builder.ToString()

if ($Encoded.Length -ne $ExpectedEncodedLength) {
    throw "Production-пакет собран не полностью: ожидалось $ExpectedEncodedLength Base64-символов, получено $($Encoded.Length)."
}

if (($Encoded.Length % 4) -ne 0) {
    throw "Длина собранного Base64-пакета некорректна."
}

try {
    $Bytes = [Convert]::FromBase64String($Encoded)
}
catch {
    throw "Production-пакет не декодируется из Base64. Удалите локальные package.part*.b64 и выполните git pull заново."
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$TempZip = "$RuntimeZip.tmp"
[System.IO.File]::WriteAllBytes($TempZip, $Bytes)

$ActualSha256 = (Get-FileHash -LiteralPath $TempZip -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualSha256 -ne $ExpectedSha256) {
    Remove-Item -LiteralPath $TempZip -Force -ErrorAction SilentlyContinue
    throw "Контрольная сумма production-пакета не совпала: $ActualSha256"
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

if ($KeepDiagnostics) {
    & $InnerLauncher `
        -Url $Url `
        -WorkRoot $WorkRoot `
        -VoxArchive $VoxArchive `
        -CpuVenv $CpuVenv `
        -Threads $Threads `
        -Steps $Steps `
        -Cfg $Cfg `
        -OriginalLevel $OriginalLevel `
        -KeepDiagnostics
}
else {
    & $InnerLauncher `
        -Url $Url `
        -WorkRoot $WorkRoot `
        -VoxArchive $VoxArchive `
        -CpuVenv $CpuVenv `
        -Threads $Threads `
        -Steps $Steps `
        -Cfg $Cfg `
        -OriginalLevel $OriginalLevel
}
