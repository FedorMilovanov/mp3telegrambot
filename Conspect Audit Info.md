# Справочник: Telegraph-конспекты — аудит и рекомендации

## Актуально на 24 мая 2026

> Технический аудит пайплайна генерации конспектов через Gemini → Telegraph.
> Содержит: найденные баги, применённые фиксы, архитектурные рекомендации.

---

## I. ПРИМЕНЁННЫЕ ИСПРАВЛЕНИЯ (Conspect Quality Patch v1.0)

### Критические (P1)

| # | Файл | Проблема | Статус |
|---|------|---------|--------|
| P1-1 | `telegraph_pages.py` | `resp.text` без try/except — краш при safety filter или thinking-only ответе | ✅ Исправлено |
| P1-2 | `telegraph_pages.py` | `\u200e`/`\u200f` RTL-маркеры удалялись из JSON → иврит/греч. рендерился LTR | ✅ Исправлено |
| P1-3 | `telegraph_pages.py` | retry при сломанном JSON не увеличивал max_tokens | ✅ Исправлено (×2, cap 65K) |
| P1-4a | `telegraph_pages.py` | StudyAnalysis max_tokens=16K — мало для длинных проповедей | ✅ → 32K |
| P1-4b | `telegraph_pages.py` | Reflection max_tokens=14K | ✅ → 24K |
| P4-11 | `requirements.txt` | `google-genai<2.0.0` блокировал SDK v2.x | ✅ → `<3.0.0` |
| P4-12 | `telegraph_pages.py` | `_parse_expanded_json` max_iterations=100K — мало для 65K token ответов | ✅ → 500K |

### Промпты (P2)

| # | Файл | Что добавлено | Статус |
|---|------|--------------|--------|
| P2-6 | `prompts.py` | 7 правил форматирования в STUDY_ANALYSIS_PROMPT и REFLECTION_APPLICATION_PROMPT | ✅ |

Правила:
1. Пробел перед ⏱ (между словом и эмодзи таймкода)
2. Русский перевод всех англоязычных цитат
3. Переводческие развилки — абзацем, не списком
4. Кавычки «ёлочки», тире `–` вместо дефиса в стихах
5. Карта источников: заголовки обычным, термины жирным
6. Без точки в title, без пустых строк после заголовков
7. Без конструкций `🏛️ **-** текст **.**`

### Постобработка (P3)

| # | Файл | Что добавлено | Статус |
|---|------|--------------|--------|
| P3 | `md_telegraph.py` | `_postprocess_telegraph_nodes()` — финальная полировка нод | ✅ |

Функция исправляет:
- Пробел перед/после ⏱ в ссылках (`<a>`)
- Двойные пробелы → одинарный
- `«. .»` → `«.»`
- Дефис → тире в ссылках на Писание
- Unicode-пробелы и bidi-изоляторы
- Склеивание последовательных текстовых нод

---

## II. ЧТО УЖЕ ПРАВИЛЬНО В КОДЕ (не трогать)

| Компонент | Статус | Почему ОК |
|-----------|--------|-----------|
| `thinking_level="high"` | ✅ | Прописан в `_gemini_text_request` (строка 544) |
| `make_text_config_smart` | ✅ | Корректно убирает `temperature` для 3.x моделей |
| `GEMINI_MODEL=gemini-3.5-flash` | ✅ | Установлен в `.env.example` |
| `_validate_and_fix_timestamps` | ✅ | В `gemini_analyze.py` — удаляет таймкоды > duration |
| `_fix_rtl_in_nodes` | ✅ | Рекурсивно применяет RTL-фикс ко всему дереву нод |
| `_fix_orphaned_bold_markers` | ✅ | Чинит непарные `**` маркеры построчно |
| `_clamp_content_timestamps` | ✅ | Корректирует таймкоды за пределами duration |
| `_TIME_BUDGET = 180` | ✅ | Достаточен для `thinking_level="high"` |
| `CONTENT_TOO_BIG → split` | ✅ | Разбивка длинных страниц при >64KB |

---

## III. ОСТАВШИЕСЯ РЕКОМЕНДАЦИИ (не критичные, но полезные)

### 3.1 `re.DOTALL` в `_HEADING_BOLD_STRIP_RE`

```python
# converters/md_telegraph.py:38
_HEADING_BOLD_STRIP_RE = re.compile(r'^\*\*(?!\*)(.*?)(?<!\*)\*\*$', re.DOTALL)
```

**Проблема:** `re.DOTALL` позволяет `.` совпадать с `\n`. Если Gemini вернёт мультистрочный заголовок `**Title\nLine2**`, regex поймает обе строки как один title → Telegraph получит заголовок с переносом.

**Рекомендация:** Убрать `re.DOTALL`:
```python
_HEADING_BOLD_STRIP_RE = re.compile(r'^\*\*(?!\*)(.*?)(?<!\*)\*\*$')
```

### 3.2 Thinking tokens logging

В `_gemini_text_request` нет логирования thinking tokens. Это мешает диагностике:

```python
# После успешного resp.text:
meta = getattr(resp, 'usage_metadata', None)
if meta:
    logger.info(
        "Gemini tokens: prompt=%s thoughts=%s output=%s",
        getattr(meta, 'prompt_token_count', '?'),
        getattr(meta, 'thoughts_token_count', '?'),
        getattr(meta, 'candidates_token_count', '?'),
    )
```

### 3.3 Timestamp drift в длинных аудио

**Проблема:** Gemini демонстрирует прогрессивный дрейф таймкодов при обработке длинных аудиофайлов (60+ минут). К середине/концу записи расхождение может достигать десятков секунд.

**Частичное решение в промпте:**
```
Таймкоды давай с точностью до 30 секунд (не до секунды).
Точность важнее ложной точности.
Если не уверен — ставь более ранний момент, не более поздний.
```

**Системное решение (будущее):** Whisper (`faster-whisper` уже в requirements) как источник точных таймкодов → калибровка Gemini-таймкодов по Whisper-сегментам.

### 3.4 `finish_reason` при MAX_TOKENS

В `_gemini_text_request` finish_reason логируется при пустом ответе, но нет специальной обработки `MAX_TOKENS`. При обрезании JSON уже срабатывает retry, но без увеличения лимита (теперь исправлено P1-3).

**Дополнительная возможность:** Continuation strategy — вместо полного retry при MAX_TOKENS, передать обрезанный ответ обратно модели с просьбой продолжить. Снижает потребление квоты.

### 3.5 Inline upload порог

```python
# services/gemini_analyze.py:166
audio_bytes = None  # AUDIT FIX: всегда upload
```

Inline payload limit вырос с 20MB до 100MB (январь 2026). Текущий код всегда использует Files API upload, что безопасно, но добавляет задержку на polling. Для небольших файлов (<50MB) inline может быть быстрее.

---

## IV. АУДИО ТОКЕНЫ — расчёт для планирования

Gemini: 1 секунда аудио = 32 токена. 1 минута = 1920 токенов.

| Длительность | Аудио токены | + промпт (~2K) | Итого input | Остаток в 1M ctx |
|---|---|---|---|---|
| 30 мин | 57,600 | 59,600 | 59,600 | ~940K |
| 60 мин | 115,200 | 117,200 | 117,200 | ~883K |
| 90 мин | 172,800 | 174,800 | 174,800 | ~825K |
| 120 мин | 230,400 | 232,400 | 232,400 | ~768K |

Максимальная поддерживаемая длина: 9.5 часов. Проблема не в input, а в output (thinking + конспект).

---

## V. РУССКИЙ ТЕКСТ — особенности токенизации

Русский/кириллица: 1 токен ≈ 0.5–0.6 слова (против 0.75 для английского).

Практическое значение: для конспекта на 5000 слов нужно ~9000–10000 токенов output. При `thinking_level="high"` thinking может занять 2-3× output → реальная потребность: 25K–35K max_output_tokens для StudyAnalysis.

Текущие значения (32K/24K) — достаточны для большинства материалов.

---

## VI. АРХИТЕКТУРА ПОТОКА ДАННЫХ

```
MP3/URL
  │
  ├─ [gemini_analyze.py] ──────────────────────────────────
  │   upload → Files API (polling PROCESSING→ACTIVE)
  │   generate_content → thinking_level="high"
  │   resp.text → try/except ✅
  │   _parse_gemini_response → parsed_data
  │   _validate_and_fix_timestamps ✅
  │   return (parsed_data, client, audio_part)
  │
  ├─ [telegraph.py] ────────────────────────────────────────
  │   create_telegraph_synopsis()
  │   _section_to_nodes_v2() → 7 этапов нормализации ✅
  │   _postprocess_telegraph_nodes() → финальная полировка ✅
  │   Telegraph createPage
  │   return (url, outline)
  │
  └─ [telegraph_pages.py] ─────────────────────────────────
      create_telegraph_study_analysis()     max_tokens=32K ✅
      create_telegraph_reflection_application() max_tokens=24K ✅
      │
      └─ _run_expanded_pipeline()
          _gemini_text_request() → thinking_level="high" ✅
          resp.text → try/except ✅
          _parse_expanded_json() → keep \u200e/\u200f ✅
          retry → escalate max_tokens ✅
          _publish_expanded_page()
          │   _section_to_nodes_v2()
          │   _postprocess_telegraph_nodes() ✅
          └─ Telegraph createPage
```

---

## VII. ФАЙЛЫ ПРОЕКТА — что есть и зачем

### Документация (в корне)
| Файл | Назначение | Актуальность |
|------|-----------|-------------|
| `README.md` | Общее описание бота | ✅ |
| `TELEGRAPH_RULES.md` | Правила форматирования для агентов | ✅ Создан в патче |
| `Conspect Audit Info.md` | Этот файл — технический аудит | ✅ |
| `deep_prompt_upgrade_notes.md` | Заметки по улучшению промптов | Справочный |

### Удалить / архивировать
| Файл | Причина |
|------|---------|
| `DeepSeenPatch.md` | Устаревший план DeepSeek интеграции, не реализован |
| `VARIANT_B_CHANGES.md` | Исторический, не актуален |
| `COMMIT_MESSAGE.txt` | Одноразовый, уже использован |
| `apply_conspect_quality_patch.py` | Одноразовый, уже применён |
| `bot.py` | Старый монолит, заменён модульной архитектурой |
| `bot_new.py` | Промежуточная версия, не используется |

---

## VIII. ЧЕКЛИСТ ДЛЯ БУДУЩИХ ИЗМЕНЕНИЙ

### При изменении промптов
- [ ] Проверить что правила форматирования (§P2-6) на месте
- [ ] Проверить что JSON-шаблон outline+sections не сломан
- [ ] Тест: сгенерировать конспект, проверить через `telegraph_validator.py`

### При обновлении Gemini SDK
- [ ] Проверить совместимость `thinking_level` API
- [ ] Проверить `resp.text` поведение (ValueError?)
- [ ] Проверить `usage_metadata` поля

### При изменении md_telegraph.py
- [ ] `_postprocess_telegraph_nodes()` должен быть последним в цепочке
- [ ] Проверить что `_fix_rtl_in_nodes()` не конфликтует
- [ ] Синтаксис: `python3 -c "import ast; ast.parse(open('converters/md_telegraph.py').read())"`

---

*Документ составлен 24 мая 2026. Основан на аудите кодовой базы mp3telegrambot, анализе скриншотов реальных конспектов, и верификации через Telegraph API.*
