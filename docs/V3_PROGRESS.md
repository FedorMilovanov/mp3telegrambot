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

## Завершено: Phase 3.3–3.5 — Combined observability + candidate schema

В одном безопасном патче:

1. `services/telegraph_pages.py` logging
2. `services/shorts_candidates.py` logging
3. `services/render_clips_montage.py` logging
4. lightweight `core/candidate_schema.py`
5. validation reports for Shorts/Clips candidates

## Завершено: Phase 3.6 — First dashboard/admin readout

Добавлено:

1. `/metrics [hours]` admin command
2. aggregate by task/model/finish_reason
3. parse failure and MAX_TOKENS visibility through `error`/`finish_reason`
4. recent Gemini runs readout
5. token/latency summary

## Завершено: Phase 4.1 — Structured Output candidates

Добавлено:

1. schema contract for Shorts candidates
2. schema contract for Clips candidates
3. schema contract for Extras candidates
4. structured JSON config for Gemini calls
5. legacy JSON fallback if SDK/model rejects schema
6. tests for schema/config/fallback contracts

## Текущая фаза

**Phase 4.2 — Candidate quality comparison + admin readout**

В одном патче:

1. store candidate validation summaries in `gemini_runs`
2. expose rejection reasons in `/metrics`
3. aggregate rejected candidate counts by reason
4. keep `postprocess_fixes` as numeric rejected-count for compatibility
5. tests for validation summary persistence and metrics report

## Следующая фаза

**Phase 4.3 — Canonical candidate objects + admin quality loop**

Порядок:

1. canonical dataclasses for accepted Shorts/Clips/Extras candidates
2. include rejection reasons in debug logs/admin readout
3. compare parse failure rate before/after Structured Output
4. затем canonical audio analysis adapter

## Pass 3 audit follow-up

Закрыто после внешнего аудита:

- skip explicit `thinking_config` when using Gemini `response_schema`
- avoid repeated observability DDL on every log write
- show truncation notice in long `/metrics` reports
- CI now runs on Python 3.11 and 3.13

Ложные/устаревшие находки:

- `/start` already uses HTML, not Markdown `**bold**`
- `/metrics` already uses HTML tags
- `extras_response_schema()` is wired into real Extras Gemini call
- PDF timecode linker already uses bounded `context_tail`
- `_RE_H3` is used by `_add_h3_platform_links()` and must not be deleted

## Release rule

Перед каждым push:

```bash
python -m compileall -q .
python -m pytest -q
git diff --check
```
