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

## Завершено: Phase 4.3 — Prompt quality hardening (patch 7)

1. `core/text_utils.py`: git/tech artifact scrub (BUG-R3-01 fix)
2. `core/prompt_rules.py`: THIRD_PERSON_BAN + FEW_SHOT_FIRST_SECTION constants
3. `core/prompts.py`: SYNOPSIS_V2 uses THIRD_PERSON_BAN + FEW_SHOT_FIRST_SECTION via expand
4. `core/prompts.py`: STUDY_ANALYSIS КРИТИЧЕСКИ ВАЖНО reduced 15 → 11
5. Eval baseline recorded (3 runs): Synopsis avg 4.13/5, Study 4.30/5, Reflection 4.40/5

## Следующая фаза

**Phase 5 — Prompt Caching + SOURCE_PACKS**

1. context caching для system_instruction (Study/Reflection)
2. SOURCE_PACKS — релевантные источники по теме вместо 100+ авторов
3. Eval run после рефакторинга (сравнение с baseline)

## Завершено: Phase 6.1 — Reflection thinking_level=medium

Данные из 5 прогонов:
- Study thoughts avg: 11954 tokens @ $9/1M = $0.108/прогон
- Reflection thoughts avg: 7556 tokens @ $9/1M = $0.068/прогон
- Reflection = пастырский стиль, не аналитика → medium достаточно

Изменения patch 14:
1. `_run_expanded_pipeline`: параметр `thinking_level` (default=high)
2. `create_telegraph_reflection_application`: thinking_level="medium"
3. Study остаётся high — богословская сложность оправдана
4. Ожидаемая экономия: ~35% от Reflection thoughts = ~$0.024/прогон

## Следующая фаза

**Phase 6.2 — Context Caching**
- Кешировать стабильную часть Study system_instruction (1024+ tokens)
- TTL 1 час, инвалидация при изменении PROMPT_VERSION
- Ожидаемая экономия: 15-25% Study input tokens

## Завершено: Phase 6.3 — editPage rate limit fix + audio key_categories hint

Прогон 8 (Раб Иеговы, МакАртур):
- editPage: 0 failures ✅
- Study thoughts: 9800 (хорошо)
- main_topic: без пафоса и 3-го лица ✅
- Verified context: 'раввины коверкали Его имя' — исторический факт ✅

Прогон 9 (Q&A Shepherds 2004, МакАртур+Спрол+Молер 80 мин):
- editPage: 2 failures (0.7с между create→edit при 87 nodes)
- QA 15/15 вопросительных ✅
- Study thoughts: 6110 (отличный результат, сложная тема)
- Reflection thoughts: 9916 (сложное приложение)

Patch 16:
1. telegraph.py: sleep(2) перед editPage loop в Synopsis
2. telegraph_pages.py: sleep(2) перед editPage в _publish_expanded_page
3. audio prompt key_categories: подсказка использовать богословски точные термины
   (они билдят source_pack для Study Analysis)
