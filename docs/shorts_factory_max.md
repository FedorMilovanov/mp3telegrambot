# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The strongest explicitly configured Gemini model is used in this order: `SHORTS_FACTORY_MODEL`, `GEMINI_MAX_MODEL`, then the normal `GEMINI_MODEL`. The Lite model is never used.
2. Audio is analyzed with `thinking_level=high` and audio timestamps enabled through the shared `make_audio_config` path.
3. Candidate selection is two-pass: a whole-source scout pass, then an independent judge pass that rechecks both boundaries against the audio.
4. Deterministic validation rejects out-of-range clips, invalid durations and heavy overlap after Gemini returns its plan.
5. The result contains up to five Shorts (35–180 seconds) and up to three complete long highlights (5–15 minutes).
6. Shorts force audio normalization and burned subtitles. Long highlights intentionally remain without subtitles.
7. For likely English sources, Yandex LiveDub is prepared in parallel. The translated full video is used as the source for both short and long cuts. If LiveDub is unavailable, the original source is still cut instead of losing the whole job.
8. The existing mature Shorts and Clips render/delivery pipelines are reused through task-local `ContextVar` overrides. No process-global candidate list is shared between users.

## Optional environment controls

```dotenv
# Strongest model available to this account. Default: GEMINI_MODEL.
# SHORTS_FACTORY_MODEL=gemini-3.6-flash

# Enable Yandex LiveDub for likely non-Russian sources.
SHORTS_FACTORY_LIVEDUB=1

# Maximum source duration accepted by this mode. Default: 3 hours.
SHORTS_FACTORY_MAX_SOURCE_SEC=10800

# Maximum wait for the parallel Yandex translation.
SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC=1800
```

## Operator decision — 2026-08-04

This mode is quality-first. It must not be silently downgraded to Flash-Lite, minimal thinking, one-pass candidate selection or approximate timestamp generation to save quota. If the maximum-quality Gemini route is unavailable, the mode should fail clearly rather than pretend that a weak plan is equivalent.
