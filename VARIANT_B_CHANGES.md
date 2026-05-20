# Variant B — что улучшено

## Главное
Сделан не просто возврат старой глубины, а более аккуратная и управляемая версия:
- глубина первичного аудио-анализа возвращена;
- добавлены режимы `deep / balanced / fast`;
- убраны дубли больших prompts из `telegraph_pages.py`;
- полный `STUDY_ANALYSIS_PROMPT` централизован в `prompts.py`;
- добавлен локальный аудит `audit_variant_b.py`.

## Изменённые файлы
- `prompts.py`
- `gemini_analyze.py`
- `database.py`
- `telegraph_pages.py`
- `audit_variant_b.py` (новый файл)

## Что именно сделано

### 1. `prompts.py`
- Добавлен `AUDIO_ANALYSIS_MODE` из `.env`:
  - `deep` — максимум глубины (по умолчанию)
  - `balanced` — умеренная глубина
  - `fast` — компактнее
- Добавлены helpers:
  - `_normalize_prompt_text()`
  - `_get_audio_analysis_profile()`
  - `build_audio_analysis_prompt()`
- Новый builder:
  - различает функции полей;
  - адаптирует насыщенность ответа под длину материала;
  - добавляет финальную self-check секцию;
  - не даёт путать summary / argument arc / categories / terms.
- В `prompts.py` перенесён полный `STUDY_ANALYSIS_PROMPT`.

### 2. `gemini_analyze.py`
- Основной prompt теперь строится через `build_audio_analysis_prompt()`.
- Явно передаётся режим `AUDIO_ANALYSIS_MODE`.
- Добавлен лог с длиной prompt и режимом.

### 3. `database.py`
- `PROMPT_SCHEMA_VERSION` обновлён до `analysis-deep-v6`, чтобы кэш не маскировал новый анализ.

### 4. `telegraph_pages.py`
- Удалены локальные дубли `STUDY_ANALYSIS_PROMPT` и `REFLECTION_APPLICATION_PROMPT`.
- Теперь используется единый источник из `prompts.py`.

### 5. `audit_variant_b.py`
Проверяет:
- режимы prompt builder;
- отсутствие дублирования больших prompts в `telegraph_pages.py`;
- совпадение Study/Reflection prompts со старым `bot.py`;
- синтаксис ключевых файлов.

## Как включить режим
В `.env` можно добавить:

```env
AUDIO_ANALYSIS_MODE=deep
```

Допустимые значения:
- `deep`
- `balanced`
- `fast`

Если переменная отсутствует, используется `deep`.

## Как проверить после скачивания
```bash
python audit_variant_b.py
```

## Рекомендация
Для вашей задачи лучше оставить:
```env
AUDIO_ANALYSIS_MODE=deep
```


### 6. `Запустить бота.bat`
- Исправлена точка входа: теперь батник запускает `bot_new.py`, а не старый монолитный `bot.py`.
