[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$PackageDir = "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL",
    [string]$WorkRoot = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL",
    [string]$VoxArchive = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [string]$SourceUrl = "https://youtube.com/shorts/RAaSAbPj-iw",
    [int]$Threads = 10,
    [int]$Steps = 16,
    [double]$Cfg = 1.80,
    [double]$OriginalLevel = 0.25,
    [int64]$BaseSeed = 2026072600,
    [switch]$OpenOutput
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$Python = Join-Path $CpuVenv "Scripts\python.exe"
$SynthScript = Join-Path $PackageDir "voxcpm2_final_production.py"
$MasterScript = Join-Path $PackageDir "master_constant_mix.py"
$PreflightScript = Join-Path $RepoRoot "tools\voxcpm2\production_preflight.py"
$ExampleDir = Join-Path $RepoRoot "tools\voxcpm2\examples\macarthur_raasabpj_iw"
$SegmentsJson = Join-Path $ExampleDir "segments_ru_final.json"
$RussianSrt = Join-Path $ExampleDir "subtitles_ru_final.srt"

$SourceDir = Join-Path $WorkRoot "source"
$ReferenceDir = Join-Path $WorkRoot "references"
$AudioDir = Join-Path $WorkRoot "audio"
$OutputDir = Join-Path $WorkRoot "output"
$SegmentWorkDir = Join-Path $WorkRoot "segment_work"
$MasterWorkDir = Join-Path $WorkRoot "master_work"
$LogDir = Join-Path $WorkRoot "logs"

$SourceVideo = Join-Path $SourceDir "source.mp4"
$ExtendedReference = Join-Path $ReferenceDir "B_extended_24s.wav"
$CompositeReference = Join-Path $ReferenceDir "C_composite_21s.wav"
$RussianTimeline = Join-Path $AudioDir "macarthur_ru_final_timeline.wav"
$FinalMixed = Join-Path $OutputDir "MacArthur_Russian_Dub_FINAL_UPLOAD.mp4"
$FinalRussianOnly = Join-Path $OutputDir "MacArthur_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4"
$FinalSrt = Join-Path $OutputDir "MacArthur_Russian_Dub_FINAL.srt"
$PreflightReport = Join-Path $LogDir "production_preflight.json"

foreach ($Directory in @(
    $WorkRoot,
    $SourceDir,
    $ReferenceDir,
    $AudioDir,
    $OutputDir,
    $SegmentWorkDir,
    $MasterWorkDir,
    $LogDir
)) {
    New-Item -ItemType Directory -Force -Path $Directory | Out-Null
}

foreach ($Required in @(
    $Python,
    $SynthScript,
    $MasterScript,
    $PreflightScript,
    $SegmentsJson,
    $RussianSrt
)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required file: $Required"
    }
}

foreach ($Tool in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "Missing PATH tool: $Tool"
    }
}

$env:CUDA_VISIBLE_DEVICES = "-1"
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:OMP_NUM_THREADS = "$Threads"
$env:MKL_NUM_THREADS = "$Threads"
$env:TOKENIZERS_PARALLELISM = "false"

Write-Host "STEP 1 - Source video" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $SourceVideo)) {
    $SourceCandidates = @(
        "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V32\source\source.mp4",
        "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V31\source\source.mp4",
        "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2\source\source.mp4",
        "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw\source\source.mp4"
    )

    $ExistingSource = $SourceCandidates |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1

    if ($ExistingSource) {
        Copy-Item -LiteralPath $ExistingSource -Destination $SourceVideo -Force
        Write-Host "Reused source: $ExistingSource" -ForegroundColor Green
    }
    else {
        $DownloadArgs = @(
            "--no-playlist",
            "--no-overwrites",
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "-o", $SourceVideo,
            $SourceUrl
        )
        & $Python -m yt_dlp @DownloadArgs
        if ($LASTEXITCODE -ne 0) {
            throw "yt-dlp failed"
        }
    }
}

Write-Host "STEP 2 - Reference audio" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $ExtendedReference)) {
    $ExtendedArgs = @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "0", "-t", "24.0",
        "-i", $SourceVideo,
        "-vn", "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2",
        $ExtendedReference
    )
    & ffmpeg @ExtendedArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create B extended reference"
    }
}

if (-not (Test-Path -LiteralPath $CompositeReference)) {
    $RefA = Join-Path $ReferenceDir "_composite_a.wav"
    $RefB = Join-Path $ReferenceDir "_composite_b.wav"

    $RefAArgs = @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "0", "-t", "10.88",
        "-i", $SourceVideo,
        "-vn", "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2",
        $RefA
    )
    & ffmpeg @RefAArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create composite reference part A"
    }

    $RefBArgs = @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-ss", "33.20", "-t", "10.0",
        "-i", $SourceVideo,
        "-vn", "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2",
        $RefB
    )
    & ffmpeg @RefBArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create composite reference part B"
    }

    $CompositeFilter = "[0:a]apad=pad_dur=0.18[a0];[a0][1:a]concat=n=2:v=0:a=1,loudnorm=I=-20:LRA=7:TP=-2[out]"
    $CompositeArgs = @(
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", $RefA,
        "-i", $RefB,
        "-filter_complex", $CompositeFilter,
        "-map", "[out]",
        "-ac", "1", "-ar", "16000",
        $CompositeReference
    )
    & ffmpeg @CompositeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create C composite reference"
    }

    Remove-Item -LiteralPath $RefA, $RefB -Force -ErrorAction SilentlyContinue
}

Write-Host "STEP 3 - Preflight" -ForegroundColor Cyan

$PreflightArgs = @(
    $PreflightScript,
    "--python-exe", $Python,
    "--package-dir", $PackageDir,
    "--work-root", $WorkRoot,
    "--model-root", $VoxArchive,
    "--source-video", $SourceVideo,
    "--segments-json", $SegmentsJson,
    "--extended-reference", $ExtendedReference,
    "--composite-reference", $CompositeReference,
    "--min-free-gb", "12",
    "--report", $PreflightReport
)
& $Python @PreflightArgs
if ($LASTEXITCODE -ne 0) {
    throw "Production preflight failed"
}

$SourceDurationText = & ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $SourceVideo
if ($LASTEXITCODE -ne 0) {
    throw "Could not read source duration"
}
$SourceDuration = [double]::Parse(
    $SourceDurationText.Trim(),
    [Globalization.CultureInfo]::InvariantCulture
)
$SourceDurationArg = $SourceDuration.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)
$CfgArg = $Cfg.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)
$OriginalLevelArg = $OriginalLevel.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)

Write-Host "STEP 4 - VoxCPM2 final synthesis" -ForegroundColor Cyan
Write-Host "CPU only; steps=$Steps; cfg=$CfgArg; original=$OriginalLevelArg" -ForegroundColor Yellow

$SynthArgs = @(
    $SynthScript,
    "--archive-root", $VoxArchive,
    "--extended-reference", $ExtendedReference,
    "--composite-reference", $CompositeReference,
    "--segments-json", $SegmentsJson,
    "--work-dir", $SegmentWorkDir,
    "--output", $RussianTimeline,
    "--threads", "$Threads",
    "--steps", "$Steps",
    "--cfg", $CfgArg,
    "--cache-length", "4096",
    "--video-duration", $SourceDurationArg,
    "--base-seed", "$BaseSeed"
)
& $Python @SynthArgs
if ($LASTEXITCODE -ne 0) {
    throw "VoxCPM2 final synthesis failed"
}

Write-Host "STEP 5 - Constant original mix and two-pass master" -ForegroundColor Cyan

$MasterArgs = @(
    $MasterScript,
    "--source-video", $SourceVideo,
    "--russian-wav", $RussianTimeline,
    "--work-dir", $MasterWorkDir,
    "--mixed-video", $FinalMixed,
    "--russian-only-video", $FinalRussianOnly,
    "--original-level", $OriginalLevelArg,
    "--target-i", "-14.0",
    "--target-lra", "9.0",
    "--target-tp", "-1.0"
)
& $Python @MasterArgs
if ($LASTEXITCODE -ne 0) {
    throw "Final master failed"
}

Copy-Item -LiteralPath $RussianSrt -Destination $FinalSrt -Force

Write-Host "" 
Write-Host "FINAL RENDER COMPLETE" -ForegroundColor Green
Write-Host "Upload: $FinalMixed" -ForegroundColor Green
Write-Host "Russian only: $FinalRussianOnly" -ForegroundColor Green
Write-Host "Subtitles: $FinalSrt" -ForegroundColor Green
Write-Host "Preflight: $PreflightReport" -ForegroundColor Green

if ($OpenOutput) {
    Start-Process explorer.exe -ArgumentList $OutputDir
}
