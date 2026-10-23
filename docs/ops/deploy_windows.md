# Установка и обновление на Windows

Документ описывает **воспроизводимую** установку с нуля: от чистой машины до
работающего бота, который сам поднимается после перезагрузки. Всё, что здесь
написано, проверяемо командами; ручных «поправь что-нибудь в реестре» нет.

## 0. Что понадобится

| Компонент | Версия | Зачем |
|---|---|---|
| Windows | 10/11 x64 | прод-платформа проекта |
| Python | **3.12 или 3.13** | floor проекта; 3.12 требуется numpy 2.5 / av 19 |
| Git | любая | клонирование и обновление |
| FFmpeg + FFprobe | 6.0+ | конвертация, субтитры, рендер |
| Node.js | 22+ | рантайм для yt-dlp (`js-runtimes`) и VOT-хелпера |
| `telegram-bot-api` | последний | файлы >20 МБ (локальный Bot API) |
| GPU (опционально) | CUDA 12.x | VoxCPM2 / TTS и ускорение Whisper |

Node и FFmpeg должны быть в `PATH` **до** запуска бота: он проверяет их на старте
и печатает диагностику.

## 1. Код и окружение

```powershell
git clone https://github.com/FedorMilovanov/mp3telegrambot.git C:\mp3bot
cd C:\mp3bot
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
```

Ставится **lock**, а не `requirements.txt`: только в нём точные версии, проверенные
аудитом уязвимостей. `Start Bot.bat` делает то же самое и дополнительно
пересоздаёт `.venv`, если hash lock'а изменился.

## 2. Конфигурация

```powershell
Copy-Item .env.example .env
notepad .env
```

Обязательный минимум:

| Переменная | Значение |
|---|---|
| `BOT_TOKEN` | токен из @BotFather |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | my.telegram.org (нужны для локального Bot API) |
| `GEMINI_API_KEY` | ключ AI Studio (бесплатный тариф) |
| `LOCAL_BOT_API_URL` | `http://127.0.0.1:8081`, если поднят локальный сервер |

Полный список — [`../env-reference.md`](../env-reference.md) (генерируется из кода).

## 3. Локальный Bot API

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\repair-local-bot-api.ps1 -ApplyConfig
```

Скрипт проверяет сеть, чистит зависшие процессы, поднимает сервер и ждёт
`/getMe`. Если сеть требует прокси/VPN — включите TUN: `telegram-bot-api` не
использует `TELEGRAM_PROXY_URL`.

## 4. Автозапуск

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Start
```

Скрипт регистрирует задачу планировщика `MP3Bot`:

- запуск при загрузке системы, от текущего пользователя (нужны его `%TEMP%`,
  его `.env` и его GPU-сессия; `SYSTEM` это ломает);
- `pythonw.exe bot_new.py` в каталоге репозитория, без окна консоли;
- перезапуск через минуту при падении, без ограничения по времени работы;
- запрет второго экземпляра (`MultipleInstances IgnoreNew`): два бота — это
  двойные ответы и гонки за SQLite.

Проверка состояния и health-check:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Status
```

`Status` печатает состояние задачи, время последнего запуска/код возврата и
ответ `/health` (если бот обслуживает HTTP). Ручной эквивалент:

```powershell
Invoke-WebRequest http://127.0.0.1:10000/health -UseBasicParsing
```

`OK` — бот жив; `503 STALE` — процесс есть, но обработка апдейтов не идёт
больше 5 минут (`mark_bot_alive` не вызывался). Это первый признак зависания.

## 5. Обновление

```powershell
cd C:\mp3bot
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Stop
git pull --ff-only
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
.\.venv\Scripts\python tools\check_requirements_lock.py
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Start
```

Если нужна конкретная версия, а не `main`:

```powershell
git fetch --tags
git checkout v4.0.0
```

## 6. Откат

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Stop
git checkout v3.9.0        # предыдущий тег
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
powershell -ExecutionPolicy Bypass -File .\scripts\install-service.ps1 -Action Start
```

Миграции БД применяются вперёд при старте. Порядок «сначала тег, потом
зависимости, потом старт» важен: lock в теге соответствует коду тега.

## 7. Данные и бэкапы

| Что | Где | Как восстанавливать |
|---|---|---|
| SQLite (`bot_cache.db`) | корень репозитория | `db_backup()` делает горячую копию раз в сутки (нативный `sqlite3.backup`, корректно при WAL) |
| Медиа и кэши | `downloads/` | не нужно: чистится по TTL |
| `.env` | корень | **не в git**; храните копию в менеджере секретов |
| Логи | `bot.log` (5 МБ × 3) | для разбора инцидента |

Перед обновлением достаточно остановить задачу — бэкап БД делается сам, но
`Copy-Item bot_cache.db bot_cache.db.manual` перед сменой мажорной версии не лишний.

## 8. Диагностика

| Симптом | Что проверить |
|---|---|
| Задача «Running», бот молчит | `bot.log`; `/health`; второй экземпляр не запущен (`Get-Process python*`) |
| `health: 503 STALE` | процесс жив, но event loop занят: смотрите блокирующие операции (§`ASYNC240` в аудите) |
| Файлы >20 МБ не отправляются | локальный Bot API запущен? `LOCAL_BOT_API_URL` совпадает с портом сервера? |
| YouTube «требует подтверждения» | Node в `PATH`, PO-token рантайм собран (`tools/ensure_bgutil_provider.py`) |
| Gemini 429 | исчерпана бесплатная квота: бот ждёт, платного fallback нет ([`../dependency-policy.md`](../dependency-policy.md)) |
| Whisper не стартует | CUDA/cuDNN версии, `faster-whisper` в lock, свободная VRAM |

## 9. Чек-лист приёмки после установки

- [ ] `mp3bot --version` (или `.\.venv\Scripts\python bot_new.py --version`) печатает версию;
- [ ] задача `MP3Bot` в состоянии `Running`;
- [ ] `/health` отвечает `OK`;
- [ ] бот отвечает на `/start` в Telegram;
- [ ] файл >20 МБ скачивается и отправляется (локальный Bot API работает);
- [ ] `bot.log` не содержит `❌` в первых 50 строках.
