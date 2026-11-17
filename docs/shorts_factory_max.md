# SHORTS FACTORY MAX

`SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source cards, alternative-link search and publication archive.

## Production contract

1. The only accepted reasoning route is the catalog role `primary` (see the generated block below). Factory requires that exact GA Flash ID for all three review passes; any other identifier fails the required runtime startup instead of silently changing the quality route.
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
# Exact GA Flash model: the `primary` role from core/model_catalog.py.
# Any other identifier fails required startup.
SHORTS_FACTORY_MODEL=gemini-3.8-flash
<!-- MODEL-ROUTE:START -->
Model contract, catalog snapshot 2026-10-22. Generated from core/model_catalog.py by scripts/render_model_catalog.py; do not edit by hand.

- Role `primary` (semantic work): `gemini-3.8-flash`.
- Role `utility` (mechanical work only): `gemini-3.5-flash-lite`.
- Provider-side aliases: `gemini-3.5-flash` (deprecated 2026-10-08) routes to `gemini-3.6-flash`, `gemini-3.7-flash` (deprecated 2026-10-08) routes to `gemini-3.8-flash`.
- Any other identifier (Pro/Lite, previous GA generation, arbitrary string) is rejected by the runtime gate instead of silently changing the quality route.
<!-- MODEL-ROUTE:END -->

# Final editorial floors. Higher values are accepted; lower values are ignored.
SHORTS_FACTORY_MIN_SHORT_SCORE=88
SHORTS_FACTORY_MIN_LONG_SCORE=85

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

## SHORTOREZ local 3–5-minute export (separate operator-only handoff)

This capability is **not a new Factory mode**. MP3Bot's existing Gemini candidate limits (35–177 seconds for Shorts; 300–897 seconds for long highlights), 1.00x Factory speed, production bot, retries, and Telegram deliveries remain unchanged.

**VISUAL CORRECTION (14 November 2026):** the historic operator-only command **python -m services.shortorez_export** reused the Shorts renderer with full_frame_vertical, whose name was misleading: its actual content is a reduced 16:9 image with a blurred duplicate background. This was wrongly used for a solo MacArthur sermon. It is **NOT** a full-bleed 9:16 layout and must never be the implicit default for one visible speaker.

**Default for a solo sermon/one speaker: fill the full 9:16 viewport for the entire segment**, tracking the face and body across camera switches instead of center-locked cropping; do not use blurred background. The old exporter therefore **fails closed** unless explicitly invoked with **--multi-person-context-blur**, which is ONLY for a genuinely reviewed multi-person Q&A/meeting where preserving several people or graphical context justifies the wide insert. This opt-in is not allowed as an editorial workaround for the MacArthur 5nKCs4bkoxE solo source; use the shot-aware full-bleed operator workflow on the authorized desktop. No bot runtime, publishing, Gemini or source audio logic is changed. The existing provider-inert output still requires final hearing and scene-by-scene visual QA.

For actual solo sermon work, record original-frame face/focus X trajectory across shots, maintain crop width approximately source-height × 9/16 with 1:1 pixels before upscale, and change horizontal crop at each camera cut. Verify final 720×1280 encoded videos and face safety in every camera position. Preserve 1.00× for Telegram/Instagram and build the separately verified minimum-speed <=180s VK version. **An FFprobe result of 720×1280 is not proof of a full-screen crop**. If no safe shot-aware crop plan is available, do not generate the old blur and call it ready.

**Refined solo-composition rule (15 Nov 2026):** the source-side 9:16 crop must be STATIC for every original camera shot by default. Select one fixed X position per shot and switch it on the *actual original camera cut frame*. No per-frame person/face chasing, no gentle pan, no zoom animation, and no 2/8/12 Hz smooth trajectory in a solo sermon. Continuous face tracking was tested on the MacArthur pilot and rejected by the owner as distracting virtual camera movement. Static cropping can reduce face containment when the filmed speaker crosses a long shot: prefer stable editorial composition, flag the limitation, and keep moving-tracker outputs as optional comparisons only; never silently revert to BLUR. Check representative frames throughout every shot and both sides of each camera cut on the final encoded file. An operator-local static variant should be source-SHA-bound and preserve the approved speech/audio. This does not authorize publication or change any running Bot mode.

The real MacArthur pilot was retrieved from the owner's 57:19 RU video and locally rendered as three 1x and three native VK review MP4s, with an original 640×358 master; the first blur composition was corrected on the authorized desktop. These are review assets, not proof of final listening or publication.

**Required explicit CLI arguments:** --source-path (local RU postmix .mp4); --source-sha256 (sha256: plus 64 lowercase hex digits); --ru-boundaries-json (reviewed source-bound audio edge evidence); --youtube-id (exact 11-character owner YouTube ID); --start-ms; --end-ms (actual integer RU postmix cutpoints, with end-start strictly >177000 and <300000); --output-dir (separate review destination). The command does nothing without these parameters. It neither fetches nor modifies the source.

The boundary evidence is JSON with schema_version=1, project_key=lord-god-strength, source_channel_id=UCeSJsC6go2c9pdJCuUI1BYA, source_video_id=5nKCs4bkoxE (pilot), source_kind=ru_postmix, source_sha256, start_ms, end_ms, plus boundary_review: status=heard_on_ru_postmix, actual reviewer, first_heard_words and last_heard_words. This is a reviewer assertion, **not independent acoustic proof**; do not populate it from source-language subtitle times or an unlistened ASR extract. MacArthur pilot original: https://www.youtube.com/watch?v=5nKCs4bkoxE. No actual local postmix or audio cutpoint was available in this wave.

Validation rejects mismatched source hashes, source interval exceeding media duration, wrong review provenance, zero/missing audio/video, wrong 720×1280 output, actual duration drifting more than 1.5 seconds, stale/output collisions and unrendered files. No second silence-snap changes the reviewed end. Temporary files are isolated; completed files are published in the destination using no-overwrite local hardlinks, otherwise the command fails rather than substituting an overwrite.

The JSON handoff contains source and final SHA256, exact media cutpoints, final duration and a **provider-inert** status. release_authorized=false, provider_write_performed=false, rights_cleared=false, final_encoded_audio_listened=false, visual_safe_zones_reviewed=false, subtitles_reviewed=false are always emitted. An exported file is a BASE PREVIEW, **not** a release-ready Telegram, VK or Instagram asset. Operators still must listen to the FINAL encoded audio at both ends and joins, check meaning, subtitles, visual safe areas and rights, and obtain separate Video Manager authorization. An accelerated or subtitle-burned derivative has changed bytes and must be reviewed again.

This closes the ability to **render** reviewed intermediate-duration candidates, not the ability of the existing Gemini Factory plan to **discover** them automatically. Expansion of its three-pass semantic plan is a separate change. The user-selected MacArthur source cannot be truthfully called rendered or published until local media and boundary listening evidence exist. Follow-up: https://github.com/FedorMilovanov/mp3telegrambot/issues/469 and https://github.com/FedorMilovanov/video-channel-manager/issues/940.

## SHORTOREZ optional VK Clip speed derivative (operator-only, does not change Factory)

After creating the separate **1.00×** 177–300-second review MP4 with the offline SHORTOREZ exporter documented above, a reviewed complete passage may also fit the VK native Clip ceiling of **180 seconds** through pitch-preserving acceleration. This is not a second FFmpeg pipeline, bot mode, default speed change or native VK writer. The existing `services.shorts_video.postprocess_short` applies FFmpeg `setpts` and `atempo` to both media streams while keeping the nominal speech pitch. The operator must still **listen to the final accelerated encoded file** for intelligibility, syllable clipping, natural pauses and visual/subtitle issues.

Run from the repository root with an actual **base MP4 and its original companion JSON**:

```powershell
python -m services.shortorez_vk_variant --base-mp4 "C:\path\to\shortorez_BASE_vertical.mp4" --base-handoff-json "C:\path\to\shortorez_BASE_handoff.json" --output-dir "C:\path\to\operator-output\shortorez-vk"
```

The base MP4 name must match the exact canonical output name, source identity, SHA256 and checked source duration of `shortorez_export`. The base is never silently downloaded, overwritten, deleted, retranscribed or sped up in place. It remains the preferred **1.00× TG/Instagram variant**; this command only creates a distinct, clearly identified **VK candidate**.

Speed is computed from the **original reviewed source-language time interval**, matching the independent platform planner in `video-channel-manager`: try 105%, 110%, 115%, 120% in order and take the first speed whose rounded-up predicted duration fits 180000 ms. If even 120% cannot fit the complete argument (for example, an interval longer than 216 seconds), reject and require a different meaning-complete source cut. Never accelerate more than 1.20×, trim the end, omit introductory qualifications or change a MacArthur theological claim just to meet a format ceiling. A forecast of exactly 180000 ms is not automatic eligibility: the **actual** post-encode probe must also be <=180000 ms, and match the reviewed interval and base-duration predictions within 1500 ms. If it fails, the file is discarded; a separate explicitly reviewed choice is required, not an automatic retry.

The exporter computes and rechecks base SHA256 before/after transformation, verifies 720×1280 video and audible audio streams, and refuses output collisions. A companion provider-inert JSON records the original video, absolute RU-postmix boundaries, original and derivative byte fingerprints, measured and predicted durations, speed percent, and explicit **false** approval flags. `release_authorized=false`, `provider_write_performed=false`, `rights_cleared=false`, `final_encoded_audio_listened=false`, `subtitles_reviewed=false`, `visual_safe_zones_reviewed=false` always remain false. A pitch-preserving filter is an algorithmic property, **not** proof the voice sounds natural. Subtitles must be rebuilt or retimed against the actual accelerated audio and safe zones checked from the final frame renders.

Actual MacArthur source `5nKCs4bkoxE` is still not on the authorized reachable desktop; this change has not exported a real video, established source rights, or published any TG/VK/Instagram posts. Upstream semantics remain Video Manager [issue #940](https://github.com/FedorMilovanov/video-channel-manager/issues/940); implementation tracked in [mp3telegrambot #472](https://github.com/FedorMilovanov/mp3telegrambot/issues/472).


## SOLO SHORTOREZ: reviewed fixed camera-shot plan (offline, not Factory)

The separate **local** `python -m services.shortorez_export` now has two
**mutually exclusive** workflows for its 177–300-second RU-postmix gap:

- **One speaker:** provide `--solo-shot-plan-json <path>` with a *human-reviewed*
  list of the actual source-camera edits and one unchanging source-pixel `crop_x`
  for each original shot. This uses the new independent full-bleed 720×1280
  encoder, **not** the generic centre-locked `crop_zoom` or blurred Q&A mode.
- **Actual multi-person Q&A / panel:** explicitly select
  `--multi-person-context-blur` to preserve the whole widescreen context.
  This retains the original reviewed legacy behavior.

The usual `--source-path`, exact SHA, RU boundary record, YouTube ID, start/end
milliseconds and `--output-dir` remain mandatory in **both** workflows. Do not
turn this into a new Telegram bot mode or change production Factory configuration.

Example `--solo-shot-plan-json` (illustrative coordinates and camera boundaries
**only**, not factual source observations or permission to publish):

```json
{
  "schema_name": "mp3bot.shortorez-fixed-shot-plan",
  "schema_version": 1,
  "project_key": "lord-god-strength",
  "source_channel_id": "UCeSJsC6go2c9pdJCuUI1BYA",
  "source_video_id": "5nKCs4bkoxE",
  "source_sha256": "sha256:<exact 64 lowercase hex of RU postmix>",
  "start_ms": 10000,
  "end_ms": 215000,
  "source_width": 640,
  "source_height": 358,
  "layout": "solo_static_crop_per_source_camera_shot",
  "camera_cut_review": {
    "status": "original_camera_edits_reviewed",
    "reviewer": "actual human reviewer",
    "method": "Compare original source edit frames and three samples in every camera take"
  },
  "shots": [
    {"start_ms": 10000, "end_ms": 100000, "crop_x": 20, "camera_shot_id": "front"},
    {"start_ms": 100000, "end_ms": 215000, "crop_x": 436, "camera_shot_id": "side"}
  ]
}
```

Exact bounds must cover the entire reviewed RU interval with **no gaps or overlaps**.
The first and last timestamps equal the heard first/last speech boundaries.
`crop_x` is an even, nonnegative **source pixel** X coordinate: crop width is
derived from the actual source height at approximately 9:16; the crop must
remain inside the source frame. Every camera shot has its **own fixed X**.
Never interpolate an X trajectory, invent additional cuts, track the head
during a shot, reuse demo coordinates, or conflate a changed camera angle with
a second simultaneously visible speaker. If the source has a non-square pixel
aspect ratio or unusual rotation, **HOLD** for manual normalization and review:
the regular media probe does not certify sample-aspect-ratio correctness.

Rendering uses FFmpeg's original-frame time PTS, one crop filter per reviewed
shot, a hard concat at the source edit, and a **single continuous audio trim**
for the full interval. It does not shorten speech to `-shortest` or use
`gblur`/picture-in-picture. The result is a staged 720×1280 MP4 and an inert
source-SHA-bound manifest. A reported 9:16 geometry is **NOT** evidence of
accurate face framing, shot alignment, lack of visual jitter or source audio
completeness.

**Release remains HOLD:** the operator must inspect the *encoded* first,
middle and last frames of **each** source shot, both sides of **every actual
camera edit**, and listen through the first/last speech and edit seams.
Compare original/master vs the final MP4 and provider derivative separately.
`final_encoded_audio_listened`, `visual_safe_zones_reviewed`,
`rights_cleared` and `release_authorized` stay `false` in the resulting
manifest; source-bound records alone cannot authorize YouTube/VK upload.
