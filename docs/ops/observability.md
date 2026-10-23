# Наблюдаемость: healthz, метрики, логи

Что смотреть, когда «бот не отвечает», и как это увидеть раньше, чем напишет
пользователь. Всё ниже работает без новых зависимостей: HTTP отдаёт `waitress`,
который уже поднят в `main.py`, метрики — обычный текст.

## Эндпоинты

Порт — переменная `PORT` (по умолчанию `10000`, так требует Render/Railway),
сервер — `waitress` на `0.0.0.0` с 4 потоками; `DISABLE_HEALTH_CHECK=1`
отключает сервер вместе с эндпоинтами (локальный запуск на Windows).

| Путь | Код | Назначение |
|---|---|---|
| `/` | 200 | `Bot is running!` — для «процесс жив?» |
| `/health` | 200 / 503 | `OK` / `STALE`. Исторический бинарный ответ; **формат не меняется**, на него могут смотреть внешние мониторы |
| `/healthz` | 200 / 503 | JSON с деталями: что именно сломано |
| `/metrics` | 200 | Prometheus text exposition (`text/plain; version=0.0.4`) |

`/health` отвечает `STALE`, если `mark_bot_alive()` не вызывался > 300 с.
`/healthz` смотрит на **реальную обработку**: возраст последнего принятого
апдейта, а не на то, что цикл проснулся.

### `/healthz`

```json
{
  "ok": false,
  "problems": ["maintenance_failed"],
  "version": "4.0.0",
  "uptime_seconds": 8412.2,
  "updates": {"total": 1523, "rejected_total": 4, "last_age_seconds": 12.4},
  "maintenance": {
    "runs_total": 3,
    "failures_total": 1,
    "ok": false,
    "last_age_seconds": 1820.0,
    "last_error": "OSError: [Errno 28] No space left on device"
  },
  "database": {"ok": true},
  "disk_free_gb": 1.4,
  "jobs": {"gpu_slots_in_use": 1, "gpu_slots_limit": 1,
           "whisper_slots_in_use": 0, "whisper_slots_limit": 1},
  "gemini_keys_configured": 2,
  "alerts_total": 1
}
```

`503` выставляется при любом из:

| `problems` | Порог | Что делать |
|---|---|---|
| `updates_stale` | > 300 с без апдейта | смотреть сеть/`getUpdates`; проверить, что не запущен второй экземпляр |
| `maintenance_stale` | > 7200 с без прогона | фоновая задача обслуживания умерла — перезапуск |
| `maintenance_failed` | последний прогон упал | `last_error` + `bot.log`; чаще всего диск |
| `database_unavailable` | `SELECT 1` не выполнился | файл `bot_cache.db` занят/повреждён |
| `disk_low` | < 2 ГБ свободно | чистить `bot_cache/`, `data/`, архивы |
| `gemini_keys_missing` | ни одного `GEMINI_API_KEY*` | без ключей анализ не работает — это не «здоровый» бот |

Сразу после старта `updates.last_age_seconds` и `maintenance.last_age_seconds`
могут быть `null` — «ещё не было», а не «зависло»; такие значения не дают 503.

Ответ не содержит путей, имён пользователей и значений ключей: `/healthz`
слушают внешние мониторы, а не доверенные лица.

### `/metrics`

```
mp3bot_up                       1 если /healthz ответил бы 200
mp3bot_database_ok              1 если SELECT 1 выполнился
mp3bot_uptime_seconds
mp3bot_updates_total            counter
mp3bot_updates_rejected_total   counter (дедупликация, устаревший pending)
mp3bot_last_update_age_seconds  -1 если апдейтов ещё не было
mp3bot_jobs_active              тяжёлые задачи, занявшие слот планировщика
mp3bot_maintenance_failures_total
mp3bot_last_maintenance_age_seconds
mp3bot_maintenance_last_ok
mp3bot_last_alert_age_seconds   -1 если алертов ещё не было
mp3bot_disk_free_bytes          -1 если размер не удалось узнать
mp3bot_gpu_slots_in_use / mp3bot_gpu_slots_limit
mp3bot_whisper_slots_in_use / mp3bot_whisper_slots_limit
```

`mp3bot_up` считается той же функцией, что и код ответа `/healthz`
(`services/health_report.py:collect_problems`), поэтому они не могут разойтись.

Пример правила Prometheus:

```yaml
groups:
  - name: mp3bot
    rules:
      - alert: Mp3BotDown
        expr: mp3bot_up == 0
        for: 10m
      - alert: Mp3BotUpdatesStale
        expr: mp3bot_last_update_age_seconds > 900
        for: 10m
      - alert: Mp3BotDiskLow
        expr: mp3bot_disk_free_bytes < 2 * 1024 * 1024 * 1024
        for: 30m
```

Проверка руками:

```bash
curl -s 127.0.0.1:10000/healthz | python -m json.tool
curl -s 127.0.0.1:10000/metrics | grep '^mp3bot_'
```

## Логи

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `LOG_FORMAT` | `text` | `text` — читаемо в консоли, `json` — строка JSON на запись |
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `LOG_FILE` | `bot.log` | путь к файлу; пустая строка — только stderr |

Файл ротируется: 5 МБ × 3 архивных копии. Каталог создаётся при старте; если
файл недоступен (нет прав, занят), бот продолжает работать и пишет в stderr —
диагностика важнее, чем падение на старте из-за прав.

Текстовая строка:

```
2026-10-24 12:31:07,412 INFO    [8842131] services.shorts_video: рендер начат
```

`[8842131]` — `update_id`, к которому относится строка. Его выставляет
`_pending_update_guard` (`main.py`) для каждого апдейта, поэтому по одному
номеру собирается вся история запроса, включая строки сервисов, которые о
Telegram ничего не знают. Фоновые задачи, созданные внутри обработки апдейта,
наследуют `update_id`; вне обработки там `-`.

JSON-строка:

```json
{"ts": "2026-10-24T12:31:07+0300", "level": "INFO", "logger": "services.shorts_video",
 "msg": "рендер начат", "update_id": "8842131", "job": "-", "thread": "MainThread"}
```

### Маскирование секретов

`SecretMaskingFormatter` заменяет на `***` значения переменных `BOT_TOKEN`,
`TELEGRAM_API_HASH`, `GEMINI_API_KEY*`, `YANDEX_*_TOKEN`/`_SECRET`, `VK_*TOKEN`,
`OPENAI_API_KEY` — в любой строке лога, включая URL с токеном внутри
(`https://api.telegram.org/bot<TOKEN>/...`). Значения короче 8 символов не
маскируются: иначе значение вида `1` вырезало бы половину сообщений.

Это defense-in-depth, а не разрешение печатать секреты: код по-прежнему не
должен их логировать.

## Алерты владельцу

Раньше падение ежечасного обслуживания уходило только в `logger.warning`, и
узнать о нём можно было, лишь открыв `bot.log`. Теперь `_periodic_maintenance`
при исключении отправляет одно сообщение `ADMIN_IDS` и увеличивает
`mp3bot_maintenance_failures_total` / `alerts_total`.

`MAINTENANCE_ALERT_INTERVAL_HOURS` (по умолчанию `6`) ограничивает частоту: если
причина постоянная (нет прав на каталог), алерт каждую минуту превратился бы в
шум, который перестают читать. `0` отключает задержку, но не сами алерты.

## Что проверить после деплоя

1. `curl /healthz` → 200 и `"problems": []` (или осмысленный список).
2. `curl /metrics | grep mp3bot_up` → `1`.
3. Отправить боту сообщение и найти в логе его `update_id`.
4. `LOG_FORMAT=json` на одной строке: `python -c 'import json,sys;json.loads(sys.stdin.readline())'`.
