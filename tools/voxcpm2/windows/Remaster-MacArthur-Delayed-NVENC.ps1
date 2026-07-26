[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$PackageDir = "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL",
    [string]$WorkRoot = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [double]$OriginalGain = 0.25,
    [int[]]$DelayMs = @(220, 160, 100, 40),
    [switch]$OpenOutput
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

if ($DelayMs.Count -ne 4) {
    throw "DelayMs must contain exactly four values"
}

$Python = Join-Path $CpuVenv "Scripts\python.exe"
$Tool = Join-Path $RepoRoot "tools\voxcpm2\remaster_delayed_nvenc.py"
$SegmentsJson = Join-Path $RepoRoot "tools\voxcpm2\examples\macarthur_raasabpj_iw\segments_ru_final.json"
$MasterScript = Join-Path $PackageDir "master_constant_mix.py"
$SourceVideo = Join-Path $WorkRoot "source\source.mp4"
$RussianTimeline = Join-Path $WorkRoot "audio\macarthur_ru_final_timeline.wav"
$RemasterWork = Join-Path $WorkRoot "remaster_delayed_nvenc"
$OutputDir = Join-Path $WorkRoot "output"
$NvencOutput = Join-Path $OutputDir "MacArthur_FINAL_ENG25_DELAYED_NVENC.mp4"

foreach ($Required in @(
    $Python,
    $Tool,
    $SegmentsJson,
    $MasterScript,
    $SourceVideo,
    $RussianTimeline
)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required file: $Required"
    }
}

foreach ($ToolName in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $ToolName -ErrorAction SilentlyContinue)) {
        throw "Missing PATH tool: $ToolName"
    }
}

New-Item -ItemType Directory -Force -Path $RemasterWork, $OutputDir | Out-Null

$GainArg = $OriginalGain.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)
$DelayArg = $DelayMs -join ","
$StartedAt = Get-Date
$SavedCudaVisibleDevices = [Environment]::GetEnvironmentVariable(
    "CUDA_VISIBLE_DEVICES",
    "Process"
)
$HadCudaVisibleDevices = $null -ne $SavedCudaVisibleDevices

Write-Host "RTX 3060 SAFE NVENC TRIAL" -ForegroundColor Cyan
Write-Host "VoxCPM2 is NOT being regenerated" -ForegroundColor Green
Write-Host "Original English gain: $GainArg" -ForegroundColor Yellow
Write-Host "Russian block delays, ms: $DelayArg" -ForegroundColor Yellow
Write-Host "Decode and audio filters: CPU" -ForegroundColor Yellow
Write-Host "Video encode only: h264_nvenc" -ForegroundColor Yellow

if ($HadCudaVisibleDevices) {
    Write-Host "Inherited CUDA_VISIBLE_DEVICES=$SavedCudaVisibleDevices" -ForegroundColor Yellow
    Write-Host "It will be removed only while the remaster/NVENC child process runs" -ForegroundColor Yellow
}

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "GPU before encode:" -ForegroundColor Cyan
    & nvidia-smi `
        --query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,utilization.gpu,memory.used `
        --format=csv
}
else {
    Write-Warning "nvidia-smi was not found; FFmpeg will still test h264_nvenc"
}

$Args = @(
    $Tool,
    "--source-video", $SourceVideo,
    "--russian-wav", $RussianTimeline,
    "--segments-json", $SegmentsJson,
    "--master-script", $MasterScript,
    "--work-dir", $RemasterWork,
    "--output-dir", $OutputDir,
    "--original-gain", $GainArg,
    "--delays-ms", $DelayArg,
    "--nvenc-preset", "p5",
    "--nvenc-cq", "18",
    "--video-bitrate", "8M",
    "--maxrate", "14M",
    "--bufsize", "28M"
)

try {
    Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue

    & $Python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Delayed remaster failed"
    }
}
finally {
    if ($HadCudaVisibleDevices) {
        [Environment]::SetEnvironmentVariable(
            "CUDA_VISIBLE_DEVICES",
            $SavedCudaVisibleDevices,
            "Process"
        )
    }
    else {
        Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    }
}

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "GPU after encode:" -ForegroundColor Cyan
    & nvidia-smi `
        --query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,utilization.gpu,memory.used `
        --format=csv
}

$SystemEvents = @(
    Get-WinEvent `
        -FilterHashtable @{ LogName = "System"; StartTime = $StartedAt } `
        -ErrorAction SilentlyContinue
)
$GpuEvents = @(
    $SystemEvents |
    Where-Object {
        ($_.ProviderName -in @("nvlddmkm", "Display")) -and
        ($_.Id -in @(14, 153, 4101))
    }
)

if ($GpuEvents.Count -gt 0) {
    Write-Warning "GPU/Display events were recorded during the trial"
    $GpuEvents |
        Select-Object TimeCreated, ProviderName, Id, LevelDisplayName, Message |
        Format-List
}
else {
    Write-Host "No nvlddmkm/Display 14, 153 or 4101 events detected" -ForegroundColor Green
}

Write-Host ""
Write-Host "FILES READY" -ForegroundColor Green
Write-Host (Join-Path $OutputDir "MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4")
Write-Host (Join-Path $OutputDir "MacArthur_FINAL_ENG25_DELAYED_VIDEO_COPY.mp4")

if (Test-Path -LiteralPath $NvencOutput) {
    Write-Host $NvencOutput -ForegroundColor Green
}
else {
    Write-Warning "NVENC output was not created; use the VIDEO_COPY master"
}

Write-Host (Join-Path $OutputDir "MacArthur_FINAL_ENG25_DELAYED_NVENC.report.json")

if ($OpenOutput) {
    Start-Process explorer.exe -ArgumentList $OutputDir
}
