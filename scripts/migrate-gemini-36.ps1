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

# Quality-first policy: 3.6 for analysis/QA/publication, 3.5 as strong backup,
# 3.5 Flash-Lite only for genuinely mechanical high-volume tasks.
Set-EnvValue -Name "GEMINI_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"
Set-EnvValue -Name "GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"
Set-EnvValue -Name "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "1"
Set-EnvValue -Name "LIVEDUB_INFO_MODEL" -Value "gemini-3.6-flash"
Set-EnvValue -Name "LIVEDUB_INFO_FALLBACK_MODELS" -Value "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
Set-EnvValue -Name "LIVEDUB_QUICK_QA_MODEL" -Value "gemini-3.6-flash"

if (-not $NoTtsFallback) {
    # Ordinary Yandex voices are tried only when Live voices are unavailable;
    # the bot marks that result as TTS and never calls it «Живые голоса».
    Set-EnvValue -Name "LIVEDUB_TTS_FALLBACK" -Value "1"
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllLines($EnvPath, $lines, $utf8NoBom)

Write-Host "✅ .env обновлён для Gemini 3.6 Flash" -ForegroundColor Green
Write-Host "   GEMINI_MODEL=gemini-3.6-flash"
Write-Host "   LIVEDUB_INFO_MODEL=gemini-3.6-flash"
Write-Host "   LIVEDUB_QUICK_QA_MODEL=gemini-3.6-flash"
Write-Host "   GEMINI_LIGHT_MODEL=gemini-3.5-flash-lite"
Write-Host "   Сильный fallback: gemini-3.5-flash"
Write-Host "💾 Резервная копия: $backup" -ForegroundColor Cyan
