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
$SynthScript = Join-Path $ScriptDir "voxcpm2_cpu_shorts_production.py"
$MasterScript = Join-Path $ScriptDir "master_constant_mix.py"
$SegmentsJson = Join-Path $ScriptDir "segments_ru_final.json"
$RussianSrt = Join-Path $ScriptDir "subtitles_ru_final.srt"
$EnglishSrt = Join-Path $ScriptDir "source_subtitles_en.srt"
$Translation = Join-Path $ScriptDir "translation_ru.txt"
$Python = Join-Path $CpuVenv "Scripts\python.exe"

$SourceDir = Join-Path $WorkRoot "source"
$ReferenceDir = Join-Path $WorkRoot "references"
$AudioDir = Join-Path $WorkRoot "audio"
$OutputDir = Join-Path $WorkRoot "output"
$SegmentWorkDir = Join-Path $WorkRoot "segment_work"
$MasterWorkDir = Join-Path $WorkRoot "master_work"
$Log = Join-Path $WorkRoot "john_piper_final_cpu.log"

$SourceVideo = Join-Path $SourceDir "source.mp4"
$ExtendedReference = Join-Path $ReferenceDir "B_extended_24s.wav"
$CompositeReference = Join-Path $ReferenceDir "C_composite_21s.wav"
$RussianTimeline = Join-Path $AudioDir "john_piper_ru_final_timeline.wav"

$FinalMixed = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_UPLOAD.mp4"
$FinalRussianOnly = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4"
$FinalSrt = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL.srt"
$FinalTranslation = Join-Path $OutputDir "John_Piper_Russian_Translation.txt"
$FinalSourceSrt = Join-Path $OutputDir "John_Piper_Source_English.srt"
$Manifest = Join-Path $OutputDir "John_Piper_FINAL.manifest.json"

New-Item -ItemType Directory -Force -Path `
    $WorkRoot, $SourceDir, $ReferenceDir, $AudioDir, `
    $OutputDir, $SegmentWorkDir, $MasterWorkDir | Out-Null

foreach ($Required in @(
    $Python,
    $SynthScript,
    $MasterScript,
    $SegmentsJson,
    $RussianSrt,
    $EnglishSrt,
    $Translation
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
    Write-Host "=== JOHN PIPER SHORTS / VOXCPM2 CPU FINAL ===" `
        -ForegroundColor Green
    Write-Host "GPU полностью скрыта. CUDA для этого процесса запрещена." `
        -ForegroundColor Yellow

    Write-Host "=== 1. Проверяю CPU-окружение ===" -ForegroundColor Cyan

    & $Python -c @'
import torch
print("Python/PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    raise SystemExit("ОШИБКА: CPU launcher увидел CUDA")
from voxcpm import VoxCPM
import numpy, soundfile
print("VoxCPM2 CPU environment: OK")
'@

    if ($LASTEXITCODE -ne 0) {
        throw "CPU-окружение VoxCPM2 не прошло проверку."
    }

    & $Python -c "import yt_dlp" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Устанавливаю только yt-dlp в существующее CPU-окружение." `
            -ForegroundColor Yellow
        & $Python -m pip install --upgrade yt-dlp
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить yt-dlp."
        }
    }

    Write-Host "=== 2. Получаю исходный SHORTS ===" -ForegroundColor Cyan

    if (-not (Test-Path -LiteralPath $SourceVideo)) {
        & $Python -m yt_dlp `
            --no-playlist `
            --windows-filenames `
            -f "bv*+ba/b" `
            --merge-output-format mp4 `
            -o $SourceVideo `
            $Url

        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось скачать John Piper SHORTS."
        }
    }
    else {
        Write-Host "Использую уже скачанный source.mp4." -ForegroundColor DarkGray
    }

    $SourceDurationText = & ffprobe `
        -v error `
        -show_entries format=duration `
        -of default=noprint_wrappers=1:nokey=1 `
        $SourceVideo

    $SourceDuration = [double]::Parse(
        $SourceDurationText.Trim(),
        [Globalization.CultureInfo]::InvariantCulture
    )

    if ($SourceDuration -lt 63.0) {
        throw "Исходный ролик подозрительно короткий: $SourceDuration сек."
    }

    Write-Host "Длительность источника: $($SourceDuration.ToString('0.000')) сек."

    Write-Host "=== 3. Строю голосовой профиль Джона Пайпера ===" `
        -ForegroundColor Cyan
    Write-Host (
        "Zero-shot voice clone, как у МакАртура: "
        + "B extended + C composite."
    ) -ForegroundColor Yellow

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 0.24 -t 24.0 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $ExtendedReference

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось создать extended-референс."
    }

    $RefA = Join-Path $ReferenceDir "_c_a.wav"
    $RefB = Join-Path $ReferenceDir "_c_b.wav"

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 0.24 -t 10.88 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $RefA

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 30.32 -t 10.88 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $RefB

    & ffmpeg -hide_banner -loglevel error -y `
        -i $RefA -i $RefB `
        -filter_complex `
        "[0:a]apad=pad_dur=0.18[a0];[a0][1:a]concat=n=2:v=0:a=1,loudnorm=I=-20:LRA=7:TP=-2[out]" `
        -map "[out]" `
        -ac 1 -ar 16000 `
        $CompositeReference

    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось создать composite-референс."
    }

    Remove-Item -LiteralPath $RefA, $RefB `
        -Force -ErrorAction SilentlyContinue

    Write-Host "=== 4. Клонирую голос и озвучиваю перевод ===" `
        -ForegroundColor Cyan
    Write-Host (
        "CPU only | Steps=$Steps | CFG=$Cfg | "
        + "5 смысловых блоков | NoChew | multi-candidate"
    ) -ForegroundColor Yellow

    $DurationArg = $SourceDuration.ToString(
        "0.000000",
        [Globalization.CultureInfo]::InvariantCulture
    )

    & $Python $SynthScript `
        --archive-root $VoxArchive `
        --extended-reference $ExtendedReference `
        --composite-reference $CompositeReference `
        --segments-json $SegmentsJson `
        --work-dir $SegmentWorkDir `
        --output $RussianTimeline `
        --threads $Threads `
        --steps $Steps `
        --cfg $Cfg `
        --cache-length 4096 `
        --video-duration $DurationArg `
        --base-seed 2026072700

    if ($LASTEXITCODE -ne 0) {
        throw "VoxCPM2 CPU-синтез завершился с ошибкой."
    }

    Write-Host "=== 5. Собираю финальный Shorts ===" `
        -ForegroundColor Cyan
    Write-Host (
        "Русский голос: 100%; оригинал постоянно: "
        + ($OriginalLevel * 100).ToString("0.0")
        + "%; master -14 LUFS / -1 dBTP."
    ) -ForegroundColor Yellow

    $OriginalLevelArg = $OriginalLevel.ToString(
        "0.000000",
        [Globalization.CultureInfo]::InvariantCulture
    )

    & $Python $MasterScript `
        --source-video $SourceVideo `
        --russian-wav $RussianTimeline `
        --work-dir $MasterWorkDir `
        --mixed-video $FinalMixed `
        --russian-only-video $FinalRussianOnly `
        --original-level $OriginalLevelArg `
        --target-i -14.0 `
        --target-lra 9.0 `
        --target-tp -1.0

    if ($LASTEXITCODE -ne 0) {
        throw "Финальный master завершился с ошибкой."
    }

    Copy-Item -LiteralPath $RussianSrt -Destination $FinalSrt -Force
    Copy-Item -LiteralPath $Translation -Destination $FinalTranslation -Force
    Copy-Item -LiteralPath $EnglishSrt -Destination $FinalSourceSrt -Force

    $ManifestPayload = [ordered]@{
        schema_version = 1
        source_url = $Url
        source_video = $SourceVideo
        source_duration_seconds = [math]::Round($SourceDuration, 4)
        engine = "VoxCPM2"
        device = "cpu"
        cuda_visible_devices = "-1"
        voice_mode = "zero-shot reference cloning"
        reference_extended = $ExtendedReference
        reference_composite = $CompositeReference
        locdit_steps = $Steps
        cfg = $Cfg
        original_level = $OriginalLevel
        mixed_upload = $FinalMixed
        russian_only = $FinalRussianOnly
        subtitles_ru = $FinalSrt
        translation_ru = $FinalTranslation
        generated_at = (Get-Date).ToString("o")
    }
    $ManifestPayload |
        ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $Manifest -Encoding utf8

    if (-not $KeepDiagnostics) {
        Write-Host "=== 6. Убираю тяжёлые временные файлы ===" `
            -ForegroundColor Cyan

        foreach ($TempDir in @(
            (Join-Path $SegmentWorkDir "attempts"),
            (Join-Path $SegmentWorkDir "segments_clean"),
            (Join-Path $SegmentWorkDir "segments_fitted")
        )) {
            if (Test-Path -LiteralPath $TempDir) {
                Remove-Item -LiteralPath $TempDir -Recurse -Force
            }
        }

        foreach ($TempFile in @(
            (Join-Path $MasterWorkDir "constant_mix_unmastered.wav"),
            (Join-Path $MasterWorkDir "constant_mix_mastered.wav"),
            (Join-Path $MasterWorkDir "russian_only_mastered.wav")
        )) {
            Remove-Item -LiteralPath $TempFile -Force `
                -ErrorAction SilentlyContinue
        }
    }

    Write-Host ""
    Write-Host "=== JOHN PIPER SHORTS ГОТОВ ===" -ForegroundColor Green
    Write-Host "ГРУЗИТЬ НА КАНАЛ:" -ForegroundColor Green
    Write-Host $FinalMixed -ForegroundColor Green
    Write-Host ""
    Write-Host "Чистый русский вариант: $FinalRussianOnly"
    Write-Host "Русские субтитры: $FinalSrt"
    Write-Host "Буквальный литературный перевод: $FinalTranslation"
    Write-Host "Манифест: $Manifest"
    Write-Host "Лог: $Log"

    Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
}
finally {
    Stop-Transcript | Out-Null
}
