param(
    [string]$EnvPath = "",
    [switch]$NoTtsFallback,
    [switch]$Priority
)

# Backward-compatible entry point. The active project policy is Gemini 3.7.
$target = Join-Path $PSScriptRoot "migrate-gemini-37.ps1"
& $target @PSBoundParameters
