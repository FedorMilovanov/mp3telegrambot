# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The default reasoning model is `gemini-3.1-pro-preview`. Factory accepts only canonical `gemini-<version>-pro...` identifiers at or above Gemini 3.1 Pro. Flash/Lite, older Pro models and aliases that merely contain the word `pro` fail the required runtime startup.
2. Before media transfer, Factory preflight requires configured Gemini clients, `faster-whisper`, `ffmpeg`, `ffprobe` and a basic free-space floor in both downloads and the system temp location.
3. Factory then simulates the exact `yt-dlp` format selection. It sums selected stream `filesize`/`filesize_approx` values or derives them from bitrate and duration, models native-audio plus lossless-FLAC preparation and separate video/audio streams plus merge output, and requires that peak on every target filesystem. If the estimate is unavailable, conservative 4 GB audio and 6 GB video floors are used.
4. Factory resets inherited/local `yt-dlp` format sorting, `format-sort-force` and free-format preference. Authentication, cookies, proxy and JS-runtime settings are retained, but ordinary mp4/m4a compatibility preferences cannot silently lower MAX source quality.
5. Russian render sources use `bestvideo+bestaudio/best` with no 720p, 1080p or container ceiling. The selected streams are merged into a probed source before any cuts are rendered.
6. Factory audio analysis downloads `bestaudio/best` without `--extract-audio` or an intermediate MP3 generation. AAC, MP3, Vorbis and FLAC are remuxed with codec copy into a Gemini-supported container; unsupported codecs such as Opus are decoded once to lossless FLAC. Every prepared file is probed, checked for truncation and submitted with its real MIME type.
7. Audio is analyzed with `thinking_level=high`, audio timestamps and a strict structured-output schema.
8. Candidate selection is strict three-pass: whole-source scout, independent semantic judge, then a separate boundary auditor that rechecks context before and after every cut.
9. The third audio pass must report one dominant actually heard spoken language in `metadata.language` as ISO 639-1. Empty, unknown or mixed language fails closed. A title, uploader name or Cyrillic script in metadata is never used as the deciding translation signal.
10. Fractional Gemini boundaries survive deterministic validation with millisecond precision. They are not rounded to whole seconds before rendering.
11. Only candidates with `boundary_verified=true` survive deterministic validation. Invalid durations, out-of-source ranges and heavy overlap are rejected.
12. A final no-compromise editorial gate requires at least `quality_score >= 88` for Shorts and `>= 85` for long clips. Shorts also require a non-empty title, hook and reason; long clips require a title and reason. Environment values may raise these floors but cannot lower them.
13. The result contains up to five Shorts. Their semantic interval is 35–177 seconds, reserving room for context and the Yandex voice tail inside the final public 180-second limit. Up to three long semantic intervals are 5–14:57, reserving the same safety inside the final 15-minute limit.
14. After the third Gemini boundary audit, Factory disables the mature renderers' second silence-snap. The renderer uses the audited fractional end literally; ordinary non-Factory modes retain their existing silence adjustment.
15. Factory forces Shorts speed to exactly `1.0`, preserving Gemini-verified boundaries. Global speed settings cannot stretch or compress a Factory cut.
16. Short subtitles are mandatory. Factory refuses to start without `faster-whisper` and requires exactly `large-v3`, karaoke, word timestamps and Gemini vocabulary hints. Smaller/compressed models, including `large-v3-turbo`, are rejected instead of silently reducing accuracy.
17. Immediately before Telegram, the actual burned `_sub.mp4` is probed again. It must contain video+audio and be no longer than 180 seconds, with only a 50 ms container-timescale tolerance. Raw/postprocessed fallback files are rejected.
18. Each long clip is also re-probed at its final delivery boundary. It must contain video+audio and be no longer than 900 seconds. Telegram duration metadata is replaced with the final probed duration rather than the AI plan.
19. A Short or long clip is counted only after Telegram accepts that proved final artifact. Partial delivery reports actual accepted counts and zero accepted files fail honestly.
20. Shorts force audio normalization, a title poster and a snapshot fallback. Long highlights intentionally remain without subtitles.
21. Source selection happens after the three audio passes. Russian speech uses the unrestricted original; every proven non-Russian language requires Yandex LiveDub «Живые голоса».
22. For a foreign source, Factory obtains only the live Russian Yandex audio and independently downloads the unrestricted maximum-quality original. It then builds the existing local Pro mix over that original. The legacy 1080p ENG downloader and opaque `get_live_dub_video` merge path are not used by Factory.
23. The Factory LiveDub result must pass video+audio probing and physically include the configured delayed Russian tail. A mix that falls back without the full `LIVEDUB_DELAY_MS + LIVEDUB_TAIL_MARGIN_MS` duration is rejected.
24. A foreign-language run performs a LiveDub preflight before starting translation. At least one client route must exist (`node` + `vot_helper`, or `vot-cli-live`/`npx`). OAuth via `VOT_API_TOKEN` or `YANDEX_OAUTH_TOKEN` is required by default.
25. If Yandex LiveDub is unavailable, the foreign-language extraction job fails clearly. It does not silently cut the untranslated original and does not start an in-house neural voice translation.
26. `SHORTS_FACTORY_TRANSLATION_BACKEND` is the reserved provider seam for future expansion. At present, every value except `yandex_live` is rejected as not implemented.
27. Yandex timing is handled as a render envelope, not a blind timestamp shift. Factory keeps the semantic start with at least the default context pre-roll where source bounds permit and appends the full configured Russian-audio tail. The Factory pre-roll and extra-tail defaults may be increased but cannot be lowered through `.env`.
28. Candidates that cannot fit the complete Yandex envelope inside the public 3/15-minute limit are rejected rather than cutting the first or last words.
29. Factory's basic free-space preflight cannot be configured below 2 GB, but that number is not treated as sufficient for a maximum source. The selected-format disk guard may require substantially more based on resolution, bitrate, duration, FLAC preparation and merge peak. The source/LiveDub wait cannot be configured below 1800 seconds.
30. Generic Short trim buttons are deliberately removed from Factory messages. The generic callback re-renders without the mandatory `large-v3` subtitle pipeline and could exceed three minutes; exposing it would violate the mode contract. The managed source may remain in the existing timed Factory cache and is removed by its TTL; long-only jobs remove it immediately.
31. The existing mature Shorts and Clips render/delivery pipelines are reused through task-local `ContextVar` overrides. No process-global candidate list, user mode or quality setting is shared between users.
32. Legacy ENG-mode Shorts, Clips, Montage and Highlights are also fail-closed: if the translated LiveDub file is missing, they are skipped instead of downloading and publishing the original-language video. Legacy Russian cuts keep their original source behavior.
33. A valid cached analysis becomes a no-publication cut replay: cached `ai_data` is reused without duplicate Gemini analysis, Telegraph pages, archive writes or main-MP3 delivery. In ENG mode a cached Telegram `file_id` cannot stand in for the local translated MP4 required by rendering, and only Telegram-accepted cut videos count as replay success.
34. The mode-context wrapper preserves the already-installed processing chain separately for ordinary links and playlist entries, so later runtime adapters are not bypassed.
35. Selectable `/segments` and `/cut` output requires final video+audio evidence, uses the actual probed delivery duration and can fall back from a bad subtitle artifact to a valid base render with a truthful warning.

## Optional environment controls

```dotenv
# Canonical Pro model at or above 3.1. Default: gemini-3.1-pro-preview.
# Flash/Lite, older Pro and noncanonical aliases fail required startup.
SHORTS_FACTORY_MODEL=gemini-3.1-pro-preview

# Final editorial floors. Higher values are accepted; lower values are ignored.
SHORTS_FACTORY_MIN_SHORT_SCORE=88
SHORTS_FACTORY_MIN_LONG_SCORE=85

# Alternative shared Pro-model setting, subject to the same canonical/version floor.
# GEMINI_PRO_MODEL=gemini-3.1-pro-preview

# Exact mandatory Whisper model. Other values fail required startup.
SHORTS_FACTORY_WHISPER_MODEL=large-v3

# Basic preflight floor only. Higher values are accepted; values below 2.0 are
# floored to 2.0 GB. The selected-format disk guard can require much more and
# cannot be bypassed by lowering this variable.
SHORTS_FACTORY_MIN_FREE_GB=2.0

# Translation provider. Only yandex_live is implemented today.
SHORTS_FACTORY_TRANSLATION_BACKEND=yandex_live

# Required for non-Russian sources. Disabling it makes those jobs fail clearly.
SHORTS_FACTORY_LIVEDUB=1

# OAuth is required by default. Set 0 only for an intentional cache-only Yandex attempt.
SHORTS_FACTORY_REQUIRE_VOT_TOKEN=1

# Maximum source duration accepted by this mode. Lower values are stricter.
SHORTS_FACTORY_MAX_SOURCE_SEC=10800

# Minimum wait for Yandex translation or the shared source download.
# Larger values are accepted; values below 1800 are floored to 1800 seconds.
SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC=1800

# Factory timing floors. Larger values are accepted; lower values are ignored.
SHORTS_FACTORY_LIVEDUB_PREROLL_SEC=0.25
SHORTS_FACTORY_LIVEDUB_TAIL_EXTRA_SEC=0.15

# Equivalent protection for legacy ENG cuts.
LIVEDUB_DOWNSTREAM_PREROLL_SEC=0.25
LIVEDUB_DOWNSTREAM_TAIL_EXTRA_SEC=0.15
LIVEDUB_DOWNSTREAM_REQUIRE_PROBE=1

# Managed Factory source TTL. Factory messages do not expose generic trim buttons;
# this cache is a bounded cleanup safety net, not a public editing feature.
SHORTS_FACTORY_SOURCE_RETENTION_HOURS=24
```

## Windows bootstrap

`Start Bot.bat` validates that `.venv` uses Python 3.11–3.13. It prefers `requirements-lock.txt`, stores its SHA-256 in `.venv/.setup-complete`, reinstalls dependencies after any lock change and runs `tools/check_requirements_lock.py` before launching the bot. A stale marker can therefore no longer hide changed dependencies after `git pull`.

## Operator decisions — 2026-08-04

This mode is quality-first. It must not be silently downgraded to a compatibility-sorted or resolution-capped source, an extra MP3 generation before Gemini, Flash/Lite or an old/noncanonical Pro model, minimal thinking, a one-pass candidate search, unverified or whole-second boundaries, a second renderer silence-snap, an unproven spoken language, low editorial scores, accelerated playback, compressed Whisper, subtitle-less delivery, an over-180/900-second final artifact, unsafe generic retrim controls, untranslated ENG cuts or approximate timestamp generation to save quota, disk or time.

Gemini is used here as the editor, semantic selector, spoken-language detector and timestamp auditor. It does not create the Russian voice translation. For foreign-language source audio, the production voice path is Yandex LiveDub «Живые голоса» only. A future neural translation backend may be added behind the reserved provider interface, but it is intentionally disabled today.
