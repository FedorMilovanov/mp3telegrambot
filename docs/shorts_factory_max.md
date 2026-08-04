# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The default reasoning model is `gemini-3.1-pro-preview`. `SHORTS_FACTORY_MODEL` and `GEMINI_PRO_MODEL` may override it only with another Pro model. A generic Flash value in `GEMINI_MAX_MODEL` is ignored instead of silently downgrading Factory.
2. Audio is analyzed with `thinking_level=high`, audio timestamps and a strict structured-output schema.
3. Candidate selection is strict three-pass: whole-source scout, independent semantic judge, then a separate boundary auditor that rechecks context before and after every cut.
4. Only candidates with `boundary_verified=true` survive deterministic validation. Invalid durations, out-of-source ranges and heavy overlap are rejected.
5. The result contains up to five Shorts (35–180 seconds) and up to three complete long highlights (5–15 minutes).
6. Factory forces Shorts speed to exactly `1.0`, preserving Gemini-verified boundaries. Global speed settings cannot stretch or compress a Factory cut.
7. Short subtitles are mandatory. Factory refuses to start without `faster-whisper` and forces a full-quality profile: `large-v3`, karaoke, word timestamps and Gemini vocabulary hints. Light subtitle mode is ignored inside the task-local Factory context.
8. Shorts force audio normalization, a title poster and a snapshot fallback. Long highlights intentionally remain without subtitles.
9. For a non-Russian source, the only enabled translation provider is Yandex LiveDub «Живые голоса». The translated full video is prepared in parallel and becomes the source for both short and long cuts.
10. If Yandex LiveDub is unavailable, the foreign-language extraction job fails clearly. It does not silently cut the untranslated original and does not start an in-house neural voice translation.
11. `SHORTS_FACTORY_TRANSLATION_BACKEND` is the reserved provider seam for future expansion. At present, every value except `yandex_live` is rejected as not implemented.
12. One shared source video is reused for every Short and long clip. For Yandex output, candidate timestamps are shifted by the configured fixed dub delay instead of adding uncontrolled padding that could exceed the 3/15-minute limits.
13. When Shorts are produced, the shared source is retained for interactive trim buttons and removed by a timed Factory cache policy. Long-only jobs remove the source immediately.
14. The existing mature Shorts and Clips render/delivery pipelines are reused through task-local `ContextVar` overrides. No process-global candidate list or quality setting is shared between users.

## Optional environment controls

```dotenv
# Explicit Pro model. Default: gemini-3.1-pro-preview.
# Flash/Lite values are rejected here.
SHORTS_FACTORY_MODEL=gemini-3.1-pro-preview

# Alternative shared Pro-model setting.
# GEMINI_PRO_MODEL=gemini-3.1-pro-preview

# Full Whisper model for Factory subtitles. Default: large-v3.
SHORTS_FACTORY_WHISPER_MODEL=large-v3

# Translation provider. Only yandex_live is implemented today.
SHORTS_FACTORY_TRANSLATION_BACKEND=yandex_live

# Required for non-Russian sources. Disabling it makes those jobs fail clearly.
SHORTS_FACTORY_LIVEDUB=1

# Maximum source duration accepted by this mode. Default: 3 hours.
SHORTS_FACTORY_MAX_SOURCE_SEC=10800

# Maximum wait for Yandex translation or the shared source download.
SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC=1800

# Small addition to the fixed LIVEDUB_DELAY_MS timestamp shift.
SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC=0.15

# Retain shared Factory sources for interactive trim buttons.
SHORTS_FACTORY_SOURCE_RETENTION_HOURS=24
```

## Operator decisions — 2026-08-04

This mode is quality-first. It must not be silently downgraded to Flash/Lite, minimal thinking, a one-pass candidate search, unverified boundaries, accelerated playback, light Whisper or approximate timestamp generation to save quota or time.

Gemini is used here as the editor, semantic selector and timestamp auditor. It does not create the Russian voice translation. For foreign-language source audio, the production voice path is Yandex LiveDub «Живые голоса» only. A future neural translation backend may be added behind the reserved provider interface, but it is intentionally disabled today.
