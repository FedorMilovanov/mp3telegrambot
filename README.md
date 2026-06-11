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

Pro-микс специально держит конец ролика ещё `LIVEDUB_DELAY_MS +
LIVEDUB_TAIL_MARGIN_MS` (по умолчанию 600 мс + 1000 мс), чтобы задержанный
русский перевод в Shorts не обрывался на последнем слове. Для коротких роликов
до `LIVEDUB_TAIL_FREEZE_MAX_SEC=180` последний кадр дозамораживается на этот
хвост; можно отключить freeze-frame через `LIVEDUB_TAIL_FREEZE_MAX_SEC=0`.
Отдельно VOT-запрос получает `ceil(duration) + LIVEDUB_VOT_DURATION_PAD_SEC`
(по умолчанию +1 сек), чтобы Shorts с фактической длиной 37.x не отдавали
live-MP3, обрезанную на последней фразе.


LiveDub captions: в `ENG Full` название берётся из уже готового Gemini-анализа
(`real_title/real_author`), без отдельного запроса. В `ENG Quick` title переводится через лёгкую модель (`GEMINI_LIGHT_MODEL`),
а при выключении `LIVEDUB_TITLE_TRANSLATE=0` используется оригинальное
YouTube-название + словарь известных авторов.


Для быстрых текстовых задач можно использовать отдельную лёгкую модель:
`GEMINI_LIGHT_MODEL=gemini-3.1-flash-lite`. Если GA-имя недоступно, fallback пробует `gemini-3.1-flash-lite-preview`, затем `gemini-2.5-flash-lite`. Она не заменяет основной
`GEMINI_MODEL`, а используется для дешёвых карточек ENG Quick: Telegram/YouTube
описание, компактные тезисы/субтитры по SRT перевода. Включено по умолчанию:
`LIVEDUB_INFO_CARD=1`; выключить можно `LIVEDUB_INFO_CARD=0`.
Title для `ENG Quick` по умолчанию тоже переводится через лёгкую модель:
`LIVEDUB_TITLE_TRANSLATE=1`; выключить можно `LIVEDUB_TITLE_TRANSLATE=0`.


`/mode` также содержит режим `⚡🔍 ENG Quick QA`: он не делает полный конспект,
но для коротких роликов (по умолчанию до 120 сек) запускает лёгкую проверку
перевода через `GEMINI_LIGHT_MODEL`. Если найдена major-ошибка, русский дубляж
в проблемном окне вырезается, а оригинал поднимается. Настройки:
`LIVEDUB_QUICK_QA_MAX_DURATION=120`, `LIVEDUB_QUICK_QA_MODEL=gemini-3.1-flash-lite`.


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

7. **Автоматический реюз видео** — пайплайн Shorts использует уже скачанные
   видео из LiveDub, экономя трафик и время.

8. **Блок «Читать также»** — в конце Telegraph-страниц автоматически выводятся
   ссылки на похожие материалы из архива по автору или теме.

9. **AV1 и NVENC** — поддержка новейших аппаратных энкодеров для максимального
   качества и сжатия видео в 2026 году.

10. **ID3-главы и метаданные** — в MP3-файлы вшиваются главы по таймкодам,
    обложка, автор и ссылки на оригинал.
