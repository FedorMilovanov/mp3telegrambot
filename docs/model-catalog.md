# Каталог моделей Gemini

Policy: `project-gemini-routing-2026-10-22`. Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — не редактируйте вручную.

## Как менять модель

1. Отредактируйте одну строку в `core/model_catalog.py` — `ROLE_MODELS[ROLE_PRIMARY]`
   (для служебной модели — `ROLE_UTILITY`).
2. Запустите `python scripts/render_model_catalog.py`.
3. Запустите `python tools/verify_repo.py`.

Продовый код импортирует роли (`MODEL_PRIMARY`, `MODEL_UTILITY`, ...), а не
конкретные идентификаторы, поэтому правки в сервисах не нужны.
Генератор обновляет `.env.example`, `config/shorts_factory.env.example`,
`README.md`, `docs/gemini-operations.md`, `docs/shorts_factory_max.md`,
`docs/translation_editorial_review.md` и `scripts/migrate-gemini-38.ps1`.

### Проверка схемы (upgrade drill)

```bash
python scripts/model_upgrade_drill.py --model gemini-4-argon
```

Дрилл подменяет `ROLE_PRIMARY`, убеждается, что `--check` фиксирует
рассинхронизацию, прогоняет генератор и контрактные тесты, затем
восстанавливает файл из бэкапа. Зелёный прогон = апгрейд модели не требует
правок в коде. Если тест начнёт падать — значит в нём появился литерал
модели вместо роли.

Каталог моделей (снимок 2026-10-22). Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — не редактируйте вручную.

### Роли

| Роль | Модель | Кто использует |
|---|---|---|
| `primary` (semantic) | `gemini-3.8-flash` | Synopsis, LiveDub QA, Factory, publication, info cards |
| `utility` (mechanical) | `gemini-3.5-flash-lite` | только служебные/косметические задачи |
| `fallback` | `(не задан)` | по умолчанию выключен: cross-model downgrade запрещён политикой |
| `tts` | `gemini-3.8-flash-tts` | доступно, пока не используется |
| `tts_lite` | `gemini-3.8-flash-lite-tts` | доступно, пока не используется |
| `transcribe` | `gemini-3.5-transcribe` | доступно, пока не используется |
| `live` | `gemini-3.8-live` | доступно, пока не используется |
| `reasoning` | `gemini-4-argon` | доступно, пока не используется |

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
| `gemini-2.5-pro` | `gemini-4-argon` |

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
