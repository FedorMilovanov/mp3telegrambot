[CmdletBinding()]
param(
    [string]$Url = "https://youtube.com/shorts/Z20Py4yQhYQ",
    [string]$WorkRoot = "C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL",
    [string]$VoxArchive = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$CpuVenv = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    [ValidateRange(1, 64)]
    [int]$Threads = 10,
    [ValidateRange(1, 100)]
    [int]$Steps = 16,
    [ValidateRange(0.1, 5.0)]
    [double]$Cfg = 1.80,
    [ValidateRange(0.0, 1.0)]
    [double]$OriginalLevel = 0.25,
    [switch]$KeepDiagnostics
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-LastExitCode {
    param([Parameter(Mandatory)][string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage завершился с кодом $LASTEXITCODE."
    }
}

function Get-InvariantNumber {
    param([Parameter(Mandatory)][double]$Value)
    return $Value.ToString("0.000000", [Globalization.CultureInfo]::InvariantCulture)
}

$ScriptDir = $PSScriptRoot
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

New-Item -ItemType Directory -Force -Path @(
    $WorkRoot,
    $SourceDir,
    $ReferenceDir,
    $AudioDir,
    $OutputDir,
    $SegmentWorkDir,
    $MasterWorkDir
) | Out-Null

$RequiredFiles = @(
    $Python,
    $SynthScript,
    $MasterScript,
    $SegmentsJson,
    $RussianSrt,
    $EnglishSrt,
    $Translation
)
foreach ($Required in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
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

$TranscriptStarted = $false
try {
    Start-Transcript -Path $Log -Force | Out-Null
    $TranscriptStarted = $true

    Write-Host "=== JOHN PIPER SHORTS / VOXCPM2 CPU FINAL ===" -ForegroundColor Green
    Write-Host "Прямой runner: без ZIP, Base64 и вложенного PowerShell." -ForegroundColor Green
    Write-Host "GPU скрыта от процесса: CUDA_VISIBLE_DEVICES=-1." -ForegroundColor Yellow

    Write-Host "=== 1. Preflight CPU-окружения ===" -ForegroundColor Cyan
    $CpuCheck = @'
import torch
print("Python/PyTorch:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())
if torch.cuda.is_available():
    raise SystemExit("CPU runner unexpectedly sees CUDA")
from voxcpm import VoxCPM
import numpy
import soundfile
print("VoxCPM2 CPU environment: OK")
'@
    & $Python -c $CpuCheck
    Assert-LastExitCode -Stage "Проверка CPU-окружения"

    & $Python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('yt_dlp') else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "yt-dlp не найден; устанавливаю его в существующее CPU-окружение." -ForegroundColor Yellow
        & $Python -m pip install --upgrade yt-dlp
        Assert-LastExitCode -Stage "Установка yt-dlp"
    }

    & $Python -m py_compile $SynthScript $MasterScript
    Assert-LastExitCode -Stage "Синтаксическая проверка Python"

    $Segments = Get-Content -LiteralPath $SegmentsJson -Raw -Encoding utf8 | ConvertFrom-Json
    if ($null -eq $Segments -or $Segments.Count -ne 5) {
        throw "segments JSON должен содержать ровно 5 блоков."
    }
    Write-Host "segments JSON: OK"

    Write-Host "=== 2. Получение исходного Shorts ===" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $SourceVideo -PathType Leaf)) {
        & $Python -m yt_dlp `
            --no-playlist `
            --windows-filenames `
            -f "bv*+ba/b" `
            --merge-output-format mp4 `
            -o $SourceVideo `
            $Url
        Assert-LastExitCode -Stage "Скачивание исходного Shorts"
    }
    else {
        Write-Host "Использую уже скачанный source.mp4." -ForegroundColor DarkGray
    }

    $SourceDurationText = & ffprobe `
        -v error `
        -show_entries format=duration `
        -of default=noprint_wrappers=1:nokey=1 `
        $SourceVideo
    Assert-LastExitCode -Stage "Чтение длительности исходного видео"

    $SourceDuration = [double]::Parse(
        $SourceDurationText.Trim(),
        [Globalization.CultureInfo]::InvariantCulture
    )
    if ($SourceDuration -lt 55.0 -or $SourceDuration -gt 70.0) {
        throw "Длительность исходного ролика вне ожидаемого диапазона 55–70 сек.: $SourceDuration сек."
    }

    $TimelineEnd = [double]$Segments[-1].end
    $TimelineDifference = [math]::Abs($TimelineEnd - $SourceDuration)
    if ($TimelineDifference -gt 0.350) {
        throw "Таймлайн дубляжа заканчивается на $TimelineEnd сек., а видео — на $SourceDuration сек. Разница $TimelineDifference сек.; синтез отменён, чтобы не обрезать финальную фразу."
    }

    Write-Host ("Длительность источника: {0:0.000} сек." -f $SourceDuration)
    Write-Host ("Конец таймлайна: {0:0.000} сек.; расхождение: {1:0.000} сек." -f $TimelineEnd, $TimelineDifference) -ForegroundColor Green

    Write-Host "=== 3. Голосовой профиль Джона Пайпера ===" -ForegroundColor Cyan
    Write-Host "Zero-shot clone: extended 24 c + composite 21 c, как в удачном пайплайне МакАртура." -ForegroundColor Yellow

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 0.24 -t 24.0 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $ExtendedReference
    Assert-LastExitCode -Stage "Создание extended-референса"

    $RefA = Join-Path $ReferenceDir "_composite_a.wav"
    $RefB = Join-Path $ReferenceDir "_composite_b.wav"

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 0.24 -t 10.88 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $RefA
    Assert-LastExitCode -Stage "Создание первой части composite-референса"

    & ffmpeg -hide_banner -loglevel error -y `
        -ss 30.32 -t 10.88 -i $SourceVideo `
        -vn -ac 1 -ar 16000 `
        -af "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2" `
        $RefB
    Assert-LastExitCode -Stage "Создание второй части composite-референса"

    & ffmpeg -hide_banner -loglevel error -y `
        -i $RefA -i $RefB `
        -filter_complex "[0:a]apad=pad_dur=0.18[a0];[a0][1:a]concat=n=2:v=0:a=1,loudnorm=I=-20:LRA=7:TP=-2[out]" `
        -map "[out]" `
        -ac 1 -ar 16000 `
        $CompositeReference
    Assert-LastExitCode -Stage "Сборка composite-референса"

    Remove-Item -LiteralPath $RefA, $RefB -Force -ErrorAction SilentlyContinue

    Write-Host "=== 4. Синтез русского голоса VoxCPM2 ===" -ForegroundColor Cyan
    Write-Host ("CPU only | Steps={0} | CFG={1} | 5 блоков | NoChew | multi-candidate | resume checkpoints" -f $Steps, $Cfg) -ForegroundColor Yellow

    $DurationArg = Get-InvariantNumber -Value $SourceDuration
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
    Assert-LastExitCode -Stage "VoxCPM2 CPU-синтез"

    Write-Host "=== 5. Постоянный микс и финальный master ===" -ForegroundColor Cyan
    Write-Host ("Русский: 100%; английский постоянно: {0:0.0}%; без sidechain/ducking." -f ($OriginalLevel * 100.0)) -ForegroundColor Yellow

    $OriginalLevelArg = Get-InvariantNumber -Value $OriginalLevel
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
    Assert-LastExitCode -Stage "Финальный master"

    Copy-Item -LiteralPath $RussianSrt -Destination $FinalSrt -Force
    Copy-Item -LiteralPath $Translation -Destination $FinalTranslation -Force
    Copy-Item -LiteralPath $EnglishSrt -Destination $FinalSourceSrt -Force

    $ManifestPayload = [ordered]@{
        schema_version = 4
        source_url = $Url
        source_video = $SourceVideo
        source_duration_seconds = [math]::Round($SourceDuration, 4)
        timeline_end_seconds = [math]::Round($TimelineEnd, 4)
        timeline_difference_seconds = [math]::Round($TimelineDifference, 4)
        engine = "VoxCPM2"
        device = "cpu"
        cuda_visible_devices = "-1"
        voice_mode = "zero-shot reference cloning"
        resume_mode = "per-segment checkpoints"
        reference_extended = $ExtendedReference
        reference_composite = $CompositeReference
        locdit_steps = $Steps
        cfg = $Cfg
        original_level = $OriginalLevel
        sidechain = $false
        mixed_upload = $FinalMixed
        russian_only = $FinalRussianOnly
        subtitles_ru = $FinalSrt
        translation_ru = $FinalTranslation
        generated_at = (Get-Date).ToString("o")
    }
    $ManifestPayload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Manifest -Encoding utf8

    if (-not $KeepDiagnostics) {
        Write-Host "=== 6. Очистка только сырых кандидатов ===" -ForegroundColor Cyan
        $AttemptsDir = Join-Path $SegmentWorkDir "attempts"
        if (Test-Path -LiteralPath $AttemptsDir) {
            Remove-Item -LiteralPath $AttemptsDir -Recurse -Force
        }
        Write-Host "Fitted WAV и checkpoints сохранены для быстрого продолжения." -ForegroundColor DarkGray

        $TempFiles = @(
            (Join-Path $MasterWorkDir "constant_mix_unmastered.wav"),
            (Join-Path $MasterWorkDir "constant_mix_mastered.wav"),
            (Join-Path $MasterWorkDir "russian_only_mastered.wav")
        )
        foreach ($TempFile in $TempFiles) {
            Remove-Item -LiteralPath $TempFile -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host ""
    Write-Host "=== JOHN PIPER SHORTS ГОТОВ ===" -ForegroundColor Green
    Write-Host "ГРУЗИТЬ НА КАНАЛ:" -ForegroundColor Green
    Write-Host $FinalMixed -ForegroundColor Green
    Write-Host ""
    Write-Host "Чистый русский вариант: $FinalRussianOnly"
    Write-Host "Русские субтитры: $FinalSrt"
    Write-Host "Перевод: $FinalTranslation"
    Write-Host "Манифест: $Manifest"
    Write-Host "Лог: $Log"

    Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
}
finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}
