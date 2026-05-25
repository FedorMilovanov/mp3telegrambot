# V3 Progress

Дата старта V3 hardening: 2026-05-25

## Стабильная точка

Tag: `v3-pre-schema-stable`

Закрыто:

- P0 runtime hardening
- metadata prompt-injection trust boundary EN/RU
- video lock wait timeout + release guard
- Shorts render range clamp
- montage cleanup guard
- `/stop` + SIGTERM/SIGINT graceful path
- PDF timecode linkifier bounded context
- MM:SS timestamps without leading zero
- Block B runtime stability gaps

## Завершено: Phase 2.5 — Safety net before Schema + Observability

Добавлено:

- `compileall`
- `pytest`
- fatal `ruff` checks
- prompt/runtime contract tests
- GitHub Actions CI

## Завершено: Phase 3.1 — Observability foundation

Добавлено:

1. `core/observability.py`
2. таблица `gemini_runs`
3. sync/async логирование Gemini runs
4. usage/finish_reason extraction helpers
5. tests без реальных Gemini clients

## Завершено: Phase 3.2 — Audio analysis observability

Подключено логирование `audio_analysis` для:

- usage metadata
- finish reason
- latency
- fallback state
- parse failures
- empty responses
- MAX_TOKENS

## Текущая фаза

**Phase 3.3–3.5 — Combined observability + candidate schema**

В одном безопасном патче:

1. `services/telegraph_pages.py` logging
2. `services/shorts_candidates.py` logging
3. `services/render_clips_montage.py` logging
4. lightweight `core/candidate_schema.py`
5. validation reports for Shorts/Clips candidates

## Следующая фаза

**Phase 3.6 — First dashboard/admin readout**

Порядок:

1. `/metrics` or admin command for latest Gemini runs
2. aggregate by task/model/finish_reason
3. show parse failure and MAX_TOKENS counts
4. prepare for Structured Output candidates
5. затем canonical audio analysis adapter

## Release rule

Перед каждым push:

```bash
python -m compileall -q .
python -m pytest -q
git diff --check
```
