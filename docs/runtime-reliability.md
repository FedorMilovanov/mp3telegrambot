# Надёжность Local Bot API и больших файлов

## Рекомендуемая конфигурация Windows

```env
LOCAL_BOT_API_URL=http://127.0.0.1:8081
LOCAL_BOT_API_EXE=C:\Program Files\TelegramBotAPI\telegram-bot-api.exe
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=ваш_api_hash
LOCAL_BOT_API_DATA_DIR=C:\Users\Fedor\AppData\Local\TelegramBotAPI\data

LOCAL_BOT_API_SMART_BOOTSTRAP=1
LOCAL_BOT_API_AUTOSTART=1
LOCAL_BOT_API_SMART_TIMEOUT_SEC=30
LOCAL_BOT_API_CLOUD_FALLBACK=1

TELEGRAM_PROXY_URL=socks5h://127.0.0.1:10808
CLOUD_MEDIA_AUTO_COMPRESS=1
CLOUD_MEDIA_TARGET_MB=47
```

### Важные правила

- `LOCAL_BOT_API_AUTOSTART=0` запрещает боту останавливать или запускать Local Bot API. Если уже работающий сервер не отвечает, бот переходит в облако только при доступном cloud proxy.
- По умолчанию бот завершает только PID из `botapi-server.pid` или подтверждённый процесс `telegram-bot-api`, слушающий порт из `LOCAL_BOT_API_URL`.
- `LOCAL_BOT_API_ALLOW_GLOBAL_KILL=1` — аварийный режим. Он разрешает глобальный kill по имени процесса и обычно не нужен.
- `LOCAL_BOT_API_PROXY_URL` поддерживается только для `http://` и `https://`. SOCKS/MTProto нельзя превращать в несуществующие CLI-флаги официального бинаря; для них нужен системный TUN/VPN или облачный Bot API через `TELEGRAM_PROXY_URL`.
- `LOCAL_BOT_API_SMART_TIMEOUT_SEC` ограничен диапазоном 10–75 секунд. Предварительный TCP-probe является только подсказкой; окончательное решение принимается по локальному `/getMe`.

## Поведение больших файлов

1. При рабочем Local Bot API исходный файл отправляется без перекодирования.
2. При фактическом cloud endpoint файл до целевого лимита отправляется как есть.
3. Большой файл кодируется во временный `.part`, проверяется `ffprobe`, затем атомарно переименовывается.
4. Кэш принимается только когда совпадают поток, длительность, актуальность и размер.
5. Если первая попытка видео не уложилась, запускается более строгая повторная попытка.
6. В Telegram сохраняется исходное имя файла, а `width`, `height` и `duration` берутся из готовой копии.

## Диагностические сообщения

Успешный Local API:

```text
✅ Local Bot API восстановлен за ...с (@bot, проверок: ...)
```

Настоящий cloud fallback:

```text
☁️ Local Bot API недоступен: ... Использую облачный API; большие видео будут автоматически сжаты...
[CloudMediaFallback] Сжимаю ... → <=47.0MB
[CloudMediaFallback] Готово: ...cloud47.mp4 (...MB, ...x...)
```

Токен бота, API hash и пароль proxy маскируются в выводимом хвосте `botapi-server.log`.

## Файлы состояния

В родительской папке `LOCAL_BOT_API_DATA_DIR` создаются:

- `botapi-server.log` — журнал официального сервера;
- `botapi-server.pid` — PID экземпляра, которым управляет этот проект.

После ручной смены пути data-dir старый PID-файл можно удалить только при полностью остановленном `telegram-bot-api.exe`.
