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
- `/mode`, `/settings`, runtime installation and CI coverage.

## Result matrix

| Mode | Trigger | Output | Quality boundary | LiveDub behavior | Final media proof | Result |
|---|---|---|---|---|---|---|
| Regular Shorts | Full analysis + `shorts=on` | Individual vertical Shorts | Gemini candidates, duration/overlap validation, optional subtitles/poster | Exact translated-source duration; preserved left context; complete delayed Russian tail | Size selection, video+audio probe, actual delivery evidence | Wired and hardened |
| Clips | Full analysis + `clips=on` | 5–15 minute source-format clips | Gemini long-candidate validation | Exact translated-source duration and complete Russian tail | Every render must pass video+audio probe | Wired and hardened |
| Montage | Full analysis + `shorts_montage=on` | One vertical reel from separated moments | Text-plan candidate validation; mature renderer, but no per-fragment source-context Whisper audit | Every fragment receives context and complete Russian tail | Shared delivery pipeline probes final video+audio and subtitle fallback | Working; intentionally lighter than Highlights |
| Highlights | Full analysis + `shorts_highlights=on` | Strict thematic vertical reel | Source-context Whisper, complete-utterance refinement, speech coverage, dangling-context checks, minimum fragment count, independent Gemini thematic review | Strict verifier receives exact translated-source duration | Verified renderer plus final media, duration and silence QA | Strongest legacy reel mode |
| Segments / `/cut` | Cached full analysis + `segments=on`; render requires `segments_render=on` | User-selected Q&A/topic segment | Deterministic boundaries from cached AI timestamps and render lock | Uses the stored source URL; not the standalone Factory path | Base and final files require video+audio proof; subtitle artifact may safely fall back to valid base render | Wired and hardened |
| SHORTS FACTORY MAX | `/mode` → `shorts_max` | Up to 5 subtitled Shorts and 3 long clips | Three high-thinking Gemini Pro audio passes, verified boundaries, overlap/range validation, final score gate 88/85 | Foreign source requires Yandex LiveDub only; no neural translation fallback | Exact source probe, mandatory burned subtitles for Shorts, actual Telegram delivery counters | Standalone quality-first mode |

## Important behavior distinctions

1. Legacy Shorts, Clips, Montage, Highlights and Segments are optional `/settings` features and remain disabled by default. They run from the full-analysis pipeline and need valid `ai_data` or cached timestamps.
2. `SHORTS FACTORY MAX` is independent of those switches. It is a persistent `/mode` route built specifically for extraction without Synopsis, Telegraph or questions.
3. Montage is a useful promotional assembly, but it must not be described as equivalent to Highlights. Highlights has the additional source-context Whisper and thematic quality gates.
4. For foreign-language extraction, Gemini selects and audits content; Yandex LiveDub «Живые голоса» supplies the Russian voice. The bot does not synthesize its own translation in these paths.

## Defects found and fixed during this recheck

1. Legacy Clips previously accepted a render mainly by file existence. The active renderer is now wrapped with a required video+audio media probe.
2. Legacy LiveDub Shorts and Montage could lose the delayed Russian ending when boundary padding was disabled or source duration came from original metadata. The downstream policy now probes the real translated file and appends the full configured LiveDub tail.
3. Highlights quality verification previously received the original YouTube duration. It now receives the exact translated-source duration, including the tail.
4. Montage now expands every LiveDub fragment independently and recalculates total duration.
5. Selectable `/cut` segments previously used planned duration and did not prove the finished video/audio streams. They now probe the base and final artifact, use the actual delivery duration and safely fall back from a failed subtitle version.
6. Factory previously accepted any self-reported score after its three reviews. It now requires default final scores of 88 for Shorts and 85 for long clips, plus complete editorial fields and verified boundaries.
7. Windows CI did not explicitly execute the new cut-mode tests. The Python 3.13 Windows job and Ruff target now include Factory, LiveDub downstream, Highlights boundary, Segments delivery and runtime-installation regressions.

## Regression evidence

- `tests/test_shorts_factory_candidates.py`
- `tests/test_shorts_factory_mode.py`
- `tests/test_shorts_factory_quality_gate.py`
- `tests/test_livedub_downstream_media.py`
- `tests/test_segment_delivery_media.py`
- `tests/test_cut_runtime_installation.py`
- `tests/test_highlights_candidate_gate.py`
- existing Highlights quality, delivery, process-ownership and transactional-output suites
- Linux Python 3.11/3.13 full-suite workflow
- Windows Python 3.13 runtime workflow
- Ruff, requirements lock, compileall and code-health regression gates

## Honest remaining boundary

The repository and regression contracts are now wired for all requested modes. A complete end-to-end proof still requires the operator runtime because it depends on external systems and secrets unavailable to repository tests:

- a real Gemini Pro API call and quota;
- a real Yandex OAuth/VOT response for «Живые голоса»;
- local `ffmpeg`, `ffprobe`, Node/VOT helper and faster-whisper;
- an actual Telegram upload under the operator's cloud or local Bot API limit.

The required smoke run is therefore operational, not another code-design step: one foreign video through Factory, one full-analysis video with all legacy cut toggles enabled, and one cached `/segments` cut.
