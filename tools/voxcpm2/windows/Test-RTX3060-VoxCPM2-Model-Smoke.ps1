[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe",
    [string]$ArchiveRoot = "C:\AI-Archive\VoxCPM2-paused-RTX3060",
    [string]$ReferenceWav = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL\references\B_extended_24s.wav",
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-VOXCPM2-MODEL-SMOKE",
    [int]$TimeoutSeconds = 300,
    [int]$Steps = 4,
    [double]$Cfg = 1.80,
    [double]$MemoryFraction = 0.70,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

if ($TimeoutSeconds -lt 60 -or $TimeoutSeconds -gt 900) {
    throw "TimeoutSeconds must be within 60..900"
}
if ($Steps -lt 1 -or $Steps -gt 8) {
    throw "Steps must be within 1..8"
}
if ($Cfg -lt 0.5 -or $Cfg -gt 3.0) {
    throw "Cfg must be within 0.5..3.0"
}
if ($MemoryFraction -lt 0.10 -or $MemoryFraction -gt 0.85) {
    throw "MemoryFraction must be within 0.10..0.85"
}

$Probe = Join-Path $RepoRoot "tools\voxcpm2\voxcpm2_cuda_model_smoke.py"
foreach ($Required in @($CudaPython, $Probe, $ArchiveRoot, $ReferenceWav)) {
    if (-not (Test-Path -LiteralPath $Required)) {
        throw "Missing required path: $Required"
    }
}

$RunningFfmpeg = @(Get-Process -Name "ffmpeg" -ErrorAction SilentlyContinue)
if ($RunningFfmpeg.Count -gt 0) {
    throw "An ffmpeg process is still running. Stop NVENC/video work before model smoke."
}

$EditorNames = @(
    "CapCut",
    "Adobe Premiere Pro",
    "Topaz Video AI",
    "TopazVideoAI",
    "Resolve"
)
$RunningEditors = @(
    Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -in $EditorNames }
)
if ($RunningEditors.Count -gt 0) {
    Write-Warning (
        "GPU-capable editor processes are open: " +
        (($RunningEditors | Select-Object -ExpandProperty ProcessName -Unique) -join ", ")
    )
    throw "Close GPU-heavy editors before the VoxCPM2 CUDA model smoke"
}

$CudaBuildOutput = @(
    & $CudaPython -c 'import torch,sys; print(torch.__version__); print(torch.version.cuda or "CPU_ONLY"); sys.exit(0 if torch.version.cuda else 3)' 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw "The selected Python does not contain a CUDA-enabled PyTorch build: $CudaPython"
}

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path $WorkRoot $RunStamp
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Report = Join-Path $RunDir "model_smoke.report.json"
$Stdout = Join-Path $RunDir "model_smoke.stdout.txt"
$Stderr = Join-Path $RunDir "model_smoke.stderr.txt"
$OutputWav = Join-Path $RunDir "model_smoke.wav"

$EnvironmentNames = @(
    "CUDA_VISIBLE_DEVICES",
    "CUDA_LAUNCH_BLOCKING",
    "PYTORCH_NO_CUDA_MEMORY_CACHING",
    "TORCH_SHOW_CPP_STACKTRACES",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "TOKENIZERS_PARALLELISM",
    "PYTHONUTF8",
    "PYTHONIOENCODING"
)
$SavedEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable(
        $Name,
        "Process"
    )
}

function Restore-ProcessEnvironment {
    foreach ($Name in $EnvironmentNames) {
        $Value = $SavedEnvironment[$Name]
        if ($null -eq $Value) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable(
                $Name,
                [string]$Value,
                "Process"
            )
        }
    }
}

function Show-GpuTelemetry {
    param([string]$Label)

    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Write-Warning "nvidia-smi was not found"
        return
    }

    Write-Host $Label -ForegroundColor Cyan
    & nvidia-smi `
        --query-gpu=name,driver_version,pstate,temperature.gpu,power.draw,utilization.gpu,utilization.memory,memory.used,memory.total `
        --format=csv
}

$StartedAt = Get-Date
$NewestSystemEvent = Get-WinEvent -LogName System -MaxEvents 1 -ErrorAction SilentlyContinue
$BaselineRecordId = if ($NewestSystemEvent) {
    [long]$NewestSystemEvent.RecordId
}
else {
    0L
}

$CfgArg = $Cfg.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)
$MemoryFractionArg = $MemoryFraction.ToString(
    "0.000000",
    [Globalization.CultureInfo]::InvariantCulture
)

Write-Host "RTX 3060 VOXCPM2 CUDA MODEL SMOKE" -ForegroundColor Cyan
Write-Host "One model load and one short generation only" -ForegroundColor Yellow
Write-Host "No automatic retry" -ForegroundColor Green
Write-Host "No TDR registry values are changed" -ForegroundColor Green
Write-Host "Python: $CudaPython" -ForegroundColor Yellow
$CudaBuildOutput | ForEach-Object { Write-Host "  $_" }
Write-Host "Reference: $ReferenceWav" -ForegroundColor Yellow
Write-Host "Run directory: $RunDir" -ForegroundColor Yellow
Write-Host "Steps: $Steps" -ForegroundColor Yellow
Write-Host "CFG: $CfgArg" -ForegroundColor Yellow
Write-Host "CUDA memory fraction limit: $MemoryFractionArg" -ForegroundColor Yellow
Write-Host "Timeout: $TimeoutSeconds seconds" -ForegroundColor Yellow
Write-Host "Baseline System RecordId: $BaselineRecordId" -ForegroundColor Yellow

$Process = $null
$Completed = $false
$ExitCode = $null
$StageStdout = ""
$StageStderr = ""
$GpuEvents = @()
$FailureMessage = $null

try {
    Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    $env:CUDA_LAUNCH_BLOCKING = "1"
    $env:PYTORCH_NO_CUDA_MEMORY_CACHING = "1"
    $env:TORCH_SHOW_CPP_STACKTRACES = "1"
    $env:HF_HUB_OFFLINE = "1"
    $env:TRANSFORMERS_OFFLINE = "1"
    $env:TOKENIZERS_PARALLELISM = "false"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    Show-GpuTelemetry "GPU before model smoke"

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $CudaPython
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true

    $Arguments = @(
        $Probe,
        "--archive-root", $ArchiveRoot,
        "--reference-wav", $ReferenceWav,
        "--output-wav", $OutputWav,
        "--report", $Report,
        "--cfg", $CfgArg,
        "--steps", [string]$Steps,
        "--memory-fraction", $MemoryFractionArg
    )
    foreach ($Argument in $Arguments) {
        [void]$StartInfo.ArgumentList.Add([string]$Argument)
    }

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) {
        throw "Could not start the VoxCPM2 CUDA model smoke process"
    }

    $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
    $StderrTask = $Process.StandardError.ReadToEndAsync()
    $Completed = $Process.WaitForExit($TimeoutSeconds * 1000)

    if (-not $Completed) {
        try {
            $Process.Kill($true)
        }
        catch {
            Write-Warning "Could not kill timed-out model smoke: $($_.Exception.Message)"
        }
        $Process.WaitForExit()
    }

    $StageStdout = $StdoutTask.GetAwaiter().GetResult()
    $StageStderr = $StderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($Stdout, $StageStdout, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($Stderr, $StageStderr, [Text.Encoding]::UTF8)
    $ExitCode = $Process.ExitCode

    Start-Sleep -Seconds 3
    Show-GpuTelemetry "GPU after model smoke"

    $SystemEvents = @(
        Get-WinEvent `
            -FilterHashtable @{ LogName = "System"; StartTime = $StartedAt } `
            -ErrorAction SilentlyContinue
    )
    $GpuEvents = @(
        $SystemEvents |
        Where-Object {
            $_.RecordId -gt $BaselineRecordId -and (
                (
                    ($_.ProviderName -in @("nvlddmkm", "Display")) -and
                    ($_.Id -in @(14, 153, 4101))
                ) -or (
                    ($_.ProviderName -eq "Microsoft-Windows-WHEA-Logger") -and
                    ($_.Id -in @(17, 18, 19, 46, 47))
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
        $FailureMessage = "Windows logged a new GPU/Display/WHEA event during the model smoke"
    }
    elseif (-not $Completed) {
        $FailureMessage = "VoxCPM2 CUDA model smoke timed out after $TimeoutSeconds seconds"
    }
    elseif ($ExitCode -ne 0) {
        Write-Host $StageStderr -ForegroundColor Red
        $FailureMessage = "VoxCPM2 CUDA model smoke failed with exit code $ExitCode"
    }
    elseif (-not (Test-Path -LiteralPath $Report)) {
        $FailureMessage = "VoxCPM2 CUDA model smoke did not write its report"
    }
    else {
        $Payload = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
        if ($Payload.status -ne "passed") {
            $FailureMessage = "VoxCPM2 CUDA model smoke report status was not passed"
        }
        elseif (-not (Test-Path -LiteralPath $OutputWav)) {
            $FailureMessage = "VoxCPM2 CUDA model smoke did not create the WAV output"
        }
    }

    if ($FailureMessage) {
        throw $FailureMessage
    }

    Write-Host "" 
    Write-Host "VOXCPM2 CUDA MODEL SMOKE PASSED" -ForegroundColor Green
    Write-Host "No new Event ID 14, 153, 4101 or selected WHEA event detected" -ForegroundColor Green
    Write-Host "WAV: $OutputWav" -ForegroundColor Green
    Write-Host "Report: $Report" -ForegroundColor Green
    Write-Host "This is not permission for an unattended or full-length GPU render." -ForegroundColor Yellow
}
finally {
    Restore-ProcessEnvironment
    if ($OpenLogs) {
        Start-Process explorer.exe -ArgumentList $RunDir
    }
}
