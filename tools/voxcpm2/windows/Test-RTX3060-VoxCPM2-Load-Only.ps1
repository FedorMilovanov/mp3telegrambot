[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe",
    [string]$ArchiveRoot = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-VOXCPM2-LOAD-ONLY",
    [int]$TimeoutSeconds = 180,
    [double]$MemoryFraction = 0.70,
    [int]$PowerLimitWatts = 100,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}
if ($TimeoutSeconds -lt 60 -or $TimeoutSeconds -gt 600) {
    throw "TimeoutSeconds must be within 60..600"
}
if ($MemoryFraction -lt 0.65 -or $MemoryFraction -gt 0.85) {
    throw "MemoryFraction must be within 0.65..0.85"
}
if ($PowerLimitWatts -lt 100 -or $PowerLimitWatts -gt 170) {
    throw "PowerLimitWatts must be within 100..170 for this RTX 3060 probation"
}

$Probe = Join-Path $RepoRoot "tools\voxcpm2\voxcpm2_cuda_load_only.py"
foreach ($Required in @($CudaPython, $Probe, $ArchiveRoot)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required path: $Required"
    }
}
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    throw "nvidia-smi was not found"
}

$RunningFfmpeg = @(Get-Process -Name "ffmpeg" -ErrorAction SilentlyContinue)
if ($RunningFfmpeg.Count -gt 0) {
    throw "An ffmpeg process is running. Stop video/NVENC work first."
}
$EditorNames = @("CapCut", "Adobe Premiere Pro", "Topaz Video AI", "TopazVideoAI", "Resolve")
$RunningEditors = @(
    Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -in $EditorNames }
)
if ($RunningEditors.Count -gt 0) {
    throw "Close GPU-heavy editors before the load-only test"
}

$CudaBuildOutput = @(
    & $CudaPython -c 'import torch,sys; print(torch.__version__); print(torch.version.cuda or "CPU_ONLY"); sys.exit(0 if torch.version.cuda else 3)' 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python does not contain a CUDA-enabled PyTorch build"
}

$DefaultPowerText = @(
    & nvidia-smi --query-gpu=power.default_limit --format=csv,noheader,nounits 2>&1
)
if ($LASTEXITCODE -ne 0 -or $DefaultPowerText.Count -lt 1) {
    throw "Could not read the default GPU power limit"
}
$DefaultPowerLimit = [int][Math]::Round(
    [double]::Parse(
        ([string]$DefaultPowerText[0]).Trim(),
        [Globalization.CultureInfo]::InvariantCulture
    )
)

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path $WorkRoot $RunStamp
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$Report = Join-Path $RunDir "load_only.report.json"
$Stdout = Join-Path $RunDir "load_only.stdout.txt"
$Stderr = Join-Path $RunDir "load_only.stderr.txt"

$EnvironmentNames = @(
    "CUDA_VISIBLE_DEVICES",
    "CUDA_LAUNCH_BLOCKING",
    "CUDA_DEVICE_MAX_CONNECTIONS",
    "PYTORCH_NO_CUDA_MEMORY_CACHING",
    "TORCH_SHOW_CPP_STACKTRACES",
    "TORCH_DISABLE_ADDR2LINE",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "TOKENIZERS_PARALLELISM",
    "PYTHONUTF8",
    "PYTHONIOENCODING"
)
$SavedEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

function Restore-ProcessEnvironment {
    foreach ($Name in $EnvironmentNames) {
        $Value = $SavedEnvironment[$Name]
        if ($null -eq $Value) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable($Name, [string]$Value, "Process")
        }
    }
}

function Show-GpuTelemetry {
    param([string]$Label)
    Write-Host $Label -ForegroundColor Cyan
    & nvidia-smi `
        --query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,power.limit,utilization.gpu,utilization.memory,memory.used,memory.total `
        --format=csv
}

$StartedAt = Get-Date
$NewestSystemEvent = Get-WinEvent -LogName System -MaxEvents 1 -ErrorAction SilentlyContinue
$BaselineRecordId = if ($NewestSystemEvent) { [long]$NewestSystemEvent.RecordId } else { 0L }
$MemoryFractionArg = $MemoryFraction.ToString("0.000000", [Globalization.CultureInfo]::InvariantCulture)
$PowerChanged = $false
$Process = $null

Write-Host "RTX 3060 VOXCPM2 CUDA LOAD-ONLY" -ForegroundColor Cyan
Write-Host "Fresh reboot diagnostic; no generation" -ForegroundColor Yellow
Write-Host "No automatic retry and no TDR registry changes" -ForegroundColor Green
Write-Host "Python: $CudaPython" -ForegroundColor Yellow
$CudaBuildOutput | ForEach-Object { Write-Host "  $_" }
Write-Host "Run directory: $RunDir" -ForegroundColor Yellow
Write-Host "Power limit: $PowerLimitWatts W; restore target: $DefaultPowerLimit W" -ForegroundColor Yellow
Write-Host "Memory fraction limit: $MemoryFractionArg" -ForegroundColor Yellow
Write-Host "Baseline System RecordId: $BaselineRecordId" -ForegroundColor Yellow

try {
    Write-Host "Setting GPU power limit to $PowerLimitWatts W" -ForegroundColor Cyan
    & nvidia-smi -pl $PowerLimitWatts
    if ($LASTEXITCODE -ne 0) {
        throw "Could not set the GPU power limit. Run PowerShell as Administrator."
    }
    $PowerChanged = $true

    Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    $env:CUDA_LAUNCH_BLOCKING = "1"
    $env:CUDA_DEVICE_MAX_CONNECTIONS = "1"
    $env:PYTORCH_NO_CUDA_MEMORY_CACHING = "1"
    $env:TORCH_SHOW_CPP_STACKTRACES = "1"
    $env:TORCH_DISABLE_ADDR2LINE = "1"
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
    $env:TOKENIZERS_PARALLELISM = "false"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    Show-GpuTelemetry "GPU before load-only"

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $CudaPython
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $Arguments = @(
        $Probe,
        "--archive-root", $ArchiveRoot,
        "--report", $Report,
        "--memory-fraction", $MemoryFractionArg
    )
    foreach ($Argument in $Arguments) {
        [void]$StartInfo.ArgumentList.Add([string]$Argument)
    }

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) {
        throw "Could not start the load-only process"
    }
    $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
    $StderrTask = $Process.StandardError.ReadToEndAsync()
    $Completed = $Process.WaitForExit($TimeoutSeconds * 1000)
    if (-not $Completed) {
        try { $Process.Kill($true) } catch { }
        $Process.WaitForExit()
    }
    $StageStdout = $StdoutTask.GetAwaiter().GetResult()
    $StageStderr = $StderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($Stdout, $StageStdout, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($Stderr, $StageStderr, [Text.Encoding]::UTF8)
    $ExitCode = $Process.ExitCode

    Start-Sleep -Seconds 3
    Show-GpuTelemetry "GPU after load-only"

    $GpuEvents = @(
        Get-WinEvent -FilterHashtable @{ LogName = "System"; StartTime = $StartedAt } -ErrorAction SilentlyContinue |
        Where-Object {
            $_.RecordId -gt $BaselineRecordId -and (
                (
                    $_.ProviderName -in @("nvlddmkm", "Display") -and
                    $_.Id -in @(14, 153, 4101)
                ) -or (
                    $_.ProviderName -eq "Microsoft-Windows-WHEA-Logger" -and
                    $_.Id -in @(17, 18, 19, 46, 47)
                )
            )
        }
    )

    if ($GpuEvents.Count -gt 0) {
        Write-Host "NEW GPU / DISPLAY / WHEA EVENTS" -ForegroundColor Red
        $GpuEvents |
            Sort-Object RecordId |
            Select-Object TimeCreated, RecordId, ProviderName, Id, LevelDisplayName, Message |
            Format-List
        throw "Windows logged a new GPU/Display/WHEA event during load-only"
    }
    if (-not $Completed) {
        throw "Load-only timed out after $TimeoutSeconds seconds"
    }
    if ($ExitCode -ne 0) {
        Write-Host $StageStderr -ForegroundColor Red
        throw "Load-only failed with exit code $ExitCode"
    }
    if (-not (Test-Path -LiteralPath $Report)) {
        throw "Load-only did not write its report"
    }
    $Payload = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if ($Payload.status -ne "passed") {
        throw "Load-only report status was not passed"
    }

    Write-Host "" 
    Write-Host "VOXCPM2 CUDA LOAD-ONLY PASSED" -ForegroundColor Green
    Write-Host "No generation was attempted" -ForegroundColor Green
    Write-Host "No new Event ID 14, 153, 4101 or selected WHEA event detected" -ForegroundColor Green
    Write-Host "Report: $Report" -ForegroundColor Green
}
finally {
    Restore-ProcessEnvironment
    if ($PowerChanged) {
        Write-Host "Restoring GPU power limit to $DefaultPowerLimit W" -ForegroundColor Cyan
        & nvidia-smi -pl $DefaultPowerLimit
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not restore the default power limit automatically"
        }
    }
    if ($OpenLogs) {
        Start-Process explorer.exe -ArgumentList $RunDir
    }
}
