param(
    [string]$EnvPath = "",
    [switch]$NoTtsFallback
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

# Production maximum-quality policy: Gemini 3.6 Flash everywhere. Reliability
# comes from API-key rotation, never from silently downgrading user-visible work
# to an older/Lite model. All semantic tasks use thinking_level=high.
Set-EnvValue -Name "GEMINI_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_MAX_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_FORCE_THINKING_LEVEL" -Value "high"
Set-EnvValue -Name "GEMINI_SCHEMA_THINKING" -Value "1"
Set-EnvValue -Name "GEMINI_LIGHT_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_LIGHT_FALLBACK_MODELS" -Value ""
Set-EnvValue -Name "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "1"

Set-EnvValue -Name "LIVEDUB_INFO_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_INFO_FALLBACK_MODELS" -Value ""
Set-EnvValue -Name "LIVEDUB_INFO_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_PUBLICATION_FALLBACK_MODELS" -Value ""
Set-EnvValue -Name "LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK" -Value "1"

Set-EnvValue -Name "LIVEDUB_QUICK_QA_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_LONG_QA_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_LONG_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_THINKING" -Value "high"

Set-EnvValue -Name "SHORTS_FACTORY_MODEL" -Value "gemini-3.6-flash"
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

Write-Host "✅ .env обновлён: Gemini 3.6 MAX без model downgrade" -ForegroundColor Green
Write-Host "   Gemini: gemini-3.6-flash / thinking=high"
Write-Host "   Model fallbacks: disabled (используется ротация API-ключей)"
Write-Host "   Shorts Factory: gemini-3.6-flash / high"
Write-Host "   Whisper: large-v3"
Write-Host "💾 Резервная копия: $backup" -ForegroundColor Cyan
