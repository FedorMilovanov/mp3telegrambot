[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-BF16-GEMM-RECOVERED",
    [int]$TimeoutSeconds = 180,
    [int]$Repeats = 4,
    [double]$MemoryFraction = 0.25,
    [int]$PowerLimitWatts = 100,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceScript = Join-Path $PSScriptRoot "Test-RTX3060-BF16-GEMM-Recovered.ps1"
if (-not (Test-Path -LiteralPath $SourceScript)) {
    throw "Missing source launcher: $SourceScript"
}
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$SourceText = Get-Content -LiteralPath $SourceScript -Raw
$OldReturn = '    return ,$Events'
$NewReturn = '    return $Events'
if (-not $SourceText.Contains($OldReturn)) {
    throw "Could not locate the empty-event handling line to patch"
}

$PatchedText = $SourceText.Replace($OldReturn, $NewReturn)
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "voxcpm2-bf16-gemm-v2"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$TempScript = Join-Path $TempRoot ("bf16-gemm-{0}.ps1" -f ([guid]::NewGuid().ToString("N")))
[IO.File]::WriteAllText($TempScript, $PatchedText, [Text.UTF8Encoding]::new($false))

$Forward = @{
    RepoRoot = $RepoRoot
    CudaPython = $CudaPython
    WorkRoot = $WorkRoot
    TimeoutSeconds = $TimeoutSeconds
    Repeats = $Repeats
    MemoryFraction = $MemoryFraction
    PowerLimitWatts = $PowerLimitWatts
}
if ($OpenLogs) {
    $Forward["OpenLogs"] = $true
}

try {
    & $TempScript @Forward
}
finally {
    Remove-Item -LiteralPath $TempScript -Force -ErrorAction SilentlyContinue
}
