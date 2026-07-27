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
    [switch]$ForceReferences,
    [switch]$ForceSynthesis,
    [switch]$ForceMaster,
    [switch]$KeepDiagnostics,
    [switch]$NoOpenOutput
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-TextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)

    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $Hash = [System.Security.Cryptography.SHA256]::HashData($Bytes)
    return [Convert]::ToHexString($Hash).ToLowerInvariant()
}

function Get-StageSignature {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][string[]]$Files
    )

    $FileHashes = [ordered]@{}
    foreach ($File in $Files) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
            throw "Не найден файл для вычисления signature: $File"
        }
        $FileHashes[$File] = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    }

    $Payload = [ordered]@{
        files = $FileHashes
        settings = $Settings
    }
    $Json = $Payload | ConvertTo-Json -Depth 10 -Compress
    return Get-TextSha256 -Text $Json
}

function Read-State {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Write-State {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Payload
    )

    $TempPath = "$Path.tmp"
    $Payload |
        ConvertTo-Json -Depth 10 |
        Set-Content -LiteralPath $TempPath -Encoding utf8
    Move-Item -LiteralPath $TempPath -Destination $Path -Force
}

$ScriptDir = $PSScriptRoot
$VoxRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$ProductionDir = Join-Path $VoxRoot "production"
$SynthScript = Join-Path $ProductionDir "segmented_voice_clone.py"
$MasterScript = Join-Path $ProductionDir "master_constant_mix.py"
$SegmentsJson = Join-Path $ScriptDir "segments_ru_final.json"
$RussianSrt = Join-Path $ScriptDir "subtitles_ru_final.srt"
$EnglishSrt = Join-Path $ScriptDir "source_subtitles_en.srt"
$Translation = Join-Path $ScriptDir "translation_ru.txt"
$ProjectJson = Join-Path $ScriptDir "project.json"
$Python = Join-Path $CpuVenv "Scripts\python.exe"

$SourceDir = Join-Path $WorkRoot "source"
$ReferenceDir = Join-Path $WorkRoot "references"
$AudioDir = Join-Path $WorkRoot "audio"
$OutputDir = Join-Path $WorkRoot "output"
$SegmentWorkDir = Join-Path $WorkRoot "segment_work"
$MasterWorkDir = Join-Path $WorkRoot "master_work"
$StateDir = Join-Path $WorkRoot "state"
$Log = Join-Path $WorkRoot "john_piper_final_cpu.log"

$SourceVideo = Join-Path $SourceDir "source.mp4"
$ExtendedReference = Join-Path $ReferenceDir "B_extended_24s.wav"
$CompositeReference = Join-Path $ReferenceDir "C_composite_21s.wav"
$RussianTimeline = Join-Path $AudioDir "john_piper_ru_final_timeline.wav"
$SynthesisReport = [System.IO.Path]::ChangeExtension($RussianTimeline, ".json")

$FinalMixed = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_UPLOAD.mp4"
$FinalRussianOnly = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4"
$FinalSrt = Join-Path $OutputDir "John_Piper_Russian_Dub_FINAL.srt"
$FinalTranslation = Join-Path $OutputDir "John_Piper_Russian_Translation.txt"
$FinalSourceSrt = Join-Path $OutputDir "John_Piper_Source_English.srt"
$Manifest = Join-Path $OutputDir "John_Piper_FINAL.manifest.json"

$ReferenceStatePath = Join-Path $StateDir "references.json"
$SynthesisStatePath = Join-Path $StateDir "synthesis.json"
$MasterStatePath = Join-Path $StateDir "master.json"

New-Item -ItemType Directory -Force -Path @(
    $WorkRoot,
    $SourceDir,
    $ReferenceDir,
    $AudioDir,
    $OutputDir,
    $SegmentWorkDir,
    $MasterWorkDir,
    $StateDir
) | Out-Null

foreach ($Required in @(
    $Python,
    $SynthScript,
    $MasterScript,
    $SegmentsJson,
    $RussianSrt,
    $EnglishSrt,
    $Translation,
    $ProjectJson
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

$TranscriptStarted = $false
try {
    Start-Transcript -Path $Log -Force | Out-Null
    $TranscriptStarted = $true

    Write-Host "=== JOHN PIPER SHORTS / VOXCPM2 CPU ===" -ForegroundColor Green
    Write-Host "Без ZIP, Base64 и вложенных launchers." -ForegroundColor Green
    Write-Host "GPU скрыта; синтез разрешён только на CPU." -ForegroundColor Yellow

    Write-Host "=== 1. Полный preflight ===" -ForegroundColor Cyan
    & $Python -m py_compile $SynthScript $MasterScript
    if ($LASTEXITCODE -ne 0) {
        throw "Python production-код не прошёл компиляцию. VoxCPM2 не запускалась."
    }

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
        Write-Host "Устанавливаю yt-dlp в существующее CPU-окружение." -ForegroundColor Yellow
        & $Python -m pip install --disable-pip-version-check --upgrade yt-dlp
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить yt-dlp."
        }
    }

    Write-Host "=== 2. Исходный SHORTS ===" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $SourceVideo -PathType Leaf)) {
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

    Write-Host "=== 3. Голосовой профиль Джона Пайпера ===" -ForegroundColor Cyan
    $ReferenceSettings = [ordered]@{
        filter = "highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2"
        extended_start = 0.24
        extended_duration = 24.0
        composite_a_start = 0.24
        composite_a_duration = 10.88
        composite_b_start = 30.32
        composite_b_duration = 10.88
        composite_gap = 0.18
    }
    $ReferenceSignature = Get-StageSignature `
        -Settings $ReferenceSettings `
        -Files @($SourceVideo)
    $ReferenceState = Read-State -Path $ReferenceStatePath
    $ReferencesCurrent = (
        -not $ForceReferences -and
        $null -ne $ReferenceState -and
        $ReferenceState.signature -eq $ReferenceSignature -and
        (Test-Path -LiteralPath $ExtendedReference -PathType Leaf) -and
        (Test-Path -LiteralPath $CompositeReference -PathType Leaf)
    )

    if ($ReferencesCurrent) {
        Write-Host "Референсы актуальны; пересборка пропущена." -ForegroundColor DarkGray
    }
    else {
        Write-Host "Строю extended и composite reference." -ForegroundColor Yellow
        & ffmpeg -hide_banner -loglevel error -y `
            -ss 0.24 -t 24.0 -i $SourceVideo `
            -vn -ac 1 -ar 16000 `
            -af $ReferenceSettings.filter `
            $ExtendedReference
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось создать extended-референс."
        }

        $RefA = Join-Path $ReferenceDir "_composite_a.wav"
        $RefB = Join-Path $ReferenceDir "_composite_b.wav"
        try {
            & ffmpeg -hide_banner -loglevel error -y `
                -ss 0.24 -t 10.88 -i $SourceVideo `
                -vn -ac 1 -ar 16000 `
                -af $ReferenceSettings.filter `
                $RefA
            if ($LASTEXITCODE -ne 0) {
                throw "Не удалось создать первую часть composite-reference."
            }

            & ffmpeg -hide_banner -loglevel error -y `
                -ss 30.32 -t 10.88 -i $SourceVideo `
                -vn -ac 1 -ar 16000 `
                -af $ReferenceSettings.filter `
                $RefB
            if ($LASTEXITCODE -ne 0) {
                throw "Не удалось создать вторую часть composite-reference."
            }

            & ffmpeg -hide_banner -loglevel error -y `
                -i $RefA -i $RefB `
                -filter_complex "[0:a]apad=pad_dur=0.18[a0];[a0][1:a]concat=n=2:v=0:a=1,loudnorm=I=-20:LRA=7:TP=-2[out]" `
                -map "[out]" `
                -ac 1 -ar 16000 `
                $CompositeReference
            if ($LASTEXITCODE -ne 0) {
                throw "Не удалось создать composite-референс."
            }
        }
        finally {
            Remove-Item -LiteralPath $RefA, $RefB -Force -ErrorAction SilentlyContinue
        }

        Write-State -Path $ReferenceStatePath -Payload ([ordered]@{
            signature = $ReferenceSignature
            completed_at = (Get-Date).ToString("o")
            extended = $ExtendedReference
            composite = $CompositeReference
        })
    }

    Write-Host "=== 4. VoxCPM2 CPU-синтез ===" -ForegroundColor Cyan
    $SynthesisSettings = [ordered]@{
        threads = $Threads
        steps = $Steps
        cfg = $Cfg
        cache_length = 4096
        video_duration = [math]::Round($SourceDuration, 6)
        base_seed = 2026072700
    }
    $SynthesisSignature = Get-StageSignature `
        -Settings $SynthesisSettings `
        -Files @($SynthScript, $SegmentsJson, $ExtendedReference, $CompositeReference)
    $SynthesisState = Read-State -Path $SynthesisStatePath
    $SynthesisCurrent = (
        -not $ForceSynthesis -and
        $null -ne $SynthesisState -and
        $SynthesisState.signature -eq $SynthesisSignature -and
        (Test-Path -LiteralPath $RussianTimeline -PathType Leaf) -and
        (Test-Path -LiteralPath $SynthesisReport -PathType Leaf)
    )

    if ($SynthesisCurrent) {
        Write-Host "Русская timeline актуальна; CPU-синтез полностью пропущен." -ForegroundColor DarkGray
    }
    else {
        Write-Host "5 блоков, два кандидата, NoChew, checkpoints после каждого блока." -ForegroundColor Yellow
        $DurationArg = $SourceDuration.ToString(
            "0.000000",
            [Globalization.CultureInfo]::InvariantCulture
        )
        $CfgArg = $Cfg.ToString(
            "0.000000",
            [Globalization.CultureInfo]::InvariantCulture
        )
        $SynthArguments = @(
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
            "--video-duration", $DurationArg,
            "--base-seed", "2026072700"
        )
        if ($ForceSynthesis) {
            $SynthArguments += "--force-segments"
        }
        & $Python @SynthArguments
        if ($LASTEXITCODE -ne 0) {
            throw "VoxCPM2 CPU-синтез завершился с ошибкой. Следующий запуск продолжит с последнего checkpoint."
        }
        if (-not (Test-Path -LiteralPath $RussianTimeline -PathType Leaf)) {
            throw "Синтез завершился без итоговой timeline."
        }

        Write-State -Path $SynthesisStatePath -Payload ([ordered]@{
            signature = $SynthesisSignature
            settings = $SynthesisSettings
            completed_at = (Get-Date).ToString("o")
            timeline = $RussianTimeline
        })
    }

    Write-Host "=== 5. Финальный master ===" -ForegroundColor Cyan
    $MasterSettings = [ordered]@{
        original_level = $OriginalLevel
        target_i = -14.0
        target_lra = 9.0
        target_tp = -1.0
    }
    $MasterSignature = Get-StageSignature `
        -Settings $MasterSettings `
        -Files @($MasterScript, $SourceVideo, $RussianTimeline)
    $MasterState = Read-State -Path $MasterStatePath
    $MasterCurrent = (
        -not $ForceMaster -and
        $null -ne $MasterState -and
        $MasterState.signature -eq $MasterSignature -and
        (Test-Path -LiteralPath $FinalMixed -PathType Leaf) -and
        (Test-Path -LiteralPath $FinalRussianOnly -PathType Leaf)
    )

    if ($MasterCurrent) {
        Write-Host "Финальный master актуален; повторная сборка пропущена." -ForegroundColor DarkGray
    }
    else {
        $OriginalPercent = ($OriginalLevel * 100).ToString("0.0")
        Write-Host "Русский 100%; оригинал $OriginalPercent%; -14 LUFS / -1 dBTP." -ForegroundColor Yellow
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

        Write-State -Path $MasterStatePath -Payload ([ordered]@{
            signature = $MasterSignature
            settings = $MasterSettings
            completed_at = (Get-Date).ToString("o")
            upload = $FinalMixed
            russian_only = $FinalRussianOnly
        })
    }

    Copy-Item -LiteralPath $RussianSrt -Destination $FinalSrt -Force
    Copy-Item -LiteralPath $Translation -Destination $FinalTranslation -Force
    Copy-Item -LiteralPath $EnglishSrt -Destination $FinalSourceSrt -Force

    $ManifestPayload = [ordered]@{
        schema_version = 2
        project = "john_piper_z20py4yqhyq"
        source_url = $Url
        source_video = $SourceVideo
        source_duration_seconds = [math]::Round($SourceDuration, 4)
        engine = "VoxCPM2"
        device = "cpu"
        cuda_visible_devices = "-1"
        voice_mode = "zero-shot reference cloning"
        reference_signature = $ReferenceSignature
        synthesis_signature = $SynthesisSignature
        master_signature = $MasterSignature
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
        ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $Manifest -Encoding utf8

    if (-not $KeepDiagnostics) {
        $AttemptsDir = Join-Path $SegmentWorkDir "attempts"
        if (Test-Path -LiteralPath $AttemptsDir) {
            Remove-Item -LiteralPath $AttemptsDir -Recurse -Force
        }
        foreach ($TempFile in @(
            (Join-Path $MasterWorkDir "constant_mix_unmastered.wav"),
            (Join-Path $MasterWorkDir "constant_mix_mastered.wav"),
            (Join-Path $MasterWorkDir "russian_only_mastered.wav")
        )) {
            Remove-Item -LiteralPath $TempFile -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host ""
    Write-Host "=== JOHN PIPER SHORTS ГОТОВ ===" -ForegroundColor Green
    Write-Host "ГРУЗИТЬ НА КАНАЛ:" -ForegroundColor Green
    Write-Host $FinalMixed -ForegroundColor Green
    Write-Host "Чистый русский вариант: $FinalRussianOnly"
    Write-Host "Русские субтитры: $FinalSrt"
    Write-Host "Буквальный литературный перевод: $FinalTranslation"
    Write-Host "Манифест: $Manifest"
    Write-Host "Лог: $Log"

    if (-not $NoOpenOutput) {
        Start-Process explorer.exe -ArgumentList "`"$OutputDir`""
    }
}
finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}
