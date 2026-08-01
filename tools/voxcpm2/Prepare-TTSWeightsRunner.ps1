[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$RunnerDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PythonExecutable,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ModelDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ReferenceWav,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$WorkDirectory,

    [ValidateNotNullOrEmpty()]
    [string]$Repository = 'FedorMilovanov/mp3telegrambot',

    [ValidateNotNullOrEmpty()]
    [string]$ProfileId = 'voxcpm2-production-v1',

    [ValidateSet('Validate', 'Apply')]
    [string]$Mode = 'Validate'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Resolve-RequiredLeaf {
    param([Parameter(Mandatory = $true)][string]$Value)
    $Resolved = Resolve-Path -LiteralPath $Value -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $Resolved.Path -PathType Leaf)) {
        throw 'Required file is missing.'
    }
    return $Resolved.Path
}

function Resolve-RequiredDirectory {
    param([Parameter(Mandatory = $true)][string]$Value)
    $Resolved = Resolve-Path -LiteralPath $Value -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $Resolved.Path -PathType Container)) {
        throw 'Required directory is missing.'
    }
    return $Resolved.Path
}

function Test-IsAdministrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RunnerServiceName {
    param([Parameter(Mandatory = $true)][string]$ResolvedRunnerDirectory)
    $Descriptor = Join-Path $ResolvedRunnerDirectory '.service'
    if (-not (Test-Path -LiteralPath $Descriptor -PathType Leaf)) {
        throw 'Runner service descriptor is missing.'
    }
    $Lines = @(
        Get-Content -LiteralPath $Descriptor -Encoding utf8 |
            ForEach-Object { $_.Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($Lines.Count -ne 1) {
        throw 'Runner service descriptor must contain one line.'
    }
    $Name = [string]$Lines[0]
    if (
        -not $Name.StartsWith('actions.runner.', [StringComparison]::Ordinal) -or
        -not $Name.EndsWith('.service', [StringComparison]::Ordinal) -or
        $Name.Length -lt 20 -or
        $Name.Length -gt 240 -or
        $Name.Contains('..')
    ) {
        throw 'Runner service descriptor has an unsafe format.'
    }
    foreach ($Character in $Name.ToCharArray()) {
        if (-not (
            [char]::IsLetterOrDigit($Character) -or
            $Character -eq '-' -or
            $Character -eq '_' -or
            $Character -eq '.'
        )) {
            throw 'Runner service descriptor has an unsafe character.'
        }
    }
    return $Name
}

try {
    if ($env:OS -ne 'Windows_NT') {
        throw 'This provisioning entrypoint supports Windows only.'
    }
    if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion.Major -lt 7) {
        throw 'PowerShell 7 or newer is required because the workflow uses pwsh.'
    }

    $ResolvedRunner = Resolve-RequiredDirectory $RunnerDirectory
    $ResolvedPython = Resolve-RequiredLeaf $PythonExecutable
    $ResolvedModel = Resolve-RequiredDirectory $ModelDirectory
    $ResolvedReference = Resolve-RequiredLeaf $ReferenceWav
    $ServiceName = Get-RunnerServiceName $ResolvedRunner
    $Service = Get-Service -Name $ServiceName -ErrorAction Stop

    $Bindings = [ordered]@{
        TTS_SMOKE_PYTHON = $ResolvedPython
        TTS_SMOKE_MODEL_ROOT = $ResolvedModel
        TTS_SMOKE_REFERENCE_WAV = $ResolvedReference
    }

    if ($Mode -eq 'Apply') {
        if (-not (Test-IsAdministrator)) {
            throw 'Apply mode requires an elevated PowerShell session.'
        }
        if (-not $PSCmdlet.ShouldProcess(
            'weighted TTS runner machine environment and service',
            'Persist three bindings and restart the configured GitHub Actions runner service'
        )) {
            throw 'Apply mode was not confirmed.'
        }
        foreach ($Entry in $Bindings.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable(
                [string]$Entry.Key,
                [string]$Entry.Value,
                [EnvironmentVariableTarget]::Machine
            )
            [Environment]::SetEnvironmentVariable(
                [string]$Entry.Key,
                [string]$Entry.Value,
                [EnvironmentVariableTarget]::Process
            )
        }
        Restart-Service -Name $ServiceName -Force -ErrorAction Stop
        $Service.WaitForStatus(
            [System.ServiceProcess.ServiceControllerStatus]::Running,
            [TimeSpan]::FromSeconds(60)
        )
    }
    else {
        foreach ($Entry in $Bindings.GetEnumerator()) {
            $MachineValue = [Environment]::GetEnvironmentVariable(
                [string]$Entry.Key,
                [EnvironmentVariableTarget]::Machine
            )
            if ([string]::IsNullOrWhiteSpace($MachineValue)) {
                throw 'Required machine environment binding is missing.'
            }
            [Environment]::SetEnvironmentVariable(
                [string]$Entry.Key,
                [string]$MachineValue,
                [EnvironmentVariableTarget]::Process
            )
        }
        if ($Service.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
            throw 'GitHub Actions runner service is not running.'
        }
    }

    $RepositoryDirectory = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    $Cli = Join-Path $RepositoryDirectory 'tools\check_tts_weighted_runner_provisioning.py'
    if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
        throw 'Provisioning checker is missing from the repository.'
    }

    & $ResolvedPython $Cli `
        --runner-directory $ResolvedRunner `
        --repository $Repository `
        --profile-id $ProfileId `
        --python-executable $ResolvedPython `
        --model-directory $ResolvedModel `
        --reference-wav $ResolvedReference `
        --work-directory $WorkDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'Weighted TTS runner provisioning checker failed.'
    }

    Write-Host 'TTS_WEIGHTS_RUNNER_SETUP_OK'
    exit 0
}
catch {
    $FailureType = $_.Exception.GetType().Name
    Write-Error "TTS_WEIGHTS_RUNNER_SETUP_FAILED $FailureType"
    exit 1
}
