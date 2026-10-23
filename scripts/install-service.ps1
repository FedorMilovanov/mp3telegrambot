#requires -Version 5.1
<#
.SYNOPSIS
Registers MP3Bot as a Windows Scheduled Task and manages its lifecycle.

.DESCRIPTION
Automates what the deployment doc describes by hand:

1. Checks that Python 3.12/3.13, FFmpeg and the repository venv exist.
2. Registers a boot-time task that starts `pythonw.exe bot_new.py` hidden,
   in the repository directory (the bot reads .env relative to its own path,
   so the working directory must be the checkout).
3. Configures restart-on-failure, so a crash is not a silent outage until the
   next reboot.
4. Supports Stop, Uninstall and Status.

The task runs as the current user: the bot needs the user's %TEMP%, the user's
GPU session and the user's .env. Running as SYSTEM breaks Local Bot API paths
and CUDA discovery.

.PARAMETER Action
Install (default), Start, Stop, Uninstall or Status.

.PARAMETER TaskName
Scheduled task name. Default: MP3Bot.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Status

.NOTES
Documentation: docs/ops/deploy_windows.md
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Start', 'Stop', 'Uninstall', 'Status')]
    [string]$Action = 'Install',
    [string]$TaskName = 'MP3Bot'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\pythonw.exe'
$Entry = Join-Path $RepoRoot 'bot_new.py'
$LogDir = Join-Path $RepoRoot 'logs'

function Get-TaskSafe {
    param([string]$Name)
    return Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
}

function Assert-Prerequisites {
    if (-not (Test-Path $Entry)) {
        throw "Не найден $Entry — скрипт должен лежать в scripts\ внутри репозитория."
    }
    if (-not (Test-Path $Python)) {
        throw "Не найден $Python. Создайте окружение: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements-lock.txt"
    }
    # `pythonw.exe` не печатает ошибки в консоль: версия проверяется через python.exe,
    # иначе неудачный старт выглядит как «бот молчит».
    $console = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    $version = & $console -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
    if ([version]$version -lt [version]'3.12') {
        throw "Нужен Python 3.12 или 3.13, в .venv стоит $version."
    }
    foreach ($tool in @('ffmpeg', 'ffprobe')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Write-Warning "$tool не найден в PATH: конвертация и субтитры не будут работать."
        }
    }
    if (-not (Test-Path (Join-Path $RepoRoot '.env'))) {
        Write-Warning ".env не найден: скопируйте .env.example и заполните токены."
    }
}

switch ($Action) {
    'Install' {
        Assert-Prerequisites
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

        $existing = Get-TaskSafe -Name $TaskName
        if ($existing) {
            # Пересоздаём задачу, а не правим: правка действий у существующей
            # задачи оставляет старые настройки перезапуска.
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }

        $action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Entry`"" -WorkingDirectory $RepoRoot
        $trigger = New-ScheduledTaskTrigger -AtStartup
        # Перезапуск при падении и запрет на неограниченный параллельный запуск:
        # второй экземпляр бота — отдельный класс инцидентов (двойные ответы).
        $settings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -MultipleInstances IgnoreNew `
            -RestartCount 999 `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero)
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal `
            -Description 'MP3Bot: Telegram media bot (pythonw bot_new.py)' | Out-Null

        Write-Host "Задача '$TaskName' зарегистрирована."
        Write-Host "Запуск:  powershell -ExecutionPolicy Bypass -File scripts\install-service.ps1 -Action Start"
        Write-Host "Логи:    $RepoRoot\bot.log и $LogDir"
    }

    'Start' {
        if (-not (Get-TaskSafe -Name $TaskName)) {
            throw "Задача '$TaskName' не зарегистрирована. Сначала -Action Install."
        }
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 3
        $state = (Get-ScheduledTask -TaskName $TaskName).State
        Write-Host "Состояние: $state"
        if (Test-Path (Join-Path $RepoRoot 'bot.log')) {
            Write-Host '--- последние строки bot.log ---'
            Get-Content (Join-Path $RepoRoot 'bot.log') -Tail 15
        }
    }

    'Stop' {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        # Останавливаем и процесс: планировщик убивает только задачу, а python.exe
        # может остаться с открытой БД и занять singleton-lock.
        Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
            Where-Object { $_.CommandLine -like "*$Entry*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "Задача '$TaskName' остановлена."
    }

    'Status' {
        $task = Get-TaskSafe -Name $TaskName
        if (-not $task) {
            Write-Host "Задача '$TaskName' не зарегистрирована."
            break
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        [pscustomobject]@{
            TaskName    = $TaskName
            State       = $task.State
            LastRunTime = $info.LastRunTime
            LastResult  = $info.LastTaskResult
            NextRunTime = $info.NextRunTime
        } | Format-List
        $health = 'DISABLE_HEALTH_CHECK' -in @(
            Get-Content (Join-Path $RepoRoot '.env') -ErrorAction SilentlyContinue |
                ForEach-Object { ($_ -split '=')[0].Trim() }
        )
        if (-not $health) {
            $port = '10000'
            Get-Content (Join-Path $RepoRoot '.env') -ErrorAction SilentlyContinue |
                Where-Object { $_ -match '^\s*PORT\s*=' } |
                ForEach-Object { $port = ($_ -split '=', 2)[1].Trim() }
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 5
                Write-Host "health: $($response.StatusCode) $($response.Content)"
            } catch {
                Write-Warning "health-check не ответил на порту $port : $($_.Exception.Message)"
            }
        }
    }

    'Uninstall' {
        if (Get-TaskSafe -Name $TaskName) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
        Write-Host "Задача '$TaskName' удалена. Файлы и .env не тронуты."
    }
}
