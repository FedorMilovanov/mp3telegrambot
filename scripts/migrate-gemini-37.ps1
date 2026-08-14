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
$backup = "$EnvPath.bak-gemini37-$stamp"
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

# Semantic quality: 3.7/high first, 3.6/high only as a quality-preserving fallback.
Set-EnvValue -Name "GEMINI_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "GEMINI_MAX_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "GEMINI_QUALITY_FALLBACK_MODELS" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_FORCE_THINKING_LEVEL" -Value "high"
Set-EnvValue -Name "GEMINI_SCHEMA_THINKING" -Value "1"

Set-EnvValue -Name "LIVEDUB_INFO_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "LIVEDUB_INFO_FALLBACK_MODELS" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_INFO_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "LIVEDUB_LONG_QA_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_LONG_QA_THINKING" -Value "high"
Set-EnvValue -Name "LIVEDUB_QA_VERIFY_THINKING" -Value "high"

Set-EnvValue -Name "SHORTS_FACTORY_MODEL" -Value "gemini-3.7-flash"
Set-EnvValue -Name "SHORTS_FACTORY_FALLBACK_MODELS" -Value "gemini-3.6-flash"

# Mechanical utility only; semantic/user-visible quality routes do not fall here.
Set-EnvValue -Name "GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"
Set-EnvValue -Name "GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"
Set-EnvValue -Name "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "0"

# Maximum ASR accuracy.
Set-EnvValue -Name "WHISPER_MODEL" -Value "large-v3"
Set-EnvValue -Name "WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"
Set-EnvValue -Name "SHORTS_FACTORY_WHISPER_MODEL" -Value "large-v3"

if (-not $NoTtsFallback) {
    Set-EnvValue -Name "LIVEDUB_TTS_FALLBACK" -Value "1"
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($EnvPath, $lines, $utf8NoBom)

Write-Host "✅ .env обновлён: Gemini 3.7/high -> 3.6/high fallback" -ForegroundColor Green
Write-Host "   Factory/LiveDub semantic: gemini-3.7-flash / high"
Write-Host "   Capacity fallback: gemini-3.6-flash / high"
Write-Host "   3.5/Lite semantic downgrade: disabled"
Write-Host "   Utility only: gemini-3.5-flash-lite -> gemini-3.5-flash"
Write-Host "   Whisper: large-v3"
Write-Host "💾 Резервная копия: $backup" -ForegroundColor Cyan
