[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$SourceUrl = "https://youtube.com/shorts/Z20Py4yQhYQ?si=5bjihK2w9K_7Y89B",
    [string]$CpuPython = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe",
    [string]$ArchiveRoot = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-VOXCPM2-VIDEO-SMOKE",
    [double]$ReferenceStartSeconds = 0.0,
    [double]$ReferenceDurationSeconds = 20.0,
    [int]$TimeoutSeconds = 300,
    [int]$Steps = 4,
    [double]$Cfg = 1.80,
    [double]$MemoryFraction = 0.70,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

if ($ReferenceStartSeconds -lt 0.0) {
    throw "ReferenceStartSeconds must be non-negative"
}
if ($ReferenceDurationSeconds -lt 5.0 -or $ReferenceDurationSeconds -gt 30.0) {
    throw "ReferenceDurationSeconds must be within 5..30"
}
if ($TimeoutSeconds -lt 60 -or $TimeoutSeconds -gt 900) {
    throw "TimeoutSeconds must be within 60..900"
}
if ($Steps -lt 1 -or $Steps -gt 8) {
    throw "Steps must be within 1..8"
}
if ($Cfg -lt 0.5 -or $Cfg -gt 3.0) {
    throw "Cfg must be within 0.5..3.0"
}
if ($MemoryFraction -lt 0.10 -or $MemoryFraction -gt 0.85) {
    throw "MemoryFraction must be within 0.10..0.85"
}

$ModelSmoke = Join-Path $PSScriptRoot "Test-RTX3060-VoxCPM2-Model-Smoke.ps1"
foreach ($Required in @($CpuPython, $CudaPython, $ArchiveRoot, $ModelSmoke)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required path: $Required"
    }
}

foreach ($ToolName in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $ToolName -ErrorAction SilentlyContinue)) {
        throw "Missing PATH tool: $ToolName"
    }
}

$VideoId = $null
if ($SourceUrl -match '(?i)/shorts/([^?&/]+)') {
    $VideoId = $Matches[1]
}
elseif ($SourceUrl -match '(?i)[?&]v=([^?&/]+)') {
    $VideoId = $Matches[1]
}
if (-not $VideoId) {
    $VideoId = "youtube-video"
}
$SafeVideoId = $VideoId -replace '[^A-Za-z0-9_-]', '_'

$VideoRoot = Join-Path $WorkRoot $SafeVideoId
$SourceDir = Join-Path $VideoRoot "source"
$ReferenceDir = Join-Path $VideoRoot "reference"
$ModelRunRoot = Join-Path $VideoRoot "model_runs"
New-Item -ItemType Directory -Force -Path $SourceDir, $ReferenceDir, $ModelRunRoot | Out-Null

$InfoJson = Join-Path $SourceDir "source.info.json"
$ReferenceWav = Join-Path $ReferenceDir "reference_16k_mono.wav"
$OutputTemplate = Join-Path $SourceDir "source.%(ext)s"

Write-Host "YOUTUBE VOXCPM2 CUDA SMOKE PREPARATION" -ForegroundColor Cyan
Write-Host "URL: $SourceUrl" -ForegroundColor Yellow
Write-Host "Video ID: $SafeVideoId" -ForegroundColor Yellow
Write-Host "Preparation root: $VideoRoot" -ForegroundColor Yellow
Write-Host "Reference window: $ReferenceStartSeconds s + $ReferenceDurationSeconds s" -ForegroundColor Yellow
Write-Host "Download and reference extraction are CPU-only" -ForegroundColor Green

$ExistingVideo = @(
    Get-ChildItem -LiteralPath $SourceDir -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.BaseName -eq "source" -and
        $_.Extension -notin @(".json", ".part", ".ytdl")
    } |
    Sort-Object LastWriteTime -Descending
) | Select-Object -First 1

if (-not $ExistingVideo) {
    $DownloadArgs = @(
        "-m", "yt_dlp",
        "--no-playlist",
        "--no-overwrites",
        "--write-info-json",
        "--merge-output-format", "mp4",
        "-f", "bv*+ba/b",
        "-o", $OutputTemplate,
        $SourceUrl
    )
    & $CpuPython @DownloadArgs
    if ($LASTEXITCODE -ne 0) {
        throw "yt-dlp failed to download the requested YouTube video"
    }

    $ExistingVideo = @(
        Get-ChildItem -LiteralPath $SourceDir -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.BaseName -eq "source" -and
            $_.Extension -notin @(".json", ".part", ".ytdl")
        } |
        Sort-Object LastWriteTime -Descending
    ) | Select-Object -First 1
}

if (-not $ExistingVideo) {
    throw "Downloaded source video was not found under $SourceDir"
}
$SourceVideo = $ExistingVideo.FullName

$DetectedInfo = Get-ChildItem -LiteralPath $SourceDir -Filter "*.info.json" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($DetectedInfo -and $DetectedInfo.FullName -ne $InfoJson) {
    Copy-Item -LiteralPath $DetectedInfo.FullName -Destination $InfoJson -Force
}

$DurationText = & ffprobe `
    -v error `
    -show_entries format=duration `
    -of default=noprint_wrappers=1:nokey=1 `
    $SourceVideo
if ($LASTEXITCODE -ne 0) {
    throw "ffprobe could not read the source duration"
}
$SourceDuration = [double]::Parse(
    $DurationText.Trim(),
    [Globalization.CultureInfo]::InvariantCulture
)
if ($ReferenceStartSeconds -ge $SourceDuration) {
    throw "Reference start is outside the video duration: $SourceDuration seconds"
}
$ActualReferenceDuration = [Math]::Min(
    $ReferenceDurationSeconds,
    $SourceDuration - $ReferenceStartSeconds
)
if ($ActualReferenceDuration -lt 5.0) {
    throw "The available reference window is shorter than five seconds"
}

$StartArg = $ReferenceStartSeconds.ToString(
    "0.000",
    [Globalization.CultureInfo]::InvariantCulture
)
$DurationArg = $ActualReferenceDuration.ToString(
    "0.000",
    [Globalization.CultureInfo]::InvariantCulture
)

Write-Host "Source: $SourceVideo" -ForegroundColor Green
Write-Host "Source duration: $SourceDuration seconds" -ForegroundColor Yellow
Write-Host "Creating reference: $ReferenceWav" -ForegroundColor Cyan

$ReferenceArgs = @(
    "-hide_banner", "-loglevel", "error", "-y",
    "-ss", $StartArg,
    "-t", $DurationArg,
    "-i", $SourceVideo,
    "-vn",
    "-ac", "1",
    "-ar", "16000",
    "-af", "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2",
    "-c:a", "pcm_s16le",
    $ReferenceWav
)
& ffmpeg @ReferenceArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not extract the VoxCPM2 reference WAV"
}
if (-not (Test-Path -LiteralPath $ReferenceWav)) {
    throw "Reference WAV was not created: $ReferenceWav"
}

Write-Host "" 
Write-Host "Starting one isolated VoxCPM2 CUDA model smoke" -ForegroundColor Cyan
$SmokeArgs = @{
    RepoRoot = $RepoRoot
    CudaPython = $CudaPython
    ArchiveRoot = $ArchiveRoot
    ReferenceWav = $ReferenceWav
    WorkRoot = $ModelRunRoot
    TimeoutSeconds = $TimeoutSeconds
    Steps = $Steps
    Cfg = $Cfg
    MemoryFraction = $MemoryFraction
}
if ($OpenLogs) {
    $SmokeArgs.OpenLogs = $true
}

& $ModelSmoke @SmokeArgs

Write-Host "" 
Write-Host "VIDEO-SPECIFIC MODEL SMOKE FINISHED" -ForegroundColor Green
Write-Host "Source: $SourceVideo" -ForegroundColor Green
Write-Host "Reference: $ReferenceWav" -ForegroundColor Green
Write-Host "Model runs: $ModelRunRoot" -ForegroundColor Green
