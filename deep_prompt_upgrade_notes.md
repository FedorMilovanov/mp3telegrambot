# Deep prompt upgrade notes

## Что изменено

### 1) Возвращена глубина первичного Gemini-анализа
- Добавлен `build_audio_analysis_prompt(...)` в `prompts.py`.
- Это новый глубокий prompt-builder для `gemini_analyze.py`.
- Он возвращает большую часть интеллектуальной плотности старого `bot.py`, но без прежней избыточной многословности.

### 2) Логика стала адаптивной к длительности
Prompt теперь подстраивает ориентиры по:
- количеству таймкодов,
- числу вопросов,
- насыщенности `key_categories`,
- объёму `terms_data`,
- размеру `whisper_hints`.

Это помогает не перегружать короткие материалы и не обеднять длинные.

### 3) Восстановлены важные различения
Prompt снова жёстко разводит:
- `main_topic`
- `analysis_summary`
- `argument_arc`
- `key_categories`
- `terms_data`

Это ключевой момент для глубины и осмысленности ответа Gemini.

### 4) Возвращена богословская нюансировка
В prompt возвращены расширенные значения `hermeneutic_method`:
- expository
- topical
- narrative
- typological
- redemptive_historical
- catechetical
- apologetic
- evangelistic
- practical
- mixed

### 5) Сохранена инженерная надёжность рефакторинга
Весь новый runtime-каркас оставлен:
- retry
- timeout
- safe delete
- parser
- fallback-обработка

То есть глубина возвращена без отката к старому монолитному стилю.

### 6) Обновлена версия схемы prompt
В `database.py`:
- `PROMPT_SCHEMA_VERSION` → `analysis-deep-v6`

Это нужно, чтобы кэш не маскировал изменения и бот реально начал использовать новый анализ.

## Где смотреть
- `prompts.py` — новый deep prompt builder
- `gemini_analyze.py` — подключение builder
- `database.py` — bump prompt schema version

## Проверка
Синтаксис проверен через `py_compile`.
