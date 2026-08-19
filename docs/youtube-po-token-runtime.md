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

`tools/ensure_bgutil_provider.py` owns exact-source provisioning. Both the
managed Windows launcher and the canonical Python entrypoint call the same
idempotent provisioner, so `Start Bot.bat` and direct `python bot_new.py`
launches no longer have different YouTube-repair semantics after `git pull`.

The provisioner:

1. requires Node.js >=22;
2. reuses an existing runtime only when the exact marker, Python plugin,
   compiled JS and `node_modules` are all present;
3. executes `node server/build/generate_once.js --version` as an offline smoke
   test, proving the compiled script and installed dependency graph actually
   load and report the expected provider version;
4. serializes rebuilds with a repo-local provision lock and uses a unique
   staging path per process;
5. initializes a staging Git repository under `.runtime/`;
6. fetches the exact reviewed bgutil commit SHA, not a moving branch or tag;
7. verifies `git rev-parse HEAD` equals the exact expected SHA before provider
   code is built;
8. requires the Python plugin entry point to exist in the checkout;
9. runs `npm ci --no-audit --no-fund` from the upstream lockfile with a bounded
   timeout;
10. invokes only the TypeScript compiler installed by that `npm ci` from
    `server/node_modules/.bin/tsc` (`tsc.cmd` on Windows), never an on-demand
    `npx` download;
11. verifies `server/build/generate_once.js` exists and passes the same offline
    `--version` smoke test before publication;
12. writes `1.3.1@a0be2352807e3bd6991f09d2cab685a0ab825b26` to the runtime marker;
13. publishes through backup -> promote -> rollback semantics, so a failed
    directory promotion does not intentionally delete the previously prepared
    runtime first.

Git, npm and TypeScript operations are bounded by explicit timeouts. A damaged
or partial existing runtime is rebuilt instead of being trusted because its
marker happens to exist.

`Start Bot.bat` additionally reconciles the obsolete WPC/nodriver browser
provider and the old `bgutil-ytdlp-pot-provider` wheel on every managed launch.
This cleanup is deliberately not marker-gated: if an obsolete provider is
reinstalled later, the launcher remains a repair path.

Even before cleanup, `yt-dlp.conf` disables all default/global plugin
directories, so a stale site-packages provider cannot be selected by the
production yt-dlp route.

## Startup contract

`bot_new.py` changes to the repository root and, after basic environment checks,
invokes the exact-source provisioner before validating YouTube readiness. A
healthy local runtime is reused; a missing or damaged runtime is rebuilt. The
entrypoint then independently validates the fail-closed routing policy before
accepting work.

Startup requires:

1. no legacy `yt-dlp-getpot-wpc` distribution;
2. no redundant installed `bgutil-ytdlp-pot-provider` wheel;
3. `yt-dlp.conf` to contain exactly the source-only plugin restriction, mweb
   route, and pinned bgutil server-home route;
4. no cookie source or manual `po_token` in `yt-dlp.conf`;
5. the exact runtime marker for `a0be2352...`;
6. the Python plugin entry and `server/build/generate_once.js` in that same
   source tree;
7. an isolated child-process import proving the plugin module is loaded from
   the pinned source directory, not site-packages;
8. Node.js >=22.

If repair cannot complete or any independent validation item has drifted,
startup fails explicitly. This is intentional: the bot must not silently fall
back to format 18 / 360p, another YouTube client, a global plugin, or a manually
copied token.

## Updating an existing local installation

The managed path remains the recommended way to synchronize Python dependencies
and remove obsolete packages:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
.\Start Bot.bat
```

If the required Python packages are already installed in the interpreter you
are using, a direct launch is also self-healing for the repo-local bgutil
runtime:

```powershell
python bot_new.py
```

The direct entrypoint will reuse or provision `.runtime` itself. It still does
not install the project's Python requirements into an arbitrary interpreter;
`Start Bot.bat` remains the managed dependency/bootstrap path for `.venv`.

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
