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
- provider plugin: `bgutil-ytdlp-pot-provider==1.3.1`;
- token generator: the matching pinned BgUtils release under `.runtime/`;
- browser automation: **none**;
- JavaScript runtime: Node.js for bgutil plus the existing Deno/Node +
  `yt-dlp-ejs` path for YouTube JS challenges;
- network path: the existing yt-dlp proxy selection in `services.ffmpeg`;
- cookies: owned independently by `services.ffmpeg`; `yt-dlp.conf` contains no
  cookie source.

The official yt-dlp PO Token guide lists bgutil as the primary featured
provider and describes browser-based WPC as a fallback/alternative. MP3Bot
therefore does not keep WPC/nodriver in production dependencies. No visible
Chrome window is part of normal RUS, Factory, subtitle, or metadata downloads.

Tokens are generated automatically, cached by the bgutil script provider, and
are never stored in `.env`, committed to Git, or copied into commands manually.

## Provisioning contract

`Start Bot.bat` owns the one-time JavaScript provider setup. After Python
requirements are synchronized, it runs:

```powershell
.\.venv\Scripts\python.exe tools\ensure_bgutil_provider.py
```

The provisioner:

1. requires Node.js >=20 (the project already recommends Node >=22);
2. clones the exact bgutil `1.3.1` release into a staging directory under
   `.runtime/`;
3. runs `npm ci` and `npx tsc` in the provider's `server` directory;
4. verifies `build/generate_once.js` exists;
5. atomically publishes the prepared runtime and writes a version marker.

`.runtime/` is ignored by Git. On later starts the version marker and generated
script are checked, so there is no repeated clone/build cost.

## Startup contract

`bot_new.py` validates the PO-token runtime before accepting work. Startup
requires:

1. `bgutil-ytdlp-pot-provider==1.3.1` installed;
2. matching `.runtime/bgutil-ytdlp-pot-provider/server/build/generate_once.js`;
3. Node.js >=20.

The plugin compatibility probe is executed in a child Python interpreter so it
does not pre-register yt-dlp providers in the long-lived bot process.

If any item is missing, startup stops with an explicit error. This is
intentional: the bot must not silently fall back to format 18 / 360p or another
quality downgrade.

After a pull that changes `requirements-lock.txt`, use:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
& ".\Start Bot.bat"
```

If launching manually, provision first:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe tools\ensure_bgutil_provider.py
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

For a healthy installation, verbose output should list a `bgutil:script-*`
external PO Token provider instead of `PO Token Providers: none`. No Chrome
window should be launched. The selected maximum-quality media URL must download
successfully; metadata extraction alone is not a sufficient health check.

For content that genuinely requires an authenticated YouTube session, use the
existing cookie configuration. Cookies do not replace a required GVS PO Token.

## Quality invariants

Do not fix future YouTube failures by:

- forcing format 18 or a fixed low-resolution format;
- removing `--abort-on-unavailable-fragments`;
- accepting a partial media file after an HTTP/fragment failure;
- embedding a manually copied PO Token in code, config, `.env`, CI, or logs;
- disabling the decoded-duration verification for Factory analysis audio;
- reintroducing browser automation into the normal downloader path without an
  explicit, separately reviewed reason.

If YouTube changes PO-token enforcement again, update the provider/client route
and its tests while preserving the maximum-quality selectors and integrity
checks.
