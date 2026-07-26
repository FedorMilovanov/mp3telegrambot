[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\tests\voxcpm2-test\Scripts\python.exe",
    [string]$ArchiveRoot = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-VOXCPM2-ZERO-SHOT-NO-CUDNN",
    [string]$Text = "Это короткая проверка синтеза речи.",
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
$Replacements = [ordered]@{
    'tools\voxcpm2\voxcpm2_cuda_zero_shot_smoke.py' = 'tools\voxcpm2\voxcpm2_cuda_zero_shot_no_cudnn_compat_smoke.py'
    '    $PreflightEvents = Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt' = '    $PreflightEvents = @(Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt)'
    '    $GpuEvents = Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt' = '    $GpuEvents = @(Get-NewGpuEvents -AfterRecordId $BaselineRecordId -AfterTime $StartedAt)'
    'RTX 3060 VOXCPM2 CUDA ZERO-SHOT — RECOVERED SESSION' = 'RTX 3060 VOXCPM2 ZERO-SHOT — NO cuDNN'
    'One zero-shot phrase, one inference step, one attempt' = 'One longer phrase, one inference step, retries disabled, cuDNN disabled'
}

$PatchedText = $SourceText
foreach ($Pair in $Replacements.GetEnumerator()) {
    if (-not $PatchedText.Contains($Pair.Key)) {
        throw "Could not locate launcher text to patch: $($Pair.Key)"
    }
    $PatchedText = $PatchedText.Replace($Pair.Key, $Pair.Value)
}

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) "voxcpm2-no-cudnn"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null
$TempScript = Join-Path $TempRoot ("no-cudnn-{0}.ps1" -f ([guid]::NewGuid().ToString("N")))
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
