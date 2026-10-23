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

- YouTube client: **no pinned `player_client`** — yt-dlp default clients
  (currently `visionos` for media) are used; pinning `mweb` is rejected by
  `services/youtube_po_token_runtime.py` since 2026-08;
- PO provider: browserless bgutil **HTTP** provider (`bgutil:http-2.0.2`), kept
  resident for the whole bot lifetime instead of spawning Node per download;
- Python plugin and JavaScript server: loaded from **one exact upstream source
  tree**, `bgutil 2.0.2` @ `26475e9d3665b972c4aeb83b4dbaafcf64b88438`
  (`core/bgutil_pin.py` is the only place that pin is written);
- source location: `.runtime/bgutil-ytdlp-pot-provider`;
- yt-dlp plugin search: default/global plugin directories disabled with
  `--no-plugin-dirs`; only the pinned `.runtime` source root is allowed;
- provider server home:
  `.runtime/bgutil-ytdlp-pot-provider/server`;
- server binding: **loopback only** — we pass `--host 127.0.0.1` explicitly and
  prove it at startup (see *Security* below);
- Node.js: >=22 (npm >=9);
- browser automation: none;
- cookies and proxy: still owned by `services.ffmpeg` and are independent from
  PO-token generation.

The bgutil PyPI wheel is deliberately **not** a Python dependency. Keeping the
plugin and the JS runtime in the same exact source checkout prevents a released
wheel from drifting away from the JS provider source.

Tokens are generated automatically and are never stored in `.env`, committed
to Git, or copied into commands manually.

## Security: why the pin is 2.0.x

Upstream advisory [`GHSA-qpv9-8xfj-xx9m`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider/security/advisories/GHSA-qpv9-8xfj-xx9m)
(published 2026-09-08, **high**) affects every release **below `2.0.0`**:

- the HTTP server bound `::` and fell back to `0.0.0.0`
  (`server/src/main.ts` at `a0be235`), so it was reachable from the local
  network;
- a crafted `POST /get_pot` reached an attacker-controlled proxy and executed
  code on the host — also triggerable from a malicious website on unpatched
  browsers.

`1.3.1` (the previous pin) and `1.3.2` are both affected; `1.3.2` only added
the homepage-challenge token minting. `2.0.0` is the first patched release; the
pin therefore moved **directly to `2.0.2`** (2026-10-07), which additionally
refreshes vulnerable lockfile dependencies.

The pin is enforced in three independent places, so no single mistake reopens
the hole:

1. **Pin level** — `core/bgutil_pin.py` refuses a version below `2.0.0`
   (`bgutil_pin_problems()`); `tools/ensure_bgutil_provider.py` calls
   `_require_patched_pin()` before any network or build work, so an
   unpatched pin cannot be provisioned at all.
2. **Our own argv** — `services/bgutil_http_runtime.py` starts the server with
   `--port 4416 --host 127.0.0.1`. The loopback binding is a property of this
   project's command line, not of an upstream default that may change again
   (in the container image the default is still overridden to `0.0.0.0`).
3. **Runtime proof** — after `/ping` reports the expected version,
   `require_loopback_binding()` tries to open a TCP connection to the port on
   every non-loopback local address. Any successful connection fails startup
   with an explicit RCE warning. Hosts without a non-loopback address skip the
   probe (nothing to prove) instead of pretending it passed.

Upstream 2.0 also rejects browser-originated requests (`Origin` /
`Sec-Fetch-Site: cross-site`) and blocks PAC proxy schemes, which is why the
upgrade matters even for a native, non-Docker install.

### Compatibility of the 2.0 pin

- builds with Node 22 via the local `tsc` from `npm ci` (no `npx` download);
- the Python plugin entry (`plugin/yt_dlp_plugins/extractor/getpot_bgutil.py`)
  still reports `__version__` and is discovered as `bgutil:http-2.0.2`;
- `/get_pot` response shape (`poToken`, `contentBinding`, `expiresAt`) is the
  one this project already consumes;
- the runtime marker format is unchanged (`<version>@<commit>`), so an existing
  `1.3.1` tree is rebuilt automatically on the next `Start Bot.bat` — the
  marker mismatch is treated as a repair, not as a warning.

The bgutil PyPI wheel is deliberately **not** a Python dependency. Keeping the
plugin and the JS runtime in the same exact source checkout prevents a released
wheel from drifting away from the JS provider source, and keeps one commit as
the supply-chain identity.

Tokens are generated automatically and are never stored in `.env`, committed
to Git, or copied into commands manually.

## Provisioning contract

`tools/ensure_bgutil_provider.py` owns exact-source provisioning. Both the
managed Windows launcher and the canonical Python entrypoint call the same
idempotent provisioner, so `Start Bot.bat` and direct `python bot_new.py`
launches no longer have different YouTube-repair semantics after `git pull`.

The provisioner:

1. requires Node.js >=22;
2. reuses an existing runtime only when the exact marker, Python plugin,
   compiled JS and `node_modules` are all present;
3. validates the compiled runtime **without executing it**: `node --check
   server/build/generate_once.js` proves the syntax, and `package.json` +
   the source and installed npm locks prove the dependency graph. Running
   `--version` would import the whole `SessionManager` (BotGuard, jsdom,
   `youtubei.js`) just to print a string, which used to time out on cold
   Windows paths;
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
    syntax/`node_modules` check before publication;
12. writes `2.0.2@26475e9d3665b972c4aeb83b4dbaafcf64b88438` (`marker_text()`)
    to the runtime marker;
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
3. `yt-dlp.conf` to contain exactly the source-only plugin restriction, no
   pinned `player_client`, and the pinned bgutil server-home route;
4. no cookie source or manual `po_token` in `yt-dlp.conf`;
5. the exact runtime marker for `2.0.2@26475e9d...`;
6. the Python plugin entry and `server/build/generate_once.js` in that same
   source tree;
7. an isolated child-process import proving the plugin module is loaded from
   the pinned source directory, not site-packages;
8. Node.js >=22;
9. loopback-only binding of the HTTP provider (`require_loopback_binding()`).

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
source plugin directory and a `bgutil:http-2.0.2` provider.  `.runtime/bgutil_http.log`
starts with `Started POT server (v2.0.2) on address 127.0.0.1:4416` (never
`[::]` or `0.0.0.0`), and no Chrome window should launch. A metadata-only success is insufficient: the final production
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
