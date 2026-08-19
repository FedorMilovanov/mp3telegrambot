# YouTube GVS production-network acceptance

`tools/check_youtube_gvs.py` is a manual end-to-end check for the exact failure
class that originally broke Shorts Factory MAX: yt-dlp successfully extracts a
maximum-quality YouTube source, but the final `googlevideo.com` media transfer
fails with HTTP 403.

The probe is intentionally **not** part of bot startup. Repeating a real media
request on every launch would add latency and unnecessary YouTube anti-bot
pressure. Run it after a YouTube/provider/runtime change, or when Factory media
acquisition starts failing.

## Run on the production Windows machine

Pull current `main`, provision through the normal launcher once if dependencies
or the provider pin changed, keep the normal TUN/proxy route enabled, then run:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py
```

The default URL is the public video used to reproduce the original GVS 403. A
different public YouTube URL can be supplied explicitly:

```powershell
.\.venv\Scripts\python.exe tools\check_youtube_gvs.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

The command uses the same `services.ffmpeg._build_ytdlp_base_args()` policy as
the bot, including repo `yt-dlp.conf`, source-only PO-provider routing,
cookies/proxy selection, JS runtimes and format sorting. It then adds only the
Factory-relevant acceptance constraints:

- `bestaudio/best`;
- `--abort-on-unavailable-fragments`;
- `--no-playlist`;
- one yt-dlp `before_dl` print containing only source metadata duration;
- a unique temporary output path.

It downloads the **complete** selected audio source. It deliberately does not
use yt-dlp `--test`: upstream reports include failures where an initial media
range succeeds and a later range returns HTTP 403, so a short partial-read test
can produce a false green result.

The yt-dlp process uses the repository's cancellation-safe process-tree owner.
If the 30-minute acceptance timeout fires on Windows, yt-dlp and descendant
Node provider processes are stopped before the temporary directory is removed.

After yt-dlp returns success, the probe requires a non-empty final media file,
reads its duration with `ffprobe`, and compares that duration with the metadata
printed before download using the same complete-source tolerance as Factory:
`max(2s, min(15s, expected_duration * 0.2%))`. The temporary download is
deleted by default. Use `--keep` only when the file itself needs manual
inspection.

## Result codes

The last status line is machine-readable:

- `GVS_ACCEPTANCE=PASS` — complete best-audio media transfer succeeded, the
  final file is readable by ffprobe, and its duration matches source metadata;
- `GVS_ACCEPTANCE=FAIL_HTTP_403` — the production path still hits GVS 403;
- `GVS_ACCEPTANCE=FAIL_LOGIN_REQUIRED` — YouTube blocked the session/IP earlier
  at player access;
- `GVS_ACCEPTANCE=FAIL_NO_PO_PROVIDER` — yt-dlp did not expose an automatic PO
  provider;
- `GVS_ACCEPTANCE=FAIL_RUNTIME` — source-only provider/startup contract failed
  before the download;
- `GVS_ACCEPTANCE=FAIL_TIMEOUT` — the complete yt-dlp transfer did not finish
  inside the probe's fail-closed timeout;
- `GVS_ACCEPTANCE=FAIL_PROCESS_OWNERSHIP` — the process-tree owner itself could
  not complete safely;
- `GVS_ACCEPTANCE=FAIL_METADATA_DURATION` — yt-dlp did not provide a positive
  source duration, so completeness could not be proven;
- `GVS_ACCEPTANCE=FAIL_DURATION_MISMATCH` — final ffprobe duration disagrees
  with source metadata outside the Factory tolerance;
- `GVS_ACCEPTANCE=FAIL_NO_MEDIA` / `FAIL_FFPROBE` — yt-dlp returned success but
  the expected complete readable media postcondition was not met;
- `GVS_ACCEPTANCE=FAIL_YTDLP` — another yt-dlp failure occurred; the command
  prints the diagnostic tail.

A PASS is the missing production-network proof that hosted CI cannot supply.
GitHub-hosted Azure IPs are currently stopped by YouTube earlier with a
`LOGIN_REQUIRED` anti-bot response, so CI can prove provider identity, build,
plugin discovery and token minting, but not the final GVS read on the user's
TUN/IP route.

## Quality policy

Do not turn a failed probe green by forcing format 18/360p, removing
`--abort-on-unavailable-fragments`, accepting a partial file, disabling the
source-only plugin restriction, embedding manual PO tokens, or bypassing the
normal proxy/cookie ownership in `services.ffmpeg`.
