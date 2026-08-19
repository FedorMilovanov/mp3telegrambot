# YouTube PO Token production runtime

## Why this exists

In August 2026 YouTube tightened Google Video Server (GVS) authorization for
some clients and media URLs. A yt-dlp extraction can still succeed, select a
maximum-quality format, and then receive HTTP 403 when the selected
`googlevideo.com` URL is opened.

This project does not treat a lower-resolution format as a recovery path.
Shorts Factory MAX keeps `bestaudio/best` and `bestvideo+bestaudio/best` and
fails closed when the maximum-quality source cannot be acquired completely.

## Production route

The production YouTube route is intentionally narrow:

- YouTube client: `mweb`;
- PO provider: browserless bgutil script provider;
- Python plugin and JavaScript generator: loaded from **one exact upstream
  source tree**, commit `a0be2352807e3bd6991f09d2cab685a0ab825b26`;
- source location: `.runtime/bgutil-ytdlp-pot-provider`;
- yt-dlp plugin search: default/global plugin directories disabled with
  `--no-plugin-dirs`; only the pinned `.runtime` source root is allowed;
- provider server home:
  `.runtime/bgutil-ytdlp-pot-provider/server`;
- Node.js: >=22;
- browser automation: none;
- cookies and proxy: still owned by `services.ffmpeg` and are independent from
  PO-token generation.

The bgutil PyPI wheel is deliberately **not** a Python dependency. Keeping the
plugin and the JS runtime in the same exact source checkout prevents a released
wheel from drifting away from the JS provider source.

Tokens are generated automatically and are never stored in `.env`, committed
to Git, or copied into commands manually.

## Why the source pin changed

The previously released bgutil `1.3.1` source at
`7608dd51ee813b48cf9a6d68c6e42cb197ce10e0` was reproducible but its pinned npm
lock contained security findings. An isolated 2026-08-19 audit of that exact
source reported 9 production dependency findings (6 high) and 14 findings in
the full graph.

Upstream commit `a0be2352807e3bd6991f09d2cab685a0ab825b26` refreshed the dependency graph.
The same isolated audit reported 0 production and 0 full-graph npm findings.
Compatibility audits also proved that this source revision:

- builds with Node 22;
- imports its Python yt-dlp plugin against the project yt-dlp version;
- is discovered as `bgutil:script-node-1.3.1`;
- generates a non-empty video-bound PO token for the exact video ID used to
  reproduce the original Factory 403.

The upstream `plugin/pyproject.toml` at that revision is not directly installed
because its README path currently points outside the plugin project directory.
MP3Bot does not patch that packaging metadata. Instead, yt-dlp uses its native
plugin-directory mechanism to load the upstream `yt_dlp_plugins` source in
place. This preserves the reviewed upstream bytes and one-source-tree identity.

## Provisioning contract

`Start Bot.bat` owns source provisioning. After Python requirements are
synchronized, it runs:

```powershell
.\.venv\Scripts\python.exe tools\ensure_bgutil_provider.py
```

The provisioner:

1. requires Node.js >=22;
2. initializes a staging Git repository under `.runtime/`;
3. fetches the exact reviewed bgutil commit SHA, not a moving branch or tag;
4. verifies `git rev-parse HEAD` equals the exact expected SHA before provider
   code is built;
5. requires the Python plugin entry point to exist in the checkout;
6. runs `npm ci --no-audit --no-fund` from the upstream lockfile;
7. invokes only the TypeScript compiler installed by that `npm ci` from
   `server/node_modules/.bin/tsc` (`tsc.cmd` on Windows), never an on-demand
   `npx` download;
8. verifies `server/build/generate_once.js` exists;
9. atomically publishes the prepared source tree and writes
   `1.3.1@a0be2352807e3bd6991f09d2cab685a0ab825b26` to the runtime marker.

On later starts the marker, JS build, and Python plugin entry are checked before
any network/build work, so a current runtime is reused.

`Start Bot.bat` also performs two idempotent one-time migrations per virtual
environment:

- removes the obsolete WPC/nodriver browser provider;
- removes the old `bgutil-ytdlp-pot-provider` PyPI wheel.

Even before the second migration, `yt-dlp.conf` disables all default/global
plugin directories, so a stale site-packages provider cannot be selected.

## Startup contract

`bot_new.py` changes to the repository root and validates the YouTube runtime
before accepting work. Startup requires:

1. no legacy `yt-dlp-getpot-wpc` distribution;
2. `yt-dlp.conf` to contain exactly the source-only plugin restriction, mweb
   route, and pinned bgutil server-home route;
3. no cookie source or manual `po_token` in `yt-dlp.conf`;
4. the exact runtime marker for `a0be2352...`;
5. the Python plugin entry and `server/build/generate_once.js` in that same
   source tree;
6. an isolated child-process import proving the plugin module is loaded from
   the pinned source directory, not site-packages;
7. Node.js >=22.

If any item is missing or has drifted, startup fails explicitly. This is
intentional: the bot must not silently fall back to format 18 / 360p, another
YouTube client, a global plugin, or a manually copied token.

## Updating an existing local installation

After pulling a commit that changes this runtime, use the launcher once so the
old wheel is removed and the new exact source is provisioned:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
.\Start Bot.bat
```

A manual direct launch is valid only after provisioning:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe tools\ensure_bgutil_provider.py
.\.venv\Scripts\python.exe bot_new.py
```

## Diagnostic smoke test

For a healthy installation, verbose yt-dlp output should show only the pinned
source plugin directory and a `bgutil:script-node-*` provider. No Chrome window
should launch. A metadata-only success is insufficient: the final production
acceptance is a real media acquisition through the same proxy/TUN/cookie path
used by the bot.

GitHub-hosted Azure runners cannot provide that final proof for the reproduced
video because YouTube currently stops those runner IPs earlier with
`LOGIN_REQUIRED / Sign in to confirm you’re not a bot`. The CI audits therefore
prove source identity, plugin discovery, build compatibility and video-bound
PO-token generation; the real GVS media read is verified on the production
network path.

## Factory source-integrity contract

Both Factory analysis audio and full video acquisition retain
`--abort-on-unavailable-fragments`. Source metadata duration is passed into
acquisition. Analysis audio is verified from the decoded FFmpeg timeline; full
video is probed and rejected if its duration disagrees with expected source
duration. The later render-source validator remains a second independent guard.

Retry-cache persistence is only an optimization. A cache write failure may be
ignored only after the prepared analysis audio passes a fresh stream and
decoded-duration verification. Media-integrity failures remain fail-closed.

## Quality invariants

Do not fix future YouTube failures by:

- forcing format 18 or another fixed low-resolution format;
- removing `--abort-on-unavailable-fragments`;
- accepting a partial media file after an HTTP/fragment failure;
- enabling default/global yt-dlp plugin directories;
- installing a second PO-provider implementation beside the pinned source tree;
- embedding a manually copied PO Token in code, config, `.env`, CI, or logs;
- disabling decoded-duration verification for Factory analysis audio;
- accepting a full-video source whose duration disagrees with source metadata;
- reintroducing browser automation into the normal downloader path without a
  separately reviewed reason.

If YouTube changes enforcement again, update the provider/client route and its
tests while preserving maximum-quality selectors and integrity checks.
