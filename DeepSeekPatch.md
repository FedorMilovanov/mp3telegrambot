# План внедрения: Whisper + OpenRouter DeepSeek для mp3telegrambot

Дата: 2026-05-21
Репозиторий: `FedorMilovanov/mp3telegrambot`

Цель: не заменить текущий Gemini-пайплайн, а добавить второй текстовый слой качества и разгрузки Gemini. Trinity не используем. GLM не используем в runtime. Whisper уже есть на компьютере — его стоит сделать источником transcript-контекста для DeepSeek.

---

## 1. Главный принцип архитектуры

Нельзя смешивать роли моделей.

Правильное разделение:

```text
Gemini          = слышит MP3, делает первичное понимание аудио
Whisper         = даёт transcript/segments как проверяемую текстовую основу
DeepSeek        = глубоко обрабатывает уже готовый текст
Локальные базы  = проверяют Писание/вероисповедания/источники
Код             = валидирует JSON, таймкоды, лимиты, структуру
PDF             = собирает уже готовые Telegraph-страницы, AI не вызывает
```

Не делаем:

```text
Gemini → Trinity → DeepSeek → GLM → ещё один редактор
```

Это ухудшит трассируемость, увеличит задержку и создаст стиль/фактические расхождения.

---

## 2. Текущий pipeline в проекте

По текущей структуре:

```text
URL
  ↓
yt-dlp metadata
  ↓
скачивание MP3
  ↓
services/gemini_analyze.py: gemini_analyze_audio()
  ↓
ai_data
  ↓
services/telegraph.py: create_telegraph_synopsis()
  ↓
services/telegraph_pages.py:
  - create_telegraph_study_analysis()
  - create_telegraph_reflection_application()
  ↓
Telegraph pages
  ↓
services/pdf_generator.py собирает PDF из Telegraph
  ↓
Shorts / Clips / Montage
```

Сейчас в проекте нет полноценного transcript как обязательного сохранённого артефакта. Есть `ai_data`, `synopsis_outline`, Telegraph-страницы и MP3. Для DeepSeek этого мало: он будет углублять не саму проповедь, а Gemini-выжимку.

Поэтому главный следующий шаг — добавить transcript.

---

## 3. Целевая архитектура

```text
URL
  ↓
yt-dlp metadata + MP3
  ↓
Gemini audio analysis
  → ai_data
  ↓
Whisper local transcription
  → transcript.json
  → transcript_plain.txt
  ↓
Gemini/DeepSeek synopsis draft
  ↓
DeepSeek edit pass
  ↓
DeepSeek StudyAnalysis по transcript + ai_data + synopsis_outline
  ↓
DeepSeek ReflectionApplication по transcript + questions + key_categories
  ↓
Optional verification:
  - Bible refs
  - confession refs
  - lexicon claims
  - named theologian claims
  ↓
JSON validation
  ↓
Telegraph publish
  ↓
PDF from Telegraph
```

---

## 4. Этапы внедрения

### Этап 1 — OpenRouter text-only provider

Добавить файл:

```text
services/openrouter.py
```

Функция:

```python
async def openrouter_text_request(
    prompt: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 8000,
    timeout: int = 180,
) -> str | None:
    ...
```

`.env`:

```env
OPENROUTER_API_KEY=...
OPENROUTER_HTTP_REFERER=https://github.com/FedorMilovanov/mp3telegrambot
OPENROUTER_APP_TITLE=mp3telegrambot

LLM_TEXT_PROVIDER=gemini
USE_OPENROUTER_FOR_STUDY=false
USE_OPENROUTER_FOR_REFLECTION=false
OPENROUTER_STUDY_MODEL=deepseek/deepseek-chat-v3-0324:free
OPENROUTER_REFLECTION_MODEL=deepseek/deepseek-chat-v3-0324:free
```

Принцип fallback:

```text
если включён OpenRouter:
  пробуем DeepSeek
  если ошибка/пусто/сломанный JSON → Gemini
  если Gemini тоже упал → compact fallback
иначе:
  текущий Gemini flow
```

Точка интеграции:

```text
services/telegraph_pages.py
```

Текущая функция:

```python
_gemini_text_request()
```

Лучше переименовать в:

```python
_llm_text_request()
```

А внутри маршрутизировать Gemini/OpenRouter.

---

### Этап 2 — Whisper transcript cache

Добавить сервис:

```text
services/transcription.py
```

Цель: получить и сохранить transcript как отдельный артефакт.

Формат:

```json
{
  "source": "faster-whisper",
  "model": "large-v3",
  "language": "ru",
  "duration": 3600,
  "segments": [
    {"start": 0.0, "end": 12.4, "text": "..."},
    {"start": 12.4, "end": 25.1, "text": "..."}
  ],
  "plain_text": "..."
}
```

Кэшировать в:

```text
downloads/transcripts/{media_id}.json
```

И/или в SQLite:

```sql
ALTER TABLE video_cache ADD COLUMN transcript_path TEXT DEFAULT '';
ALTER TABLE video_cache ADD COLUMN transcript_model TEXT DEFAULT '';
```

Начать проще с файлового кэша. В SQLite сохранить только путь и модель.

---

### Этап 3 — DeepSeek grounded StudyAnalysis

Когда есть transcript, промпт для StudyAnalysis должен стать grounded:

```text
Ты анализируешь строго transcript ниже.
Запрещено добавлять цитаты, богословов, факты и ссылки, которых нет в transcript,
кроме явно помеченного раздела "историко-богословский контекст".
Если утверждение не подтверждается transcript, не включай его.
```

Вход:

```text
- ai_data
- synopsis_outline
- transcript_plain или transcript_segments
```

Лучше давать transcript_segments, чтобы DeepSeek мог ссылаться на интервалы.

---

### Этап 4 — Edit pass перед публикацией

Не редактировать уже опубликованный Telegraph как основной метод.

Правильное место:

```text
_run_expanded_pipeline()
  raw = LLM(prompt)
  parsed = _parse_expanded_json(raw)
  outline, sections = parsed
  sections = edit_sections(...)
  validate sections
  publish
```

Редакторские задачи:

- убрать повторы;
- выровнять стиль;
- сохранить JSON;
- сохранить title/time/content;
- не добавлять новых фактов;
- не менять цитаты;
- не ломать таймкоды;
- проверить запрещённые фразы.

DeepSeek подходит как финальный text-only редактор. Trinity не нужна.

---

### Этап 5 — Verification layer

Это отдельная задача, не часть LLM сама по себе.

Файлы:

```text
services/verification.py
services/source_lookup.py
services/tavily_search.py  # optional
```

Локальные источники:

```text
data/sources/lbcf_1689_ru.md
data/sources/lbcf_1689_en.md
data/sources/wcf_1646.md
data/sources/canons_of_dort.md
data/sources/heidelberg.md
data/sources/chicago_inerrancy.md
```

Проверка должна возвращать статусы:

```text
confirmed
partially_confirmed
not_found
conflict
unverifiable
```

Не надо молча переписывать материал. Лучше создать отдельный блок:

```text
Проверка источников
```

Пример:

```text
✅ Матфея 7:21–23 — ссылка корректна, используется по смыслу.
⚠️ LBCF 1689, глава 11 — тема оправдания действительно там, но формулировка в материале является пересказом, не прямой цитатой.
❓ Цитата Эдвардса — точный источник не найден; лучше не оформлять как прямую цитату.
```

---

## 5. Приоритеты

### MVP-1

OpenRouter DeepSeek как optional provider для:

```text
StudyAnalysis
ReflectionApplication
```

Без transcript. Это разгрузит Gemini, но будет работать по `ai_data + synopsis_outline`, не по полной проповеди.

### MVP-2

Whisper transcript cache.

Это самый важный качественный шаг.

### MVP-3

DeepSeek по transcript.

После этого модель действительно работает с оригинальным материалом, а не с выжимкой.

### MVP-4

Edit pass перед Telegraph.

### MVP-5

Локальные источники + verification report.

---

## 6. Что не внедрять сейчас

- Trinity — не нужна.
- GLM в runtime — не нужен.
- Полный веб-поиск для каждого видео — дорого и сложно.
- Редактирование опубликованных Telegraph-страниц как основной механизм — не надо.
- Несколько моделей подряд без чётких ролей — не надо.

---

## 7. Контроль качества

Для каждого нового LLM-шага нужны проверки:

```text
1. response не пустой
2. JSON валиден
3. есть sections
4. outline соответствует sections
5. нет запрещённых фраз
6. нет квадратных скобок, если промпт запрещает
7. таймкоды валидны
8. объём в пределах лимитов Telegraph
9. если включён grounded mode — нет новых имён/цитат вне transcript
```

Если проверка не прошла:

```text
retry with stricter prompt
↓
fallback Gemini
↓
compact fallback
```

---

## 8. Рекомендуемая первая реализация

1. Создать `services/openrouter.py`.
2. Создать `_llm_text_request()` в `services/telegraph_pages.py`.
3. Подключить DeepSeek только для `StudyAnalysis`, оставить `Reflection` на Gemini.
4. Прогнать 5–10 материалов.
5. Если JSON стабилен — включить Reflection.
6. Потом добавлять Whisper transcript.

---

## 9. Важное замечание по PDF

PDF не вызывает LLM. Он только скачивает Telegraph-страницы, чистит HTML и рендерит через `wkhtmltopdf` или `WeasyPrint`.

Поэтому добавление DeepSeek/Whisper не должно ломать PDF напрямую. PDF может пострадать только если новые страницы:

- слишком большие;
- имеют сломанный HTML/Markdown;
- содержат слишком много невалидных заголовков;
- Telegraph не отдаёт контент.

Значит, контроль PDF — это контроль качества Telegraph sections до публикации.
