# YouTube PO Token production runtime

## Why this exists

In August 2026 YouTube tightened Google Video Server (GVS) authorization for
some clients and media URLs. A yt-dlp extraction can still succeed, select a
maximum-quality format, and then receive HTTP 403 when the selected
`googlevideo.com` URL is opened.

This project does not treat a lower-resolution format as a recovery path.
Shorts Factory MAX keeps `bestaudio/best` and `bestvideo+bestaudio/best`, keeps
fragment-integrity checks, and fails closed when the maximum-quality source
cannot be acquired completely.

## Production route

The repository uses the yt-dlp PO Token Provider framework with:

- YouTube client: `mweb`;
- Python provider plugin: `bgutil-ytdlp-pot-provider==1.3.1`;
- companion JS runtime: upstream bgutil tag `1.3.1`, exact commit
  `7608dd51ee813b48cf9a6d68c6e42cb197ce10e0`;
- execution mode: official bgutil Node script provider;
- JavaScript challenge runtime: existing Deno/Node + `yt-dlp-ejs` path;
- network path: existing yt-dlp proxy selection in `services.ffmpeg`;
- cookies: owned independently by `services.ffmpeg`; `yt-dlp.conf` contains no
  cookie source.

The production PO-token route is browserless. WPC/nodriver/Chrome are not part
of the dependency graph. A short-lived Node process mints the video-bound token
when yt-dlp needs one; no manual token is stored in `.env`, Git or command-line
configuration.

## Pinned JS bootstrap

The Python plugin alone is not enough: bgutil also needs its matching JavaScript
provider. `Start Bot.bat` runs `tools/bootstrap_bgutil_provider.py` before the
bot starts. The bootstrap:

1. requires Git, Node.js >=20 and npm >=9;
2. clones upstream tag `1.3.1` into a temporary repo-local directory;
3. verifies that the tag resolves to the exact pinned commit above;
4. installs the upstream `package-lock.json` with `npm ci`;
5. builds with the project's pinned TypeScript tool;
6. atomically installs the result under `.runtime/bgutil-ytdlp-pot-provider`;
7. writes a version+commit marker so normal subsequent starts do no network or
   npm work.

`.runtime/` is ignored by Git. Changing the provider requires an intentional PR
that updates both Python plugin and JS tag/commit together.

## Startup contract

`bot_new.py` validates before accepting work that:

- the bgutil Python distribution is exactly `1.3.1`;
- the plugin imports against the current yt-dlp in an isolated child Python
  interpreter (no duplicate plugin registration in the bot process);
- Node.js >=20 is available;
- the repo-local JS build exists and matches the pinned version+commit marker.

If any item is missing, startup stops explicitly. The bot never silently falls
back to format 18 / 360p.

After a pull that changes this runtime, run:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
& ".\Start Bot.bat"
```

The first run builds the pinned bgutil JS runtime. Expected startup diagnostic:

```text
✅ YouTube PO Token: bgutil 1.3.1; script-node; browser=none; runtime=server
```

## Diagnostic smoke test

Use a real public YouTube URL from the project root:

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

Healthy verbose output lists `bgutil:script-node-1.3.1` instead of
`PO Token Providers: none`. The selected maximum-quality media URL must actually
download; metadata extraction alone is not a sufficient health check.

For content requiring an authenticated YouTube session, use the existing cookie
configuration. Cookies do not replace a required GVS PO Token.

## Quality invariants

Do not fix future YouTube failures by:

- forcing format 18 or a fixed low-resolution format;
- removing `--abort-on-unavailable-fragments`;
- accepting partial media after an HTTP/fragment failure;
- embedding a manually copied PO Token in code, config, `.env`, CI or logs;
- reintroducing a visible browser into the default bot path;
- disabling decoded-duration verification for Factory analysis audio.

If YouTube changes PO-token enforcement again, update the provider/client route
and tests while preserving maximum-quality selectors and integrity checks.
