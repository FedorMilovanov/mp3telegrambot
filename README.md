# MP3Bot — Telegram Media Audio Converter + AI Analysis

## Структура проекта

```
mp3bot/
├── bot_new.py              ← Точка входа (запускать ЭТО)
├── bot.py                  ← Совместимый launcher (перенаправляет на bot_new.py)
├── main.py                 ← run_bot_async(), main()
├── Start Bot.bat               ← Windows launcher
├── cookies.txt             ← Куки для yt-dlp
├── yt-dlp.conf             ← Конфиг yt-dlp
├── .env                    ← Переменные окружения (создать самому!)
│
├── core/                   ← Фундамент (без внешних зависимостей проекта)
│   ├── globals.py          ← Глобальные переменные, Flask, Gemini-клиенты
│   ├── core_utils.py       ← Чистые утилиты (time_to_seconds, RTL fix и т.д.)
│   ├── database.py         ← SQLite CRUD, кэш, настройки, rate limit
│   ├── text_utils.py       ← Очистка текста, нормализация
│   ├── url_utils.py        ← YouTube URL helpers
│   ├── utils.py            ← Вспомогательные функции (файлы, thumbnail)
│   ├── prompts.py          ← Все промпты для Gemini AI
│   ├── json_parser.py      ← Парсинг JSON от Gemini
│   └── progress.py         ← Прогресс-бар
│
├── converters/             ← Конвертеры контента
│   ├── md_telegraph.py     ← Markdown → Telegraph nodes (⚠️ бывш. markdown.py)
│   └── caption.py          ← build_caption для Telegram
│
├── services/               ← Внешние сервисы и тяжёлая логика
│   ├── telegraph.py        ← Telegraph publisher
│   ├── telegraph_pages.py  ← Study/Reflection/Analytics/Terms/Questions
│   ├── search.py           ← RuTube/VK поиск альтернатив
│   ├── gemini_analyze.py   ← Анализ аудио через Gemini API
│   ├── ffmpeg.py           ← FFmpeg / yt-dlp helpers
│   ├── shorts_video.py     ← Рендер субтитров и видео Shorts
│   ├── shorts_candidates.py← Поиск кандидатов для Shorts/Clips
│   ├── render_clips_montage.py ← Рендер клипов и монтажа
│   └── pdf_generator.py    ← Генерация PDF из Telegraph
│
├── handlers/               ← Telegram bot хэндлеры
│   ├── commands.py         ← /start, /help, /settings, /pdf, /resetcache, /stop
│   └── callbacks.py        ← Кнопки (InlineKeyboard callbacks)
│
└── pipelines/              ← Бизнес-логика обработки
    ├── main_pipeline.py    ← process_single_video (главный pipeline)
    ├── shorts.py           ← process_and_send_shorts
    ├── clips.py            ← process_and_send_clips
    ├── montage.py          ← process_and_send_montage/highlights
    └── playlist.py         ← handle_playlist
```

## Быстрый старт

1. Создайте `.env` файл:
```
BOT_TOKEN=ваш_телеграм_токен
GEMINI_API_KEY=ваш_ключ_gemini
TELEGRAPH_TOKEN=ваш_telegraph_токен
# Для ENG-режимов с Яндекс «Живыми голосами»:
# VOT_API_TOKEN=y0_AgA...  # OAuth-токен Яндекса, см. .env.example
# YANDEX_OAUTH_TOKEN=...    # альтернативное имя, тоже поддерживается
```

2. Установите зависимости:
```
pip install python-telegram-bot python-dotenv google-genai yt-dlp flask requests Pillow
pip install faster-whisper  # опционально, для субтитров
pip install pdfkit beautifulsoup4  # опционально, для PDF
pip install waitress  # опционально, для production HTTP
```

3. Запустите:
```bash
python bot_new.py        # Linux/macOS
py -3.13 bot_new.py      # Windows
```

Или дважды кликните `Start Bot.bat` на Windows.


### Работа без TUN/VPN

Бот может работать без TUN-режима, но proxy надо задавать в правильном слое:

- без `LOCAL_BOT_API_URL`: задайте `TELEGRAM_PROXY_URL` — Python/PTB будет ходить
  к облачному Bot API через proxy;
- с `LOCAL_BOT_API_URL`: Python ходит только в `127.0.0.1`, а сам
  `telegram-bot-api.exe` должен подключаться к Telegram DC через
  `LOCAL_BOT_API_PROXY_URL` или `LOCAL_BOT_API_TDLIB_PROXY_*`.

Примеры есть в `.env.example`.

### ENG / «Живые голоса» Яндекса

Для режимов `ENG Full` и `ENG Quick` нужен `VOT_API_TOKEN` в `.env`
(или альтернативное имя `YANDEX_OAUTH_TOKEN`).
Это OAuth access token аккаунта Яндекса для VOT API. Без него «Живые голоса»
работают только для роликов, которые уже есть в серверном кэше Яндекса; новые
ролики могут отвечать `SESSION_REQUIRED` / `Translation not available`.

Короткая инструкция получения токена и пример строки `.env` есть в `.env.example`.
Обычные TTS-голоса по умолчанию выключены: `LIVEDUB_TTS_FALLBACK=0`, чтобы ENG
не подменял живой перевод неживым.


LiveDub captions: в `ENG Full` название берётся из уже готового Gemini-анализа
(`real_title/real_author`), без отдельного запроса. В `ENG Quick` отдельный
Gemini-запрос на перевод названия по умолчанию выключен; используется оригинальное
YouTube-название + словарь известных авторов. Включить отдельный перевод title:
`LIVEDUB_TITLE_TRANSLATE=1`.


## Исправленные баги (относительно рефакторинга)

### 🔴 Критические

1. **`markdown.py` → `md_telegraph.py`** — файл `markdown.py` конфликтовал с 
   системной библиотекой `markdown` (pip). Python импортировал не тот модуль →
   `ImportError: cannot import name '_HEADING_BOLD_STRIP_RE'`.

2. **Потеряны regex-паттерны `_HEADING_BOLD_STRIP_RE` и `_ENSURE_TS_INLINE_RE`** —
   были определены в оригинальном `bot.py` (строка 3050), но потерялись при 
   разнесении по файлам. Восстановлены в `converters/md_telegraph.py`.

3. **Циклический импорт `markdown.py ↔ telegraph.py`** — `markdown.py` лениво 
   импортировал из `telegraph.py`, а `telegraph.py` — из `markdown.py`. 
   Решено: lazy imports сохранены с правильными путями.

### 🟡 Средние

4. **`telegraph_pages.py` импортировал `_fix_rtl_in_text` из `md_telegraph`** — 
   но эта функция определена в `core_utils.py`. Импорт исправлен.

5. **`Start Bot.bat` использовал абсолютный путь** — заменён на `%~dp0` (относительный).

### 🟢 Улучшения

6. **Организация по папкам** — 30+ файлов из одной папки разнесены по 5 логическим 
   пакетам (`core/`, `converters/`, `services/`, `handlers/`, `pipelines/`).
