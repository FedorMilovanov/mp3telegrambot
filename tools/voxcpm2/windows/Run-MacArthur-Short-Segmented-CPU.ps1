#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$Url = "https://youtube.com/shorts/RAaSAbPj-iw",
    [string]$WorkRoot = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2",
    [string]$VoxArchive = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [string]$RepoRoot = "",
    [int]$Threads = 10,
    [int]$Steps = 4,
    [double]$Cfg = 2.0,
    [ValidateSet("reference", "ultimate", "continuation")]
    [string]$CloneMode = "reference",
    [double]$EnglishVolume = 0.18
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$PythonScript = Join-Path $RepoRoot "tools\voxcpm2\segmented_cpu_dub.py"
$ExampleDir = Join-Path $RepoRoot "tools\voxcpm2\examples\macarthur_raasabpj_iw"
$SegmentsJson = Join-Path $ExampleDir "segments_ru.json"
$PromptTextFile = Join-Path $ExampleDir "reference_transcript_en.txt"
$RussianSrt = Join-Path $ExampleDir "subtitles_ru.srt"

$Python = Join-Path $CpuVenv "Scripts\python.exe"
$SourceDir = Join-Path $WorkRoot "source"
$AudioDir = Join-Path $WorkRoot "audio"
$OutputDir = Join-Path $WorkRoot "output"
$SegmentWorkDir = Join-Path $WorkRoot "segment_work"
$Log = Join-Path $WorkRoot "macarthur_segmented_cpu_dub.log"

$ReferenceWav = Join-Path $AudioDir "macarthur_reference_clean_16k.wav"
$RussianTimeline = Join-Path $AudioDir "macarthur_ru_segmented_timeline.wav"
$FinalVideo = Join-Path $OutputDir "MacArthur_Russian_Dub_CPU_V2.mp4"
$FinalSrt = Join-Path $OutputDir "MacArthur_Russian_Dub_CPU_V2.srt"

New-Item -ItemType Directory -Force -Path `
    $WorkRoot, $SourceDir, $AudioDir, $OutputDir, $SegmentWorkDir |
    Out-Null

foreach ($Required in @(
    $Python,
    $PythonScript,
    $SegmentsJson,
    $PromptTextFile,
    $RussianSrt
)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Не найден обязательный файл: $Required"
    }
}

foreach ($Tool in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "$Tool не найден в PATH."
    }
}

# Must be set before Python imports torch/voxcpm.
$env:CUDA_VISIBLE_DEVICES = "-1"
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:OMP_NUM_THREADS = "$Threads"
$env:MKL_NUM_THREADS = "$Threads"
$env:TOKENIZERS_PARALLELISM = "false"

Start-Transcript -Path $Log -Force | Out-Null

try {
    Write-Host "=== 1. Проверяю yt-dlp ===" -ForegroundColor Cyan

    & $Python -c "import yt_dlp; print('yt-dlp OK')" 2>$null

    if ($LASTEXITCODE -ne 0) {
        & $Python -m pip install -U yt-dlp
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить yt-dlp."
        }
    }

    $ExistingVideo = Get-ChildItem -LiteralPath $SourceDir -File `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Extension -in ".mp4", ".mkv", ".webm" -and
            $_.Name -notmatch "\.part$"
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $ExistingVideo) {
        Write-Host "=== 2. Скачиваю SHORTS ===" -ForegroundColor Cyan

        & $Python -m yt_dlp `
            --no-playlist `
            --no-overwrites `
            -f "bv*+ba/b" `
            --merge-output-format mp4 `
            -o (Join-Path $SourceDir "source.%(ext)s") `
            $Url

        if ($LASTEXITCODE -ne 0) {
            throw "yt-dlp завершился с ошибкой."
        }
    }
    else {
        Write-Host "=== 2. Использую уже скачанное видео ===" `
            -ForegroundColor Cyan
    }

    $SourceVideo = Get-ChildItem -LiteralPath $SourceDir -File |
        Where-Object {
            $_.Extension -in ".mp4", ".mkv", ".webm" -and
            $_.Name -notmatch "\.part$"
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $SourceVideo) {
        throw "Скачанное видео не найдено."
    }

    Write-Host "Видео: $($SourceVideo.FullName)" -ForegroundColor Green

    $SourceDurationText = & ffprobe `
        -v error `
        -show_entries format=duration `
        -of default=noprint_wrappers=1:nokey=1 `
        $SourceVideo.FullName

    $SourceDuration = [double]::Parse(
        $SourceDurationText.Trim(),
        [Globalization.CultureInfo]::InvariantCulture
    )

    Write-Host (
        "=== 3. Готовлю чистый референс МакАртура, 10.88 сек. ==="
    ) -ForegroundColor Cyan

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 0 `
        -t 10.88 `
        -i $SourceVideo.FullName `
        -vn `
        -ac 1 `
        -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $ReferenceWav

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось подготовить референс."
    }

    Write-Host (
        "=== 4. Генерирую семь смысловых сегментов VoxCPM2 ==="
    ) -ForegroundColor Cyan
    Write-Host "RTX 3060 полностью скрыта от процесса." `
        -ForegroundColor Yellow
    Write-Host "CloneMode: $CloneMode" -ForegroundColor Yellow

    $SourceDurationArg = $SourceDuration.ToString(
        "0.000000",
        [Globalization.CultureInfo]::InvariantCulture
    )

    & $Python $PythonScript `
        --archive-root $VoxArchive `
        --reference-wav $ReferenceWav `
        --prompt-text-file $PromptTextFile `
        --segments-json $SegmentsJson `
        --work-dir $SegmentWorkDir `
        --output $RussianTimeline `
        --threads $Threads `
        --steps $Steps `
        --cfg $Cfg `
        --clone-mode $CloneMode `
        --cache-length 2048 `
        --video-duration $SourceDurationArg

    if ($LASTEXITCODE -ne 0) {
        throw "Сегментный VoxCPM2 завершился с ошибкой."
    }

    Write-Host "=== 5. Собираю профессиональный микс ===" `
        -ForegroundColor Cyan

    $EnglishVolumeText = $EnglishVolume.ToString(
        "0.000",
        [Globalization.CultureInfo]::InvariantCulture
    )

    $MixFilter = (
        "[1:a]asplit=2[ru_sc][ru_mix];" +
        "[0:a]volume=$EnglishVolumeText[en];" +
        "[en][ru_sc]sidechaincompress=" +
        "threshold=0.025:ratio=8:attack=18:release=280[en_duck];" +
        "[en_duck][ru_mix]amix=" +
        "inputs=2:duration=first:dropout_transition=0:normalize=0," +
        "alimiter=limit=0.97[a]"
    )

    & ffmpeg -hide_banner -loglevel error -y `
        -i $SourceVideo.FullName `
        -i $RussianTimeline `
        -filter_complex $MixFilter `
        -map 0:v:0 `
        -map "[a]" `
        -c:v copy `
        -c:a aac `
        -b:a 192k `
        -movflags +faststart `
        -shortest `
        $FinalVideo

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось собрать финальное видео."
    }

    Copy-Item -LiteralPath $RussianSrt `
        -Destination $FinalSrt `
        -Force

    Write-Host ""
    Write-Host "=== ГОТОВО ===" -ForegroundColor Green
    Write-Host "Видео: $FinalVideo" -ForegroundColor Green
    Write-Host "Русская таймлиния: $RussianTimeline" `
        -ForegroundColor Green
    Write-Host "Субтитры: $FinalSrt" -ForegroundColor Green
    Write-Host "Лог: $Log" -ForegroundColor Green

    Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
}
finally {
    Stop-Transcript | Out-Null
}
