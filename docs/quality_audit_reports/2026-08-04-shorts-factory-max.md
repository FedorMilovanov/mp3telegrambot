# Quality audit — SHORTS FACTORY MAX

Date: 2026-08-04

## Operator requirements

- Extraction-only mode: no Synopsis, Telegraph, questions or normal research pages.
- Maximum reasoning quality; do not trade accuracy for quota, speed or latency.
- Short highlights: up to 3 minutes, with burned subtitles.
- Long highlights: 5–15 minutes, without subtitles.
- Foreign-language voice path: Yandex LiveDub «Живые голоса» only.
- No in-house neural translation at this stage; retain a disabled provider seam for future work.

## Quality decisions implemented

1. Default model is `gemini-3.1-pro-preview`; Flash and Lite are not accepted as Factory overrides.
2. Three full-audio passes run with `thinking_level=high`: scout, independent semantic judge and independent boundary auditor.
3. Every pass uses structured JSON output. Only `boundary_verified=true` candidates survive deterministic validation.
4. Factory render speed is fixed at `1.0` so verified boundaries are not changed by user settings.
5. Shorts require `faster-whisper`, default `large-v3`, karaoke and word timestamps. Subtitle-less fallback delivery is rejected.
6. A Factory job cannot report success when zero burned-subtitle Shorts reach Telegram.
7. The shared render source must pass video+audio media probe. Its real duration, including LiveDub tail, is used by renderers.
8. Yandex timing uses a context-safe envelope: preserve the semantic start, add small pre-roll, and append the full configured delayed Russian-audio tail. A candidate is rejected if this cannot fit inside the public 3/15-minute limit.
9. A non-Russian job fails clearly when Yandex LiveDub is unavailable. The untranslated original and neural-voice fallback are not delivered.
10. Runtime installation is required and fail-closed through `services.runtime_manifest`.

## Regression evidence

- `tests/test_shorts_factory_candidates.py`
- `tests/test_shorts_factory_mode.py`
- `tests/test_runtime_manifest.py`
- `docs/code_health_reports/2026-08-04-shorts-factory-max.json`
