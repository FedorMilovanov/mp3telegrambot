#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$WorkRoot = "C:\AI-Archive\MacArthur-Quality-Sweep",
    [string]$VoxArchive = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [int]$Threads = 10,
    [string]$CfgValues = "1.55,1.75,1.95",
    [string]$StepsValues = "10"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$Python = Join-Path $CpuVenv "Scripts\python.exe"
$SweepScript = Join-Path $RepoRoot "tools\voxcpm2\quality_sweep.py"
$OutputDir = Join-Path $WorkRoot "outputs"
$ReferenceDir = Join-Path $WorkRoot "references"
$Reference = Join-Path $ReferenceDir "B_extended_24s.wav"
$SourceVideo = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V32\source\source.mp4"

if (-not (Test-Path -LiteralPath $SourceVideo)) {
    $SourceVideo = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2\source\source.mp4"
}

foreach ($Required in @($Python, $SweepScript, $SourceVideo)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Не найден обязательный файл: $Required"
    }
}

foreach ($Tool in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "$Tool не найден в PATH."
    }
}

New-Item -ItemType Directory -Force -Path `
    $WorkRoot, $OutputDir, $ReferenceDir | Out-Null

$env:CUDA_VISIBLE_DEVICES = "-1"
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:OMP_NUM_THREADS = "$Threads"
$env:MKL_NUM_THREADS = "$Threads"
$env:TOKENIZERS_PARALLELISM = "false"

Write-Host "=== Готовлю B extended reference ===" -ForegroundColor Cyan

& ffmpeg -hide_banner -loglevel error -y `
    -ss 0 -t 24.0 -i $SourceVideo `
    -vn -ac 1 -ar 16000 `
    -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
    $Reference

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось подготовить B-референс."
}

Write-Host "=== Короткий CFG/Steps sweep, только CPU ===" `
    -ForegroundColor Cyan

& $Python $SweepScript `
    --archive-root $VoxArchive `
    --reference-wav $Reference `
    --output-dir $OutputDir `
    --threads $Threads `
    --cfg-values $CfgValues `
    --steps-values $StepsValues

if ($LASTEXITCODE -ne 0) {
    throw "Quality sweep завершился с ошибкой."
}

Write-Host ""
Write-Host "Готово: $OutputDir" -ForegroundColor Green
Write-Host "Сначала сравни CFG 1.55 / 1.75 / 1.95." -ForegroundColor Green
Write-Host "Потом отдельным запуском добавь -StepsValues '10,16'." `
    -ForegroundColor Yellow

Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
