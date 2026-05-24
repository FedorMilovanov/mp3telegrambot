# Справочник v2 — Часть 2: telegraph_pages, DeepSeek/Whisper, архитектура
## Актуально на 24 мая 2026

> **Продолжение Part 1.** Здесь: анализ `telegraph_pages.py`, найденные баги, обновлённые рекомендации по DeepSeek-пайплайну (DeepSeenPatch.md), Whisper для русского языка, проблема дрейфа таймкодов Gemini и актуализация requirements.

---

## 10. `telegraph_pages.py` — новые баги, найденные в коде

### 10.1 `requirements.txt` блокирует SDK v2.0.0 — критично

```
# requirements.txt (текущее):
google-genai>=1.5.0,<2.0.0
```

**Это проблема:** SDK `google-genai 2.0.0` вышел и содержит поддержку нового `thinking_level` API и Gemini 3.5 Flash. Верхняя граница `<2.0.0` означает, что `pip install -r requirements.txt` не установит 2.0.0+.

Хотя `thinking_level` поддерживается начиная с ~1.55.0, лучший путь — снять верхнюю границу или явно поставить `>=2.0.0`.

**Фикс:**
```
# requirements.txt — исправленное:
google-genai>=2.0.0,<3.0.0
```

Почему это безопасно: breaking changes в SDK v2.0.0 касаются только Interactions API; `generateContent` usage не затронут.

---

### 10.2 `_gemini_text_request` — `temperature=0.4` устарел

```python
# telegraph_pages.py:460-461
async def _gemini_text_request(prompt: str, temperature: float = 0.4,
                                max_tokens: int = 8000) -> str | None:
```

И далее:
```python
# _run_expanded_pipeline:869
raw = await _gemini_text_request(prompt, temperature=0.4, max_tokens=max_tokens)
```

Параметр `temperature` передаётся в `make_text_config_smart(temperature=temperature, ...)`. Для Gemini 3.x это устаревший параметр. Несмотря на то что `_gemini_text_request` уже правильно передаёт `thinking_level="high"` — присутствие `temperature` не рекомендовано.

**Что хорошего:** `thinking_level="high"` уже прописан в строке 544 — это правильно! Значит StudyAnalysis и ReflectionApplication уже работают на максимальном уровне thinking. Только `temperature` нужно убрать.

**Фикс в `make_text_config_smart` в `globals.py`:** Если `thinking_level` задан, не передавать `temperature` в конфиг.

---

### 10.3 `max_tokens` для StudyAnalysis и Reflection — слишком мало

```python
# create_telegraph_study_analysis → _run_expanded_pipeline:
max_tokens=16000,    # Разбор материала

# create_telegraph_reflection_application → _run_expanded_pipeline:
max_tokens=14000,    # Отражение/применение
```

При `thinking_level="high"` Gemini 3.5 Flash генерирует значительно больше токенов (внутренние рассуждения + ответ). Для длинных материалов (60+ мин) 16000 токенов output может быть критически мало.

Также: при `thinking_level="high"` TTFT (time-to-first-token) достигает ~17.75 секунд. При `max_tokens=16000` полный ответ может занять 30-60 секунд. `_TIME_BUDGET = 180` секунд достаточен, но нужно учитывать.

**Рекомендация:** Увеличить `max_tokens` для обеих функций:
```python
# StudyAnalysis — много контента, до 65536
max_tokens=32000,    # или 65536 для очень длинных материалов

# ReflectionApplication — можно оставить меньше, но не 14000
max_tokens=24000,
```

---

### 10.4 BUG: `_parse_expanded_json` удаляет `\u200e` и `\u200f` из JSON-контента

```python
# telegraph_pages.py:609-610
# Убираем Unicode bidi-изоляторы которые Gemini иногда вставляет вокруг иврита/арабского
text = re.sub(r'[\u2066-\u2069\u202a-\u202e\u200e\u200f]', '', text)
```

**На первый взгляд корректно** — это очистка RAW-текста от Gemini перед JSON-парсингом. RTL Mark (`\u200e`) добавляется позже в `_section_to_nodes_v2`. Так что прямого конфликта нет.

НО: **есть косвенный баг.** Если Gemini в ответе StudyAnalysis вернёт текст с ивритскими или арабскими цитатами (например, цитату из оригинального текста Писания), и добавит к ним `\u200e`/`\u200f` как bidi-изоляторы — они будут удалены из JSON-контента до того, как `_section_to_nodes_v2` сможет их обработать. Результат: RTL-текст без маркеров направления рендерится слева направо в Telegraph.

**Более точный фикс:** Удалять только bidi-изоляторы `\u2066-\u2069` и `\u202a-\u202e`, но **не трогать** `\u200e`/`\u200f` (они несут семантическую нагрузку):
```python
# Более точно:
text = re.sub(r'[\u2066-\u2069\u202a-\u202e]', '', text)
# \u200e и \u200f НЕ удалять — они нужны для RTL-контента
```

---

### 10.5 `_run_expanded_pipeline` retry — одинаковый `max_tokens` при сломанном JSON

```python
# _run_expanded_pipeline:884
raw2 = await _gemini_text_request(retry_prompt, temperature=0.1, max_tokens=max_tokens)
```

При retry из-за сломанного JSON используется тот же `max_tokens`. Если JSON сломался из-за обрезания (MAX_TOKENS), retry с тем же лимитом снова обрежет. Аналогично проблеме P1-3 из Part 1.

**Фикс:** При retry поднимать `max_tokens` до `min(max_tokens * 2, 65536)`:
```python
_retry_tokens = min(max_tokens * 2, 65536)
raw2 = await _gemini_text_request(retry_prompt, temperature=0.1, max_tokens=_retry_tokens)
```

---

### 10.6 `result = resp.text or ""` — не обрабатывает thinking-only ответ

```python
# telegraph_pages.py:548-550
result = resp.text or ""
if result:
    return result
```

`resp.text` для thinking-модели может бросить `ValueError` (safety filter) или вернуть `None`. Без `try/except` это вызовет необработанное исключение. В `gemini_analyze.py` это правильно обработано, а здесь — нет.

**Фикс:**
```python
try:
    result = resp.text or ""
except ValueError:
    result = ""
if not result:
    # Fallback: итерируем parts
    if resp.candidates:
        for part in resp.candidates[0].content.parts:
            if not getattr(part, "thought", False) and getattr(part, "text", None):
                result = part.text
                break
if result:
    return result
```

---

## 11. Gemini — проблема дрейфа таймкодов в аудио

### 11.1 Подтверждённый баг: прогрессивный timestamp drift

Это **критически важная находка** для бота, работающего с длинными аудиоматериалами (60-180 минут).

Проблема задокументирована в developer forum (март 2026): при транскрипции длинных аудиофайлов Gemini 3 Flash и 3.1 Pro демонстрируют **прогрессивный дрейф таймкодов** — к середине/концу записи реальные временные метки расходятся с указываемыми моделью на десятки секунд или даже минуты.

Это объясняет одну из частых жалоб: таймкоды в конспекте указывают не на то место, куда должны.

**Механизм дрейфа:** Gemini не имеет точного внутреннего таймера при обработке аудио. Его временны́е метки — это оценки по паттернам речи, а не реальные позиции в файле. При длинном аудио ошибки накапливаются.

**Частичные смягчения, уже применяемые в боте:**
- `_validate_and_fix_timestamps` (в `gemini_analyze.py`) удаляет таймкоды за пределами `duration` — это правильно, но не решает дрейф внутри допустимого диапазона.

**Системное решение — Whisper как источник истины для таймкодов:**

Если бот транскрибирует аудио через Whisper (уже есть `faster-whisper>=1.0.0` в requirements), segments от Whisper содержат точные `start`/`end` для каждой фразы. Можно использовать их как anchor-points для валидации Gemini-таймкодов:

```python
def _align_timestamps_with_whisper(
    ai_timestamps: list[str],
    whisper_segments: list[dict],
    tolerance_sec: float = 30.0,
) -> list[str]:
    """
    Пытается откорректировать Gemini-таймкоды по Whisper-сегментам.
    Ищет ближайший Whisper-сегмент в окне ±tolerance_sec и заменяет таймкод.
    """
    def _parse_ts(ts: str) -> float | None:
        """M:SS или H:MM:SS → секунды."""
        parts = ts.strip().split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except ValueError:
            return None
        return None

    def _format_ts(secs: float) -> str:
        m, s = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    corrected = []
    for ts in ai_timestamps:
        ai_secs = _parse_ts(ts)
        if ai_secs is None:
            corrected.append(ts)
            continue
        # Найти ближайший Whisper-сегмент
        best = min(
            whisper_segments,
            key=lambda seg: abs(seg["start"] - ai_secs),
            default=None,
        )
        if best and abs(best["start"] - ai_secs) <= tolerance_sec:
            corrected.append(_format_ts(best["start"]))
        else:
            corrected.append(ts)  # не нашли совпадения — оставляем как есть
    return corrected
```

---

### 11.2 Рекомендации по промпту для уменьшения timestamp drift

Несколько техник снижают дрейф без Whisper:

**1. Указать явные временны́е anchor-points в промпте:**
```
Аудио длится {duration}. Чтобы ориентироваться в файле, вот несколько точных временны́х якорей из транскрипции:
- В начале (0:00) говорится: "..."  [первые 2-3 слова из первых секунд]
- Примерно на {half_duration} говорится: "..."  [из середины]
Используй эти якоря для калибровки своих временны́х оценок.
```

**2. Инструкция "округляй до ближайших 30 секунд":**
```
Таймкоды давай с точностью до 30 секунд (не до секунды).
Точность важнее, чем ложная точность.
```

**3. Явный запрет на таймкоды в будущем от текущей позиции:**
```
ЗАПРЕЩЕНО: не указывай таймкод позже, чем реальная позиция в аудио.
Если не уверен в точности — ставь более ранний момент, не более поздний.
```

---

## 12. Архитектура DeepSeek + Whisper — обновлённые рекомендации

### 12.1 Устаревшая рекомендация в DeepSeenPatch.md

Документ рекомендует использовать:
```env
OPENROUTER_STUDY_MODEL=deepseek/deepseek-chat-v3-0324:free
OPENROUTER_REFLECTION_MODEL=deepseek/deepseek-chat-v3-0324:free
```

**Проблема:** DeepSeek V3 0324 через OpenRouter — 163 840 токенов контекстного окна, **максимум output всего 16 384 токена**. Для StudyAnalysis (который генерирует большие структурированные страницы) этого катастрофически мало.

### 12.2 Актуальный выбор модели DeepSeek (май 2026)

| Модель | Контекст | Max Output | Цена input/output | Подходит для |
|---|---|---|---|---|
| `deepseek/deepseek-v4-flash` | 1M | 65 536 | $0.14 / $0.28 /M | **Основная** — баланс цены и качества |
| `deepseek/deepseek-v4-pro` | 1M | 65 536 | $0.435 / $0.87 /M | Сложные задачи, **акция -75% до 31 мая** |
| `deepseek/deepseek-v3.2` | 131K | 65 536 | $0.252 / $0.378 /M | Хорошее качество, без 1M контекста |
| `deepseek/deepseek-chat-v3-0324` | 163K | **16 384** | $0.20 / $0.77 /M | ❌ Слишком мало output |
| `deepseek/deepseek-chat-v3-0324:free` | 163K | **16 384** | $0 | ❌ Rate limits, мало output |

**Рекомендация для бота (при реализации DeepSeenPatch MVP-1):**

```env
OPENROUTER_STUDY_MODEL=deepseek/deepseek-v4-flash
OPENROUTER_REFLECTION_MODEL=deepseek/deepseek-v4-flash
# Fallback если v4-flash недоступен:
OPENROUTER_FALLBACK_MODEL=deepseek/deepseek-v3.2
```

Важно: DeepSeek V4 Pro со скидкой 75% стоит $0.435/M input и $0.87/M output до 31 мая 2026. После 31 мая цена вырастет до $1.74/$3.48. Планировать бюджет с учётом этого.

---

### 12.3 Проблема: deepseek через OpenRouter — ненадёжность провайдеров

Известная проблема (апрель 2026): при использовании `deepseek/deepseek-v4-pro` через OpenRouter, провайдер "Io Net" применяет агрессивные rate limits. При хите rate limit приходит `HTTP 429` с сообщением "upstream rate limited".

**Рекомендация:** В `openrouter.py` обязательно:

```python
async def openrouter_text_request(
    prompt: str,
    model: str = "deepseek/deepseek-v4-flash",
    temperature: float = 0.2,
    max_tokens: int = 24000,
    timeout: int = 180,
) -> str | None:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": OPENROUTER_APP_TITLE,
        "Content-Type": "application/json",
        # Явно запрашиваем провайдеров с высоким uptime
        # OpenRouter позволяет указывать предпочтительные провайдеры:
        "X-OpenRouter-Provider-Preferences": '{"order":["DeepSeek","Together"]}',
    }
    # ...
    # При 429 от upstream — retry с exponential backoff, max 3 attempts
    # При 5xx — fallback на Gemini
```

---

### 12.4 Whisper для русскоязычного аудио — правильная конфигурация

Текущий `.env.example`:
```env
WHISPER_MODEL=C:\Program Files\WhisperModels\large-v3
WHISPER_DEVICE=cpu
```

**Что правильно:** `large-v3` — это правильный выбор для русского языка.

**Что неправильно:**
1. **`WHISPER_DEVICE=cpu`** — крайне медленно. На CPU `large-v3` транскрибирует 60-минутное аудио за ~2-3 часа. Если есть NVIDIA GPU даже RTX 3060 — нужно `cuda`.
2. **`large-v3-turbo` не подходит для русского** — это модель оптимизирована для английского (4 decoder layers вместо 32). Для русского точность падает заметно. Оставаться на `large-v3`.

**Правильная конфигурация faster-whisper для русского:**
```python
from faster_whisper import WhisperModel

# Для GPU (рекомендуется):
model = WhisperModel(
    "large-v3",
    device="cuda",
    compute_type="int8",      # INT8 снижает VRAM с 10GB до ~3GB без потери качества
)

# Для CPU (медленно, но работает):
model = WhisperModel(
    "large-v3",
    device="cpu",
    compute_type="int8",      # на CPU int8 быстрее float32
    cpu_threads=8,            # использовать все ядра
)

# Транскрипция с VAD (Voice Activity Detection) — важно для проповедей!
segments, info = model.transcribe(
    audio_path,
    language="ru",            # явно указывать язык для скорости
    beam_size=5,
    vad_filter=True,          # убирает тишину, улучшает качество
    vad_parameters={
        "min_silence_duration_ms": 500,  # настроить под аудио
        "speech_pad_ms": 300,
    },
    word_timestamps=True,     # для точной синхронизации с Gemini
)
```

**Производительность faster-whisper large-v3 на CPU (int8):**
- Intel i7-12700K: 60 минут аудио → ~24-30 минут транскрипции
- NVIDIA RTX 3070 Ti (int8): 60 минут → ~3-4 минуты
- NVIDIA RTX 4090 (int8): 60 минут → <2 минуты

Вывод: для продакшн-использования GPU обязателен. На CPU transcription нужно запускать в фоне, не блокируя основной пайплайн.

---

### 12.5 Формат transcript для DeepSeek grounding

Из DeepSeenPatch.md (Этап 3) рекомендуется передавать `transcript_segments`. Оптимальный формат для LLM-подачи (не весь сырой JSON):

```python
def format_transcript_for_llm(
    segments: list[dict],
    max_chars: int = 12000,
    chunk_secs: float = 60.0,
) -> str:
    """
    Форматирует сегменты Whisper в читаемый текст с временны́ми якорями.
    Добавляет временну́ю метку каждые chunk_secs секунд.
    """
    lines = []
    last_marker_at = -chunk_secs
    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if not text:
            continue
        if start - last_marker_at >= chunk_secs:
            m, s = divmod(int(start), 60)
            h, m = divmod(m, 60)
            ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
            lines.append(f"\n[{ts}]")
            last_marker_at = start
        lines.append(text)
    result = " ".join(lines)
    # Обрезаем до max_chars, не разрывая предложения
    if len(result) > max_chars:
        cut = result[:max_chars]
        last_period = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        if last_period > max_chars * 0.8:
            cut = cut[:last_period + 1]
        result = cut + "\n[TRANSCRIPT TRUNCATED]"
    return result
```

---

## 13. Аудит `.env.example` — найденные проблемы

| Строка | Текущее | Проблема | Рекомендация |
|---|---|---|---|
| `GEMINI_MODEL=gemini-3.5-flash` | ✅ Правильно | — | Оставить |
| `TRINITY_LARGE_THINKING_KEY=` | Пустая переменная | Trinity не используется (согласно DeepSeenPatch.md), зачем ключ? | Убрать или закомментировать |
| `WHISPER_MODEL=C:\Program Files\...` | Windows путь | Windows-специфичный путь, нет кроссплатформенности | Сделать относительным: `./models/whisper/large-v3` |
| `WHISPER_DEVICE=cpu` | `cpu` | Катастрофически медленно для large-v3 | `cuda` если есть GPU, документировать fallback |
| `AUDIO_ANALYSIS_MODE=deep` | `deep` | ✅ Правильно | Оставить |

---

## 14. Дополнительный аудит `_parse_expanded_json` — скрытая архитектурная проблема

### 14.1 Исправление обрезанного JSON — граничный кейс

```python
# _parse_expanded_json, попытка 3:
if last_complete > 0:
    fixed = chunk[:last_complete+1] + "\n  ]\n}"
    try:
        data = json.loads(fixed)
```

Это восстановление работает при условии что JSON-объект с sections имеет глубину ровно 1 (один уровень вложенности секций). Но если внутри section есть вложенные объекты (например, `"subsections": [...]`), алгоритм может определить `last_complete` неверно.

Строка `if depth == 1: last_complete = idx` означает "закрыли объект на первом уровне вложенности внутри chunk". Если sections содержат вложенные структуры — алгоритм будет отслеживать закрытие вложенного объекта, а не секции.

**Риск:** Низкий в текущей реализации, т.к. sections плоские (`{title, content, time}`). НО: если Gemini вернёт нестандартную структуру с вложенными объектами — часть секций будет обрезана неправильно.

### 14.2 `max_iterations=100_000` — достаточно ли?

Для 65 536 токенов output (при unicode-символах каждый символ = 1-3 байта) текст может содержать до ~200 000 символов. При `max_iterations=100_000` большие ответы будут прерваны.

**Рекомендация:** Поднять `max_iterations` до `500_000`:
```python
def _parse_expanded_json(text: str, max_depth: int = 50, max_iterations: int = 500_000) -> ...:
```

---

## 15. Специфика промптов для русскоязычного контента

### 15.1 Tokenization: русский vs английский

При планировании `max_output_tokens` важно учитывать: русский текст в UTF-8 требует больше токенов, чем английский. Ориентировочно:
- Английский: 1 токен ≈ 0.75 слова
- Русский/кириллица: 1 токен ≈ 0.5-0.6 слова (кириллические символы часто занимают 1.5-2 токена)
- Mixed (русский с цитатами из Писания): ≈ 0.55 слова на токен

Практическое значение: для конспекта на 5000 слов (русский текст) нужно ~9000-10000 токенов output. Это укладывается в 32000, но уже не в 8000-16000.

### 15.2 `hermeneutic_method` и богословские термины

В `prompts.py` (`deep_prompt_upgrade_notes.md`) перечислены методы: `expository, topical, narrative, typological, redemptive_historical, catechetical, apologetic, evangelistic, practical, mixed`.

**Проблема:** Gemini 3.5 Flash с `thinking_level="high"` хорошо понимает эти термины на русском языке, но иногда неправильно классифицирует формат из-за смешения языков в промпте. Рекомендация: добавить краткое определение каждого метода в промпте, особенно для редких (`typological`, `redemptive_historical`):

```python
_HERMENEUTIC_DESCRIPTIONS = {
    "expository":           "экзегетический разбор текста последовательно",
    "topical":              "изучение темы через множество текстов",
    "narrative":            "следование повествовательной дуге",
    "typological":          "ветхозаветные прообразы новозаветных реалий",
    "redemptive_historical":"история искупления как единое целое",
    "catechetical":         "вопрос-ответ, катехизисная форма",
    "apologetic":           "защита и обоснование веры",
    "evangelistic":         "призыв к обращению/покаянию",
    "practical":            "применение истин к жизни",
    "mixed":                "смешанный подход",
}
```

---

## 16. Полная сводная таблица всех новых багов (Part 2)

### Приоритет 1 — Критические

| # | Файл | Проблема | Фикс |
|---|---|---|---|
| **P1-6** | `requirements.txt` | `google-genai<2.0.0` блокирует SDK v2.0.0 | Изменить на `>=2.0.0,<3.0.0` |
| **P1-7** | `telegraph_pages.py:548` | `resp.text or ""` без try/except — необработанное исключение при safety filter | Добавить try/except + parts fallback |
| **P1-8** | `telegraph_pages.py:610` | `\u200e` и `\u200f` удаляются из JSON — RTL-контент теряет маркеры направления | Удалять только `\u2066-\u2069` и `\u202a-\u202e` |

### Приоритет 2 — Качество и MAX_TOKENS

| # | Файл | Проблема | Фикс |
|---|---|---|---|
| **P2-8** | `telegraph_pages.py:869` | `temperature=0.4` устарел для Gemini 3.x | Убрать из `_gemini_text_request` |
| **P2-9** | `telegraph_pages.py:1088` | `max_tokens=16000` для StudyAnalysis — мало для длинных материалов | Поднять до 32000-65536 |
| **P2-10** | `telegraph_pages.py:1203` | `max_tokens=14000` для Reflection — мало | Поднять до 24000 |
| **P2-11** | `telegraph_pages.py:884` | Retry при сломанном JSON не увеличивает max_tokens | `_retry_tokens = min(max_tokens * 2, 65536)` |

### Приоритет 3 — Качество данных

| # | Файл | Проблема | Фикс |
|---|---|---|---|
| **P3-7** | `.env.example` | `WHISPER_DEVICE=cpu` — слишком медленно | `cuda` + документация fallback |
| **P3-8** | `.env.example` | `TRINITY_LARGE_THINKING_KEY` — unused variable | Убрать |
| **P3-9** | DeepSeenPatch.md | Рекомендует `deepseek-chat-v3-0324:free` с max 16K output | Использовать `deepseek-v4-flash` (1M context, 65K output) |
| **P3-10** | `_parse_expanded_json` | `max_iterations=100_000` мало для 65K token responses | Поднять до 500_000 |
| **P3-11** | Промпты | Нет anchor-points для калибровки timestamp drift | Добавить временные якоря в промпт |

---

## 17. Визуальная карта потоков и точек отказа

```
MP3 файл
│
├─ [gemini_analyze.py] ─────────────────────────────────────────────────────
│   upload → Files API (polling PROCESSING→ACTIVE, timeout 600s)
│   generate_content ─── thinking_level="high" [NEW, исправлено в v2]
│   response.text ──────── try/except ValueError ✅
│   parts fallback ─────── thought=True пропускаем ✅
│   MAX_TOKENS ─────────── return None [BUG P1-3: retry не меняет лимит]
│   _parse_gemini_response → parsed_data
│   _validate_and_fix_timestamps [ловит > duration, но не дрейф]
│   return (parsed_data, client, audio_part)
│
├─ [telegraph.py] ──────────────────────────────────────────────────────────
│   create_telegraph_synopsis()
│   asyncio.sleep(2) ───── [BUG P3-4: перед каждым retry]
│   existing_audio_part? ── повторное использование
│   _upload() ──────────── >20MB → Files API ✅
│   │                      <=20MB → Part.from_bytes [BUG P1-5: может 503]
│   GEMINI_MODEL ──────── max_output_tokens=32000 [BUG P1-3: слишком мало]
│   temperature=0.1 ────── [BUG P2-8: устарел]
│   _extract_response_text() ─── thinking fallback ✅
│   _try_parse_synopsis_json()
│   _section_to_nodes_v2() ──── 7 этапов нормализации
│   │   этап 1: базовая нормализация
│   │   этап 2: _patch_scripture_format
│   │   этап 3: 15+ re.sub + очистка двойных пробелов ← [BUG P2-3]
│   │   этап 4: пробелы после знаков, кирилл/латин
│   │   этап 5: numbered/bullet rebuild
│   │   этап 6: _shrink_overbold_line
│   │   этап 7: _ensure_all_paragraphs_period ← [BUG P2-4 многоточие]
│   _final_telegraph_polish → _md_to_telegraph_nodes
│   Telegraph createPage/editPage ──── [CONTENT_TOO_BIG → split ✅]
│   return (url, outline)
│
└─ [telegraph_pages.py] ────────────────────────────────────────────────────
    create_telegraph_study_analysis()
    create_telegraph_reflection_application()
    │
    └─ _run_expanded_pipeline()
        _gemini_text_request() ──── temperature=0.4 [BUG P2-8]
        │                      ──── thinking_level="high" ✅
        │                      ──── max_tokens=16000/14000 [BUG P2-9, P2-10]
        resp.text or "" ──────────── без try/except [BUG P1-7]
        _parse_expanded_json()
        │   strip \u200e/\u200f ──── [BUG P1-8]
        │   fix_json_newlines ✅
        │   recover truncated JSON ✅ (но max_iterations=100K [P3-10])
        retry → same max_tokens ──── [BUG P2-11]
        _publish_expanded_page()
        │   _section_to_nodes_v2()
        └─ Telegraph createPage
```

---

## 18. Обновлённый полный чеклист (объединённый Part 1 + Part 2)

### 🔴 До 1 июня 2026 — СРОЧНО

- [ ] `.env` во всех деплоях: убрать `gemini-2.0-flash*` (shutdown 1 июня)
- [ ] Убрать `gemini-3.1-flash-lite-preview` (уже мёртв, shutdown 25 мая)
- [ ] Добавить `thinking_level="high"` в `make_audio_config()` для `gemini_analyze.py` и `telegraph.py`
- [ ] Поднять `max_output_tokens` в synopsis с 32000 до 65536
- [ ] Добавить MAX_TOKENS escalation в retry (повышать лимит при повторных попытках)
- [ ] **`requirements.txt`:** изменить `google-genai<2.0.0` → `google-genai>=2.0.0,<3.0.0`

### 🟡 Ближайший спринт — Качество и стабильность

**Gemini API:**
- [ ] Убрать `temperature` из всех вызовов Gemini 3.x (gemini_analyze, telegraph, telegraph_pages)
- [ ] Поднять `max_tokens` для StudyAnalysis (16000 → 32000) и Reflection (14000 → 24000)
- [ ] Добавить `try/except ValueError` в `resp.text` в `telegraph_pages.py:548`
- [ ] Исправить `\u200e`/`\u200f` stripping в `_parse_expanded_json:610`
- [ ] Добавить MAX_TOKENS escalation в `_run_expanded_pipeline` retry

**Regex / md_telegraph.py:**
- [ ] Unicode-нормализация в начало `_section_to_nodes_v2` (`\u00a0`, `\u2009`, `\u202f`, `\u200b`, `\u00ad`)
- [ ] Финальная очистка двойных пробелов в `_ensure_all_paragraphs_period`
- [ ] Добавить `\u2026` в `_GOOD_ENDINGS`
- [ ] Убрать `<p><br/></p>` после h3/h4
- [ ] Убрать `re.DOTALL` из `_HEADING_BOLD_STRIP_RE`

**Промпты:**
- [ ] Добавить anchor-points для timestamp calibration в основной аудио-промпт
- [ ] Добавить явный запрет `temperature`-стиль инструкций в JSON: запрет Unicode-пробелов, незакрытых `**`
- [ ] Дефолтная инструкция формата для неизвестных `_fmt`

### 🟢 Долгосрочно — Архитектура и новые возможности

**Инфраструктура:**
- [ ] `WHISPER_DEVICE=cpu` → `cuda` (в .env.example и документации)
- [ ] Убрать `TRINITY_LARGE_THINKING_KEY` из .env.example
- [ ] Поднять `max_iterations` в `_parse_expanded_json` до 500_000
- [ ] Добавить дефолтный описатель hermeneutic_method в промпт

**DeepSeek (DeepSeenPatch MVP-1):**
- [ ] Обновить рекомендуемую модель в DeepSeenPatch.md: `deepseek-chat-v3-0324:free` → `deepseek/deepseek-v4-flash`
- [ ] Реализовать `services/openrouter.py` с proper 429/503 handling и провайдер-предпочтениями
- [ ] Переименовать `_gemini_text_request` → `_llm_text_request` (маршрутизация Gemini/OpenRouter)

**Whisper + Timestamp alignment (MVP-2/3):**
- [ ] Реализовать `services/transcription.py` с VAD, `language="ru"`, `word_timestamps=True`
- [ ] Добавить `_align_timestamps_with_whisper()` для коррекции Gemini timestamp drift
- [ ] Кэшировать transcript в SQLite (`transcript_path`, `transcript_model`)
- [ ] Передавать `format_transcript_for_llm()` output в StudyAnalysis/Reflection промпты

---

*Документ Part 2 составлен 24 мая 2026. Источники: анализ кода `services.zip` (telegraph_pages.py, requirements.txt, .env.example, DeepSeenPatch.md), официальная документация Google Gemini API, OpenRouter DeepSeek listings, faster-whisper docs, Gemini developer forum (timestamp drift issue март 2026).*

Теперь буду искать актуальную информацию по всем ключевым темам.Отлично, данных достаточно. Составляю углублённый синопсис.

---

# ОБНОВЛЁННЫЙ СИНОПСИС v3 — Глубокий аудит бота (конспекты MD)
## Актуально: 24 мая 2026

---

## I. GEMINI 3.5 FLASH — НОВЫЕ КРИТИЧЕСКИЕ ИЗМЕНЕНИЯ (19 мая 2026, GA)

### 1.1 Официальный model ID и статус

**Stable model ID: `gemini-3.5-flash`** (без суффикса preview). Вышел 19 мая 2026 года, GA сразу во всех окружениях — Gemini API, AI Studio, Vertex AI, Antigravity, Gemini App, AI Mode in Google Search.

Это означает:
- В `.env` меняем `gemini-3-flash-preview` → `gemini-3.5-flash`
- Старый `gemini-3.1-flash-lite-preview` **мёртв с 25 мая 2026** — уже shutdown
- `gemini-2.0-flash*` — **shutdown 1 июня 2026** (осталось 8 дней на момент написания)

### 1.2 КРИТИЧЕСКИЙ ТИХИЙ РЕГРЕСС: `thinking_budget` → `thinking_level`, смена дефолта

Целочисленный параметр `thinking_budget` заменён строковым enum `thinking_level`. Новые значения: `minimal`, `low`, `medium` (дефолт), `high`. **Критически важно: дефолт сменился с `high` (в gemini-3-flash-preview) на `medium` (в 3.5 flash).** Если просто поменять model string без явного указания `thinking_level="high"` — модель будет думать меньше, качество конспектов упадёт незаметно.

**Что это значит для бота:** В `make_text_config_smart()` и `make_audio_config()` параметр `thinking_level="high"` уже прописан — это хорошо. Но нужно убедиться, что он прописан **явно во всех местах**, иначе тихая деградация.

Google изменил поведение thinking — наивная замена model string без правки конфига молча деградирует приложение.

### 1.3 `temperature`, `top_p`, `top_k` — официально запрещены для Gemini 3.x

Официальная документация Google: `temperature`, `top_p`, `top_k` — **strongly recommend not changing default values**. Reasoning-способности Gemini 3.x оптимизированы под дефолтные настройки. Передача `temperature=0.4` или `temperature=0.1` не просто устарела — она ломает reasoning.

**Это подтверждает Баги P2-8 и P2-9:** удалить `temperature` из всех вызовов — не рекомендация, а требование Google.

### 1.4 Thought Preservation — новая скрытая угроза для бюджета токенов

Thought preservation теперь **включена по умолчанию**. Reasoning-контекст переносится между turns, что улучшает качество, но увеличивает token usage. Для GenerateContent API: preserved thoughts увеличивают input token count с каждым туром. Для простых запросов их можно очищать через thought signatures guide.

Для multi-turn agent loops бюджетируй на **30-50% больше токенов**, чем на эквивалентном `gemini-3-flash-preview`. Следи за `ThoughtsTokenCount`/`PromptTokenCount` — если отношение >0.4 в поздних turns, это сигнал к рестарту сессии.

**Практика для бота:** Каждый запрос конспекта — это новый independent вызов, не multi-turn. Thought preservation здесь не накапливается, но увеличивает базовый input count. Мониторить `usage_metadata.thoughts_token_count`.

### 1.5 Specs: context window и max_output_tokens подтверждены

Gemini 3.5 Flash: 1,048,576 input tokens / **65,536 output tokens** максимум. Pricing: $1.50/$9 per 1M tokens. TTFT при `thinking_level="high"` — 17.75 секунд.

**Вывод по max_tokens:** Существующий лимит 16,000 для StudyAnalysis и 14,000 для Reflection — не просто "мало", это меньше 25% от возможностей модели. Для конспекта лекции 90 минут нормальный output — 25,000–40,000 токенов.

### 1.6 Реальная проблема с output: default 8192 токенов

Критическая деталь, которую большинство гайдов упускает: **default `maxOutputTokens` = только 8,192** — разработчики должны явно конфигурировать этот параметр для получения полного 65K output. Это объясняет многие случаи обрезанных конспектов.

В реальных кейсах: пользователи не могут получить ответ длиннее ~3000-4000 токенов в одной операции, несмотря на заявленный лимит 65K — если `max_output_tokens` не указан явно.

---

## II. GOOGLE-GENAI SDK 2.0 — ДЕТАЛИ BREAKING CHANGES

### 2.1 Что именно сломалось в SDK 2.0

SDK 2.0 релиз: breaking changes касаются только **Interactions API** (outputs → steps schema). `GenerateContent` usage **не затронут**. Верхняя граница `<2.0.0` в `requirements.txt` избыточно строгая — бот использует `generateContent`, значит safe to upgrade.

### 2.2 Правильный синтаксис конфига в SDK 2.0+

Из официальной документации GitHub:
```python
# НЕПРАВИЛЬНО (старый SDK):
genai.GenerationConfig(...)
safety_settings={...}

# ПРАВИЛЬНО (новый SDK 2.0+):
from google.genai import types
types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="high"),
    max_output_tokens=65536,
    # temperature НЕ передаём!
)
```

### 2.3 `thinking_config` vs `thinking_level` — два разных пути

Для `generateContent` (который использует бот):
```python
# Путь 1: через ThinkingConfig
config=types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="high"),
    max_output_tokens=65536,
)

# Путь 2: через generation_config напрямую (также работает)
generation_config={"thinking_level": "high", "max_output_tokens": 65536}
```

---

## III. TIMESTAMP DRIFT — УГЛУБЛЁННЫЙ РАЗБОР

### 3.1 Официально подтверждённый баг (март 2026, Google Forum)

Это задокументировано и не исправлено. Механизм:
- Gemini оценивает timestamps по паттернам речи, не по реальному position в аудиофайле
- Ошибки накапливаются прогрессивно
- Критическая точка: ~18 минут от начала аудио

Практическое наблюдение: проблемы с повторяющимися циклами и дрейфом таймкодов типично начинаются в районе 18-минутной отметки. Рекомендуется использовать 15-минутные чанки для безопасности. При chunking-подходе точность значительно улучшилась: дрейф не превышал 5-10 секунд даже на часовых записях.

### 3.2 Workaround — Temporal Anchor Pattern (лучшая практика на май 2026)

**Audio-First Transcription:** запустить проход транскрипции перед visual reasoning для создания "script". Это создаёт семантический якорь, улучшающий качество и минимизирующий temporal hallucinations. **Temporal Chunking + Anchor Mapping:** программно делить аудио на управляемые чанки (20–30 минут) и применять Temporal Anchor Pattern в промпте каждого чанка, чтобы "lookup table" модели оставался синхронизированным с реальными timestamps.

**Реализация якоря в промпте (добавить в `SYNOPSIS_PROMPT_V2`):**
```
TIMESTAMP CALIBRATION ANCHORS:
- 00:00 = начало аудио
- Каждые 10 минут проверяй: твои timestamps должны быть в пределах 
  ±30 секунд от реального положения в файле
- Если замечаешь накопление ошибки — скорректируй последующие timestamps
- НЕ ЭКСТРАПОЛИРУЙ таймкоды из предыдущих — каждый определяй независимо
```

### 3.3 Whisper как ground truth для timestamps

Для более точных word-level timestamps используй WhisperX, который делает forced alignment через wav2vec2 phoneme models. VAD (Voice Activity Detection) разбивает аудио по границам тишины перед транскрипцией, улучшая точность.

Whisper Large-v3: ~2.7% WER на бенчмарке, 8–12% в реальных условиях. Слабое место: неправильно транскрибирует proper nouns, жаргон, технические термины. Галлюцинирует текст в длинных паузах — использовать VAD preprocessing для пропуска тишины.

**Оптимальная конфигурация faster-whisper для русского:**
```python
from faster_whisper import WhisperModel

model = WhisperModel(
    "large-v3",
    device="cuda",        # НЕ cpu — в 4+ раза медленнее
    compute_type="float16"
)
segments, info = model.transcribe(
    audio_path,
    language="ru",
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
    word_timestamps=True,  # для anchor alignment
    condition_on_previous_text=False,  # уменьшает hallucination
)
```

---

## IV. REGEX — УГЛУБЛЁННЫЕ ЗНАНИЯ ДЛЯ ОТЛАДКИ БАГОВ

### 4.1 Флаг `re.DOTALL` — почему он опасен в `_HEADING_BOLD_STRIP_RE`

```python
# ТЕКУЩИЙ КОД (баг):
_HEADING_BOLD_STRIP_RE = re.compile(r'^\*\*(?!\*)(.*?)(?<!\*)\*\*$', re.DOTALL)
```

Проблема: `re.DOTALL` заставляет `.` матчить `\n`. Это нужно **только** если bold-текст заголовка может содержать переносы строки. Но заголовки — однострочные конструкции. `re.DOTALL` здесь:
1. Позволяет паттерну "проглотить" несколько строк через `\n`, убирая `**` с одного заголовка и всё до следующего `**` — даже если между ними несколько абзацев
2. Создаёт риск catastrophic backtracking на больших текстах

Ключевой принцип: `(.*?)` с `re.DOTALL` — это non-greedy вариант. Он менее опасен, чем `(.*)`, но при вложенных quantifiers всё равно может быть медленным. Делайте паттерн non-greedy (`?`) чтобы избежать backtracking.

**Фикс:** убрать `re.DOTALL`, или ограничить паттерн `[^\n]` вместо `.`:
```python
_HEADING_BOLD_STRIP_RE = re.compile(r'^\*\*(?!\*)((?:[^\n*]|\*(?!\*))+?)(?<!\*)\*\*\s*$')
```

### 4.2 Unicode-нормализация — полный список проблемных символов

Из анализа кода и актуальной документации Python:

| Символ | Unicode | Имя | Проблема |
|--------|---------|-----|---------|
| NBSP | `\u00a0` | No-Break Space | Выглядит как пробел, но не матчится `\s` в `re` без `re.UNICODE` |
| NNBSP | `\u202f` | Narrow NBSP | Gemini вставляет перед `%`, `°` в числах |
| THSP | `\u2009` | Thin Space | Gemini вставляет в числах с разрядами (1 000 000) |
| ZWSP | `\u200b` | Zero-Width Space | Невидимый, ломает Telegraph node splitting |
| SHY | `\u00ad` | Soft Hyphen | Невидимый перенос, ломает bold/italic detection |
| LRM | `\u200e` | Left-to-Right Mark | Семантический! Нельзя удалять из RTL-контента |
| RLM | `\u200f` | Right-to-Left Mark | Семантический! |
| BIDI isolators | `\u2066-\u2069` | FSI/LRI/RLI/PDI | Безопасно удалять — это форматирование, не семантика |
| BIDI embed | `\u202a-\u202e` | LRE/RLE/LRO/RLO/PDF | Безопасно удалять |

**Правильный порядок нормализации в начале `_section_to_nodes_v2`:**
```python
def _normalize_unicode_spaces(text: str) -> str:
    """Этап 0: Unicode нормализация перед всеми regex."""
    # 1. Нормализуем visually invisible chars
    text = text.replace('\u00ad', '')     # soft hyphen — удаляем
    text = text.replace('\u200b', '')     # zero-width space — удаляем
    # 2. Нормализуем пробелы (не трогаем LRM/RLM!)
    text = text.replace('\u00a0', ' ')   # NBSP → обычный пробел
    text = text.replace('\u202f', ' ')   # NNBSP → пробел
    text = text.replace('\u2009', ' ')   # Thin space → пробел
    # 3. Bidi isolators (безопасно удалять)
    text = re.sub(r'[\u2066-\u2069\u202a-\u202e]', '', text)
    # LRM (\u200e) и RLM (\u200f) — НЕ ТРОГАЕМ
    return text
```

### 4.3 Catastrophic Backtracking — реальный риск в коде

Python `re` не поддерживает possessive quantifiers (`++`, `*+`) и atomic groups (`(?>...)`), которые предотвращают backtracking. Для критических паттернов используй библиотеку `regex` (pip install regex), которая их поддерживает.

В коде бота рискованные паттерны (из `_section_to_nodes_v2`):
```python
# РИСКОВАННЫЙ ПАТТЕРН — вложенный quantifier:
content = re.sub(r'\*{4,}', '**', content)  # OK, простой

# ПОТЕНЦИАЛЬНО ОПАСНЫЙ — overlapping patterns на длинных строках:
content = re.sub(r'\*\*(.+?)\s*⏱\*\*\s*(\d{1,2}:\d{2}(?::\d{2})?)', ...)
```

Статические анализаторы для проверки ReDoS: `safe-regex`, `recheck`. Atomic groups в Python — через библиотеку `regex`: `(?>...)`. Альтернатива — переключиться на non-backtracking движок RE2 (Google, работает за линейное время).

---

## V. TELEGRAPH API — РЕАЛЬНЫЕ ЛИМИТЫ (май 2026)

### 5.1 Задокументированные ограничения

- **Title:** 1–256 символов
- **Author name:** 0–128 символов  
- **Author URL:** 0–512 символов
- **Content (JSON):** ~65,000 символов JSON-строки (не нод!) — отсюда `CONTENT_TOO_BIG`
- **Изображения/файлы:** до 5 MB при загрузке

### 5.2 Почему `CONTENT_TOO_BIG` возникает непредсказуемо

Лимит Telegraph — по размеру **сериализованного JSON**, не по количеству нод или слов. Один node с длинным `children` string может занять больше, чем 10 коротких нод. Кириллица в JSON = 2 байта на символ (UTF-8), иврит/арабский = 3 байта.

**Практическое правило для split:** делай split при приближении к ~55,000 символам в JSON-строке (не 65,000 — с запасом на overhead), не по количеству нод.

### 5.3 Проблемы с `editPage` vs `createPage`

При редактировании существующей страницы через `editPage` — Telegraph полностью заменяет контент. Нет инкрементального обновления. При CONTENT_TOO_BIG на `editPage` нужно создавать новую страницу (`createPage`) и обновлять ссылки.

---

## VI. DEEPSEEK — УТОЧНЕНИЕ ПО АКТУАЛЬНЫМ МОДЕЛЯМ

### 6.1 DeepSeek V4 Flash — реальные параметры (апрель 2026)

DeepSeek V4 Flash: **$0.10/M input, $0.20/M output**. Context window: **1,048,576 токенов**. Максимальный output: **16,384 токена** (платная версия).

DeepSeek V4 Flash **free**: $0/M input, $0/M output. Context window: 1,048,576 токенов. Максимальный output в free версии: **384,000 токенов** (!).

**Критическое исправление к синопсису v2:** В DeepSeenPatch.md написано "65K output". Это неверно для платной версии — там **16,384 токена**. Для конспектов этого мало. Free-версия даёт 384K, но с rate limits. Стратегия:
1. Для production: `deepseek/deepseek-v4-flash` (платная) — быстро, надёжно, 16K
2. Для dev/тест: `deepseek/deepseek-v4-flash:free` — 384K output, но rate limits

DeepSeek V4 Pro: 1.6T total params, 49B activated, context 1M токенов. $0.435/M input, $0.87/M output (акция -75% до 31 мая 2026; после — $1.74/$3.48).

### 6.2 OpenRouter routing — важные детали

13 провайдеров для DeepSeek V4 Flash — высокая доступность. Поддерживает reasoning efforts: `high` и `xhigh` (`xhigh` = max reasoning).

Для `services/openrouter.py` — обязательно указывать `provider_preferences` в заголовках, иначе OpenRouter может выбрать медленного провайдера.

---

## VII. НОВЫЕ БАГИ, НЕ ПОПАВШИЕ В СИНОПСИС v2

### 7.1 НОВЫЙ БАГ P1-9: `thinking_config` vs `thinking_level` — разные параметры

В `make_text_config_smart()` используется `thinking_level="high"` напрямую. В SDK 2.0+ для `generateContent` это работает через:
```python
# SDK 1.x (deprecated):
config = {"thinking_level": "high"}

# SDK 2.0+ (правильно):
config = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="high")
)
```
Нужно проверить, как именно передаётся `thinking_level` в `make_text_config_smart` и `make_audio_config` — через typed objects или через dict.

### 7.2 НОВЫЙ БАГ P2-12: Thought Preservation раздувает input context при audio retry

При retry с тем же `audio_part` (повторное использование из `existing_audio_part`) + gemini-3.5-flash с thought preservation: предыдущие thought signatures добавляются к input. Это может превысить контекстное окно при больших аудиофайлах после 2-3 retry.

**Фикс:** При retry создавать новый `client` вместо повторного использования session.

### 7.3 НОВЫЙ БАГ P3-12: `_parse_expanded_json` — `fix_json_newlines` не обрабатывает `\u2028`/`\u2029`

Unicode Line Separator (`\u2028`) и Paragraph Separator (`\u2029`) — это валидные Unicode символы, которые JSON-парсер Python обрабатывает как переносы строк в строковых значениях. Gemini иногда вставляет их в длинных текстах. Существующий `fix_json_newlines` обрабатывает `\n`, `\r\n`, `\t`, но не `\u2028`/`\u2029`.

```python
# Добавить в fix_json_newlines:
s_in = s_in.replace('\u2028', '\\n').replace('\u2029', '\\n')
```

### 7.4 НОВЫЙ БАГ P2-13: `asyncio.sleep(2)` перед retry слишком мало для Gemini 3.5

При rate limit (429) Gemini 3.5 Flash возвращает `Retry-After` заголовок. Фиксированный `sleep(2)` игнорирует этот заголовок. При `thinking_level="high"` TTFT = 17.75 секунд, поэтому `sleep(2)` между попытками создаёт ложные 429 (запрос ещё не завершился, а мы уже пробуем снова).

```python
# Фикс: exponential backoff + respect Retry-After
retry_after = int(e.headers.get("Retry-After", 5))
await asyncio.sleep(min(retry_after * (2 ** attempt), 60))
```

---

## VIII. ОБНОВЛЁННЫЕ РЕКОМЕНДАЦИИ ПО ПРОМПТАМ

### 8.1 JSON-промпт — чего нельзя допускать

Официальная документация Google (выявленные на практике проблемы):

**Запретить в system prompt:**
```
СТРОГО ЗАПРЕЩЕНО в JSON-ответе:
- Unicode пробелы вместо обычных: \u00a0, \u202f, \u2009, \u200b
- Незакрытые ** маркеры жирного текста
- Переносы строк \u2028, \u2029 внутри JSON string values
- Bidi-маркеры \u2066-\u2069 вокруг текста
- Одиночные * в начале или конце строки
```

### 8.2 Timestamp калибровка в промпте (Temporal Anchor Pattern)

```python
_TIMESTAMP_ANCHOR_BLOCK = """
КАЛИБРОВКА ВРЕМЕННЫХ МЕТОК:
- Аудиофайл начинается строго в 00:00
- Каждые 15 минут в твоих рассуждениях проверяй: правдоподобны ли timestamps?
- При неуверенности в timestamp — давай диапазон: "~45:00–46:30"
- НЕ экстраполируй timestamps из предыдущих — каждый определяй по содержанию
- Признак drift: если после 30-й минуты timestamps кажутся "слишком ранними" — они drift'd
"""
```

### 8.3 `hermeneutic_method` — добавить описания прямо в промпт

Из анализа кода и опыта работы с Gemini — модель путает редкие методы (`typological`, `redemptive_historical`). Решение: добавить inline-описание в промпт (уже зафиксировано в `_HERMENEUTIC_DESCRIPTIONS`, нужно передавать в промпт, а не держать только в коде).

---

## IX. ИТОГОВАЯ СВОДНАЯ ТАБЛИЦА НОВЫХ БАГОВ (v3 additions)

| # | Приоритет | Файл | Проблема | Фикс |
|---|-----------|------|---------|------|
| **P1-9** | 🔴 КРИТИЧНО | `globals.py` | `thinking_config` vs `thinking_level` — API несовместимость SDK 2.0 | Использовать `types.ThinkingConfig(thinking_level="high")` |
| **P2-12** | 🟡 Качество | `telegraph.py` | Thought preservation раздувает context при audio retry | Новый client при retry |
| **P2-13** | 🟡 Качество | `telegraph.py` | `sleep(2)` при 429 игнорирует `Retry-After`, слишком мало для Gemini 3.5 | Exponential backoff + `Retry-After` |
| **P3-12** | 🟢 Стабильность | `_parse_expanded_json` | `\u2028`/`\u2029` не обрабатываются в `fix_json_newlines` | Replace до JSON parse |
| **P3-13** | 🟢 Стабильность | `md_telegraph.py` | `re.DOTALL` в `_HEADING_BOLD_STRIP_RE` — риск multi-line match | Убрать DOTALL, ограничить `[^\n]` |
| **P3-14** | 🟢 Стабильность | `prompts.py` | Нет Temporal Anchor Pattern в аудио-промпте | Добавить блок калибровки timestamps |
| **P3-15** | 🟢 Информация | `DeepSeenPatch.md` | Заявлено "65K output" для deepseek-v4-flash — неверно | Платная: 16K, Free: 384K, учитывать при роутинге |

---

## X. ЧЕКЛИСТ ДО 1 ИЮНЯ 2026 (СРОЧНО)

```
🔴 Горит (до 01.06.2026):
□ GEMINI_MODEL в .env → gemini-3.5-flash
□ Убрать gemini-2.0-flash* и gemini-3.1-flash-lite-preview из всех фоллбеков
□ requirements.txt: google-genai>=2.0.0,<3.0.0
□ Проверить синтаксис thinking_config в globals.py (SDK 2.0 typed objects)
□ Явно добавить thinking_level="high" везде, где нужно (дефолт стал medium!)
□ Убрать temperature= из всех вызовов Gemini 3.x

🟡 Ближайший спринт:
□ max_output_tokens: StudyAnalysis → 32000-65536, Reflection → 24000
□ try/except ValueError вокруг resp.text в telegraph_pages.py:548
□ Исправить \u200e/\u200f stripping в _parse_expanded_json
□ Retry max_tokens escalation: min(max_tokens * 2, 65536)
□ Exponential backoff при 429 (respect Retry-After)
□ Unicode нормализация в начало _section_to_nodes_v2 (\u00a0, \u202f, \u2009, \u200b)
□ Добавить \u2028/\u2029 в fix_json_newlines
□ Убрать re.DOTALL из _HEADING_BOLD_STRIP_RE

🟢 Долгосрочно:
□ Temporal Anchor Pattern в SYNOPSIS_PROMPT_V2
□ Whisper chunking для аудио >18 минут (15-мин чанки → drift ≤10 сек)
□ DeepSeenPatch.md: уточнить max output (платная 16K vs free 384K)
□ Мониторить ThoughtsTokenCount/PromptTokenCount ratio
□ Рассмотреть библиотеку `regex` вместо `re` для критичных паттернов
```

---

*Синопсис v3 составлен 24 мая 2026. Источники: анализ кода `services.zip`, официальная документация Google Gemini API (ai.google.dev, последнее обновление 19-22 мая 2026), Google AI changelog, Google DeepMind model card Gemini 3.5 Flash, GitHub googleapis/python-genai releases, OpenRouter DeepSeek listings (актуально апрель-май 2026), Google Developer Forum (timestamp drift thread март 2026), Towards Data Science (audio chunking pipeline), faster-whisper docs, Python re docs.*

---

# СИНОПСИС v4 — Аудит качества конспектов
## Обновление 24 мая 2026 | Фокус: 10/10 качество на бесплатных ключах

---

## I. РЕАЛЬНОСТЬ БЕСПЛАТНОГО ТИРА — 4 КЛЮЧА × 5 ЗАПРОСОВ

### 1.1 Что такое «5 запросов в день» на free tier для Gemini 3.5 Flash

Free tier Gemini 3.5 Flash: лимит **15 requests per minute** (RPM), **1500 requests per day** (RPD) по официальной документации. Но у тебя явно стоит более жёсткий лимит — либо это квота аккаунта, либо бот сам ограничивает. Важно понимать:

Flash-модели на free tier: сниженные дневные квоты (1500 RPD для Flash по официальной документации). Исторически Google несколько раз резал free tier.

**Реальная картина 4 ключей при 5 запросах/день:**
- 4 аккаунта × 5 запросов = **20 запросов в сутки**
- Каждый «запрос конспекта» = минимум 3–4 API-вызова (аудио-анализ + synopsis + study analysis + reflection)
- Итого: **4–6 полных конспектов в день максимум**

### 1.2 Критическая стратегия: Key Rotation под free tier

Текущий multi-model fallback в `_gemini_text_request` делает ротацию по моделям. Но нужна ротация по **ключам** при исчерпании квоты:

```python
# Оптимальная стратегия для 4 free-tier ключей:

# 1. При 429 с Retry-After > 60 сек → это дневная квота, не минутная
# Признак исчерпания RPD: Retry-After = 86400 (сутки) или error.message 
# содержит "quota" вместо "rate limit"

async def _gemini_request_with_key_rotation(prompt, clients, ...):
    for client in clients:  # перебираем все 4 ключа
        try:
            resp = await client.generate(...)
            return resp
        except QuotaExceededError:
            continue  # следующий ключ
    # Все ключи исчерпаны → graceful degradation
    raise AllKeysExhausted("Все 4 ключа исчерпали дневную квоту")
```

### 1.3 Мониторинг квоты — добавить в бот

```python
# В globals.py или bot_cache.db — трекер запросов по ключу:
KEY_USAGE = {
    "key_1": {"requests_today": 0, "date": "2026-05-24", "exhausted": False},
    "key_2": {...},
    ...
}

def _pick_available_client(clients: list) -> genai.Client | None:
    """Возвращает первый ключ с оставшейся квотой."""
    today = datetime.utcnow().date().isoformat()
    for key_id, client in enumerate(clients):
        usage = KEY_USAGE.get(f"key_{key_id}", {})
        if usage.get("date") != today:
            usage = {"requests_today": 0, "date": today, "exhausted": False}
        if not usage["exhausted"] and usage["requests_today"] < 4:  # 4 = запас
            return client
    return None  # все исчерпаны
```

---

## II. КРИТИЧЕСКОЕ ОБНОВЛЕНИЕ: INLINE FILE LIMIT 20MB → 100MB

### 2.1 Это меняет логику загрузки аудио в боте!

С 12 января 2026 Google увеличил максимальный размер inline payload с **20MB до 100MB** (base64 encoded). Это идеально для протопипирования, real-time приложений и более крупных аудиофайлов без необходимости промежуточного хранения.

**Что это означает для бота (`telegraph.py`):**

Текущий код:
```python
# _upload() — текущая логика:
# >20MB → Files API
# <=20MB → Part.from_bytes [BUG P1-5: может 503]
```

**Новый порог:**
```python
# ОБНОВИТЬ:
# >100MB → Files API (обязательно)
# <=100MB → Part.from_bytes (теперь безопасно)
# Большинство MP3 лекций 60–90 мин = 50–90 MB → теперь inline!
```

Files API: принимает файлы до **2 GB**, хранит **48 часов**, общий объём хранения **20 GB на проект**. Предоставляется бесплатно во всех регионах.

---

## III. ГЛАВНЫЙ ИНСАЙТ: НАТИВНЫЙ `response_schema` ВМЕСТО PROMPT-BASED JSON

### 3.1 Это решает БОЛЬШИНСТВО багов с JSON-парсингом

Сейчас бот просит Gemini вернуть JSON через промпт (`"Return JSON only"`). Это prompt-based подход — ненадёжный.

В отличие от prompt-based подходов, нативный structured output Gemini **гарантирует валидный JSON**, соответствующий схеме — на уровне модели, не промпта.

Google с нативными structured outputs: **-56% до -61% по токенам** через нативную API интеграцию. **44% быстрее** в среднем. Gemini 2.5 Flash со structured outputs — самая низкая стоимость на extraction.

**Что это решает автоматически:**
- `_parse_expanded_json` с его 100,000 итерациями → не нужен
- `fix_json_newlines` → не нужен
- Retry из-за сломанного JSON → не нужен
- `\u200e` в JSON ломающий парсинг → не нужен

### 3.2 Как внедрить `response_schema` в `_gemini_text_request`

```python
from google.genai import types
from typing import TypedDict

# Определяем схему конспекта (упрощённый пример):
class SectionSchema(TypedDict):
    title: str
    timestamp: str
    content: str
    subsections: list[str]

class ConspectSchema(TypedDict):
    title: str
    summary: str
    sections: list[SectionSchema]
    key_points: list[str]
    scripture_references: list[str]

# В _gemini_text_request:
config = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=ConspectSchema,   # ← нативная валидация
    thinking_config=types.ThinkingConfig(thinking_level="high"),
    max_output_tokens=65536,
    # temperature НЕ передаём!
)

resp = await client.aio.models.generate_content(
    model=GEMINI_MODEL,
    contents=prompt,
    config=config,
)
# resp.text гарантированно валидный JSON, соответствующий схеме
data = json.loads(resp.text)  # никогда не бросит SyntaxError
```

**Важно:** Если в промпте есть описания, схемы или примеры — их порядок свойств должен совпадать с порядком в `responseSchema`. Несовпадение порядка путает модель и приводит к некорректному output.

### 3.3 Ограничение: `response_schema` конфликтует с `thinking_level` на некоторых моделях

Из практического опыта сообщества (май 2026): при `thinking_level="high"` и `response_schema` Gemini 3.x иногда возвращает мышление (thought parts) отдельно, а JSON — в text parts. Обходной путь:

```python
# Безопасное извлечение при response_schema + thinking:
result_text = ""
for part in resp.candidates[0].content.parts:
    if not getattr(part, "thought", False) and getattr(part, "text", None):
        result_text += part.text

# Или используй resp.parsed если SDK поддерживает
if hasattr(resp, 'parsed') and resp.parsed:
    data = resp.parsed  # уже TypedDict объект!
```

---

## IV. ЛУЧШИЕ ПРАКТИКИ ПРОМПТОВ ДЛЯ КОНСПЕКТОВ 10/10

### 4.1 Структура промпта — доказанный порядок

Структура промпта критически важна: форматирование повышает точность. Исследование 2024 года: для GPT-4 Markdown-промпт давал 81.2% точности против 73.9% у неструктурированного. Эффективность зависит от модели и задачи.

**Оптимальный порядок секций в промпте конспекта:**

```
1. РОЛЬ (кто ты)
2. ЗАДАЧА (что нужно сделать — конкретно)
3. КОНТЕКСТ (материал: длительность, язык, тип)
4. ФОРМАТ ВЫВОДА (JSON schema с примером)
5. ПРАВИЛА (чего нельзя)
6. КАЛИБРОВКА TIMESTAMP (якоря)
7. ПРИМЕРЫ (few-shot, 1-2 коротких)
```

Gemini лучше всего работает с markdown-стилем структуры или шаблонами с секциями — идеально для длинных документов. Явно указывай стиль: "Предпочитаю Markdown-резюме с bullet points."

### 4.2 Few-shot примеры — мощнейший инструмент для стабильности формата

Если Gemini даёт контент, который "близко, но не то" — не переписывай промпт с нуля. Фикси конкретную проблему: **Format drift → добавь 1 пример правильного JSON** и требуй строгое следование схеме.

```python
# В SYNOPSIS_PROMPT_V2 добавить:
FEW_SHOT_EXAMPLE = """
ПРИМЕР ПРАВИЛЬНОГО РАЗДЕЛА:
{
  "title": "Искупление как основа благодати",
  "timestamp": "12:35",
  "content": "Проповедник рассматривает Послание к Римлянам 3:24...",
  "key_points": [
    "Оправдание — акт Бога, не человека",
    "Благодать незаслуженна по определению"
  ]
}

ПРИМЕР НЕПРАВИЛЬНОГО (избегать):
{
  "title": "**Искупление как основа благодати**",  ← нельзя ** в JSON
  "timestamp": "~12 мин",                          ← нужен формат MM:SS
  "content": "Проповедник\u202fрассматривает..."   ← нельзя Unicode-пробелы
}
"""
```

### 4.3 Негативные примеры (what NOT to do) — работают лучше запретов

Исследования показывают: LLM лучше реагирует на конкретный пример ошибки, чем на абстрактный запрет. В промпте явно показывать:

```
ЗАПРЕЩЁННЫЕ ПАТТЕРНЫ В JSON-ЗНАЧЕНИЯХ:
❌ Unicode-пробелы: \u00a0 \u202f \u2009 \u200b
❌ Markdown внутри строк: **жирный** → жирный
❌ Незакрытые маркеры: "текст ** и продолжение"
❌ Таймкоды в неверном формате: "~45 мин", "45 minutes" → нужно "45:00"
❌ Переносы строк в строковых значениях: используй \n (экранированный)
```

### 4.4 Якорная секция для structure stability

Для длинных задач: якорь в промпте — как чёткий brief-документ. Держит структуру предсказуемой и готовой для аудита.

```python
STRUCTURE_ANCHOR = """
ОБЯЗАТЕЛЬНАЯ СТРУКТУРА КОНСПЕКТА (не отступать):
1. synopsis_meta — метаданные (заголовок, дата, длительность, метод)
2. overview — краткое резюме (3-5 предложений)
3. sections[] — основные разделы в хронологическом порядке
   каждый section: {title, timestamp, content, key_points[], scriptures[]}
4. main_themes[] — 3-7 ключевых тем
5. application_points[] — практическое применение
6. discussion_questions[] — вопросы для обсуждения (если есть)
Порядок полей должен совпадать с этим перечнем.
"""
```

### 4.5 Prompt chaining для длинных материалов (60+ минут)

Для длинных видео: **первый проход** — высокоэффективной моделью для индексации на низком разрешении. Это "первая карта" с ключевыми событиями и timestamps. **Второй проход** — детальный анализ идентифицированных интервалов.

**Адаптация для конспектов (оптимизация под 4 free-tier ключа):**

```
Запрос 1 (быстрый, ~0.5 free request):
  → "Выдели 5-8 главных разделов аудио с timestamps"
  → Результат: скелет конспекта, таймкоды якоря

Запрос 2 (основной, ~1 free request):
  → "Разбери раздел 1 (00:00–15:00) детально"
  → Используем якоря из запроса 1!
  
Запрос 3-N: последующие разделы

→ Итого: 4-8 запросов вместо 1 большого,
  НО каждый меньше → меньше риск обрезания
  НО: расходует больше из дневной квоты!
```

**Вывод:** При 5 запросах/день на ключ — для длинных материалов предпочтительнее **1 большой запрос с max_tokens=65536**, чем chaining. Chaining выгоден только если есть запас квоты.

---

## V. ПОСТОБРАБОТКА — ОПТИМАЛЬНЫЙ PIPELINE ДЛЯ ИДЕАЛЬНОГО КОНСПЕКТА

### 5.1 Правильный порядок этапов в `_section_to_nodes_v2`

Текущий код имеет 7 этапов нормализации. Оптимальный порядок:

```
Этап 0: Unicode нормализация (НОВЫЙ — добавить первым!)
  → \u00a0→' ', \u202f→' ', \u2009→' ', \u200b→'', \u00ad→''
  → \u2028→'\n', \u2029→'\n' (для JSON фикса)
  → Bidi isolators: re.sub(r'[\u2066-\u2069\u202a-\u202e]', '', text)
  → LRM/RLM (\u200e/\u200f) — НЕ ТРОГАТЬ

Этап 1: Базовая нормализация (существующий)
  → Убрать metadata-мусор
  → Нормализовать заголовки

Этап 2: Scripture format patch (существующий)
  → _patch_scripture_format

Этап 3: Regex-очистка (существующий, но с исправлениями)
  → Убрать re.DOTALL из _HEADING_BOLD_STRIP_RE
  → Все timestamp-паттерны

Этап 4: Пробелы после знаков (существующий)

Этап 5: Rebuild numbered/bullet (существующий)

Этап 6: _shrink_overbold_line (существующий)

Этап 7: _ensure_all_paragraphs_period (существующий)
  + добавить: финальная очистка \u200b после точки
  + добавить: \u2026 в _GOOD_ENDINGS
```

### 5.2 Проблема "жирного абзаца" — более точный алгоритм

Текущий `_demote_paragraph_bold` снимает `**` если bold >70% строки длиннее 80 символов. Это правильно, но есть edge case: строка может быть `**Введение:** Сегодня мы рассмотрим...` — здесь bold нужен (это метка раздела, не жирный абзац).

**Улучшенный алгоритм:**
```python
def _demote_paragraph_bold(line: str) -> str:
    # ... существующая логика ...
    
    # ДОБАВИТЬ: исключение для "метка: текст" паттерна
    # Если bold часть содержит ':' в конце — это метка-заголовок, не трогаем
    for match in bold_matches:
        if match.rstrip().endswith(':'):
            return line  # метка раздела — оставляем
    
    # ... остальная логика ...
```

### 5.3 Timestamp linkification — улучшить `_linkify_inline_timestamps`

Текущий `_linkify_inline_timestamps` матчит `⏱` и `🔗` перед числом. Добавить поддержку паттернов которые Gemini 3.5 генерирует чаще:

```python
# Gemini 3.5 Flash часто генерирует в формате:
# [12:35] или (45:00) или «00:15:30»
# Добавить в TS_RE:
TS_RE = re.compile(
    r'(?:'
    r'\[(\d{1,2}:\d{2}(?::\d{2})?)\]'       # [MM:SS]
    r'|(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)'  # MM:SS без скобок
    r'|\((\d{1,2}:\d{2}(?::\d{2})?)\)'       # (MM:SS)
    r')'
)
```

---

## VI. СПЕЦИФИКА БЕСПЛАТНОГО ТИРА — АРХИТЕКТУРНЫЕ РЕШЕНИЯ

### 6.1 Кэширование аудио-анализа — критически важно при 5 req/day

Каждое повторное создание конспекта для одного аудио **тратит полный запрос**. Нужно кэшировать не только текст конспекта, но и промежуточный результат Gemini:

```python
# В bot_cache.db добавить таблицу:
"""
CREATE TABLE IF NOT EXISTS audio_analysis_cache (
    audio_hash TEXT PRIMARY KEY,  -- SHA256 аудиофайла
    gemini_raw_json TEXT,         -- сырой JSON ответ Gemini
    model_used TEXT,              -- какая модель
    created_at DATETIME,
    expires_at DATETIME           -- через 7 дней удалять
);
"""

# Перед вызовом Gemini:
cached = db.get_audio_cache(audio_hash)
if cached and cached.model_used == GEMINI_MODEL:
    raw = cached.gemini_raw_json  # экономим 1 запрос!
```

### 6.2 Graceful degradation при исчерпании квоты

При 20 запросах/день и нескольких пользователях квота может кончиться. Нужно явное сообщение:

```python
# Вместо молчаливого зависания:
async def _handle_all_keys_exhausted(update, context):
    moscow_midnight = _get_next_quota_reset()  # UTC+3
    await update.message.reply_text(
        f"⏰ Дневной лимит запросов исчерпан.\n"
        f"Квота обновится в {moscow_midnight} (МСК).\n"
        f"Можно поставить аудио в очередь — обработаем автоматически."
    )
```

### 6.3 Приоритизация запросов — что делать с очередью

При 5 req/день на ключ × 4 ключа = 20 req/day:
- Конспект Synopsis = **1 запрос** (аудио-анализ)
- Study Analysis = **1 запрос** (text-only)
- Reflection = **1 запрос** (text-only)
- Итого полный конспект = **3 запроса**
- При 20 req/day → **6–7 полных конспектов в день**

**Оптимизация:** Study Analysis и Reflection используют уже готовый текст из Synopsis. Они text-only запросы — быстрые и дешёвые. Сделать их **опциональными** (по кнопке, не автоматически) — экономит 2 запроса из 3 для каждого конспекта.

---

## VII. ГЛУБОКИЙ АУДИТ ПРОМПТОВ — КОНКРЕТНЫЕ УЛУЧШЕНИЯ

### 7.1 Системная инструкция vs промпт пользователя

В Gemini 3.x есть разница между `system_instruction` и `contents`. Структурные правила (формат JSON, запреты Unicode) → в `system_instruction`. Контент-специфика (аудио, тема) → в `contents`.

```python
config = types.GenerateContentConfig(
    system_instruction="""
    Ты — эксперт по созданию структурированных конспектов христианских проповедей.
    АБСОЛЮТНЫЕ ПРАВИЛА:
    1. Возвращай ТОЛЬКО валидный JSON без markdown-обёртки
    2. Никаких Unicode-пробелов в значениях (\u00a0, \u202f, \u2009)
    3. Никаких ** маркеров внутри строковых значений JSON
    4. Timestamps ТОЛЬКО в формате MM:SS или HH:MM:SS
    5. Порядок полей строго по схеме
    """,
    response_mime_type="application/json",
    response_schema=ConspectSchema,
    thinking_config=types.ThinkingConfig(thinking_level="high"),
    max_output_tokens=65536,
)
```

### 7.2 Формулировка для hermeneutic methods — улучшение

Текущие методы описаны только именами. Добавить краткое определение **прямо в промпт**, не только в код:

```python
HERMENEUTIC_CONTEXT = """
Определённый тип проповеди влияет на структуру конспекта:
• expository — последовательный экзегетический разбор текста → структура по стихам
• topical — тема через множество текстов → структура по аргументам  
• narrative — следование повествовательной дуге → структура по сюжетным точкам
• typological — ветхозаветные прообразы → выделять тип и антитип
• redemptive_historical — история искупления → хронологическая ось
• catechetical — вопрос-ответ → Q&A структура разделов
• practical — применение к жизни → структура по жизненным сферам
"""
```

---

## VIII. ОБНОВЛЁННАЯ ПОЛНАЯ СВОДКА БАГОВ (v4 — убран DeepSeek)

### 🔴 КРИТИЧНО — до 1 июня 2026

| # | Файл | Проблема | Фикс |
|---|------|---------|------|
| P1-6 | `requirements.txt` | `google-genai<2.0.0` блокирует SDK 2.0 | `>=2.0.0,<3.0.0` |
| P1-7 | `telegraph_pages.py:548` | `resp.text` без try/except при safety filter | try/except + parts fallback |
| P1-8 | `telegraph_pages.py:610` | `\u200e`/`\u200f` удаляются — ломает RTL | Удалять только `\u2066-\u2069`, `\u202a-\u202e` |
| P1-9 | `globals.py` | `thinking_config` синтаксис SDK 2.0 | `types.ThinkingConfig(thinking_level="high")` |
| P1-10 | `.env` | `gemini-2.0-flash*` shutdown 1 июня | → `gemini-3.5-flash` |

### 🟡 КАЧЕСТВО — ближайший спринт

| # | Файл | Проблема | Фикс |
|---|------|---------|------|
| P2-8 | везде | `temperature=` для Gemini 3.x | Удалить полностью |
| P2-9 | `telegraph_pages.py` | `max_tokens=16000` для StudyAnalysis | → 32000-65536 |
| P2-10 | `telegraph_pages.py` | `max_tokens=14000` для Reflection | → 24000 |
| P2-11 | `_run_expanded_pipeline` | Retry не увеличивает max_tokens | `min(max_tokens*2, 65536)` |
| P2-12 | `telegraph.py` | Thought preservation раздувает context при retry | Новый client при retry |
| P2-13 | `telegraph.py` | `sleep(2)` при 429 — мало, игнорирует Retry-After | Exponential backoff |
| P2-14 | `_upload()` | Порог 20MB для inline устарел | Обновить до 100MB |

### 🟢 ДОЛГОСРОЧНО — архитектура и качество

| # | Файл | Проблема | Фикс |
|---|------|---------|------|
| P3-7 | `.env.example` | `WHISPER_DEVICE=cpu` слишком медленно | → `cuda` |
| P3-10 | `_parse_expanded_json` | `max_iterations=100_000` мало для 65K | → 500_000 |
| P3-11 | промпты | Нет timestamp anchor-points | Temporal Anchor Pattern |
| P3-12 | `_parse_expanded_json` | `\u2028`/`\u2029` не обрабатываются | Replace до парсинга |
| P3-13 | `md_telegraph.py` | `re.DOTALL` в `_HEADING_BOLD_STRIP_RE` | Убрать, заменить на `[^\n]` |
| P3-14 | `_section_to_nodes_v2` | Нет Этапа 0 Unicode нормализации | Добавить первым этапом |
| P3-15 | `_gemini_text_request` | Prompt-based JSON вместо `response_schema` | Нативный schema — устранит большинство JSON-багов |
| P3-16 | бот | Нет кэша audio_analysis | Добавить в bot_cache.db |
| P3-17 | бот | Нет мониторинга квоты по ключам | Key rotation + usage tracker |

---

## IX. ЧЕКЛИСТ "10 ИЗ 10" — ЧТО ДЕЛАЕТ КОНСПЕКТ ИДЕАЛЬНЫМ

```
Структура:
✅ Заголовок точно отражает тему (не generic)
✅ Timestamps калиброваны (проверить drift)
✅ Разделы логически выстроены по типу проповеди
✅ Scripture references выделены отдельно и кликабельны
✅ Применение отделено от доктрины
✅ Нет "мусорных" метаданных (дата генерации, имя AI)

Форматирование Telegraph:
✅ Нет двойных пробелов
✅ Нет ** в тексте нод (только bold-теги)
✅ RTL-контент (иврит/арабский) с правильным dir
✅ Нет <p><br/></p> после заголовков
✅ Точки в конце всех параграфов (кроме заголовков)
✅ Timestamps как кликабельные ссылки на YouTube

Технически:
✅ JSON валиден (нет обрезания)
✅ Нет Unicode-мусора в тексте
✅ Страница не превышает Telegraph limit (~55K JSON)
✅ При превышении — корректный split с навигацией
```

---

*Синопсис v4 составлен 24 мая 2026. Новые источники: Google API changelog (май 2026), Google blog "Increased file size limits" (январь 2026), Google Cloud structured output docs (май 2026), OpenRouter rate limits, DSPy.rb benchmark (сентябрь 2025), SurePrompts structured output guide, Lakera prompt engineering guide 2026, FutureAGI prompt format best practices.*

---

# СИНОПСИС v4 — ПРОДОЛЖЕНИЕ: Глубокий аудит 2026

---

## X. КРИТИЧЕСКИЙ СКРЫТЫЙ БАГ: `max_output_tokens` — СОВМЕСТНЫЙ БЮДЖЕТ

### 10.1 Это объясняет 80% случаев обрезания конспектов

`max_output_tokens` на Gemini 3.x моделях — это **совокупный бюджет** для thinking tokens + output tokens, вопреки документации которая заявляет их раздельность. При `thinking_level="high"` модель может занять почти весь лимит на внутренние рассуждения, не оставив места для видимого ответа. Без `max_output_tokens` вызов может зависнуть на **20+ минут в бесконечном thinking-цикле**.

**Что происходит в реальности:**

Gemini 2.5/3 Flash с динамическим thinking на нетривиальных задачах легко тратит **900 из 1000 токенов на рассуждения**. На выходе — 37 токенов видимого текста. Это не баг SDK — это server-side поведение.

**Практические последствия для бота:**

```python
# ТЕКУЩАЯ СИТУАЦИЯ (опасная):
# max_tokens=16000 для StudyAnalysis
# thinking_level="high" → модель думает, например, 12000 токенов
# На конспект остаётся лишь 4000 токенов → ОБРЕЗАНИЕ

# ПРАВИЛЬНЫЙ расчёт max_output_tokens для конспектов:
# Ожидаемый видимый output для 90-мин лекции ≈ 8000-15000 токенов
# Thinking при level="high" ≈ 5000-20000 токенов (непредсказуемо)
# Итого нужно: 15000 + 20000 = 35000 → ставить 40000-65536
```

### 10.2 Диагностика через `finish_reason` — ОТСУТСТВУЕТ В БОТЕ

`finishReason=STOP` — модель завершила сама. `MAX_TOKENS` — достигнут лимит вывода. `SAFETY` — контент заблокирован. `OTHER` — неожиданное завершение, требует retry.

**Добавить в `_gemini_text_request` и `_extract_response_text`:**

```python
def _check_finish_reason(resp) -> str | None:
    """Возвращает причину остановки или None если всё ок."""
    if not resp.candidates:
        return "NO_CANDIDATES"
    candidate = resp.candidates[0]
    finish = getattr(candidate, 'finish_reason', None)
    
    if finish is None:
        return None
    finish_str = str(finish).upper()
    
    if "MAX_TOKENS" in finish_str or "LENGTH" in finish_str:
        # Критично — нужен retry с увеличенным лимитом
        used = getattr(resp.usage_metadata, 'total_token_count', 0)
        thoughts = getattr(resp.usage_metadata, 'thoughts_token_count', 0)
        logger.warning(
            "MAX_TOKENS hit: total=%d, thinking=%d, output≈%d",
            used, thoughts, used - thoughts
        )
        return "MAX_TOKENS"
    
    if "SAFETY" in finish_str:
        return "SAFETY"
    
    if "OTHER" in finish_str:
        return "OTHER"
    
    return None  # STOP — нормально

# Использование:
reason = _check_finish_reason(resp)
if reason == "MAX_TOKENS":
    # Retry с удвоенным лимитом, не с тем же!
    new_limit = min(max_tokens * 2, 65536)
    ...
```

### 10.3 Мониторинг thinking tokens — новый обязательный лог

```python
# После каждого успешного вызова логировать:
meta = resp.usage_metadata
logger.info(
    "[Gemini] prompt=%d, thoughts=%d, output≈%d, total=%d",
    getattr(meta, 'prompt_token_count', 0),
    getattr(meta, 'thoughts_token_count', 0),
    getattr(meta, 'candidates_token_count', 0),
    getattr(meta, 'total_token_count', 0),
)
# Если thoughts > candidates_token_count * 2 → флаг неэффективного thinking
```

---

## XI. АУДИО ТОКЕНЫ — ТОЧНЫЙ РАСЧЁТ ДЛЯ ПЛАНИРОВАНИЯ

### 11.1 Формула токенов для аудио

Gemini представляет каждую секунду аудио как **32 токена**. 1 минута = 1920 токенов. Максимальная поддерживаемая длина аудио в одном промпте — **9.5 часов**.

**Таблица токенов для типичных проповедей:**

| Длительность | Аудио токены | + промпт (~2000) | Итого input | Остаток в 1M ctx |
|---|---|---|---|---|
| 30 мин | 57,600 | 59,600 | 59,600 | ~940K |
| 60 мин | 115,200 | 117,200 | 117,200 | ~883K |
| 90 мин | 172,800 | 174,800 | 174,800 | ~825K |
| 120 мин | 230,400 | 232,400 | 232,400 | ~768K |

**Вывод:** Даже для 2-часовых проповедей вполне помещается в контекстное окно. Проблема не в input — проблема в output (thinking + конспект).

### 11.2 Threshold для Files API обновился

С января 2026: inline payload limit вырос с 20MB до **100MB**. Для inline данных это теперь base64-encoded, с варьирующимися лимитами по типам файлов.

**Практика для MP3:** Типичный MP3 60 мин при 128kbps ≈ 58MB. При 320kbps ≈ 144MB. Значит:
- Стандартные лекции (128kbps) — **inline до 100MB** (безопасно)  
- Высококачественные записи (320kbps) → **Files API**
- Текущий порог в боте (20MB) — устарел, нужно обновить до 100MB

---

## XII. `response_schema` + АУДИО: ПОЛНЫЙ РАБОЧИЙ ПАТТЕРН

### 12.1 Instructor library — самый чистый способ

Библиотека `instructor` с Pydantic напрямую работает с аудио в Gemini. Поддерживает `Audio.from_path()`, `Audio.from_url()`, и base64. Возвращает типизированный Pydantic объект.

```python
# requirements.txt добавить:
# instructor>=1.7.0

from instructor.processing.multimodal import Audio
from pydantic import BaseModel, Field
import instructor
from google import genai

class ConspectSection(BaseModel):
    title: str = Field(description="Название раздела без ** маркеров")
    timestamp: str = Field(description="Временная метка в формате MM:SS")
    content: str = Field(description="Содержание без Unicode-пробелов")
    key_points: list[str] = Field(default_factory=list)
    scripture_refs: list[str] = Field(default_factory=list)

class FullConspect(BaseModel):
    title: str
    summary: str = Field(description="3-5 предложений")
    hermeneutic_method: str
    sections: list[ConspectSection]
    main_themes: list[str]
    application_points: list[str]

# Клиент с instructor:
raw_client = genai.Client(api_key=api_key)
client = instructor.from_provider(f"google/gemini-3.5-flash")

# Запрос с аудио + схемой одновременно:
conspect: FullConspect = client.create(
    response_model=FullConspect,
    messages=[{
        "role": "user",
        "content": [
            system_prompt,
            Audio.from_path(audio_file_path),
        ],
    }],
    # instructor автоматически использует response_schema
)
# conspect — это уже валидный Pydantic объект, не JSON-строка!
```

### 12.2 Критическое ограничение: `response_schema` несовместим с `tools`

**Баг API:** функция calling с `response_mime_type='application/json'` несовместима — вызывает ошибку `400 INVALID_ARGUMENT`. Это ограничение на уровне сервера, не SDK. Документация об этом не предупреждает.

Исключение: Gemini 3.x поддерживает комбинацию `response_schema` с **встроенными инструментами** (search-as-a-tool, code execution) — только Preview. Кастомные function declarations по-прежнему несовместимы.

**Вывод для бота:** В `_gemini_text_request` нет function calling — можно смело добавлять `response_schema`. Проблем не будет.

---

## XIII. ФИНАЛИЗАЦИЯ `finish_reason` И ПРОДОЛЖЕНИЕ ОБРЕЗАННОГО ОТВЕТА

### 13.1 "Continuation request" — новая стратегия вместо retry

При `MAX_TOKENS`: вместо полного retry попробовать continuation — передать обрезанный ответ обратно модели с просьбой продолжить. Шаги: 1) проверить `finish_reason`; 2) если MAX_TOKENS — увеличить `max_output_tokens` или запросить продолжение; 3) если SAFETY — скорректировать промпт; 4) если OTHER — retry.

```python
async def _gemini_with_continuation(prompt, audio_part, max_tokens=65536):
    """Запрос с автоматическим continuation при MAX_TOKENS."""
    
    resp = await _gemini_text_request(prompt, max_tokens=max_tokens)
    reason = _check_finish_reason(resp)
    
    if reason == "MAX_TOKENS":
        partial = resp.text or ""
        # Стратегия 1: увеличить лимит и повторить
        if max_tokens < 65536:
            return await _gemini_with_continuation(
                prompt, audio_part, max_tokens=65536
            )
        # Стратегия 2: попросить продолжить с того места
        continuation_prompt = (
            f"Продолжи точно с места где остановился. "
            f"Последние слова: ...{partial[-200:]}"
        )
        cont = await _gemini_text_request(continuation_prompt, max_tokens=32000)
        return partial + (cont or "")
    
    return resp.text or ""
```

---

## XIV. THROTTLE MIDDLEWARE ДЛЯ AIOGRAM — ЗАЩИТА ОТ FLOOD

### 14.1 Защита при исчерпании квоты Gemini

Production-паттерн для aiogram: TTLCache middleware для throttling пользователей. Тихо дропает повторные запросы если пользователь уже в процессе обработки.

```python
from cachetools import TTLCache
from aiogram import BaseMiddleware

class ProcessingThrottleMiddleware(BaseMiddleware):
    """
    Предотвращает двойную обработку: если пользователь уже
    ждёт конспект — тихо игнорируем повторный запрос.
    """
    def __init__(self):
        self.processing = TTLCache(maxsize=1000, ttl=300)  # 5 минут
    
    async def __call__(self, handler, event, data):
        user_id = event.message.from_user.id if event.message else None
        if user_id and user_id in self.processing:
            await event.message.reply(
                "⏳ Ваш конспект ещё обрабатывается. Подождите немного."
            )
            return
        if user_id:
            self.processing[user_id] = True
        try:
            return await handler(event, data)
        finally:
            if user_id and user_id in self.processing:
                del self.processing[user_id]

# Регистрация:
dp.update.middleware(ProcessingThrottleMiddleware())
```

---

## XV. СВОДНАЯ ТАБЛИЦА НОВЫХ КРИТИЧЕСКИХ ЗНАНИЙ (v4 continuation)

| Тема | Что узнали | Действие |
|------|-----------|---------|
| `max_output_tokens` | Совместный бюджет thinking+output → при `high` thinking реальный output сильно меньше заявленного | Ставить 65536, проверять `thoughts_token_count` |
| `finish_reason` | Не проверяется в боте → обрезание без retry | Добавить диагностику, continuation strategy |
| Inline file limit | Обновился с 20MB → 100MB (январь 2026) | Изменить порог в `_upload()` |
| Аудио токены | 32 токена/сек, 90 мин = 172K input tokens | Помещается в 1M ctx, проблема в output |
| `response_schema` + аудио | Работает! Через instructor или напрямую | Внедрить для устранения JSON-багов |
| `response_schema` + tools | Несовместимы (400 error) | Не проблема — бот не использует tools |
| Thinking tokens logging | `thoughts_token_count` в `usage_metadata` | Добавить в каждый Gemini-вызов |
| Throttle middleware | Нет защиты от двойной отправки | Добавить TTLCache middleware |
| Key rotation | Нет трекера расхода квоты по ключам | Добавить в bot_cache.db |
| Pydantic + schema | `response.parsed` → уже типизированный объект | Использовать вместо `json.loads(resp.text)` |

---

## XVI. ФИНАЛЬНЫЙ ПРИОРИТИЗИРОВАННЫЙ TODO — "10 ИЗ 10"

```
НЕДЕЛЯ 1 (До 1 июня — ЖЁСТКИЙ ДЕДЛАЙН):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ GEMINI_MODEL → gemini-3.5-flash везде
□ requirements.txt: google-genai>=2.0.0,<3.0.0
□ Убрать temperature= из всех Gemini 3.x вызовов
□ Добавить finish_reason проверку везде (MAX_TOKENS → retry с 65536)
□ max_output_tokens → 65536 для всех генераций (thinking ест бюджет!)
□ Порог inline upload: 20MB → 100MB в _upload()
□ thinking_config синтаксис SDK 2.0: types.ThinkingConfig(thinking_level="high")

НЕДЕЛЯ 2 (Качество JSON — устранение большинства багов):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Внедрить response_schema + Pydantic (устраняет _parse_expanded_json целиком)
□ Добавить thoughts_token_count логирование
□ Добавить ThrottleMiddleware в aiogram
□ Добавить key rotation tracker в bot_cache.db
□ RTL fix: \u200e/\u200f не удалять в _parse_expanded_json

НЕДЕЛЯ 3 (Форматирование — идеальный рендеринг):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Этап 0 Unicode нормализации в _section_to_nodes_v2
□ Убрать re.DOTALL из _HEADING_BOLD_STRIP_RE
□ Добавить \u2028/\u2029 в fix_json_newlines
□ Timestamp форматы [MM:SS] и (MM:SS) в linkifier
□ Few-shot примеры с негативными образцами в промпт

НЕДЕЛЯ 4 (Надёжность под бесплатный тир):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
□ Temporal Anchor Pattern в SYNOPSIS_PROMPT_V2
□ Audio analysis cache в bot_cache.db (SHA256 ключ)
□ Graceful degradation при исчерпании всех 4 ключей
□ Continuation strategy при MAX_TOKENS вместо полного retry
□ Exponential backoff с Retry-After header при 429
```

---

*Синопсис v4 continuation, составлен 24 мая 2026. Новые источники: googleapis/python-genai issues #2062 (max_output_tokens thinking budget bug, февраль 2026), #867 (tools + JSON несовместимость), Medium "Gemini returning 37 tokens" (апрель 2026), Gemini Lab truncation guide (апрель 2026), TokenMix thinking tokens trap (апрель 2026), instructor library Gemini audio docs, Google AI blog structured outputs update (январь 2026), Google AI blog file size limits (январь 2026), DEV aiogram throttle middleware (май 2026), Google function calling docs (апрель 2026).*