#requires -Version 5.1
<#+
.SYNOPSIS
Repairs and verifies the Windows local Telegram Bot API setup used by mp3telegrambot.

.DESCRIPTION
The official telegram-bot-api binary does not route its TDLib connection through
TELEGRAM_PROXY_URL or a SOCKS LOCAL_BOT_API_PROXY_URL. It needs a working system
route (TUN/WireGuard/VPN). This script:

1. Reads BOT_TOKEN, TELEGRAM_API_ID and TELEGRAM_API_HASH from .env.
2. Optionally rewrites the local Bot API settings to a known-good configuration.
3. Verifies that Windows itself can reach Telegram before starting the server.
4. Calls the cloud Bot API logOut method once before moving the bot locally.
5. Stops stale telegram-bot-api processes, starts a clean local server and waits
   for /getMe using a real elapsed-time deadline.
6. Prints the relevant server-log tail when authorization still fails.

Run the proxy/VPN client with TUN enabled before invoking this script.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\repair-local-bot-api.ps1 -ApplyConfig

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\repair-local-bot-api.ps1 -ApplyConfig -SkipCloudLogout
#>

[CmdletBinding()]
param(
    [string]$EnvFile = ".env",
    [int]$Port = 8081,
    [ValidateRange(15, 300)]
    [int]$TimeoutSec = 90,
    [switch]$ApplyConfig,
    [switch]$SkipCloudLogout,
    [switch]$SkipNetworkCheck
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function ConvertFrom-DotEnvValue([string]$Value) {
    $result = $Value.Trim()
    if ($result.Length -ge 2) {
        $first = $result[0]
        $last = $result[$result.Length - 1]
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $result = $result.Substring(1, $result.Length - 2)
        }
    }
    return $result
}

function Read-DotEnv([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*#' -or $line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            continue
        }
        $values[$matches[1]] = ConvertFrom-DotEnvValue $matches[2]
    }
    return $values
}

function Set-DotEnvValues([string]$Path, [hashtable]$Updates) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $seen = @{}

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
            $key = $matches[1]
            if ($Updates.ContainsKey($key)) {
                $lines.Add("$key=$($Updates[$key])")
                $seen[$key] = $true
                continue
            }
        }
        $lines.Add($line)
    }

    foreach ($key in $Updates.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $lines.Add("$key=$($Updates[$key])")
        }
    }

    $backup = "$Path.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    [System.IO.File]::WriteAllLines(
        (Resolve-Path -LiteralPath $Path).Path,
        $lines,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Configuration updated. Backup: $backup" -ForegroundColor Green
}

function Require-Value([hashtable]$Values, [string]$Name) {
    if (-not $Values.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace([string]$Values[$Name])) {
        throw "$Name is missing in $EnvFile"
    }
    return [string]$Values[$Name]
}

function Convert-ToCurlProxy([string]$ProxyUrl) {
    if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
        return ""
    }
    return $ProxyUrl.Trim()
}

function Invoke-CurlJson {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [string]$ProxyUrl = "",
        [int]$MaxTimeSec = 30,
        [switch]$NoProxy
    )

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        throw "curl.exe was not found. Windows 10/11 normally includes it."
    }

    $arguments = @("--silent", "--show-error", "--fail-with-body", "--max-time", "$MaxTimeSec")
    if ($NoProxy) {
        $arguments += @("--noproxy", "*")
    } elseif (-not [string]::IsNullOrWhiteSpace($ProxyUrl)) {
        $arguments += @("--proxy", $ProxyUrl)
    }
    $arguments += $Url

    $output = & $curl.Source @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "curl failed with exit code $LASTEXITCODE`: $output"
    }
    return ($output | Out-String | ConvertFrom-Json)
}

function Test-SystemTelegramRoute {
    Write-Step "Checking the system/TUN route to Telegram"
    $result = Test-NetConnection -ComputerName api.telegram.org -Port 443 -WarningAction SilentlyContinue
    if ($result.TcpTestSucceeded) {
        Write-Host "Windows can reach api.telegram.org:443. A TUN/VPN route appears available." -ForegroundColor Green
        return $true
    }

    Write-Warning "Windows cannot reach api.telegram.org:443 directly. telegram-bot-api.exe will also fail because it cannot use the Python SOCKS proxy."
    Write-Host "Enable TUN/WireGuard/VPN in the proxy client, run it as Administrator if required, then rerun this script." -ForegroundColor Yellow
    return $false
}

function Get-BotApiLogTail([string]$LogPath, [int]$Lines = 100) {
    if (-not (Test-Path -LiteralPath $LogPath)) {
        return "(server log does not exist: $LogPath)"
    }
    return (Get-Content -LiteralPath $LogPath -Tail $Lines -ErrorAction SilentlyContinue | Out-String)
}

function Show-BotApiDiagnosis([string]$LogTail) {
    Write-Host "`n--- telegram-bot-api log tail ---" -ForegroundColor DarkCyan
    Write-Host $LogTail
    Write-Host "--- end log tail ---`n" -ForegroundColor DarkCyan

    if ($LogTail -match '(?i)(network is unreachable|connection refused|connection reset|failed to connect|timeout|handshake|149\.154\.|91\.108\.)') {
        Write-Warning "The server log indicates a Telegram network/TUN routing failure. SOCKS settings in LOCAL_BOT_API_PROXY_URL cannot repair this path."
    } elseif ($LogTail -match '(?i)(access is denied|can.t be opened|permission denied)') {
        Write-Warning "The server cannot write its data directory. Use a directory under LOCALAPPDATA or repair the ACL."
    } elseif ($LogTail -match '(?i)(unknown option|unrecognized option)') {
        Write-Warning "The server was launched with an unsupported command-line option. Remove old proxy-server/tdlib-proxy flags."
    } elseif ($LogTail -match '(?i)(api[_ -]?id|api[_ -]?hash).*(invalid|wrong)|you must provide valid') {
        Write-Warning "TELEGRAM_API_ID or TELEGRAM_API_HASH is invalid. Obtain both from my.telegram.org."
    }
}

$resolvedEnv = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$EnvFile = $resolvedEnv
$dotenv = Read-DotEnv $EnvFile

$botToken = Require-Value $dotenv "BOT_TOKEN"
$apiId = Require-Value $dotenv "TELEGRAM_API_ID"
$apiHash = Require-Value $dotenv "TELEGRAM_API_HASH"

$defaultExe = "C:\Program Files\TelegramBotAPI\telegram-bot-api.exe"
$exe = if ($dotenv.ContainsKey("LOCAL_BOT_API_EXE") -and $dotenv["LOCAL_BOT_API_EXE"]) {
    [string]$dotenv["LOCAL_BOT_API_EXE"]
} else {
    $defaultExe
}
if (-not (Test-Path -LiteralPath $exe)) {
    throw "telegram-bot-api.exe was not found: $exe"
}

$userDataDir = Join-Path $env:LOCALAPPDATA "TelegramBotAPI\data"
$dataDir = if ($dotenv.ContainsKey("LOCAL_BOT_API_DATA_DIR") -and $dotenv["LOCAL_BOT_API_DATA_DIR"]) {
    [string]$dotenv["LOCAL_BOT_API_DATA_DIR"]
} else {
    $userDataDir
}

# ProgramData directories created by an elevated installer often become unreadable
# when the bot later starts as a normal user. Prefer a per-user directory here.
if ($dataDir -like "$env:ProgramData*") {
    Write-Warning "LOCAL_BOT_API_DATA_DIR points to ProgramData. Switching the repair run to the user-writable LOCALAPPDATA directory."
    $dataDir = $userDataDir
}
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$logPath = Join-Path (Split-Path -Parent $dataDir) "botapi-server.log"

$unsupportedProxy = ""
if ($dotenv.ContainsKey("LOCAL_BOT_API_PROXY_URL")) {
    $unsupportedProxy = [string]$dotenv["LOCAL_BOT_API_PROXY_URL"]
}
if ($unsupportedProxy -match '^(?i)(socks|mtproto)') {
    Write-Warning "LOCAL_BOT_API_PROXY_URL=$unsupportedProxy is not a TDLib proxy for telegram-bot-api.exe and is ignored for Telegram DC connections."
}

if ($ApplyConfig) {
    Write-Step "Writing a known-good local Bot API configuration"
    $updates = @{
        LOCAL_BOT_API_URL = "http://127.0.0.1:$Port"
        LOCAL_BOT_API_WAIT_LOCAL = "1"
        LOCAL_BOT_API_GETME_TIMEOUT_SEC = "$TimeoutSec"
        LOCAL_BOT_API_DATA_DIR = $dataDir
        # These old values cannot proxy TDLib in the official binary. Keep them
        # empty so startup logs no longer suggest that SOCKS is being applied.
        LOCAL_BOT_API_PROXY_URL = ""
        LOCAL_BOT_API_TDLIB_PROXY_TYPE = ""
        LOCAL_BOT_API_PROXY_SERVER = ""
        LOCAL_BOT_API_PROXY_PORT = ""
        LOCAL_BOT_API_PROXY_LOGIN = ""
        LOCAL_BOT_API_PROXY_PASSWORD = ""
        LOCAL_BOT_API_PROXY_SECRET = ""
    }
    Set-DotEnvValues -Path $EnvFile -Updates $updates
    $dotenv = Read-DotEnv $EnvFile
}

if (-not $SkipNetworkCheck) {
    if (-not (Test-SystemTelegramRoute)) {
        exit 2
    }
}

if (-not $SkipCloudLogout) {
    Write-Step "Logging the bot out of the cloud Bot API before local authorization"
    $telegramProxy = if ($dotenv.ContainsKey("TELEGRAM_PROXY_URL")) {
        Convert-ToCurlProxy ([string]$dotenv["TELEGRAM_PROXY_URL"])
    } else {
        ""
    }

    try {
        $logout = Invoke-CurlJson -Url "https://api.telegram.org/bot$botToken/logOut" -ProxyUrl $telegramProxy -MaxTimeSec 30
        if ($logout.ok -ne $true) {
            throw "Telegram returned: $($logout | ConvertTo-Json -Compress)"
        }
        Write-Host "Cloud Bot API logOut succeeded." -ForegroundColor Green
    } catch {
        Write-Warning "Cloud logOut failed: $($_.Exception.Message)"
        Write-Host "The local server may still authorize, but Telegram officially requires logOut when moving a bot from cloud to local." -ForegroundColor Yellow
        throw
    }
}

Write-Step "Stopping stale local Bot API processes"
Get-Process -Name "telegram-bot-api" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Write-Step "Starting telegram-bot-api.exe on port $Port"
$serverArguments = @(
    "--api-id=$apiId",
    "--api-hash=$apiHash",
    "--local",
    "--http-port=$Port",
    "--dir=$dataDir",
    "--log=$logPath",
    "--verbosity=2"
)
$process = Start-Process -FilePath $exe -ArgumentList $serverArguments -WindowStyle Hidden -PassThru
Write-Host "Started PID $($process.Id). Log: $logPath"

Write-Step "Waiting for local /getMe (deadline: $TimeoutSec seconds)"
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$lastError = ""
$localGetMeUrl = "http://127.0.0.1:$Port/bot$botToken/getMe"

while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSec) {
    if ($process.HasExited) {
        $lastError = "telegram-bot-api.exe exited with code $($process.ExitCode)"
        break
    }

    try {
        $getMe = Invoke-CurlJson -Url $localGetMeUrl -MaxTimeSec 3 -NoProxy
        if ($getMe.ok -eq $true) {
            $username = [string]$getMe.result.username
            Write-Host "`nLocal Bot API is healthy: @$username" -ForegroundColor Green
            Write-Host "The bot can now upload files up to 2000 MB through http://127.0.0.1:$Port." -ForegroundColor Green
            Write-Host "Restart mp3telegrambot. Its startup log must contain: 'Локальный Bot API getMe OK'." -ForegroundColor Green
            exit 0
        }
        $lastError = ($getMe | ConvertTo-Json -Compress)
    } catch {
        $lastError = $_.Exception.Message
    }

    $elapsed = [math]::Floor($stopwatch.Elapsed.TotalSeconds)
    Write-Host "Waiting... ${elapsed}s/$TimeoutSec ($lastError)"
    Start-Sleep -Seconds 2
}

$tail = Get-BotApiLogTail -LogPath $logPath -Lines 120
Show-BotApiDiagnosis -LogTail $tail
throw "Local Bot API /getMe did not become ready within $TimeoutSec seconds. Last error: $lastError"
