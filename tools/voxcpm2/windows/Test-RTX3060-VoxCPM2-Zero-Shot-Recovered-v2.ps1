[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe",
    [string]$ArchiveRoot = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-VOXCPM2-ZERO-SHOT-RECOVERED",
    [string]$Text = "Тест.",
    [int]$TimeoutSeconds = 300,
    [double]$Cfg = 1.80,
    [double]$MemoryFraction = 0.70,
    [int]$PowerLimitWatts = 100,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceScript = Join-Path $PSScriptRoot "Test-RTX3060-VoxCPM2-Zero-Shot-Recovered.ps1"
if (-not (Test-Path -LiteralPath $SourceScript)) {
    throw "Missing source launcher: $SourceScript"
}

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$SourceText = Get-Content -LiteralPath $SourceScript -Raw
$OldPreflight = '    $PreflightEvents = Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt'
$NewPreflight = '    $PreflightEvents = @(Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt)'
$OldFinal = '    $GpuEvents = Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt'
$NewFinal = '    $GpuEvents = @(Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt)'

if (-not $SourceText.Contains($OldPreflight)) {
    throw "Could not locate the recovery-preflight event assignment to patch"
}
if (-not $SourceText.Contains($OldFinal)) {
    throw "Could not locate the final event assignment to patch"
}

$PatchedText = $SourceText.Replace($OldPreflight, $NewPreflight).Replace($OldFinal, $NewFinal)
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "voxcpm2-recovered-v2"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$TempScript = Join-Path $TempRoot ("recovered-{0}.ps1" -f ([guid]::NewGuid().ToString("N")))
[IO.File]::WriteAllText($TempScript, $PatchedText, [Text.UTF8Encoding]::new($false))

$Forward = @{
    RepoRoot = $RepoRoot
    CudaPython = $CudaPython
    ArchiveRoot = $ArchiveRoot
    WorkRoot = $WorkRoot
    Text = $Text
    TimeoutSeconds = $TimeoutSeconds
    Cfg = $Cfg
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
