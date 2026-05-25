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

## Текущая фаза

**Phase 2.5 — Safety net before Schema + Observability**

Цель: перед большими изменениями иметь локальные и GitHub checks:

- `compileall`
- `pytest`
- fatal `ruff` checks
- prompt/runtime contract tests

## Следующая фаза

**Phase 3 — Schema + Observability**

Порядок:

1. `core/observability.py`
2. таблица `gemini_runs`
3. логирование Gemini calls
4. validation report для candidates
5. schema для Shorts/Clips/Extras
6. затем canonical audio analysis adapter

## Release rule

Перед каждым push:

```bash
python -m compileall -q .
python -m pytest -q
git diff --check
```
