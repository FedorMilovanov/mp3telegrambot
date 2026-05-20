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

---

## 2026-05-21 — Deep Quality Audit v7 (Telegraph + Gemini 3.5)

### Контекст
Глубинная проверка всего пайплайна (Synopsis → Study → Reflection → Telegraph) после обновления знаний о реальном поведении `gemini-3.5-flash` (GA с 19.05.2026) и актуальной Telegraph API. Проведено **30+ изолированных bash-проверок** через `python3` без сети, найдено **13 багов разной серьёзности**, **11 исправлено**, **2 задокументированы как не в скоупе**.

Полный отчёт: `dev-tools/docs/2026-05-21_DEEP_AUDIT_v7.md`
Применить: `git apply dev-tools/patches/2026-05-21_deep_quality_v7.patch`

### Применённые патчи (одним коммитом)

| # | Severity | Файл | Что |
|---|---|---|---|
| #2 | 🔴 **P0** | `converters/md_telegraph.py:_section_to_nodes_v2` | `re.sub(r'([.!?…:])(\*\*)', r'\1 \2', content)` ломал закрывающий `**` после `:` → "съезжала" вся жирная разметка абзаца. Исправлено: добавляем пробел только если следующий символ не `\s` и не `*`. |
| #3 | 🟠 P1 | `services/telegraph_pages.py:_parse_expanded_json` | Не восстанавливал реальные `\n` внутри JSON-строк (хотя такой код был в `telegraph.py:_fix_json_newlines`). Теперь продублирован + расширен на `\r`, `\t`. |
| #5 | 🟠 P1 | `converters/md_telegraph.py`, `services/telegraph.py` | `author_name` не обрезался до 128 chars → `AUTHOR_NAME_TOO_LONG`. Добавлен `(author or "")[:128]` во всех 3 местах публикации. |
| #8 | 🟠 P1 | `converters/md_telegraph.py:safe_trim_caption` | При превышении первой строкой лимита `break` терял ВЕСЬ хвост (таймкоды/ссылки/хэштеги). Теперь greedy fit with skip — пропускаем «толстую» строку, продолжаем добавлять последующие. |
| #10 | 🟠 P1 | `core/database.py:590` | Default `GEMINI_MODEL` обновлён `gemini-2.5-flash` → `gemini-3.5-flash` (в соответствии с AI_GUIDELINES.md). |
| #1 | 🟡 P2 | `converters/md_telegraph.py:_ensure_trailing_period` | Точка ставилась СНАРУЖИ `**` (`**Жирный**.`) вместо внутри (`**Жирный.**`). Исправлено для обоих случаев — `**` и `***`. |
| #7 | 🟡 P2 | `services/telegraph.py:_md_to_telegraph_nodes` | `##### H5` и `###### H6` уходили в `<p>` вместо `<h4>`. Добавлены 2 ветки. |
| #11 | 🟡 P2 | `core/json_parser.py:_parse_gemini_response` | Не убирал ```` ```json...``` ```` префикс/суффикс, логировал «JSON не найден». Добавлен strip code-fence в начале функции. |
| #12 | 🟡 P2 | `core/globals.py:make_audio_config/make_text_config_smart` | Cap `max_output_tokens` для 3.x повышен с 40000 до **65000** — реальный лимит `gemini-3.5-flash` по Google I/O 2026. |
| #4 | 🟢 P3 | `converters/md_telegraph.py:_build_toc_nodes_v2` | Убран ведущий пробел в `" Структура материала"` → `"Структура материала"`. |
| #6 | 🟢 info | `converters/md_telegraph.py:_final_telegraph_polish` | Telegraph API игнорирует все attrs кроме `href/src`. Полишер теперь срезает их сам ДО публикации (чище payload, чище логи). RTL продолжает работать через LRM-маркеры `\u200e` в тексте. |
| #13 | 🟢 info | `services/telegraph_pages.py:23-29` | Мёртвый импорт `_try_parse_synopsis_json` — закомментирован. |

### Документировано (не фиксили):
- **#9** Markdown-ссылки `[text](url)` и code-blocks в `_md_to_telegraph_nodes` не конвертируются. Gemini в наших промптах их не использует → пока не нужно. Добавлять только если будем включать Q&A технических лекций.

### Проблемы, которые ОСТАЛИСЬ решёнными
- ✅ Все 30 модулей репозитория импортируются без ошибок
- ✅ Все теги Telegraph nodes — в whitelist
- ✅ `<b>`/`</b>` парные, нет сырых `**` в caption
- ✅ ReDoS-устойчивость: 50K input ≤ 0.5s
- ✅ RTL работает (LRM-маркеры в тексте)
- ✅ JSON-парсер выживает на code-fence + сырых `\n` + обрезанных ответах
- ✅ `thinking_level=HIGH` применяется для всех 3.x вызовов

### Известные нерешённые проблемы
См. `KNOWN_ISSUES.md` — на 2026-05-21 список тот же что и был.
