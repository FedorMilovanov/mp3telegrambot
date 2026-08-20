# Audit: `GVS_ACCEPTANCE=FAIL_HTTP_403` — root cause & remediation

**Status:** audit only — **not pushed**. No code changed in this pass.
**Scope:** why `python tools/check_youtube_gvs.py` still prints
`FAIL_HTTP_403` after PR #170 (`2a1c8cf`, main HEAD).

---

## TL;DR (read this first)

The PO-token **provider layer is fixed and works.** PR #170 did its job:
bgutil HTTP provider is up on `127.0.0.1:4416`, a GVS PO token **is** generated
and **is** attached to the media URL (`pot=...` is present in the googlevideo
URL). The remaining `HTTP 403` is **not** a provider/token problem and no
further bgutil / Node / timeout / warmup change will fix it.

The 403 happens at the **media-download egress layer**:

1. yt-dlp downloads through the v2rayN mixed proxy `http://127.0.0.1:10808`,
   whose exit IP is **`2a01:e5c0:2e0a::2` = AS210644 AEZA GROUP LLC, Paris, FR**
   — a budget **datacenter/VPS** IP, not residential. YouTube GVS refuses media
   (googlevideo.com) downloads from datacenter IPs even when a valid
   video-bound PO token is supplied.
2. Downloads are **unauthenticated** (no `cookies.txt`), which maximises GVS
   IP-reputation scrutiny.
3. *(Possible secondary code gap — see H1)* the proxy may not be forwarded to
   bgutil, so the token could additionally be minted from the machine's direct
   IP ≠ the proxy exit IP.

Either way the **AEZA datacenter exit is the dominant cause**. The fix is
operational (egress IP + auth), not another provider patch.

---

## Evidence chain (new, decisive facts)

### 1. The exit IP is a datacenter/VPS, not residential
From the verbose run, the googlevideo URL embeds `ip=2a01:e5c0:2e0a::2`. That is
what YouTube saw. Lookup:
```
2a01:e5c0:2e0a::2 → AS210644 AEZA GROUP LLC, Paris, Île-de-France, FR
```
AEZA is a hosting/VPS provider. Its ranges are routinely flagged by YouTube GVS.
The bot's `_proxy_for_ytdlp()` feeds `--proxy http://127.0.0.1:10808`
(`services/ffmpeg.py:263-265`), i.e. v2rayN → AEZA.

### 2. bgutil's JS server uses the proxy **only** from the request body, never from env
Pinned upstream `server/src/main.ts` (`/get_pot`):
```ts
const proxy: string = body.proxy;          // comes from the POST JSON body
...
const sessionData = await sessionManager.generatePoToken(contentBinding, proxy, ...);
```
`server/src/session_manager.ts` wraps that in a `ProxyAgent` (`proxy-agent`)
and explicitly comments `// This needs to be reworked as POTs are IP-bound`.
The minter cache is keyed by `[proxy, sourceAddress]` (i.e. by egress IP).

Consequence: **`HTTP_PROXY`/`HTTPS_PROXY` env vars are ignored by bgutil's Node
server.** It only egresses through the proxy if the plugin sends `proxy` in the
body.

### 3. The yt-dlp HTTP plugin forwards `request.request_proxy` into that body
Pinned `plugin/yt_dlp_plugins/extractor/getpot_bgutil_http.py` (`_real_request_pot`):
```python
Request(f'{self._base_url}/get_pot', data=json.dumps({
    ...
    'proxy': request.request_proxy,
    'source_address': request.request_source_address,
}).encode(), ..., proxies={'all': None})
```
So the token is minted from the same IP as the download **iff**
`request.request_proxy` is populated. yt-dlp populates it from its *explicitly
configured* proxy (`--proxy`), **not** from ambient `HTTP_PROXY` env vars
(those are applied only at the lower request-handler layer). The DeepWiki POT
doc states this field exists specifically "to ensure the token is generated from
the same network environment that will perform the final download".

### 4. The bot passes `--proxy` only conditionally
`services/ffmpeg.py:263-265`:
```python
_proxy = _proxy_for_ytdlp()      # reads YTDLP_PROXY_URL / TELEGRAM_PROXY_URL / LOCAL_BOT_API_PROXY_URL
if _proxy:
    args += ["--proxy", _proxy]
```
`core/globals.py` *additionally* sets `HTTP_PROXY`/`HTTPS_PROXY` process-wide
from the Gemini/Telegram proxy knobs. So:
- If `YTDLP_PROXY_URL` (etc.) is set → `--proxy` is passed → `request_proxy` set
  → bgutil egresses via AEZA → token bound to AEZA. → **H2 (datacenter rep).**
- If those knobs are empty but the system/Gemini proxy env is set → no `--proxy`
  flag, yet yt-dlp still proxies the *download* via env `HTTP_PROXY` (AEZA),
  while `request_proxy=None` → bgutil mints the token from the machine's
  **direct** IP → token-IP ≠ download-IP → **H1 (IP mismatch).**

Both produce `HTTP 403`. H1 is code-fixable; H2 is not (it is IP reputation).

### 5. The diagnostic is currently blind to which case you are in
`services/bgutil_http_runtime.py:81-83,173-175`:
```python
stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
```
bgutil logs `Started POT server (v1.3.1) ...` and `Using proxy: <url>` to stdout.
Both are discarded, so you can never see whether bgutil actually used the proxy.
That is exactly the fact the prior hand-wringing was missing.

### 6. Downloads are unauthenticated
`cookies.txt` does not exist in the repo and the verbose log shows no
`--cookies`/`--cookies-from-browser`. Unauthenticated GVS from a datacenter IP
is the highest-risk combination for a media 403.

---

## Why every prior fix didn't resolve this

PRs #167 → #168 → #169 → #170 all operated on the **provider/token layer**
(spawn Node, warmup `generate_once.js`, own a persistent HTTP server, pin
source identity, strict `/ping` version). That layer is now correct: the token
is generated and retrieved (verbose: `Retrieved a gvs PO Token for mweb client`
+ `pot=` present in the URL). The 403 is returned by **googlevideo.com** at the
*download* step — governed by egress-IP reputation and auth state, which no
provider change touches. Chasing another bgutil tweak is the wrong layer.

bgutil's own docs say it plainly: *"Providing a POT token does not guarantee
bypassing 403 errors or bot checks"* and the fix for "POT tokens not working" is
*“Check your IP — Your IP might be flagged. Try a different network or proxy.”*

---

## Disambiguation (run on the Windows machine — proves H1 vs H2)

The one fact that resolves everything is **whether bgutil logged
`Using proxy: http://127.0.0.1:10808`**. Today that log is hidden. Two
no-code checks first:

**A) Confirm the exit IP class** (from PowerShell):
```powershell
(Invoke-WebRequest https://ipinfo.io/json -Proxy http://127.0.0.1:10808).Content
```
Expect AEZA / hosting. If the bot's residential IP is different and clean,
direct egress is viable.

**B) Test authenticated egress through the SAME AEZA proxy** (logged-in
cookies). This is the single most informative test:
```powershell
# 1) export cookies.txt from a browser logged into YouTube (Get cookies.txt LOCALLY extension)
# 2) drop it at C:\Users\Fedor\Projects\mp3telegrambot\cookies.txt
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=-vq7fH7ANUs"
```
- If **PASS** → root cause confirmed as **unauthenticated + datacenter IP**;
  keep cookies; you may keep AEZA for speed. Done.
- If still **FAIL_HTTP_403** → cookies don't save this exit; you must change
  egress IP (see Fix #2).

**C) Test direct residential egress (no proxy):**
```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=-vq7fH7ANUs"
```
- If **PASS** → AEZA was the blocker; route YouTube direct (or residential),
  keep the proxy only for Gemini/Telegram.
- If **slow but eventually PASS** → same conclusion; accept the throttle for
  audio extraction, or use a residential proxy.

---

## Remediation, ranked

### Fix #1 — Authenticated `cookies.txt` (highest leverage, zero code)
Logged-in GVS requests tolerate datacenter egress far better. The bot already
wires it: `services/ffmpeg.py` adds `--cookies cookies.txt` whenever the file
exists. Export a fresh `cookies.txt` from a YouTube-logged-in browser and place
it at the project root, then rerun the probe. (Per `.env.example` you can also
set `YTDLP_COOKIES_FROM_BROWSER=firefox[:profile]`.)

### Fix #2 — Stop egressing YouTube media through AEZA
Either go **direct residential** (likely slow in RU but PO-token-clean), or use a
**residential / clean mobile proxy** for yt-dlp only (`YTDLP_PROXY_URL`).
Caveat: `core/globals.py` sets `HTTP_PROXY`/`HTTPS_PROXY` process-wide from the
Gemini/Telegram knobs, so yt-dlp inherits them even if `YTDLP_PROXY_URL` is
empty — see proposed change **C2** below to make YouTube egress truly
isolatable.

### Fix #3 — (code hygiene) remove the H1 variable + make it observable
Proposed code changes, **offered, not yet applied** (this pass is audit-only):
- **C1 — expose bgutil logs.** Tee bgutil stdout/stderr to
  `.runtime/bgutil_http.log` instead of `DEVNULL`, so `Using proxy:` and
  startup errors are visible. Directly closes the diagnostic gap.
- **C2 — deterministic, isolatable yt-dlp proxy.** Always pass an explicit
  `--proxy` from `YTDLP_PROXY_URL` (so `request_proxy` is forwarded to bgutil
  and token-IP == download-IP, eliminating H1), and add a clean way to force
  YouTube **direct** even when Gemini/Telegram proxy env is set (strip
  `HTTP_PROXY`/`HTTPS_PROXY` from the yt-dlp child env under an opt-in flag).
- **C3 — smarter probe.** Extend `tools/check_youtube_gvs.py` to print the
  resolved proxy + cookies state and add `--no-proxy` / cookies modes so a
  single tool classifies H1/H2/cookies cleanly.
- **C4 — no stale cross-IP token.** Call bgutil `/invalidate_caches` (or
  `/invalidate_it`) when the resolved proxy changes at startup, so a token
  minted under a previous egress IP can't be reused after a proxy switch.

---

## Recommended order of operations

1. Run disambiguation **A** (confirm IP class) and **B** (`cookies.txt` via
   AEZA) on the Windows box. Send the output back.
2. If **B** → `PASS`: the problem is solved operationally; we then land C1–C4
   as hardening and stop.
3. If **B** still fails: run **C** (direct residential). Based on the result,
   pick Fix #2 (residential proxy or direct) and land C2/C3 so the bot can
   route YouTube independently of Gemini/Telegram.

Only after a real `GVS_ACCEPTANCE=PASS` on the Windows machine is the YouTube
part closed — never "fixed" from CI alone.

---

## Update 2026-08-21 — empirical A/B results + decisive next run

**A (egress class):** `147.45.68.219` IPv4 + `2a01:e5c0:2e0a::2` IPv6 are both
`AS210644 AEZA GROUP LLC, Paris`. The v2rayN inbound is a Shadowsocks mixed
relay (`proxy-relay-ss`); the googlevideo connections are accepted and proxied,
but YouTube answers the media URL with 403. So the download **is** egressing
through AEZA and YouTube **is** refusing it.

**B (cookies.txt via AEZA):** still `FAIL_HTTP_403`. *Caveat:* the probe ran
with `--quiet`, so cookie application was not confirmed — this run only proves
"with whatever was attached, AEZA still 403s." C3 below removes that ambiguity.

### Code changes applied locally (NOT pushed) to make the next run decisive
- **C1** `services/bgutil_http_runtime.py`: bgutil stdout+stderr are now tee'd
  to `.runtime/bgutil_http.log` (was `DEVNULL`). Override via
  `BGUTIL_HTTP_LOG` (`none` to silence). This exposes bgutil's
  `Using proxy: <url>` line — the only on-host proof of which IP minted the
  token.
- **C2** `services/ffmpeg.py:_proxy_for_ytdlp()`: when the explicit
  `YTDLP_PROXY_URL`/`TELEGRAM_PROXY_URL` knobs are empty, fall back to ambient
  `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`. This makes `--proxy` explicit whenever
  a proxy is in use, so yt-dlp forwards `request_proxy` to bgutil and the token
  is minted from the **same** IP as the download (closes the H1 mismatch fork).
- **C3** `tools/check_youtube_gvs.py`: prints `GVS egress` (resolved `--proxy`),
  `GVS auth` (`cookies.txt` present/MISSING) and `GVS bgutil log` path.

Tests: `tests/test_bgutil_http_runtime.py`, `test_youtube_gvs_probe.py`,
`test_youtube_po_token_runtime.py` → 37 passed.

### The decisive next run (on the Windows box)
```powershell
git stash           # only if you have local edits; otherwise skip
# (pull the C1–C3 branch / apply the local patch from this session first)
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=-vq7fH7ANUs"
# then read what bgutil actually did:
Get-Content .runtime\bgutil_http.log | Select-String "Using proxy","Started POT server","error"
```
Read it as follows:
- `GVS egress: yt-dlp --proxy = http://127.0.0.1:10808` and bgutil log shows
  `Using proxy: http://127.0.0.1:10808` → token and download share AEZA. A
  remaining 403 is then **H2 (AEZA flagged)** and **no code fix will help** →
  change YouTube egress (residential proxy or authenticated residential).
- bgutil log has **no** `Using proxy:` → the token was minted from the direct
  IP → it **was** H1; with C2 it should now show `Using proxy:`; if it still
  doesn't, `request_proxy` isn't being forwarded by this yt-dlp build and we
  patch the routing explicitly.
- `GVS auth: cookies.txt = MISSING` → B never actually tested cookies; place a
  real one and rerun before concluding cookies don't help.

### Direct-residential control (test C), isolated to this shell only
```powershell
$env:HTTPS_PROXY=""; $env:HTTP_PROXY=""; $env:https_proxy=""; $env:http_proxy=""; $env:ALL_PROXY=""
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=-vq7fH7ANUs"
```
Expect `GVS egress: (none — direct egress)`. PASS ⇒ AEZA was the blocker;
slow-but-PASS ⇒ same; still 403 ⇒ not IP-only (then cookies/client).

---

## Update 2026-08-21 (b) — ROOT CAUSE CONFIRMED in yt-dlp source; it is NOT a code bug

Reading `yt_dlp/extractor/youtube/_video.py` (stable@2026.07.04), the PO-token
request is built in `_fetch_po_token`:

```python
proxies = self._downloader.proxies.copy()        # line 2850
...
request_proxy = (                                 # line 2873
    select_proxy('https://www.youtube.com', proxies)
    or select_proxy(f'https://{innertube_host}', proxies)
)
```

`self._downloader.proxies` is exactly the map that prints
`[debug] Proxy map: {'http': 'http://127.0.0.1:10808', ...}` — and it
**includes the environment proxy even with no `--proxy` flag**. Therefore
bgutil has been receiving `http://127.0.0.1:10808` on every run and minting the
token from AEZA — the same IP yt-dlp downloads from.

**H1 (IP mismatch) is definitively ruled out by yt-dlp's own source.** The token
is generated, video-bound, and IP-consistent with the download. YouTube GVS
still returns 403. Combined with the proxy log showing the **browser streaming
YouTube through this same AEZA IP**, the only material difference between
"works" and "403" is **authentication**:

- Browser = logged-in session → streams fine from AEZA.
- yt-dlp = **unauthenticated** → YouTube GVS refuses the media download from a
  datacenter IP.

So the bot's code is **correct**; the GVS 403 is an authentication/egress-policy
outcome, not a provider bug. **No further bgutil/Node/timeout/client change will
fix it.** The fix is:

1. **Authenticated `cookies.txt`** (logged-in account; Premium bypasses GVS
   PO-token entirely per yt-dlp's own policy `not_required_for_premium`). The
   bot already wires `--cookies cookies.txt`; C3 now confirms it is actually
   attached. A prior "cookies" run failed but never confirmed attachment.
2. **Residential/clean egress** for YouTube only (`YTDLP_PROXY_URL`), isolated
   from the Gemini/Telegram proxy — the `--direct` probe mode (below) makes the
   residential test valid for the first time (it overrides `.env` proxy, which
   the earlier manual `$env:` test silently did not).

### Code applied locally this session (NOT yet pushed) — observability + a valid direct test
- **C1** bgutil stdout/stderr → `.runtime/bgutil_http.log` (was `DEVNULL`).
- **C2** `_proxy_for_ytdlp()` falls back to ambient `HTTP(S)_PROXY`/`ALL_PROXY`
  so `--proxy` is explicit whenever a proxy is in use (hygiene/determinism).
- **C3** probe prints `GVS egress` (`--proxy`), `GVS auth` (`cookies.txt`),
  `GVS bgutil log` path.
- **C4 (new)** probe `--direct` flag clears all proxy env in-process before
  `load_dotenv`/`_build_ytdlp_base_args`, so a residential run is real.
- Tests: 37 passed (`test_bgutil_http_runtime`, `test_youtube_gvs_probe`,
  `test_youtube_po_token_runtime`).

### Decisive commands (run AFTER pulling this branch)
```powershell
# 1) confirm what's actually attached + what IP bgutil used (cookies + AEZA)
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=-vq7fH7ANUs"
Get-Content .runtime\bgutil_http.log | Select-String "Using proxy"

# 2) residential, no proxy (overrides .env — finally a valid test)
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py --direct "https://www.youtube.com/watch?v=-vq7fH7ANUs"
```
Interpretation:
- (1) shows `cookies.txt = present` AND `Using proxy: http://127.0.0.1:10808`
  yet still 403 ⇒ logged-out / non-Premium account from datacenter = refused ⇒
  use a logged-in (ideally Premium) cookies.txt, or change egress.
- (2) PASS ⇒ residential works; wire `YTDLP_PROXY_URL` to a residential exit (or
  accept direct). slow-but-PASS ⇒ same. still 403 ⇒ deeper client issue to chase.
