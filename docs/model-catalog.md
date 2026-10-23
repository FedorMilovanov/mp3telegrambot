# Каталог моделей Gemini

Policy: `project-gemini-routing-2026-10-22`. Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — не редактируйте вручную.

## Как менять модель

1. Отредактируйте одну строку в `core/model_catalog.py` — `ROLE_MODELS[ROLE_PRIMARY]`
   (для служебной модели — `ROLE_UTILITY`). Новая модель обязана быть
   доступна на free tier: платная модель ломает все бесплатные ключи.
2. Запустите `python scripts/render_model_catalog.py`.
3. Запустите `python tools/verify_repo.py`.

Продовый код импортирует роли (`MODEL_PRIMARY`, `MODEL_UTILITY`, ...), а не
конкретные идентификаторы, поэтому правки в сервисах не нужны.
Генератор обновляет `.env.example`, `config/shorts_factory.env.example`,
`README.md`, `docs/gemini-operations.md`, `docs/shorts_factory_max.md`,
`docs/translation_editorial_review.md` и `scripts/migrate-gemini-38.ps1`.

### Проверка схемы (upgrade drill)

```bash
python scripts/model_upgrade_drill.py --model gemini-3.6-flash
```

Дрилл подменяет `ROLE_PRIMARY`, убеждается, что `--check` фиксирует
рассинхронизацию, прогоняет генератор и контрактные тесты, затем
восстанавливает файл из бэкапа. Зелёный прогон = апгрейд модели не требует
правок в коде. Если тест начнёт падать — значит в нём появился литерал
модели вместо роли.

Каталог моделей (снимок 2026-10-22). Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — не редактируйте вручную.

### Роли

| Роль | Модель | Free tier | Кто использует |
|---|---|---|---|
| `primary` (semantic) | `gemini-3.8-flash` | ✅ free | Synopsis, LiveDub QA, Factory, publication, info cards |
| `utility` (mechanical) | `gemini-3.5-flash-lite` | ✅ free | только служебные/косметические задачи |
| `fallback` (downgrade) | `(не задан)` | — | по умолчанию выключен: cross-model downgrade запрещён |
| `tts` (speech) | `gemini-3.8-flash-tts` | ✅ free | доступно, пока не используется |
| `tts_lite` (speech) | `gemini-3.8-flash-lite-tts` | ✅ free | доступно, пока не используется |
| `transcribe` (speech) | `gemini-3.5-transcribe` | ✅ free | доступно, пока не используется |
| `live` (realtime) | `gemini-3.8-live` | ✅ free | доступно, пока не используется |
| `reasoning` (reasoning) | `(не задан)` | — | нет бесплатной модели: 4 Argon только paid tier |

### Бесплатные ключи (основное ограничение deployment'а)

Режим: `FREE_TIER_ONLY = True` — проект работает на бесплатных ключах Gemini, поэтому платная модель не может быть маршрутом по умолчанию.
Источник: https://ai.google.dev/gemini-api/docs/pricing (Standard, 'Free Tier' column), verified 2026-10-23.

**Бесплатно (free tier):**

`gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-flash-native-audio-preview-12-2025`, `gemini-2.5-flash-preview-tts`, `gemini-2.5-pro`, `gemini-3-flash-preview`, `gemini-3.1-flash-lite`, `gemini-3.1-flash-tts-preview`, `gemini-3.5-flash`, `gemini-3.5-flash-lite`, `gemini-3.5-transcribe`, `gemini-3.6-flash`, `gemini-3.7-flash`, `gemini-3.8-flash`, `gemini-3.8-flash-lite-tts`, `gemini-3.8-flash-tts`, `gemini-3.8-live`, `gemini-3.8-live-extended-thinking`

**Только paid tier — бесплатный ключ получит отказ:**

`gemini-2.5-pro-preview-tts`, `gemini-3.1-pro-preview`, `gemini-4-argon`

**Не проверено / модель выключена:**

`gemini-1.0-pro`, `gemini-1.5-flash`, `gemini-1.5-flash-001`, `gemini-1.5-pro`, `gemini-2.0-flash`, `gemini-2.0-flash-001`, `gemini-2.0-flash-lite`, `gemini-2.0-flash-lite-001`, `gemini-2.5-computer-use-preview-10-2025`, `gemini-2.5-flash-preview-05-20`, `gemini-2.5-flash-preview-09-25`, `gemini-2.5-pro-preview`, `gemini-2.5-pro-preview-03-25`, `gemini-2.5-pro-preview-05-06`, `gemini-2.5-pro-preview-06-05`, `gemini-3-pro-preview`, `gemini-3.1-flash-lite-preview`, `gemini-3.1-flash-live-preview`, `gemini-pro`, `gemini-pro-vision`

Что это значит на практике:

- лимиты free tier считаются **на Google Cloud project и на модель**, а не на ключ; несколько ключей одного проекта квоту не умножают;
- четыре слота `GEMINI_API_KEY`..`_4` имеют смысл только как четыре разных проекта (`GEMINI_QUOTA_DOMAIN*` фиксирует это локально);
- апгрейд на платную модель — это не «одна строка», а осознанное решение: сначала `FREE_TIER_ONLY = False`, потом правка роли;
- `reasoning`-роль пуста намеренно: бесплатной reasoning-модели у Google нет.

### Устаревшие alias'ы (провайдер сам перенаправляет запрос)

| Alias | Deprecated | Реальная модель |
|---|---|---|
| `gemini-3.5-flash` | 2026-10-08 | `gemini-3.6-flash` |
| `gemini-3.7-flash` | 2026-10-08 | `gemini-3.8-flash` |

### Объявленные миграции

| Модель | Крайний срок | Замена |
|---|---|---|
| `gemini-3.1-flash-tts-preview` | 2026-11-17 | `gemini-3.8-flash-tts` |
| `gemini-2.5-flash-preview-tts` | 2026-11-17 | `gemini-3.8-flash-tts` |
| `gemini-2.5-pro-preview-tts` | 2026-11-17 | `gemini-3.8-flash-lite-tts` |
| `gemini-3.1-flash-live-preview` | 2026-11-17 | `gemini-3.8-live` |
| `gemini-2.5-flash-native-audio-preview-12-2025` | 2026-11-17 | `gemini-3.8-live` |
| `gemini-3.1-flash-lite` | 2027-05-07 | `gemini-3.5-flash-lite` |

### Legacy, доступ ограничен (не deprecated)

| Модель | Замена |
|---|---|
| `gemini-2.5-flash` | `gemini-3.8-flash` |
| `gemini-2.5-flash-lite` | `gemini-3.5-flash-lite` |
| `gemini-2.5-pro` | `gemini-3.8-flash` |

### Отключены

`gemini-1.0-pro`, `gemini-1.5-flash`, `gemini-1.5-flash-001`, `gemini-1.5-pro`, `gemini-2.0-flash`, `gemini-2.0-flash-001`, `gemini-2.0-flash-lite`, `gemini-2.0-flash-lite-001`, `gemini-2.5-computer-use-preview-10-2025`, `gemini-2.5-flash-preview-05-20`, `gemini-2.5-flash-preview-09-25`, `gemini-2.5-pro-preview`, `gemini-2.5-pro-preview-03-25`, `gemini-2.5-pro-preview-05-06`, `gemini-2.5-pro-preview-06-05`, `gemini-3-pro-preview`, `gemini-3.1-flash-lite-preview`, `gemini-pro`, `gemini-pro-vision`

## Переопределения

| Переменная | Роль |
|---|---|
| `MP3BOT_PRIMARY_MODEL` | `primary` |
| `GEMINI_MODEL` | `primary` |
| `MP3BOT_UTILITY_MODEL` | `utility` |
| `MP3BOT_FALLBACK_MODEL` | `fallback` |
| `MP3BOT_TTS_MODEL` | `tts` |
| `MP3BOT_TTS_LITE_MODEL` | `tts_lite` |
| `MP3BOT_TRANSCRIBE_MODEL` | `transcribe` |
| `MP3BOT_LIVE_MODEL` | `live` |
| `MP3BOT_REASONING_MODEL` | `reasoning` |
| `MP3BOT_FALLBACK_MODEL` | `fallback` (пусто = выключено) |
