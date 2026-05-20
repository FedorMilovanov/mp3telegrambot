# CHANGELOG патчей

Хронология всех применённых патчей. Заполняется при каждом новом фиксе.

---

## 2026-05-20 — Серия фиксов перехода на gemini-3.5-flash

### Контекст
Бот раньше работал на `gemini-2.5-pro`. После того как Google убрал её из free tier (1 апреля 2026), начались каскадные 503 на `gemini-2.5-flash`. Перешли на `gemini-3.5-flash` (релиз 19 мая 2026).

### Применённые патчи

| # | Файл | Что сделал |
|---|---|---|
| 1 | `apply_fixes.py` | Базовые фиксы Gemini SDK 1.x: убран `audio_timestamp`, добавлен `author_url`, дефолт модели, логирование `finish_reason` |
| 2 | `fix_audio_upload.py` | `services/gemini_analyze.py`: всегда File API upload вместо inline base64 (исправило 503 на 19MB файлах) |
| 3 | `fix_503_retry.py` | `services/gemini_analyze.py`: retry внутри ключа 15s/30s + второй круг через 60s |
| 4 | `fix_multi_model.py` | `services/gemini_analyze.py`: fallback 3.5-flash → 3-flash → 3.1-flash-lite для audio-анализа |
| 5 | `fix_ultimate_3_5_flash.py` | `core/globals.py`: `thinking_level="high"` для 3.x, helper `make_text_config_smart`. `services/telegraph_pages.py`: multi-model fallback + thinking_level для текстовых вызовов (Reflection/Study). `converters/caption.py`: `_strip_markdown_artifacts` для всех полей |
| 6 | `2026-05-20_deep_quality.patch` ⭐ | **Сериализация Study+Reflection**, умный быстрый retry, жирные слова в таймкодах, чистка markdown в Telegraph. Перенос в `dev-tools/`, очистка мусора |

### Проблемы, которые ОСТАЛИСЬ решёнными
- ✅ Audio analysis работает на `gemini-3.5-flash` за ~75 сек (вместо 503)
- ✅ Multi-model fallback для аудио
- ✅ Multi-model fallback для текстовых вызовов
- ✅ Сериализация Study+Reflection (не параллельно)
- ✅ Markdown-чистка в caption и таймкодах

### Известные нерешённые проблемы
См. `KNOWN_ISSUES.md`

---

## Шаблон для будущих записей

```markdown
## YYYY-MM-DD — Краткое название серии

### Контекст
Что было и зачем фиксили.

### Применённые патчи
| # | Файл | Что сделал |
|---|---|---|
| N | `имя.patch` | Описание |

### Проблемы, которые ОСТАЛИСЬ решёнными
- ✅ ...

### Известные нерешённые проблемы
См. `KNOWN_ISSUES.md`
```
