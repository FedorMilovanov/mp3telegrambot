# YouTube PO Token production runtime

## Why this exists

In August 2026 YouTube tightened Google Video Server (GVS) authorization for
some clients and media URLs. A yt-dlp extraction can still succeed, select a
maximum-quality format, and then receive HTTP 403 when the selected
`googlevideo.com` URL is opened.

This project does not treat a lower-resolution format as a recovery path.
Shorts Factory MAX keeps its existing `bestaudio/best` and
`bestvideo+bestaudio/best` contracts and fails closed when the maximum-quality
source cannot be acquired completely.

## Production route

The repository uses the yt-dlp PO Token Provider framework with:

- YouTube client: `mweb`;
- provider: `yt-dlp-getpot-wpc==1.1.2`;
- browser automation: `nodriver==0.50.3`;
- JavaScript challenge runtime: the existing Deno/Node + `yt-dlp-ejs` path;
- network path: the existing yt-dlp proxy selection in `services.ffmpeg`;
- cookies: owned independently by `services.ffmpeg`; `yt-dlp.conf` contains no
  cookie source.

WPC launches YouTube in a Chrome/Chromium browser when yt-dlp requests a PO
Token and mints the required token for that request. Tokens are not stored in
`.env`, committed to Git, or copied into the command line manually.

## Startup contract

`bot_new.py` validates the PO-token runtime before accepting work. Startup
requires:

1. `yt-dlp-getpot-wpc` installed;
2. `nodriver` installed;
3. Google Chrome or Chromium discoverable by nodriver.

If any item is missing, startup stops with an explicit error. This is
intentional: the bot must not silently fall back to format 18 / 360p or another
quality downgrade.

After a pull that changes `requirements-lock.txt`, prefer:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
.\Start Bot.bat
```

The launcher detects the changed dependency lock and installs the new runtime.
If launching manually, sync the environment first:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe bot_new.py
```

## Diagnostic smoke test

Use a real public YouTube URL and the project virtual environment:

```powershell
$url = "https://www.youtube.com/watch?v=REAL_VIDEO_ID"

.\.venv\Scripts\python.exe -m yt_dlp -v --no-config `
  --config-location yt-dlp.conf `
  --js-runtimes deno `
  --remote-components ejs:github `
  -f "bestaudio/best" `
  --no-playlist `
  "$url"
```

For a healthy installation, verbose output must list an external WPC PO Token
provider instead of `PO Token Providers: none`. The selected maximum-quality
media URL must download successfully; metadata extraction alone is not a
sufficient health check.

For content that genuinely requires an authenticated YouTube session, use the
existing cookie configuration. Cookies do not replace a required GVS PO Token.

## Quality invariants

Do not fix future YouTube failures by:

- forcing format 18 or a fixed low-resolution format;
- removing `--abort-on-unavailable-fragments`;
- accepting a partial media file after an HTTP/fragment failure;
- embedding a manually copied PO Token in code, config, `.env`, CI, or logs;
- disabling the decoded-duration verification for Factory analysis audio.

If YouTube changes PO-token enforcement again, update the provider/client route
and its tests while preserving the maximum-quality selectors and integrity
checks.
