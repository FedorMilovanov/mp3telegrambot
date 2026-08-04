# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The default reasoning model is `gemini-3.1-pro-preview`. `SHORTS_FACTORY_MODEL` and `GEMINI_PRO_MODEL` may override it only with another Pro model. A generic Flash value in `GEMINI_MAX_MODEL` is ignored instead of silently downgrading Factory.
2. Audio is analyzed with `thinking_level=high`, audio timestamps and a strict structured-output schema.
3. Candidate selection is strict three-pass: whole-source scout, independent semantic judge, then a separate boundary auditor that rechecks context before and after every cut.
4. Only candidates with `boundary_verified=true` survive deterministic validation. Invalid durations, out-of-source ranges and heavy overlap are rejected.
5. A final no-compromise editorial gate then requires, by default, `quality_score >= 88` for Shorts and `>= 85` for long clips. Shorts also require a non-empty title, hook and reason; long clips require a title and reason. A run with no surviving strong candidate fails honestly instead of rendering filler.
6. The result contains up to five Shorts. Their semantic interval is 35–177 seconds, reserving room for context and the Yandex voice tail inside the final public 180-second limit. Up to three long semantic intervals are 5–14:57, reserving the same safety inside the final 15-minute limit.
7. Factory forces Shorts speed to exactly `1.0`, preserving Gemini-verified boundaries. Global speed settings cannot stretch or compress a Factory cut.
8. Short subtitles are mandatory. Factory refuses to start without `faster-whisper` and forces a full-quality profile: `large-v3`, karaoke, word timestamps and Gemini vocabulary hints. Light subtitle mode is ignored inside the task-local Factory context.
9. A Short is counted as delivered only after Telegram accepts the burned `_sub.mp4` artifact. Raw/postprocessed fallback files are rejected; zero subtitled deliveries fail the Factory job instead of producing a false success.
10. Shorts force audio normalization, a title poster and a snapshot fallback. Long highlights intentionally remain without subtitles.
11. For a non-Russian source, the only enabled translation provider is Yandex LiveDub «Живые голоса». The translated full video is prepared in parallel and becomes the source for both short and long cuts.
12. If Yandex LiveDub is unavailable, the foreign-language extraction job fails clearly. It does not silently cut the untranslated original and does not start an in-house neural voice translation.
13. `SHORTS_FACTORY_TRANSLATION_BACKEND` is the reserved provider seam for future expansion. At present, every value except `yandex_live` is rejected as not implemented.
14. Yandex timing is handled as a render envelope, not a blind timestamp shift. Factory keeps the semantic start with a small context pre-roll and appends the full configured `LIVEDUB_DELAY_MS + LIVEDUB_TAIL_MARGIN_MS` Russian-audio tail. Candidates that cannot fit that envelope inside the public 3/15-minute limit are rejected rather than cutting the first or last words.
15. The shared source passes a real video+audio media probe. Its exact probed duration, including the LiveDub tail, is used by the renderer so the final Russian phrase is not clipped by the original source duration.
16. When Shorts are produced, the shared source is retained for interactive trim buttons and removed by a timed Factory cache policy. Long-only jobs remove the source immediately.
17. The existing mature Shorts and Clips render/delivery pipelines are reused through task-local `ContextVar` overrides. No process-global candidate list or quality setting is shared between users.
18. The same exact-duration and complete-Russian-tail policy now also protects legacy ENG-mode Shorts, Clips, Montage and Highlights. Legacy Russian cuts keep their original timing behavior.
19. Selectable `/segments` and `/cut` output now requires final video+audio evidence, uses the actual probed delivery duration and can fall back from a bad subtitle artifact to a valid base render with a truthful warning.

## Optional environment controls

```dotenv
# Explicit Pro model. Default: gemini-3.1-pro-preview.
# Flash/Lite values are rejected here.
SHORTS_FACTORY_MODEL=gemini-3.1-pro-preview

# Final editorial thresholds after all three Gemini passes.
SHORTS_FACTORY_MIN_SHORT_SCORE=88
SHORTS_FACTORY_MIN_LONG_SCORE=85

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

# Small context before the exact Gemini boundary.
SHORTS_FACTORY_LIVEDUB_PREROLL_SEC=0.25

# Extra safety after the normal delay + tail margin.
SHORTS_FACTORY_LIVEDUB_TAIL_EXTRA_SEC=0.15

# Equivalent protection for legacy ENG cuts.
LIVEDUB_DOWNSTREAM_PREROLL_SEC=0.25
LIVEDUB_DOWNSTREAM_TAIL_EXTRA_SEC=0.15

# Retain shared Factory sources for interactive trim buttons.
SHORTS_FACTORY_SOURCE_RETENTION_HOURS=24
```

## Operator decisions — 2026-08-04

This mode is quality-first. It must not be silently downgraded to Flash/Lite, minimal thinking, a one-pass candidate search, unverified boundaries, low editorial scores, accelerated playback, light Whisper, subtitle-less delivery or approximate timestamp generation to save quota or time.

Gemini is used here as the editor, semantic selector and timestamp auditor. It does not create the Russian voice translation. For foreign-language source audio, the production voice path is Yandex LiveDub «Живые голоса» only. A future neural translation backend may be added behind the reserved provider interface, but it is intentionally disabled today.
