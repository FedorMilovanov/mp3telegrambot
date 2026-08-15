# 2026-08-15 — Explicit LiveDub delivery / no Telegram monkey-patch

## Problem

Production LiveDub delivery had accumulated a stack of runtime adapters that
replaced `Bot.send_video`, `Bot.send_audio`, `Bot.send_message`,
`Message.reply_audio`, private companion functions and used a `sys.meta_path`
import hook. The behavior was heavily tested, but ownership depended on import
order and wrapper order.

## Refactor

- `services` package import is side-effect free; `runtime_manifest` is the only
  startup lifecycle owner.
- Gemini/network/quality policy is an explicit PRE_MAIN feature.
- LiveDub video/MP3 publication is called explicitly from `main_pipeline`.
- New and cached companion MP3 sets are transactions in
  `livedub_delivery_coordinator`: strict clean/mixed role validation, rollback,
  verified file IDs and request-local single-flight.
- ENG Full source MP3 fallback is a request-scoped `SourceAudioDeferral`, not a
  global `Message.reply_audio` interceptor.
- Companion cache corruption recovery is a directly called persistence backend.
- Clean RU selection, Windows UTF-8 probing, QA major-interval coverage and
  yt-dlp runtime argument shape are source-owned.
- Shared Gemini config enforces 3.6/HIGH semantic and 3.5 utility thinking at the
  config owner; no post-import `sys.modules` reference rewrite is required.

## Quality invariants

No semantic downgrade was introduced: user-visible Gemini remains exact
`gemini-3.6-flash`/HIGH with no 3.5 semantic fallback; Whisper stays `large-v3`;
Factory score/boundary/render contracts are untouched. A new LiveDub video stays
visible if companion delivery fails, but it is not cached as a complete pair. A
stale cached video is rolled back when its companion set cannot be proven.

Exact-head full repository CI, Windows full-suite and `tools/verify_repo.py` are
required before merge.
