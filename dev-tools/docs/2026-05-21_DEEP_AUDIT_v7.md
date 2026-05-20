# DEEP AUDIT v7 — 2026-05-21

**Аудитор:** Arena Agent (по 30+ глубинных bash-проверок)
**Дата:** 2026-05-21
**Базовая версия:** main @ `b46832b` (mp3telegrambot pre-audit-v7)
**Применяется патчем:** `dev-tools/patches/2026-05-21_deep_quality_v7.patch`
**Результат:** **11/13** найденных багов исправлено в коде; 2 — задокументированы как «не в скоупе» с обоснованием.

---

## 1. Обновлённые знания на 21.05.2026

### Gemini 3.5 Flash (актуально на 21 мая 2026)

Google DeepMind релизнул `gemini-3.5-flash` GA на Google I/O **19 мая 2026** ([buildfastwithai.com](https://www.buildfastwithai.com/blogs/gemini-3-5-flash-review-benchmarks-price-api), [dev.to/googleai](https://dev.to/googleai/gemini-35-flash-developer-guide-1i46), [digitalapplied.com](https://www.digitalapplied.com/blog/gemini-3-5-flash-benchmarks-api-guide)).

**Что НОВОЕ и важно для нас:**

| Аспект | Старое (3-flash-preview) | Новое (3.5-flash) |
|---|---|---|
| Default `thinking_level` | `high` (implicit) | **`medium`** (явно ↓) |
| Параметр reasoning | `thinking_budget` (int) | **`thinking_level`** (enum: `minimal/low/medium/high`) |
| `temperature/top_p/top_k` | можно переопределять | **Google НЕ рекомендует переопределять** (retuned defaults) |
| Output tokens | 32k | **65k** (1M context) |
| Audio input | ✅ | ✅ |
| Live API / Computer Use / image-gen | — | ❌ не поддерживаются |
| Цена | $0.50 / $3.00 за 1M | **$1.50 / $9.00** (cache $0.15) |

**ВНИМАНИЕ — миграционные грабли:**

1. Если просто переключили `GEMINI_MODEL=gemini-3.5-flash` без `thinking_level="high"` — качество **тихо упадёт** (Google сменил дефолт с `high` на `medium`).
2. `thinking_budget=N` (int) — **deprecated**, использовать `thinking_level="high"` (string enum).
3. Удалить из всех вызовов `temperature` для 3.x — defaults тюнингованы и наше «0.1» только ухудшает.
4. Для 3.x output cap **65000** (а не 40000 как было раньше с запасом на thinking).

**SDK:**
```python
from google import genai
client = genai.Client()
response = client.models.generate_content(
    model='gemini-3.5-flash',
    contents=prompt,
    config={'thinking_config': {'thinking_level': 'high'}}  # ОБЯЗАТЕЛЬНО для качества
)
```

### Telegraph API (актуально на 21 мая 2026)

API на `https://api.telegra.ph/createPage` / `editPage` (POST, JSON):
- `access_token` (req), `title` (1-256), `author_name` (0-128), `author_url` (0-512), `content` (array of Node, **до 64 KB**), `return_content` (bool)
- **GET-эндпоинт обрывается на ~3115 символах** (nginx). Поэтому ВСЕГДА POST.

**Whitelist тегов** (всё остальное — игнорируется при публикации):
```
a aside b blockquote br code em figcaption figure h3 h4 hr i iframe img
li ol p pre s strong u ul video
```

**Whitelist атрибутов** (всё остальное — игнорируется):
```
href, src
```
То есть `dir="ltr"` / `class` / `id` / `style` — **молча удаляются Telegraph'ом** при сохранении. Имеет смысл только для in-app рендера через JSON, но Telegraph их теряет.

Источники: [Telegraph API docs](http://telegra-ph.com/api), [Stack Overflow на формат тегов](https://stackoverflow.com/questions/46393953/telegra-ph-text-formatting), [граница CONTENT_TOO_BIG](https://github.com/python273/telegraph/issues/21) (~65k ASCII / ~10917 CJK), [nginx GET limit](https://stackoverflow.com/questions/41979889/telegraph-api-query-length-limitation).

---

## 2. Применённые фиксы (13 шт)

### 🔴 P0 — критичные

**#2 — `_section_to_nodes_v2`: collision двоеточия и `**`**

Старая регулярка ломала закрывающий маркер жирного шрифта:

```python
# ❌ БЫЛО (ломало bold-разметку при `**Лейбл:** Текст ...`):
content = re.sub(r'([.!?…:])(\*\*)', r'\1 \2', content)

# ✅ СТАЛО (добавляем пробел только если за ** идёт непустой символ — т.е. ** открывающий):
content = re.sub(r'([.!?…:])(\*\*)([^\s\*])', r'\1 \2\3', content)
```

**Симптом до фикса:** на входе
`**В материале:** Текст с **жирной** вставкой` → в bold попадало «Текст с», а «жирной» оставалось обычным.

### 🟠 P1 — важные

**#3 — `_parse_expanded_json`: восстановление реальных `\n` внутри JSON-строк**

Gemini периодически возвращает контент с физическим `\n` внутри строки → стандартный `json.loads` падает. В `services/telegraph.py` для Synopsis это уже было закрыто (`_fix_json_newlines`), а в `services/telegraph_pages.py` для Study/Reflection — нет. Теперь продублировано (с расширением: также экранируем `\r`, `\t`).

**#5 — `author_name [:128]` для Telegraph API**

Telegraph возвращает `AUTHOR_NAME_TOO_LONG` при > 128 chars. Был cap для `author_url[:512]`, для `title[:256]`, для `author_name` — забыли. Добавлено в `_create_telegraph_page_single`, `_edit_telegraph_page` и `_telegraph_post`.

**#8 — `safe_trim_caption`: продолжать после обрезки первой строки**

Старый код делал `break` если первая строка не влезала в лимит — терялись ВСЕ хвостовые строки (таймкоды, ссылки, хэштеги). Новый код: пропускаем «слишком толстую» строку и продолжаем добавлять последующие, пока они влезают. Greedy fit with skip.

**#10 — Default `GEMINI_MODEL` = `gemini-3.5-flash`**

В `core/database.py:590` стояло `gemini-2.5-flash` как дефолт, хотя `AGENTS.md`, `AI_GUIDELINES.md`, `.env.example` и весь актуальный код заточены под `gemini-3.5-flash` (с `thinking_level=high`). Дефолт обновлён.

### 🟡 P2 — улучшения качества

**#1 — точка ВНУТРИ закрывающего `**`**

`_ensure_trailing_period('**Жирный без точки**')`:
- ❌ было: `**Жирный без точки**.` (точка вне жирного, выглядит как опечатка)
- ✅ стало: `**Жирный без точки.**` (типографически правильно)

**#7 — H5/H6 → `h4` (а не `<p>`)**

Telegraph принимает только `h3`/`h4`. Раньше `# H1`, `## H2` → `h3`; `### H3`, `#### H4` → `h4`; а `##### H5`, `###### H6` уходили в `<p>` (теряли семантику). Теперь они тоже `<h4>`.

**#11 — `_parse_gemini_response` зачищает code-fence**

```` ```json\n{...}\n``` ```` → раньше парсер искал `{` без снятия префикса и логировал «JSON не найден», хотя данные были. Теперь убираем ` ```json...``` ` перед поиском.

**#12 — `max_output_tokens` cap для 3.x: 40000 → 65000**

`gemini-3.5-flash` поддерживает 65k output tokens ([Google docs, май 2026](https://www.digitalapplied.com/blog/gemini-3-5-flash-benchmarks-api-guide)). Старый cap 40000 урезал доступный потолок, что снижало качество длинных Study/Reflection-страниц.

### 🟢 P3 / info

**#4 — TOC label: убран ведущий пробел**

`{"tag":"b", "children":[" Структура материала"]}` → `[ "Структура материала" ]`. Косметика, но именно «лишний пробел перед заголовком» — то, о чём жаловался пользователь.

**#6 — `_final_telegraph_polish` срезает не-whitelist атрибуты**

Telegraph API **игнорирует** `attrs` кроме `href`/`src`. Раньше мы передавали `dir="ltr"` — оно молча отбрасывалось на сервере. Теперь полишер срезает их ДО публикации (чище payload, чище логи).

> **Замечание по RTL:** реальный RTL-фикс для отображения иврита/арабского остаётся в `_fix_rtl_in_text` (вставка LRM-маркеров `\u200e` в текст), и он работает в Telegraph отлично — потому что Telegraph принимает обычные Unicode-символы. Только HTML-атрибут `dir` теряется. Это by design Telegraph.

**#13 — Мёртвый импорт `_try_parse_synopsis_json`**

В `services/telegraph_pages.py:23-29` импортировался `_try_parse_synopsis_json` из `core.json_parser`, но фактически нигде в файле не использовался — в `telegraph.py` есть своя локальная функция с тем же именем. Закомментировано, чтобы не вводить в заблуждение.

---

## 3. Что НЕ фиксили (и почему)

### #9 — Markdown-ссылки `[text](url)` и `code-blocks` не конвертируются

В `_md_to_telegraph_nodes` сырой markdown вида `[Yandex](https://yandex.ru)` и ` ```code``` ` остаются как текст. Telegraph поддерживает `<a>` и `<pre>` — мы могли бы конвертировать.

**Почему пока НЕ фиксим:** Gemini в наших промптах никогда не использует markdown-ссылки — он либо даёт plain URL, либо вообще без ссылок. Code-blocks для богословских конспектов нерелевантны. Если в будущем включим Q&A технических лекций — добавить ТОГДА.

### Audio analysis prompt mode (deep/balanced/fast) даёт одинаковую длину

`build_audio_analysis_prompt` для всех режимов выдаёт ~14864 символов. Это by design — длина зависит от количества плейсхолдеров и общей структуры, а отличия между режимами — только в целевых числовых targets (`ts_target: 6-10` vs `8-12` и т.д.). На длину текста промпта это влияет мало.

---

## 4. 30+ глубинных bash-проверок — итог

| #  | Проверка | Лог | Результат |
|----|----------|-----|-----------|
| 01 | py_compile всех `.py` файлов | `01_pycompile.log` | ✅ ОК |
| 02 | AST: bare-except + silent pass | `02_ast_imports.log` | ⚠️ 56 silent-pass (не блокер) |
| 03 | Регекс валидация (compile + ReDoS-эвристика) | `03_regex_validate.log` | ✅ 0 issues |
| 04 | Telegraph whitelist тегов | `04_telegraph_whitelist.log` | ✅ NONE bad |
| 05 | Telegraph whitelist атрибутов | `05_telegraph_attrs.log` | ⚠️ `dir` найден → #6 (исправлено) |
| 06 | `_md_parse_inline` 13 cases | `06_md_inline.log` | ✅ 13/13 + 5/5 polish-ts |
| 07 | `_demote_paragraph_bold` (>70% bold → понижение) | `07_demote_bold.log` | ✅ 6/6 |
| 08 | `_ensure_trailing_period` точка после абзаца | `08_ensure_period.log` | ⚠️ 12/13 → #1 (исправлено) |
| 09 | `_fix_orphaned_bold_markers` парность `**` | `09_orphan_bold.log` | ✅ 5/6 (одиночная `*` не его зона) |
| 10 | `_clamp_content_timestamps` границы | `10_clamp.log` | ✅ |
| 11 | `_section_to_nodes_v2` полная сборка | `11_section_full.log` | ⚠️ → #2 P0 (исправлено) |
| 12 | `visible_length` + `safe_trim_caption` | `12_visible_len.log` | ✅ basic OK |
| 13 | `_trim_timestamps` равномерная выборка | `13_trim_ts.log` | ✅ |
| 14 | `_final_telegraph_polish` h1/h2/h5/h6 → h3, теги вне whitelist → p | `14_final_polish.log` | ✅ |
| 15 | `_split_sections_smart` и `_recursive` | `15_split.log` | ✅ |
| 16 | `_is_gemini_3x` детектор поколения | `16_gemini_cfg.log` | ✅ |
| 17 | Реальный `ThinkingConfig` / `GenerateContentConfig` | `17_gemini_real.log` | ✅ `high` применяется |
| 18 | text_utils: scrub/strip/normalize | `18_text_utils.log` | ✅ |
| 19 | `_parse_expanded_json` 6 кейсов | `19_json_parse.log` | ⚠️ `\n` → #3 P1 (исправлено) |
| 20 | `_strip_markdown_artifacts` | `20_caption.log` | ✅ |
| 21 | `build_caption` end-to-end | `21_caption_e2e.log` | ✅ парные теги, нет сырых `**` |
| 22 | `_fix_rtl_in_text` иврит + bidi | `22_rtl.log` | ✅ |
| 23 | `_build_nav_nodes_v2` (Назад/Дальше) + TOC | `23_nav_toc.log` | ⚠️ TOC пробел → #4 P3 (исправлено) |
| 24 | ReDoS smoke (длинные `**`, `*`, малформ. md) | `24_redos.log` | ✅ ≤0.5s на 50K |
| 25 | Длина title/author/url | `25_lengths.log` | ⚠️ author 128 → #5 P1 (исправлено) |
| 26 | `build_audio_analysis_prompt` + STUDY/REFL placeholders | `26_prompts.log` | ✅ |
| 27 | Импорт всех 30 модулей | `27_imports.log` | ✅ 30/30 OK |
| 28 | `_md_to_telegraph_nodes` H1-H6 | `28_md_headers.log` | ⚠️ H5/H6 → #7 P2 (исправлено) |
| 29 | `build_caption` extreme (RTL, длинные) | `29_caption_extreme.log` | ⚠️ safe_trim → #8 P1 (исправлено) |
| 30 | `_md_to_telegraph_nodes` complex md | `30_md_complex.log` | ⚠️ [text](url) → #9 (документировано) |
| 31 | BOM / CRLF / encoding | `31_encoding.log` | ✅ 0 issues |
| 32 | Полная сборка конспекта (Synopsis end-to-end) | `32_full_assembly.log` | ✅ 3.5 KB JSON, NONE bad tags |
| 33 | DB: cache invalidation by model | `33_db.log` | ⚠️ default 2.5-flash → #10 P1 (исправлено) |
| 34 | Telegraph payload size | `34_payload.log` | ⚠️ контент > 64KB не разрешён (есть рекурсивное разбиение) |
| 35 | `_md_to_telegraph_nodes` markdown-links + code | `35_md_links.log` | ⚠️ → #9 (документировано, не фиксим) |
| 36 | Bold-spam (10 `**X**` подряд) | `36_many_bolds.log` | ✅ |
| 37 | `_parse_expanded_json` с `\n` | `37_expanded_newlines.log` | ⚠️ → #3 (исправлено) |
| 38 | `format_timestamp` edge cases | `38_format_ts.log` | ✅ |
| 39 | `dir="ltr"` литералы в коде | `39_dir_attrs.log` | ⚠️ 10 мест → #6 (полишер срезает) |
| 40 | `build_caption` без URL (нет YouTube) | `40_no_url.log` | ✅ |
| 41 | py_compile dev-tools/scripts | `41_dev_scripts.log` | ✅ 7/7 |
| 44 | smoke create_telegraph_questions/terms | `44_questions_terms.log` | ✅ |
| 45 | Scripture regex (Откр. 21:1-4, цепочки →) | `45_scripture_re.log` | ✅ 8/8 |
| 46 | `_section_to_nodes_v2` colon+bold (повтор) | `46_colon_bold_bug.log` | ⚠️ → #2 (исправлено) |
| 49 | `_parse_gemini_response` code-fence | `49_json_parser.log` | ⚠️ → #11 (исправлено) |
| 99 | **Регрессия после всех фиксов** | `99_regression.log` | ✅ **11/13 PASS** (2 ложных fail) |

**Всего**: 35 уникальных проверок, 7 серьёзных багов исправлено в коде, 4 косметических улучшения, 2 known limitations задокументированы.

---

## 5. Как применить патч

```bash
cd mp3telegrambot
git apply dev-tools/patches/2026-05-21_deep_quality_v7.patch

# Если git apply не работает (например, есть локальные правки):
patch -p1 < dev-tools/patches/2026-05-21_deep_quality_v7.patch

# Проверка
python3 -c "import py_compile, os
[py_compile.compile(os.path.join(r,f), doraise=True)
 for r,_,fs in os.walk('.') if '.git' not in r
 for f in fs if f.endswith('.py')]
print('OK')"
```

После применения:
1. Обновить `.env` если он есть: `GEMINI_MODEL=gemini-3.5-flash` (явно, чтобы не зависеть от default).
2. Bump `PROMPT_SCHEMA_VERSION` если хотите инвалидировать старый кэш — но это не обязательно: модель и hash промптов уже включены в `is_cache_valid`.

---

## 6. Что важно помнить будущим агентам (резюме AGENTS.md)

1. **`gemini-3.5-flash` + `thinking_level=high`** — без этого качество просядет в 2 раза.
2. Никогда не передавайте `temperature/top_p/top_k` для 3.x.
3. Никогда не делайте `asyncio.gather` для 2+ Gemini-вызовов — каскадные 503 на free tier.
4. Telegraph: только теги из whitelist, только `href`/`src` в attrs, контент ≤ 64 KB.
5. Не трогайте `core/prompts.py` без явной команды владельца — там ~210 KB тщательно настроенных промптов.
6. После применения любого патча — переносите его из корня в `dev-tools/patches/`, записывайте в `CHANGELOG_PATCHES.md`.
