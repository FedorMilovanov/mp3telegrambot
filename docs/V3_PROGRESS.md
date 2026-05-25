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

## Текущая фаза

**Phase 3.1 — Observability foundation**

Добавляем безопасный фундамент без подключения ко всем Gemini-вызовам сразу:

1. `core/observability.py`
2. таблица `gemini_runs`
3. sync/async логирование Gemini runs
4. usage/finish_reason extraction helpers
5. tests без реальных Gemini clients

## Следующая фаза

**Phase 3.2 — Gemini call integration**

Порядок подключения:

1. `services/gemini_analyze.py`
2. `services/telegraph_pages.py`
3. `services/shorts_candidates.py`
4. `services/render_clips_montage.py`
5. validation report для candidates
6. schema для Shorts/Clips/Extras
7. затем canonical audio analysis adapter

## Release rule

Перед каждым push:

```bash
python -m compileall -q .
python -m pytest -q
git diff --check
```
