# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The strongest explicitly configured Gemini model is used in this order: `SHORTS_FACTORY_MODEL`, `GEMINI_MAX_MODEL`, then the normal `GEMINI_MODEL`. A model name containing `lite` is rejected.
2. Audio is analyzed with `thinking_level=high` and audio timestamps enabled through the shared `make_audio_config` path.
3. Candidate selection is strict three-pass: whole-source scout, independent semantic judge, then a separate boundary auditor that rechecks context before and after every cut.
4. Only candidates with `boundary_verified=true` survive deterministic validation. Invalid durations, out-of-source ranges and heavy overlap are rejected.
5. The result contains up to five Shorts (35–180 seconds) and up to three complete long highlights (5–15 minutes).
6. Shorts force audio normalization and burned subtitles. Long highlights intentionally remain without subtitles.
7. For a non-Russian source, the only enabled translation provider is Yandex LiveDub «Живые голоса». The translated full video is prepared in parallel and becomes the source for both short and long cuts.
8. If Yandex LiveDub is unavailable, the foreign-language extraction job fails clearly. It does not silently cut the untranslated original and does not start an in-house neural voice translation.
9. `SHORTS_FACTORY_TRANSLATION_BACKEND` is the reserved provider seam for future expansion. At present, every value except `yandex_live` is rejected as not implemented.
10. The existing mature Shorts and Clips render/delivery pipelines are reused through task-local `ContextVar` overrides. No process-global candidate list is shared between users.

## Optional environment controls

```dotenv
# Strongest full Gemini model available to this account. Default: GEMINI_MODEL.
# SHORTS_FACTORY_MODEL=gemini-3.6-flash

# Translation provider. Only yandex_live is implemented today.
SHORTS_FACTORY_TRANSLATION_BACKEND=yandex_live

# Required for non-Russian sources. Disabling it makes those jobs fail clearly.
SHORTS_FACTORY_LIVEDUB=1

# Maximum source duration accepted by this mode. Default: 3 hours.
SHORTS_FACTORY_MAX_SOURCE_SEC=10800

# Maximum wait for the parallel Yandex translation.
SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC=1800

# Extra protection around Yandex's delayed Russian speech for long clips.
SHORTS_FACTORY_LIVEDUB_PREROLL_SEC=1.0
SHORTS_FACTORY_LIVEDUB_POSTROLL_SEC=2.5
```

## Operator decisions — 2026-08-04

This mode is quality-first. It must not be silently downgraded to Flash-Lite, minimal thinking, a one-pass candidate search, unverified boundaries or approximate timestamp generation to save quota.

Gemini is used here as the editor, semantic selector and timestamp auditor. It does not create the Russian voice translation. For foreign-language source audio, the production voice path is Yandex LiveDub «Живые голоса» only. A future neural translation backend may be added behind the reserved provider interface, but it is intentionally disabled today.
