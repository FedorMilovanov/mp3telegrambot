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
- BgUtils JS source pinned to exact commit
  `7608dd51ee813b48cf9a6d68c6e42cb197ce10e0`;
- token generator: the repo-local browserless script runtime under `.runtime/`;
- browser automation: **none**;
- JavaScript runtime: Node.js for bgutil plus the existing Deno/Node +
  `yt-dlp-ejs` path for YouTube JS challenges;
- network path: the existing yt-dlp proxy selection in `services.ffmpeg`;
- cookies: owned independently by `services.ffmpeg`; `yt-dlp.conf` contains no
  cookie source.

The official yt-dlp PO Token guide lists bgutil as a featured provider. MP3Bot
therefore does not keep WPC/nodriver in production dependencies. No visible
Chrome window is part of normal RUS, Factory, subtitle, or metadata downloads.
Startup also fails closed if the legacy `yt-dlp-getpot-wpc` distribution is
manually reintroduced after migration.

Tokens are generated automatically and are never stored in `.env`, committed
to Git, or copied into commands manually.

## Provisioning contract

`Start Bot.bat` owns the JavaScript provider setup. After Python requirements
are synchronized, it runs:

```powershell
.\.venv\Scripts\python.exe tools\ensure_bgutil_provider.py
```

The provisioner:

1. requires Node.js >=20 (the project already recommends Node >=22);
2. clones bgutil release `1.3.1` into a staging directory under `.runtime/`;
3. verifies `git rev-parse HEAD` is exactly
   `7608dd51ee813b48cf9a6d68c6e42cb197ce10e0` before executing provider code;
4. runs `npm ci` from the upstream lockfile;
5. invokes only the TypeScript compiler installed by that `npm ci` from
   `server/node_modules/.bin/tsc` (`tsc.cmd` on Windows), never an on-demand
   `npx` download;
6. verifies `build/generate_once.js` exists;
7. atomically publishes the prepared runtime and records both version and exact
   commit in `.mp3bot-bgutil-version`.

`.runtime/` is ignored by Git. On later starts the exact version+commit marker
and generated script are checked, so there is no repeated clone/build cost.
If the pinned tag ever resolves to another commit, provisioning fails instead
of silently accepting changed upstream source.

`Start Bot.bat` removes the obsolete WPC/nodriver stack once per virtual
environment and writes `.venv/.wpc-provider-removed`. Recreating `.venv`
naturally repeats the migration. The startup runtime check remains an
independent guard against a manually reinstalled WPC provider.

## Startup contract

`bot_new.py` changes its working directory to the repository root before
loading repo-relative configuration, then validates the PO-token runtime before
accepting work. Startup requires:

1. `bgutil-ytdlp-pot-provider==1.3.1` installed;
2. no installed legacy `yt-dlp-getpot-wpc` distribution;
3. an exact `1.3.1@7608dd51ee813b48cf9a6d68c6e42cb197ce10e0`
   runtime marker;
4. matching `.runtime/bgutil-ytdlp-pot-provider/server/build/generate_once.js`;
5. Node.js >=20.

The plugin compatibility probe is executed in a child Python interpreter so it
does not pre-register yt-dlp providers in the long-lived bot process.

If any item is missing, startup stops with an explicit error. This is
intentional: the bot must not silently fall back to format 18 / 360p or another
quality downgrade.

After a pull that changes runtime/dependency policy, use:

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

## Factory source-integrity contract

Both Factory analysis audio and full video acquisition use
`--abort-on-unavailable-fragments`. Source metadata duration is passed directly
into acquisition. Analysis audio is verified from the decoded FFmpeg timeline;
full video is probed and rejected if its duration does not match the expected
source duration. The later render-source validator remains a second independent
duration guard.

Retry-cache persistence is only an optimization. A filesystem/cache write
failure may be ignored only after the already-prepared analysis audio passes a
fresh stream and decoded-duration verification. Media-integrity failures remain
fail-closed.

## Quality invariants

Do not fix future YouTube failures by:

- forcing format 18 or a fixed low-resolution format;
- removing `--abort-on-unavailable-fragments`;
- accepting a partial media file after an HTTP/fragment failure;
- embedding a manually copied PO Token in code, config, `.env`, CI, or logs;
- disabling decoded-duration verification for Factory analysis audio;
- accepting a full-video source whose duration disagrees with source metadata;
- reintroducing browser automation into the normal downloader path without an
  explicit, separately reviewed reason.

If YouTube changes PO-token enforcement again, update the provider/client route
and its tests while preserving maximum-quality selectors and integrity checks.
