# MP3Bot — Telegram Media Converter и AI-анализ

MP3Bot принимает ссылки на видео и плейлисты, извлекает аудио, строит русскоязычные публикационные материалы и поддерживает режимы перевода через Yandex LiveDub.

Запускать нужно **только `bot_new.py`**. Эта точка входа проверяет окружение, резервирует единственный процесс, поднимает обязательный Local Bot API и устанавливает runtime-защиты до запуска polling.

## Что выдаёт бот

### Русский режим

- MP3 с обложкой и метаданными;
- тема, описание, таймкоды и цитаты;
- конспект и дополнительные Telegraph-страницы;
- опциональные Shorts, клипы и монтаж.

### ENG Full

- полный AI-анализ;
- видео с русским LiveDub-переводом;
- **чистый русский MP3** без английского фона;
- **финальный объединённый MP3** — звук именно из отправленного видео;
- смысловая QA-проверка перевода и автоисправление подтверждённых major-участков.

### ENG Quick

- видео с переводом;
- чистый русский MP3;
- финальный объединённый MP3;
- без полного конспекта и смысловой QA.

### ENG Quick QA

Тот же комплект, что в ENG Quick, плюс облегчённая QA коротких роликов.

## Требования

- Python 3.11 или новее;
- FFmpeg и FFprobe в `PATH`;
- локальный сервер `telegram-bot-api`;
- системный TUN/VPN для Local Bot API либо рабочая поддерживаемая сеть;
- Node.js 22+ или Deno 2.3+ для современных YouTube challenge-механизмов;
- OAuth-токен Яндекса для стабильного создания новых LiveDub-переводов.

Для Whisper-субтитров, PDF и аппаратного рендера нужны дополнительные зависимости, описанные в `requirements.txt`, `requirements-dev.txt` и `.env.example`.

## Быстрый старт

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Создайте `.env`:

```dotenv
BOT_TOKEN=telegram_bot_token
GEMINI_API_KEY=gemini_api_key

# Обязательный Local Bot API
LOCAL_BOT_API_URL=http://127.0.0.1:8081
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=telegram_api_hash

# Для новых LiveDub-переводов
VOT_API_TOKEN=yandex_oauth_access_token
# Альтернативное имя:
# YANDEX_OAUTH_TOKEN=yandex_oauth_access_token
```

Запуск:

```bash
python bot_new.py          # Linux/macOS
py -3.13 bot_new.py        # Windows
```

На Windows также можно использовать `Start Bot.bat`.

## Local Bot API и сеть

Текущий production-контракт **не использует тихий облачный fallback**. Если локальный `/getMe` не отвечает, `bot_new.py` завершает запуск до скачивания и обработки больших файлов.

Это сделано намеренно:

- локальный API поддерживает большие медиафайлы;
- результат не меняется скрытно из-за облачного лимита Telegram;
- ошибка сети обнаруживается при старте, а не после долгой обработки видео.

Для локального сервера обычно нужен системный TUN/VPN. Переменные proxy для Python или yt-dlp не заменяют сетевой маршрут самого `telegram-bot-api` процесса.

### Работа без TUN/VPN

Полностью облачный режим оставлен только в старых совместимых модулях и не используется точкой входа `bot_new.py`. В production Local Bot API остаётся обязательным. Proxy-настройки всё ещё полезны для обязательного cloud `logOut`, Gemini и отдельного процесса yt-dlp:

```dotenv
TELEGRAM_PROXY_URL=socks5h://127.0.0.1:10808
YTDLP_PROXY_URL=http://127.0.0.1:10808
GEMINI_PROXY_URL=http://127.0.0.1:10808
LOCAL_BOT_API_CLOUD_FALLBACK=0
```

Официальный `telegram-bot-api` не получает старые SOCKS/MTProto TDLib-флаги. Для самого локального сервера нужен работающий системный маршрут.

## Gemini policy

**Все идентификаторы моделей живут в одном месте — [`core/model_catalog.py`](core/model_catalog.py).**
Продовый код импортирует роли (`MODEL_PRIMARY`, `MODEL_UTILITY`, ...), а не строки, поэтому
обновление модели = правка каталога + `python scripts/render_model_catalog.py` + `tools/verify_repo.py`.

Runtime фиксирует quality-policy до импорта AI-клиентов:

- основной user-visible semantic-контур: роль `primary` + `HIGH` thinking;
- Shorts Factory MAX остаётся на `primary` / HIGH / трёх независимых проходах и не понижается до utility/Lite при ошибках провайдера;
- LiveDub QA и публикационный смысловой текст используют тот же `primary`/HIGH route;
- Translation Editorial review — отдельный явный `primary`/HIGH контракт, не fallback из Factory;
- дешёвые механические/utility-задачи: только роль `utility` + minimal;
- cross-model fallback по умолчанию **выключен** (`MP3BOT_FALLBACK_MODEL=`); подмена модели на лету — осознанное решение, а не скрытый дефолт;
- `503/high demand` трактуется как backend-capacity: bounded retry остаётся на том же client/upload и не умножается числом ключей;
- `429/RESOURCE_EXHAUSTED` трактуется отдельно как quota/rate-limit и может переключить credential, не обнуляя уже израсходованный 503 budget;
- несколько API-ключей одного Google Cloud project **не дают несколько независимых quota** — лимиты Gemini применяются к проекту.

Рекомендуемая явная настройка:

```dotenv
MP3BOT_PRIMARY_MODEL=gemini-3.8-flash
MP3BOT_UTILITY_MODEL=gemini-3.5-flash-lite
MP3BOT_FALLBACK_MODEL=
```

Ручки `GEMINI_MAX_MODEL`, `GEMINI_FORCE_THINKING_LEVEL`, `GEMINI_LIGHT_MODEL`,
`GEMINI_LIGHT_FALLBACK_MODELS`, `GEMINI_LIGHT_ALLOW_MAIN_FALLBACK` удалены:
код их не читал (маршрут задаётся каталогом и ролью), а стартовая политика
перезаписывала значения, так что они создавали только иллюзию управления.
Оставшиеся `GEMINI_MODEL` / `SHORTS_FACTORY_MODEL` по-прежнему форсируются
политикой — если значение отличается, в лог пишется предупреждение.

Старые/слабые значения в QA-контурах поддерживаются только как миграционный вход: startup-policy заменяет их на текущий quality route. Для локального `.env` используйте `scripts/migrate-gemini-38.ps1`.

<!-- MODEL-CATALOG:START -->
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
<!-- MODEL-CATALOG:END -->

Операционный контракт, различие `503`/`429`, проверка project quota и миграция Standard → Auth keys описаны в [`docs/gemini-operations.md`](docs/gemini-operations.md).

## LiveDub и два MP3

Для каждого успешного LiveDub-видео бот формирует две независимые версии:

1. `Название — чистый RU.mp3` — сохранённая русская дорожка перевода;
2. `Название — финальный микс.mp3` — аудио, извлечённое из окончательного видео после задержек, ducking и QA-исправлений.

Обе версии:

- отдельно проверяются FFprobe;
- сверяются по длительности с финальным видео;
- получают отдельные Telegram `file_id`;
- отдельно хранятся в кэше;
- не считаются полным комплектом, если одна из версий отсутствует или повреждена.

Старые производные файлы вроде `*.final-mix.mp3` и `*.ru-audio.mp3` не могут быть повторно выбраны как чистая русская дорожка.

Production ENG-контракт использует **только Yandex Live Voices**. Если живые голоса недоступны, бот должен вернуть явную ошибку, а не тихо перейти на обычные TTS-голоса. Startup-policy принудительно держит:

```dotenv
LIVEDUB_TTS_FALLBACK=0
```

Старое локальное значение `LIVEDUB_TTS_FALLBACK=1` не считается production opt-in: pre-main policy всё равно возвращает live-only режим.

## QA перевода

- все валидные `major`-таймкоды покрываются автоисправлением;
- пересекающиеся интервалы объединяются;
- положительный лимит не обрезает список молча: частичный автофикс отклоняется;
- пересобранное видео проверяется по длительности и наличию видеопотока;
- финальный объединённый MP3 создаётся уже из проверенной версии видео.

## Качество Synopsis и timed transcript

Перед созданием дословного Synopsis бот может скачать YouTube/auto captions и передать Gemini **timed transcript** как временной скелет речи. Цель — получить не обзорную статью, а материал, который остаётся структурированной почти-дословной стенограммой исходного выступления.

```dotenv
SYNOPSIS_YT_TRANSCRIPT=1
SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS=120000
SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE=0.70
SYNOPSIS_VERBATIM_PROMPT=1
SYNOPSIS_STRUCTURED=0
```

Частичные captions с недостаточным покрытием отбрасываются. Verbatim-prompt и schema-free режим сохраняются по умолчанию, потому что лучше держат плотность и последовательность исходной речи.

## Основные команды

- `/start` — стартовая панель;
- `/help` — актуальная справка по режимам и результатам;
- `/mode` — выбор RUS / ENG Full / ENG Quick / ENG Quick QA;
- `/settings` — настройки функций;
- `/archive` — последние публикации;
- `/search <текст>` — поиск по архиву;
- `/segments` — список смысловых сегментов;
- `/cut` — вырезание сегмента;
- `/status`, `/metrics`, `/codehealth` — административная диагностика.

## Структура

```text
bot_new.py                 проверенная точка входа
main.py                    lifecycle Telegram Application
core/                      БД, настройки, промпты, общие модели
handlers/                  команды и callback-обработчики
pipelines/                 основные пользовательские сценарии
services/                  LiveDub, Gemini, FFmpeg, Telegraph и runtime-защиты
converters/                Telegram/Telegraph форматирование
tests/                     unit и regression-тесты
tools/                     диагностика и вспомогательные CLI
```

## Проверка качества

GitHub Actions выполняет:

- полный `pytest` на Python 3.11, 3.12 и 3.13;
- компиляцию всех Python-файлов;
- Ruff fatal checks;
- отдельный Windows runtime-набор на Python 3.13;
- `dependency-audit`: контракт lock'а + `pip-audit` по разрешённым пинам;
- реальные FFmpeg/FFprobe-интеграционные тесты двух MP3;
- code-health отчёт по росту regex/postprocess-слоя.

Ручной запуск:

```bash
python -m pytest -q
python -m ruff check --select E9,F63,F7,F82 .
python tools/check_code_health.py
python tools/check_requirements_lock.py
python tools/audit_requirements_lock.py   # требует pip-audit в PATH
```

Порядок обновления зависимостей и правила исключений описаны в
[`docs/dependency-policy.md`](docs/dependency-policy.md).

## Безопасная эксплуатация

- не запускайте одновременно несколько экземпляров бота;
- не публикуйте `.env`, OAuth-токены, cookies и Telegram API hash;
- после изменения аудиомикса или QA-политики меняйте cache fingerprint;
- проверяйте startup-логи после обновления FFmpeg, yt-dlp, Node/Deno или Gemini-моделей.
