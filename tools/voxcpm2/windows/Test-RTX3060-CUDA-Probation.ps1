[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$CudaPython = "",
    [ValidateSet("Init", "Quick", "Mixed", "Standard")]
    [string]$Profile = "Quick",
    [int]$StageTimeoutSeconds = 45,
    [string]$WorkRoot = "C:\AI-Archive\RTX3060-CUDA-PROBATION",
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

$Probe = Join-Path $RepoRoot "tools\voxcpm2\cuda_probation_probe.py"
if (-not (Test-Path -LiteralPath $Probe)) {
    throw "Missing CUDA probation probe: $Probe"
}

if ($StageTimeoutSeconds -lt 10 -or $StageTimeoutSeconds -gt 300) {
    throw "StageTimeoutSeconds must be within 10..300"
}

$RunningFfmpeg = @(Get-Process -Name "ffmpeg" -ErrorAction SilentlyContinue)
if ($RunningFfmpeg.Count -gt 0) {
    throw "An ffmpeg process is still running. Stop GPU/video work before CUDA probation."
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
    Write-Warning "Close them and save work before continuing."
    throw "Concurrent GPU applications are not allowed during CUDA probation"
}

New-Item -ItemType Directory -Force -Path $WorkRoot | Out-Null
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path $WorkRoot $RunStamp
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$EnvironmentNames = @(
    "CUDA_VISIBLE_DEVICES",
    "CUDA_LAUNCH_BLOCKING",
    "PYTORCH_NO_CUDA_MEMORY_CACHING",
    "TORCH_SHOW_CPP_STACKTRACES"
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

function Get-NewGpuEvents {
    param([datetime]$Since)

    $Events = @(
        Get-WinEvent `
            -FilterHashtable @{ LogName = "System"; StartTime = $Since } `
            -ErrorAction SilentlyContinue
    )

    return @(
        $Events |
        Where-Object {
            (
                ($_.ProviderName -in @("nvlddmkm", "Display")) -and
                ($_.Id -in @(14, 153, 4101))
            ) -or (
                ($_.ProviderName -eq "Microsoft-Windows-WHEA-Logger") -and
                ($_.Id -in @(17, 18, 19, 46, 47))
            )
        }
    )
}

function Test-CudaBuildPython {
    param([string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        return $false
    }

    $Output = @(
        & $PythonPath -c 'import torch,sys; print(torch.__version__); print(torch.version.cuda or "CPU_ONLY"); sys.exit(0 if torch.version.cuda else 3)' 2>&1
    )
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -eq 0) {
        Write-Host "CUDA PyTorch candidate: $PythonPath" -ForegroundColor Green
        $Output | ForEach-Object { Write-Host "  $_" }
        return $true
    }
    return $false
}

function Invoke-ProbeStage {
    param(
        [string]$Stage,
        [double]$DurationSeconds = 15.0
    )

    $StageStart = Get-Date
    $Report = Join-Path $RunDir "$Stage.report.json"
    $Stdout = Join-Path $RunDir "$Stage.stdout.txt"
    $Stderr = Join-Path $RunDir "$Stage.stderr.txt"

    Write-Host "" 
    Write-Host "CUDA STAGE: $Stage" -ForegroundColor Cyan
    Show-GpuTelemetry "GPU before $Stage"

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $CudaPython
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    [void]$StartInfo.ArgumentList.Add($Probe)
    [void]$StartInfo.ArgumentList.Add("--stage")
    [void]$StartInfo.ArgumentList.Add($Stage)
    [void]$StartInfo.ArgumentList.Add("--report")
    [void]$StartInfo.ArgumentList.Add($Report)
    [void]$StartInfo.ArgumentList.Add("--duration-seconds")
    [void]$StartInfo.ArgumentList.Add(
        $DurationSeconds.ToString(
            "0.000",
            [Globalization.CultureInfo]::InvariantCulture
        )
    )

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $StartInfo
    if (-not $Process.Start()) {
        throw "Could not start CUDA stage: $Stage"
    }

    $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
    $StderrTask = $Process.StandardError.ReadToEndAsync()
    $Completed = $Process.WaitForExit($StageTimeoutSeconds * 1000)

    if (-not $Completed) {
        try {
            $Process.Kill($true)
        }
        catch {
            Write-Warning "Could not kill timed-out CUDA process: $($_.Exception.Message)"
        }
        $Process.WaitForExit()
    }

    $StageStdout = $StdoutTask.GetAwaiter().GetResult()
    $StageStderr = $StderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($Stdout, $StageStdout, [Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($Stderr, $StageStderr, [Text.Encoding]::UTF8)

    Start-Sleep -Seconds 2
    Show-GpuTelemetry "GPU after $Stage"
    $GpuEvents = @(Get-NewGpuEvents -Since $StageStart)

    if ($GpuEvents.Count -gt 0) {
        Write-Host "GPU/WHEA events detected after $Stage" -ForegroundColor Red
        $GpuEvents |
            Select-Object TimeCreated, ProviderName, Id, LevelDisplayName, Message |
            Format-List
        throw "CUDA probation stopped: Windows logged a GPU/WHEA event"
    }

    if (-not $Completed) {
        throw "CUDA stage timed out after $StageTimeoutSeconds seconds: $Stage"
    }

    if ($Process.ExitCode -ne 0) {
        Write-Host $StageStderr -ForegroundColor Red
        throw "CUDA stage failed with exit code $($Process.ExitCode): $Stage"
    }

    if (-not (Test-Path -LiteralPath $Report)) {
        throw "CUDA stage did not write its report: $Stage"
    }

    $Payload = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if ($Payload.status -ne "passed") {
        throw "CUDA stage report did not pass: $Stage"
    }

    Write-Host "PASSED: $Stage" -ForegroundColor Green
    Write-Host "No Event ID 14, 153, 4101 or selected WHEA event detected" -ForegroundColor Green
}

try {
    Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
    $env:CUDA_LAUNCH_BLOCKING = "1"
    $env:PYTORCH_NO_CUDA_MEMORY_CACHING = "1"
    $env:TORCH_SHOW_CPP_STACKTRACES = "1"

    if (-not $CudaPython) {
        $Candidates = @(
            "C:\AI\voxcpm2_env\Scripts\python.exe",
            "C:\AI-Archive\VoxCPM2-paused-RTX3060\.venv\Scripts\python.exe",
            "C:\AI-Archive\VoxCPM2-CUDA-TEST\.venv\Scripts\python.exe",
            "C:\AI-Archive\VoxCPM2-GPU-TEST\.venv\Scripts\python.exe"
        )

        $SystemPython = Get-Command python -ErrorAction SilentlyContinue
        if ($SystemPython) {
            $Candidates += $SystemPython.Source
        }

        foreach ($Candidate in ($Candidates | Select-Object -Unique)) {
            if (Test-CudaBuildPython -PythonPath $Candidate) {
                $CudaPython = $Candidate
                break
            }
        }
    }
    elseif (-not (Test-CudaBuildPython -PythonPath $CudaPython)) {
        throw "The selected Python does not contain a CUDA-enabled PyTorch build: $CudaPython"
    }

    if (-not $CudaPython) {
        throw (
            "No CUDA-enabled PyTorch environment was found. " +
            "Pass -CudaPython with the exact python.exe path from the old GPU environment."
        )
    }

    Write-Host "" 
    Write-Host "RTX 3060 CUDA PROBATION" -ForegroundColor Cyan
    Write-Host "Python: $CudaPython" -ForegroundColor Yellow
    Write-Host "Profile: $Profile" -ForegroundColor Yellow
    Write-Host "Run directory: $RunDir" -ForegroundColor Yellow
    Write-Host "CUDA_LAUNCH_BLOCKING=1" -ForegroundColor Yellow
    Write-Host "No TDR registry values are changed" -ForegroundColor Green
    Write-Host "One failure stops the sequence; there are no automatic retries" -ForegroundColor Green

    $Stages = switch ($Profile) {
        "Init" { @("init") }
        "Quick" { @("init", "memory", "fp32") }
        "Mixed" { @("init", "memory", "fp32", "fp16") }
        "Standard" { @("init", "memory", "fp32", "fp16", "sustained") }
    }

    foreach ($Stage in $Stages) {
        if ($Stage -eq "sustained") {
            Invoke-ProbeStage -Stage $Stage -DurationSeconds 20.0
        }
        else {
            Invoke-ProbeStage -Stage $Stage
        }
    }

    Write-Host "" 
    Write-Host "CUDA PROBATION PROFILE PASSED: $Profile" -ForegroundColor Green
    Write-Host "This proves only these small stages, not VoxCPM2 CUDA stability." -ForegroundColor Yellow
}
finally {
    Restore-ProcessEnvironment
}

if ($OpenLogs) {
    Start-Process explorer.exe -ArgumentList $RunDir
}
