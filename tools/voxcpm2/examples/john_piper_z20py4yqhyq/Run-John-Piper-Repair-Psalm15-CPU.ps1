[CmdletBinding()]
param(
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
$Python = Join-Path $CpuVenv "Scripts\python.exe"
$SynthScript = Join-Path $ScriptDir "voxcpm2_cpu_shorts_production.py"
$MasterScript = Join-Path $ScriptDir "master_constant_mix.py"
$SegmentsJson = Join-Path $ScriptDir "segments_ru_final.json"
$RussianSrt = Join-Path $ScriptDir "subtitles_ru_final.srt"
$Translation = Join-Path $ScriptDir "translation_ru.txt"

$SourceVideo = Join-Path $WorkRoot "source\source.mp4"
$ExtendedReference = Join-Path $WorkRoot "references\B_extended_24s.wav"
$CompositeReference = Join-Path $WorkRoot "references\C_composite_21s.wav"
$OriginalTimeline = Join-Path $WorkRoot "audio\john_piper_ru_final_timeline.wav"
$FixedTimeline = Join-Path $WorkRoot "audio\john_piper_ru_final_timeline_PSALM15_FIXED.wav"
$OutputDir = Join-Path $WorkRoot "output"
$RepairRoot = Join-Path $WorkRoot "repair_psalm15"
$RepairWork = Join-Path $RepairRoot "segment_work"
$RepairMaster = Join-Path $RepairRoot "master_work"
$RepairJson = Join-Path $RepairRoot "segment_02_psalm15.json"
$CorrectionTimeline = Join-Path $RepairRoot "segment_02_psalm15_full_timeline.wav"

$FixedMixed = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_UPLOAD_PSALM15_FIXED.mp4"
$FixedRussianOnly = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY_PSALM15_FIXED.mp4"
$FixedSrt = Join-Path $OutputDir "John_Piper_Russian_Dub_PSALM15_FIXED.srt"
$FixedTranslation = Join-Path $OutputDir "John_Piper_Russian_Translation_PSALM15_FIXED.txt"

foreach ($Required in @(
    $Python,
    $SynthScript,
    $MasterScript,
    $SegmentsJson,
    $RussianSrt,
    $Translation,
    $SourceVideo,
    $ExtendedReference,
    $CompositeReference,
    $OriginalTimeline
)) {
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

Write-Host "=== JOHN PIPER: ТОЧЕЧНЫЙ РЕМОНТ ПСАЛОМ 16 -> ПСАЛОМ 15 ===" -ForegroundColor Green
Write-Host "Пересчитывается только блок №2; остальные четыре блока не генерируются заново." -ForegroundColor Yellow

& $Python -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); raise SystemExit(1 if torch.cuda.is_available() else 0)"
Assert-LastExitCode -Stage "Проверка CPU-окружения"

& $Python -m py_compile $SynthScript $MasterScript
Assert-LastExitCode -Stage "Синтаксическая проверка Python"

$Segments = @(Get-Content -LiteralPath $SegmentsJson -Raw -Encoding utf8 | ConvertFrom-Json)
$SegmentMatches = @($Segments | Where-Object { [int]$_.id -eq 2 })
if ($SegmentMatches.Count -ne 1) {
    throw "В segments_ru_final.json должен быть ровно один сегмент с id=2."
}
$Segment = $SegmentMatches[0]
if ([string]$Segment.text -notmatch "пятнадцатом псалме") {
    throw "Сегмент №2 ещё не исправлен на 'пятнадцатом псалме'. Выполните git pull origin main."
}

New-Item -ItemType Directory -Force -Path $RepairRoot, $RepairWork, $RepairMaster, $OutputDir | Out-Null
$RepairPayload = @($Segment)
ConvertTo-Json -InputObject $RepairPayload -Depth 10 | Set-Content -LiteralPath $RepairJson -Encoding utf8

$RepairCheck = Get-Content -LiteralPath $RepairJson -Raw -Encoding utf8 | ConvertFrom-Json
if ($RepairCheck -isnot [System.Array] -or @($RepairCheck).Count -ne 1) {
    throw "Временный JSON ремонта должен содержать массив из одного сегмента."
}

$VideoDurationText = & ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $SourceVideo
Assert-LastExitCode -Stage "Чтение длительности видео"
$VideoDuration = [double]::Parse($VideoDurationText.Trim(), [Globalization.CultureInfo]::InvariantCulture)
$DurationArg = Get-InvariantNumber -Value $VideoDuration

Write-Host "=== 1. Генерирую только исправленный блок №2 ===" -ForegroundColor Cyan
& $Python $SynthScript `
    --archive-root $VoxArchive `
    --extended-reference $ExtendedReference `
    --composite-reference $CompositeReference `
    --segments-json $RepairJson `
    --work-dir $RepairWork `
    --output $CorrectionTimeline `
    --threads $Threads `
    --steps $Steps `
    --cfg $Cfg `
    --cache-length 4096 `
    --video-duration $DurationArg `
    --base-seed 2026072700
Assert-LastExitCode -Stage "Синтез исправленного блока №2"

$SpeechStart = [double]$Segment.start + ([double]$Segment.start_delay_ms / 1000.0)
$SpeechEnd = [double]$Segment.end + ([double]$Segment.start_delay_ms / 1000.0) - [double]$Segment.tail_guard
$SpeechStartArg = Get-InvariantNumber -Value $SpeechStart
$SpeechEndArg = Get-InvariantNumber -Value $SpeechEnd

Write-Host "=== 2. Заменяю участок $SpeechStartArg–$SpeechEndArg сек. без сдвига таймлайна ===" -ForegroundColor Cyan
$PatchFilter = "[0:a]volume=volume=0:enable='between(t\,$SpeechStartArg\,$SpeechEndArg)'[base];[1:a]atrim=duration=$DurationArg,asetpts=N/SR/TB[fix];[base][fix]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.985[out]"

& ffmpeg -hide_banner -loglevel error -y `
    -i $OriginalTimeline `
    -i $CorrectionTimeline `
    -filter_complex $PatchFilter `
    -map "[out]" `
    -ar 48000 `
    -ac 2 `
    -c:a pcm_s24le `
    $FixedTimeline
Assert-LastExitCode -Stage "Точечная замена русского таймлайна"

Write-Host "=== 3. Пересобираю финальный master с постоянными 25% оригинала ===" -ForegroundColor Cyan
$OriginalLevelArg = Get-InvariantNumber -Value $OriginalLevel
& $Python $MasterScript `
    --source-video $SourceVideo `
    --russian-wav $FixedTimeline `
    --work-dir $RepairMaster `
    --mixed-video $FixedMixed `
    --russian-only-video $FixedRussianOnly `
    --original-level $OriginalLevelArg `
    --target-i -14.0 `
    --target-lra 9.0 `
    --target-tp -1.0
Assert-LastExitCode -Stage "Финальный master исправленной версии"

Copy-Item -LiteralPath $RussianSrt -Destination $FixedSrt -Force
Copy-Item -LiteralPath $Translation -Destination $FixedTranslation -Force

if (-not $KeepDiagnostics) {
    Remove-Item -LiteralPath $CorrectionTimeline, $RepairJson -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $RepairWork, $RepairMaster -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== ТОЧЕЧНЫЙ РЕМОНТ ГОТОВ ===" -ForegroundColor Green
Write-Host "ГРУЗИТЬ ИСПРАВЛЕННЫЙ ФАЙЛ:" -ForegroundColor Green
Write-Host $FixedMixed -ForegroundColor Green
Write-Host "Чистая русская версия: $FixedRussianOnly"
Write-Host "Исправленная русская дорожка: $FixedTimeline"
Write-Host "Исправленные субтитры: $FixedSrt"
Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
