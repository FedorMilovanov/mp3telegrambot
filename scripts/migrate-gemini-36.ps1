param(
    [string]$EnvPath = "",
    [switch]$NoTtsFallback,
    [switch]$Priority
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($EnvPath)) {
    $EnvPath = Join-Path $projectRoot ".env"
}
$EnvPath = [System.IO.Path]::GetFullPath($EnvPath)

if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
    throw ".env не найден: $EnvPath"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$EnvPath.bak-gemini36-$stamp"
Copy-Item -LiteralPath $EnvPath -Destination $backup -Force

$lines = [System.Collections.Generic.List[string]]::new()
foreach ($line in [System.IO.File]::ReadAllLines($EnvPath)) {
    [void]$lines.Add($line)
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Value
    )

    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $pattern) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }
    if (-not $found) {
        [void]$lines.Add("$Name=$Value")
    }
}

# Verified production semantic route: Gemini 3.6 Flash + HIGH thinking.
# Do not silently downgrade Factory/analysis/translation-QA to 3.5.
Set-EnvValue -Name "GEMINI_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_MAX_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_FORCE_THINKING_LEVEL" -Value "high"
Set-EnvValue -Name "GEMINI_SCHEMA_THINKING" -Value "1"

Set-EnvValue -Name "LIVEDUB_INFO_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_INFO_FALLBACK_MODELS" -Value ""
Set-EnvValue -Name "LIVEDUB_INFO_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_LONG_QA_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_LONG_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_THINKING" -Value "high"
Set-EnvValue -Name "SHORTS_FACTORY_MODEL" -Value "gemini-3.6-flash"

# Gemini-only analysis surrogate. This does not alter original video, LiveDub
# media, render sources or Whisper input. 128 kbps mono/48 kHz is deliberately
# conservative while avoiding the former hundreds-of-megabytes FLAC upload.
Set-EnvValue -Name "SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS" -Value "128"
Set-EnvValue -Name "SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE" -Value "48000"

# Priority inference is a premium Tier 2/3 feature. Enable it only when this
# switch is explicitly supplied; otherwise production remains on Standard.
if ($Priority) {
    Set-EnvValue -Name "GEMINI_SERVICE_TIER" -Value "priority"
} else {
    Set-EnvValue -Name "GEMINI_SERVICE_TIER" -Value "standard"
}

# Light work: separate 3.5 quota for explicitly mechanical utility only.
Set-EnvValue -Name "GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"
Set-EnvValue -Name "GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"
Set-EnvValue -Name "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "0"
Set-EnvValue -Name "LIVEDUB_PUBLICATION_FALLBACK_MODELS" -Value "gemini-3.5-flash"
Set-EnvValue -Name "LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK" -Value "1"

# Maximum ASR accuracy in production.
Set-EnvValue -Name "WHISPER_MODEL" -Value "large-v3"
Set-EnvValue -Name "WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"
Set-EnvValue -Name "SHORTS_FACTORY_WHISPER_MODEL" -Value "large-v3"

if (-not $NoTtsFallback) {
    # Ordinary Yandex voices are tried only when Live voices are unavailable;
    # the bot marks that result as TTS and never calls it «Живые голоса».
    Set-EnvValue -Name "LIVEDUB_TTS_FALLBACK" -Value "1"
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($EnvPath, $lines, $utf8NoBom)

$tier = if ($Priority) { "priority" } else { "standard" }
Write-Host "✅ .env обновлён: Gemini 3.6 / HIGH / без semantic downgrade" -ForegroundColor Green
Write-Host "   Heavy: gemini-3.6-flash / thinking=high"
Write-Host "   Factory Gemini audio: AAC mono 128 kbps / 48 kHz"
Write-Host "   Service tier: $tier"
Write-Host "   Light utility only: gemini-3.5-flash-lite -> gemini-3.5-flash"
Write-Host "   Whisper: large-v3"
Write-Host "💾 Резервная копия: $backup" -ForegroundColor Cyan
