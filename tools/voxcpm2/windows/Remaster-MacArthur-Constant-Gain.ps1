[CmdletBinding()]
param(
    [string]$PackageDir = "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL",
    [string]$WorkRoot = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [double]$OriginalGain = 0.18,
    [switch]$OpenOutput
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($OriginalGain -lt 0.0 -or $OriginalGain -gt 1.0) {
    throw "OriginalGain must be between 0.0 and 1.0"
}

$Python = Join-Path $CpuVenv "Scripts\python.exe"
$MasterScript = Join-Path $PackageDir "master_constant_mix.py"
$SourceVideo = Join-Path $WorkRoot "source\source.mp4"
$RussianTimeline = Join-Path $WorkRoot "audio\macarthur_ru_final_timeline.wav"
$MasterWorkDir = Join-Path $WorkRoot "master_work_gain_$($OriginalGain.ToString('0.00', [Globalization.CultureInfo]::InvariantCulture))"
$OutputDir = Join-Path $WorkRoot "output"
$GainTag = [int][Math]::Round($OriginalGain * 100)
$FinalMixed = Join-Path $OutputDir "MacArthur_Russian_Dub_FINAL_UPLOAD_ORIGINAL_${GainTag}pct.mp4"
$FinalRussianOnly = Join-Path $OutputDir "MacArthur_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4"

foreach ($Required in @($Python, $MasterScript, $SourceVideo, $RussianTimeline)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required file: $Required"
    }
}

New-Item -ItemType Directory -Force -Path $MasterWorkDir, $OutputDir | Out-Null

$GainArg = $OriginalGain.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)

Write-Host "Remaster only; no VoxCPM2 synthesis" -ForegroundColor Cyan
Write-Host "Original constant gain: $GainArg" -ForegroundColor Yellow

$MasterArgs = @(
    $MasterScript,
    "--source-video", $SourceVideo,
    "--russian-wav", $RussianTimeline,
    "--work-dir", $MasterWorkDir,
    "--mixed-video", $FinalMixed,
    "--russian-only-video", $FinalRussianOnly,
    "--original-level", $GainArg,
    "--target-i", "-14.0",
    "--target-lra", "9.0",
    "--target-tp", "-1.0"
)

& $Python @MasterArgs
if ($LASTEXITCODE -ne 0) {
    throw "Constant-gain remaster failed"
}

Write-Host "Remaster complete: $FinalMixed" -ForegroundColor Green

if ($OpenOutput) {
    Start-Process explorer.exe -ArgumentList $OutputDir
}
