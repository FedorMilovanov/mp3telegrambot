param(
    [string]$EnvPath = "",
    [switch]$NoTtsFallback,
    [switch]$Priority
)

# Backward-compatible entry point for old operator notes/scripts.
# The active project policy is the current GA Flash route (see
# docs/gemini-operations.md); Gemini 3.6 and older model IDs are not a target.
$target = Join-Path $PSScriptRoot "migrate-gemini-38.ps1"
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw "migrate-gemini-38.ps1 не найден рядом с $($MyInvocation.MyCommand.Path)"
}
& $target @PSBoundParameters
