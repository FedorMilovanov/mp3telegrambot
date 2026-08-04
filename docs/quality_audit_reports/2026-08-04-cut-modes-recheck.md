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
- boundary precision from Gemini JSON through the final ffmpeg command.

## Result matrix

| Mode | Trigger | Output | Quality boundary | LiveDub behavior | Final media proof | Result |
|---|---|---|---|---|---|---|
| Regular Shorts | Full analysis + `shorts=on` | Individual vertical Shorts | Gemini candidates, duration/overlap validation, optional subtitles/poster | Exact translated-source duration; preserved left context; complete delayed Russian tail; ENG mode refuses original-language fallback | Size selection, video+audio probe, actual delivery evidence | Wired and hardened |
| Clips | Full analysis + `clips=on` | 5–15 minute source-format clips | Gemini long-candidate validation | Exact translated-source duration and complete Russian tail; ENG mode refuses original-language fallback | Every render must pass video+audio probe; Telegram duration comes from the final MP4 | Wired and hardened |
| Montage | Full analysis + `shorts_montage=on` | One vertical reel from separated moments | Text-plan candidate validation; mature renderer, but no per-fragment source-context Whisper audit | Every fragment receives context and complete Russian tail; ENG mode refuses original-language fallback | Shared delivery pipeline probes final video+audio and subtitle fallback | Working; intentionally lighter than Highlights |
| Highlights | Full analysis + `shorts_highlights=on` | Strict thematic vertical reel | Source-context Whisper, complete-utterance refinement, speech coverage, dangling-context checks, minimum fragment count, independent Gemini thematic review | Strict verifier receives exact translated-source duration; ENG mode refuses original-language fallback | Verified renderer plus final media, duration and silence QA | Strongest legacy reel mode |
| Segments / `/cut` | Cached full analysis + `segments=on`; render requires `segments_render=on` | User-selected Q&A/topic segment | Deterministic boundaries from cached AI timestamps and render lock | Uses the stored source URL; not the standalone Factory path | Base and final files require video+audio proof; subtitle artifact may safely fall back to valid base render | Wired and hardened |
| SHORTS FACTORY MAX | `/mode` → `shorts_max` | Up to 5 subtitled Shorts and 3 long clips | Canonical Gemini Pro >=3.1; three high-thinking audio passes; proven spoken language; millisecond verified boundaries; score floors 88/85; no second silence-snap; exact Whisper large-v3 | Source chosen only after audio analysis; every non-Russian language requires Yandex LiveDub; OAuth/route preflight; no neural or untranslated fallback | Exact source probe, mandatory burned subtitles for Shorts, actual Telegram delivery counters and truthful partial status | Standalone no-downgrade mode |

## Important behavior distinctions

1. Legacy Shorts, Clips, Montage, Highlights and Segments are optional `/settings` features and remain disabled by default. They run from the full-analysis pipeline and need valid `ai_data` or cached timestamps.
2. `SHORTS FACTORY MAX` is independent of those switches. It is a persistent `/mode` route built specifically for extraction without Synopsis, Telegraph or questions.
3. Montage is a useful promotional assembly, but it must not be described as equivalent to Highlights. Highlights has the additional source-context Whisper and thematic quality gates.
4. For foreign-language extraction, Gemini selects and audits content and proves the spoken language from audio; Yandex LiveDub «Живые голоса» supplies the Russian voice. The bot does not synthesize its own translation in these paths.
5. Factory partial success is not reported as full success. It uses the number of Telegram-accepted files, keeps the trim source only when at least one Short was delivered and fails when the delivery count is zero.
6. The persisted user mode is task-local during full-analysis execution. Main, ordinary-link and playlist entrypoints each preserve their prior runtime-wrapper chain.
7. Factory quality controls are floors, not preferences. Environment variables may make selection, timing, disk reserve or timeout stricter, but cannot reduce the production minimums.
8. Ordinary legacy renderers retain silence snapping. Factory alone bypasses the second snap because its third Gemini pass has already audited both boundaries against source audio.

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
14. The new mode-context wrapper initially risked replacing already-installed command/playlist adapters with the raw main pipeline. It now wraps and preserves each existing entrypoint independently.
15. `Start Bot.bat` treated `.venv/.setup-complete` as a permanent success marker. After a pull, changed dependencies could remain uninstalled. The marker now stores the SHA-256 of `requirements-lock.txt`, the lock is reinstalled and verified when the hash changes, and an unsupported Python virtual environment is recreated.
16. A valid analysis cache returned before legacy cut stages, so repeated links could deliver cached analysis but never run enabled Shorts/Clips/Montage/Highlights. It now becomes a no-publication replay that reuses cached `ai_data`, suppresses duplicate main MP3/pages/archive writes and requires at least one Telegram-accepted cut video for success.
17. A cached LiveDub Telegram `file_id` cannot be cut locally. ENG cache replay now clears that transient delivery shortcut and rebuilds a local translated MP4 before any cut renderer is allowed to run.
18. Factory structured output accepted fractional seconds but deterministic validation rounded both boundaries to whole seconds, losing up to half a second on each side after the final audio audit. Production validation now preserves milliseconds.
19. The mature Shorts and Clips renderers performed a second silence search after the third Gemini boundary audit. That could extend a 177-second Short by up to 10 seconds, extend a long clip by up to 12 seconds or shrink a five-minute clip below its contract. Factory now uses the audited fractional end literally; non-Factory modes keep their existing behavior.
20. Configuration could silently lower Factory quality through an old/noncanonical Pro model, `large-v3-turbo` or a smaller Whisper model, score values below 88/85, reduced LiveDub pre-roll/tail, disk reserve below 2 GB or timeout below 1800 seconds. Required startup now rejects model/Whisper downgrades and floors all numeric quality controls.
21. New runtime policy files changed the code-health inventory. The baseline now records `files_scanned=168`, one dedicated canonical-model regex (`726` total) with its regression test, and unchanged editorial postprocess debt (`271`).

## Regression evidence

- `tests/test_factory_execution_and_cut_source_policy.py`
- `tests/test_cut_replay_delivery_policy.py`
- `tests/test_shorts_factory_no_downgrade.py`
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

- a real Gemini Pro API call, file upload, structured fractional timestamps and quota;
- a real Yandex OAuth/VOT response for «Живые голоса»;
- local `ffmpeg`, `ffprobe`, Node/VOT helper, faster-whisper and the exact `large-v3` model download;
- an actual Telegram upload under the operator's local Bot API limit;
- a real YouTube source whose language metadata disagrees with the spoken audio, to verify the audio-first branch in production logs.

The required smoke run is operational: one Russian-spoken video with an English title through Factory, one English-spoken video with a Russian title through Factory, one boundary candidate containing fractional seconds near the 177/897-second ceiling, one full-analysis ENG video with all legacy cut toggles enabled, and one cached `/segments` cut. The expected evidence is the spoken-language log, Yandex-only source selection for the foreign video, no untranslated legacy cut fallback, unchanged audited fractional endpoints, actual delivered counts and final probed durations.
