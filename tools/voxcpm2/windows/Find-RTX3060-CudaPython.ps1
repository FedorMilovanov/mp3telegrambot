[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string[]]$SearchRoots = @(
        "C:\AI",
        "C:\AI-Archive",
        "C:\Users\Fedor\Projects",
        "C:\Users\Fedor\AppData\Local\Programs\Python"
    ),
    [int]$ProbeTimeoutSeconds = 15,
    [switch]$RunInit,
    [switch]$OpenLogs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

if ($ProbeTimeoutSeconds -lt 5 -or $ProbeTimeoutSeconds -gt 60) {
    throw "ProbeTimeoutSeconds must be within 5..60"
}

function Invoke-PythonBuildProbe {
    param([string]$PythonPath)

    $StartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $PythonPath
    $StartInfo.UseShellExecute = $false
    $StartInfo.CreateNoWindow = $true
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    [void]$StartInfo.ArgumentList.Add("-c")
    [void]$StartInfo.ArgumentList.Add(
        'import json,sys; ' +
        'r={"python":sys.executable}; ' +
        'import torch; ' +
        'r.update({"torch":torch.__version__,"torch_cuda":torch.version.cuda}); ' +
        'print(json.dumps(r)); ' +
        'sys.exit(0 if torch.version.cuda else 3)'
    )

    $SavedCuda = [Environment]::GetEnvironmentVariable(
        "CUDA_VISIBLE_DEVICES",
        "Process"
    )

    try {
        Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue

        $Process = [System.Diagnostics.Process]::new()
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) {
            return [pscustomobject]@{
                Path = $PythonPath
                Status = "start_failed"
                Torch = $null
                Cuda = $null
                Detail = "Could not start python.exe"
            }
        }

        $StdoutTask = $Process.StandardOutput.ReadToEndAsync()
        $StderrTask = $Process.StandardError.ReadToEndAsync()
        $Completed = $Process.WaitForExit($ProbeTimeoutSeconds * 1000)

        if (-not $Completed) {
            try { $Process.Kill($true) } catch { }
            $Process.WaitForExit()
            return [pscustomobject]@{
                Path = $PythonPath
                Status = "timeout"
                Torch = $null
                Cuda = $null
                Detail = "Probe exceeded $ProbeTimeoutSeconds seconds"
            }
        }

        $Stdout = $StdoutTask.GetAwaiter().GetResult().Trim()
        $Stderr = $StderrTask.GetAwaiter().GetResult().Trim()

        if ($Process.ExitCode -eq 0 -and $Stdout) {
            try {
                $Payload = $Stdout | ConvertFrom-Json
                return [pscustomobject]@{
                    Path = $PythonPath
                    Status = "cuda_build"
                    Torch = [string]$Payload.torch
                    Cuda = [string]$Payload.torch_cuda
                    Detail = "CUDA-enabled PyTorch build"
                }
            }
            catch {
                return [pscustomobject]@{
                    Path = $PythonPath
                    Status = "invalid_output"
                    Torch = $null
                    Cuda = $null
                    Detail = $Stdout
                }
            }
        }

        $Detail = if ($Stderr) { $Stderr } elseif ($Stdout) { $Stdout } else { "exit=$($Process.ExitCode)" }
        if ($Detail.Length -gt 400) {
            $Detail = $Detail.Substring(0, 400)
        }
        return [pscustomobject]@{
            Path = $PythonPath
            Status = "not_cuda_build"
            Torch = $null
            Cuda = $null
            Detail = $Detail
        }
    }
    finally {
        if ($null -eq $SavedCuda) {
            Remove-Item Env:CUDA_VISIBLE_DEVICES -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable(
                "CUDA_VISIBLE_DEVICES",
                $SavedCuda,
                "Process"
            )
        }
    }
}

$ExplicitCandidates = @(
    "C:\AI\voxcpm2_env\Scripts\python.exe",
    "C:\AI-Archive\VoxCPM2-paused-RTX3060\.venv\Scripts\python.exe",
    "C:\AI-Archive\VoxCPM2-CUDA-TEST\.venv\Scripts\python.exe",
    "C:\AI-Archive\VoxCPM2-GPU-TEST\.venv\Scripts\python.exe"
)

$Candidates = [System.Collections.Generic.List[string]]::new()
foreach ($Path in $ExplicitCandidates) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $Candidates.Add((Resolve-Path -LiteralPath $Path).Path)
    }
}

foreach ($Root in $SearchRoots) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        Write-Host "Search root absent: $Root" -ForegroundColor DarkYellow
        continue
    }

    Write-Host "Searching: $Root" -ForegroundColor Cyan
    Get-ChildItem `
        -LiteralPath $Root `
        -Filter "python.exe" `
        -File `
        -Recurse `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -match '(?i)(\\Scripts\\python\.exe$|\\python\.exe$)' -and
            $_.FullName -notmatch '(?i)\\WindowsApps\\'
        } |
        ForEach-Object {
            $Candidates.Add($_.FullName)
        }
}

$SystemPython = Get-Command python -ErrorAction SilentlyContinue
if ($SystemPython) {
    $Candidates.Add($SystemPython.Source)
}

$UniqueCandidates = @($Candidates | Sort-Object -Unique)
if ($UniqueCandidates.Count -eq 0) {
    throw "No python.exe candidates were found under the configured search roots"
}

Write-Host ""
Write-Host "Found $($UniqueCandidates.Count) Python candidate(s). Probing torch builds..." -ForegroundColor Cyan

$Results = foreach ($Candidate in $UniqueCandidates) {
    Write-Host "Checking: $Candidate" -ForegroundColor DarkGray
    Invoke-PythonBuildProbe -PythonPath $Candidate
}

$Results |
    Select-Object Status, Torch, Cuda, Path, Detail |
    Format-Table -AutoSize -Wrap

$Winner = $Results |
    Where-Object { $_.Status -eq "cuda_build" } |
    Select-Object -First 1

if (-not $Winner) {
    throw (
        "Python interpreters were found, but none contains a CUDA-enabled PyTorch build. " +
        "No CUDA workload was started and the RTX 3060 was not tested."
    )
}

Write-Host ""
Write-Host "CUDA-enabled PyTorch found:" -ForegroundColor Green
Write-Host $Winner.Path -ForegroundColor Green
Write-Host "torch=$($Winner.Torch), CUDA runtime=$($Winner.Cuda)" -ForegroundColor Green

if ($RunInit) {
    $Probation = Join-Path $PSScriptRoot "Test-RTX3060-CUDA-Probation.ps1"
    if (-not (Test-Path -LiteralPath $Probation)) {
        throw "Missing probation launcher: $Probation"
    }

    $ProbationArgs = @{
        RepoRoot = $RepoRoot
        CudaPython = $Winner.Path
        Profile = "Init"
    }
    if ($OpenLogs) {
        $ProbationArgs.OpenLogs = $true
    }

    & $Probation @ProbationArgs
    if (-not $?) {
        throw "CUDA Init probation failed"
    }
}
else {
    Write-Host ""
    Write-Host "Run Init with:" -ForegroundColor Yellow
    Write-Host (
        ".\tools\voxcpm2\windows\Test-RTX3060-CUDA-Probation.ps1 " +
        "-RepoRoot (Get-Location).Path -CudaPython `"$($Winner.Path)`" " +
        "-Profile Init -OpenLogs"
    )
}
