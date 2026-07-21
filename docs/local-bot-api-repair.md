# Repairing local Telegram Bot API on Windows

## Symptom

The local `telegram-bot-api.exe` process opens port `8081`, but
`http://127.0.0.1:8081/bot<TOKEN>/getMe` times out. The Python bot then falls
back to `api.telegram.org`, so the effective upload limit becomes 50 MB even
though `LOCAL_BOT_API_URL` is configured.

A typical startup log looks like this:

```text
Local Bot API port is open, but getMe is not responding: TimeoutError
...
Auto-fallback: switching to the cloud Bot API
...
File is too large: 87.3 MB (limit 50 MB)
```

## Root cause

An open local port only proves that the HTTP front end started. The server must
also establish its own TDLib/MTProto connection to Telegram data centers.

`TELEGRAM_PROXY_URL` is used by the Python bot when it talks to the cloud Bot
API. It does **not** configure the TDLib connection inside
`telegram-bot-api.exe`.

The official binary's `--proxy` option is an HTTP proxy for outgoing webhook
requests. It is not a SOCKS/MTProto proxy for Telegram data-center traffic.
Therefore values such as the following do not make the local server work:

```dotenv
LOCAL_BOT_API_PROXY_URL=socks5://127.0.0.1:1080
LOCAL_BOT_API_TDLIB_PROXY_TYPE=socks5
```

The local process needs a Windows-level route through TUN, WireGuard, or another
full VPN. The proxy/VPN client must be running before `telegram-bot-api.exe`
starts.

## Automated repair

From the repository root, enable TUN/VPN first and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair-local-bot-api.ps1 -ApplyConfig
```

The script:

- backs up `.env`;
- clears unsupported local SOCKS/TDLib proxy settings;
- keeps `TELEGRAM_PROXY_URL` for cloud fallback and the one-time cloud `logOut`;
- switches the server data directory to the current user's writable
  `%LOCALAPPDATA%\TelegramBotAPI\data` when necessary;
- verifies the Windows route to Telegram;
- calls cloud Bot API `logOut`, as required when moving a bot to a local server;
- stops stale `telegram-bot-api.exe` processes;
- starts a clean local server;
- waits for local `/getMe` using an actual elapsed-time deadline;
- prints and classifies the server-log tail on failure.

Success is reported only after local `/getMe` returns `ok: true`.

## Expected configuration

```dotenv
LOCAL_BOT_API_URL=http://127.0.0.1:8081
LOCAL_BOT_API_WAIT_LOCAL=1
LOCAL_BOT_API_GETME_TIMEOUT_SEC=90
LOCAL_BOT_API_DATA_DIR=C:\Users\<USER>\AppData\Local\TelegramBotAPI\data

# Keep this for the Python/cloud fallback path if needed:
TELEGRAM_PROXY_URL=socks5h://127.0.0.1:10808

# These do not proxy TDLib in the official binary:
LOCAL_BOT_API_PROXY_URL=
LOCAL_BOT_API_TDLIB_PROXY_TYPE=
LOCAL_BOT_API_PROXY_SERVER=
LOCAL_BOT_API_PROXY_PORT=
```

After a successful repair, restart the bot. Startup must contain both lines:

```text
Local Bot API getMe OK
Using local Telegram Bot API: http://127.0.0.1:8081
```

The effective send limit will then be 2000 MB.

## Manual checks

```powershell
# The local server process and its listening port
Get-Process telegram-bot-api -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue

# Windows/TUN route must work for the local process
Test-NetConnection api.telegram.org -Port 443

# Server log
Get-Content "$env:LOCALAPPDATA\TelegramBotAPI\botapi-server.log" -Tail 120
```

Do not solve this by merely increasing `LOCAL_BOT_API_GETME_TIMEOUT_SEC`. A
server with no Telegram route can keep port 8081 open forever and still never
complete `/getMe`.
