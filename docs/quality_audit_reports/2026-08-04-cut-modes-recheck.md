# Full cut-mode recheck

Date: 2026-08-04
Repository: `FedorMilovanov/mp3telegrambot`
Branch: `main`

## Scope

This audit rechecked every operator-requested extraction path:

- regular Shorts;
- Clips;
- Montage;
- Highlights;
- selectable Segments and `/cut`;
- `SHORTS FACTORY MAX`;
- Yandex LiveDub source preparation and downstream timing;
- `/mode`, `/settings`, runtime installation and CI coverage;
- Windows bootstrap and dependency refresh after `git pull`;
- task-local runtime-wrapper composition and truthful partial delivery;
- model, ASR, score, timing, disk and timeout downgrade surfaces;
- boundary precision from Gemini JSON through the final ffmpeg command;
- final rendered-file limits and interactive post-delivery controls;
- selected yt-dlp format quality, codec generations, LiveDub original resolution and peak disk usage.

## Result matrix

| Mode | Trigger | Output | Quality boundary | LiveDub behavior | Final media proof | Result |
|---|---|---|---|---|---|---|
| Regular Shorts | Full analysis + `shorts=on` | Individual vertical Shorts | Gemini candidates, duration/overlap validation, optional subtitles/poster | Exact translated-source duration; preserved left context; complete delayed Russian tail; ENG mode refuses original-language fallback | Size selection, video+audio probe, actual delivery evidence | Wired and hardened |
| Clips | Full analysis + `clips=on` | 5–15 minute source-format clips | Gemini long-candidate validation | Exact translated-source duration and complete Russian tail; ENG mode refuses original-language fallback | Every render must pass video+audio probe; Telegram duration comes from the final MP4 | Wired and hardened |
| Montage | Full analysis + `shorts_montage=on` | One vertical reel from separated moments | Text-plan candidate validation; mature renderer, but no per-fragment source-context Whisper audit | Every fragment receives context and complete Russian tail; ENG mode refuses original-language fallback | Shared delivery pipeline probes final video+audio and subtitle fallback | Working; intentionally lighter than Highlights |
| Highlights | Full analysis + `shorts_highlights=on` | Strict thematic vertical reel | Source-context Whisper, complete-utterance refinement, speech coverage, dangling-context checks, minimum fragment count, independent Gemini thematic review | Strict verifier receives exact translated-source duration; ENG mode refuses original-language fallback | Verified renderer plus final media, duration and silence QA | Strongest legacy reel mode |
| Segments / `/cut` | Cached full analysis + `segments=on`; render requires `segments_render=on` | User-selected Q&A/topic segment | Deterministic boundaries from cached AI timestamps and render lock | Uses the stored source URL; not the standalone Factory path | Base and final files require video+audio proof; subtitle artifact may safely fall back to valid base render | Wired and hardened |
| SHORTS FACTORY MAX | `/mode` → `shorts_max` | Up to 5 subtitled Shorts and 3 long clips | Canonical Gemini Pro >=3.1; three high-thinking audio passes; proven spoken language; millisecond verified boundaries; score floors 88/85; no second silence-snap; exact Whisper large-v3 | Russian and foreign sources use unrestricted bestvideo; foreign mode mixes only Yandex live audio over that original and proves the delayed Russian tail | Exact final artifacts are re-probed, capped at 180/900 seconds and counted only after Telegram acceptance; selected formats receive peak-disk proof; unsafe trim controls are removed | Standalone no-downgrade mode |

## Important behavior distinctions

1. Legacy Shorts, Clips, Montage, Highlights and Segments are optional `/settings` features and remain disabled by default. They run from the full-analysis pipeline and need valid `ai_data` or cached timestamps.
2. `SHORTS FACTORY MAX` is independent of those switches. It is a persistent `/mode` route built specifically for extraction without Synopsis, Telegraph or questions.
3. Montage is a useful promotional assembly, but it must not be described as equivalent to Highlights. Highlights has the additional source-context Whisper and thematic quality gates.
4. For foreign-language extraction, Gemini selects and audits content and proves the spoken language from audio; Yandex LiveDub «Живые голоса» supplies the Russian voice. The bot does not synthesize its own translation in these paths.
5. Factory partial success is not reported as full success. It uses the number of Telegram-accepted files and fails when the delivery count is zero.
6. The persisted user mode is task-local during full-analysis execution. Ordinary-link and playlist entrypoints preserve their own prior runtime-wrapper chains.
7. Factory quality controls are floors, not preferences. Environment variables may make selection, timing, disk reserve or timeout stricter, but cannot reduce the production minimums.
8. Ordinary legacy renderers retain silence snapping. Factory alone bypasses the second snap because its third Gemini pass has already audited both boundaries against source audio.
9. Generic Short trim callbacks remain available for ordinary Shorts. Factory suppresses those buttons because the generic callback does not repeat the mandatory `large-v3` subtitle pipeline and has no Factory 180-second final gate.
10. Legacy source downloaders keep their deliberate compatibility/performance ceilings. Only Factory resets compatibility sorting and removes the 720p/1080p ceilings; this avoids changing ordinary bot cost and speed.
11. `SHORTS_FACTORY_MIN_FREE_GB=2` is only the basic startup floor. The maximum-source transfer is controlled by a separate selected-format peak model that cannot be lowered through that variable.

## Defects found and fixed during this recheck

1. Legacy Clips previously accepted a render mainly by file existence. The active renderer is now wrapped with a required video+audio media probe.
2. Legacy LiveDub Shorts and Montage could lose the delayed Russian ending when boundary padding was disabled or source duration came from original metadata. The downstream policy now probes the real translated file and appends the full configured LiveDub tail.
3. Highlights quality verification previously received the original YouTube duration. It now receives the exact translated-source duration, including the tail.
4. Montage now expands every LiveDub fragment independently and recalculates total duration.
5. Selectable `/cut` segments previously used planned duration and did not prove the finished video/audio streams. They now probe the base and final artifact, use the actual delivery duration and safely fall back from a failed subtitle version.
6. Factory previously accepted any self-reported score after its three reviews. It now requires score floors of 88 for Shorts and 85 for long clips, plus complete editorial fields and verified boundaries.
7. Windows CI did not explicitly execute the new cut-mode tests. Focused Linux 3.11 and Windows 3.13 workflows now compile, test and Ruff-check the critical cut-policy surface.
8. Factory previously selected translation before listening to the audio, using yt-dlp language and, when absent, Cyrillic in the title. Source selection now happens after the three audio passes, and the third pass must prove one dominant spoken language. Empty, unknown or mixed language fails closed.
9. Full ENG analysis could continue legacy Shorts/Clips/Montage/Highlights with the original English video after LiveDub failure. Those modes now require the translated source and skip instead of silently publishing untranslated output.
10. Factory could send some files and later report the entire run as failed, delete a trim source already referenced by delivered Shorts or skip the long stage after a Short-stage exception. The strict executor isolates both stages, reads actual delivery counters, preserves delivered work and reports partial completion explicitly.
11. Clips could render to a silence-refined end but still tell Telegram the old candidate duration. Delivery metadata now uses the final probed MP4 duration.
12. A missing Gemini client, Whisper, `ffmpeg`, `ffprobe` or disk space was discovered late after unnecessary work. Factory now performs one aggregate preflight before downloads.
13. A foreign-language Factory run could begin a long LiveDub path without OAuth or any usable helper/CLI route. Translation preflight now checks both; tokenless cache-only operation requires explicit `SHORTS_FACTORY_REQUIRE_VOT_TOKEN=0`.
14. The new mode-context wrapper initially risked replacing already-installed command/playlist adapters with the raw main pipeline. The source policy and final Factory router now preserve ordinary-link and playlist entrypoints independently.
15. `Start Bot.bat` treated `.venv/.setup-complete` as a permanent success marker. After a pull, changed dependencies could remain uninstalled. The marker now stores the SHA-256 of `requirements-lock.txt`, the lock is reinstalled and verified when the hash changes, and an unsupported Python virtual environment is recreated.
16. A valid analysis cache returned before legacy cut stages, so repeated links could deliver cached analysis but never run enabled Shorts/Clips/Montage/Highlights. It now becomes a no-publication replay that reuses cached `ai_data`, suppresses duplicate main MP3/pages/archive writes and requires at least one Telegram-accepted cut video for success.
17. A cached LiveDub Telegram `file_id` cannot be cut locally. ENG cache replay now clears that transient delivery shortcut and rebuilds a local translated MP4 before any cut renderer is allowed to run.
18. Factory structured output accepted fractional seconds but deterministic validation rounded both boundaries to whole seconds, losing up to half a second on each side after the final audio audit. Production validation now preserves milliseconds.
19. The mature Shorts and Clips renderers performed a second silence search after the third Gemini boundary audit. That could extend a 177-second Short by up to 10 seconds, extend a long clip by up to 12 seconds or shrink a five-minute clip below its contract. Factory now uses the audited fractional end literally; non-Factory modes keep their existing behavior.
20. Configuration could silently lower Factory quality through an old/noncanonical Pro model, `large-v3-turbo` or a smaller Whisper model, score values below 88/85, reduced LiveDub pre-roll/tail, disk reserve below 2 GB or timeout below 1800 seconds. Required startup now rejects model/Whisper downgrades and floors all numeric quality controls. Validation occurs before any imported module is patched, so a bad `.env` cannot leave a half-installed runtime.
21. The final burned Short and long MP4 were probed for streams but not compared with their public duration limits. Encoder/container padding or an unexpected postprocess extension could therefore pass a 180/900-second plan and still deliver an overlong file. Factory now re-probes the exact Telegram artifact and rejects anything above the cap plus a 50 ms timescale tolerance.
22. Factory exposed the generic `-10/+10/+20` trim keyboard. That callback renders a new raw Short without rerunning mandatory `large-v3` transcription/burn-in and could exceed three minutes. Factory delivery now removes only that unsafe keyboard; ordinary Shorts remain unchanged. Existing managed Factory sources still expire through the bounded TTL policy.
23. The Russian Factory source reused the ordinary Shorts downloader, which hard-capped video at 720p. A vertical crop therefore had only roughly 405 source pixels across before being enlarged to 720, and long clips also lost available resolution. Factory now resets inherited format sorting and selects unrestricted `bestvideo+bestaudio/best` with a mandatory media probe.
24. Factory audio used `yt-dlp --extract-audio --audio-format mp3`, adding a second lossy generation before all three Gemini passes. It now downloads the best native stream, remuxes already-supported AAC/MP3/Vorbis/FLAC by codec copy and decodes unsupported codecs such as Opus once to lossless FLAC. The actual prepared MIME is passed to Gemini and the result is probed for truncation.
25. Legacy ENG Pro mix downloaded its original with a 1080p ceiling. Factory previously inherited that path for foreign sources. It now gets only the Yandex live Russian audio, downloads the same unrestricted original used by Russian MAX mode, builds the local Pro mix over it and rejects any result that physically lacks the configured delayed Russian tail. Legacy ENG remains unchanged.
26. Repository/local yt-dlp compatibility sorting could still prefer mp4/m4a containers over the objectively best stream even after selecting `bestvideo+bestaudio`. Factory now appends `--format-sort-reset`, disables `format-sort-force` and disables free-format preference while retaining cookies, proxy, authentication and JS-runtime arguments.
27. A fixed two-gigabyte preflight is not sufficient for unrestricted 4K/8K video, simultaneous video-only/audio-only streams, merged output or three-hour native-audio plus FLAC preparation. Factory now simulates its exact format selector, uses selected `filesize`/`filesize_approx` or bitrate×duration, models the temporary peak and requires it on each target filesystem. Unknown estimates use conservative 4 GB audio and 6 GB video floors.
28. New runtime policy files changed the code-health inventory. The baseline records `files_scanned=170`, one dedicated canonical-model regex (`726` total) with its regression test, and unchanged editorial postprocess debt (`271`).

## Regression evidence

- `tests/test_factory_execution_and_cut_source_policy.py`
- `tests/test_cut_replay_delivery_policy.py`
- `tests/test_shorts_factory_no_downgrade.py`
- `tests/test_shorts_factory_source.py`
- `tests/test_shorts_factory_disk_guard.py`
- `tests/test_start_bot_bootstrap.py`
- `tests/test_shorts_factory_candidates.py`
- `tests/test_shorts_factory_mode.py`
- `tests/test_shorts_factory_quality_gate.py`
- `tests/test_livedub_downstream_media.py`
- `tests/test_segment_delivery_media.py`
- `tests/test_cut_runtime_installation.py`
- `tests/test_highlights_candidate_gate.py`
- existing Highlights quality, delivery, process-ownership and transactional-output suites
- `.github/workflows/cut-policy-ci.yml`: focused Linux Python 3.11 and Windows Python 3.13
- `.github/workflows/windows-bootstrap-ci.yml`: Windows BAT/bootstrap contract
- existing Linux Python 3.11/3.13 full-suite workflow
- existing Windows Python 3.13 runtime workflow
- Ruff, requirements lock, compileall and code-health regression gates

## Honest remaining boundary

The repository contracts are wired for all requested modes, but a complete end-to-end proof still requires the operator runtime because repository tests cannot supply or impersonate the external production systems:

- a real Gemini Pro API call, supported-file upload, structured fractional timestamps and quota;
- a real Yandex OAuth/VOT live-audio response;
- a real source whose objectively best format is WebM/AV1/VP9 and exceeds the legacy 720p/1080p ceilings;
- local `ffmpeg`, `ffprobe`, Node/VOT helper, faster-whisper and the exact `large-v3` model download;
- actual free-space and filesystem behavior for the selected source peak;
- an actual Telegram upload under the operator's local Bot API limit;
- a real YouTube source whose language metadata disagrees with the spoken audio, to verify the audio-first branch in production logs.

The required smoke run is operational: one Russian-spoken high-resolution video with an English title through Factory, one English-spoken high-resolution video with a Russian title through Factory, one Opus source that must become FLAC for Gemini, one fractional boundary near the 177/897-second ceiling, one full-analysis ENG video with all legacy cut toggles enabled, and one cached `/segments` cut. Expected evidence includes the selected-format estimate, unrestricted source dimensions, prepared audio codec/MIME, spoken-language log, Yandex-only source selection, full Russian tail, no untranslated legacy cut fallback, unchanged audited fractional endpoints, final duration at or below 180/900 seconds, no Factory trim keyboard and actual Telegram-accepted delivery counts.
