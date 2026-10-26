# Архитектура MP3Bot

Документ описывает **как устроено сейчас**, а не как хотелось бы. Всё измеримо:
числа в таблицах получены разбором AST по репозиторию, ссылки на исключения —
конкретные `файл:строка`.

## Что это

Telegram-бот для обработки медиа: скачивание (YouTube и др.), конвертация в MP3,
транскрипция, AI-анализ через Gemini, публикация конспектов в Telegraph,
генерация PDF, субтитров и Shorts, а также дубляж (LiveDub / Dub Studio) с
синтезом речи через локальные TTS-модели (VoxCPM2 и др.).

Работает **на своей машине**: Windows-хост с GPU, локальный сервер
`telegram-bot-api` (нужен для файлов >20 МБ), файловая система и SQLite вместо
внешней БД. Отсюда два следствия, которые видны в коде:

- всё состояние — локальное (`core/database.py`, каталог `downloads/`);
- фоновая работа идёт в пуле потоков (`run_in_executor`) рядом с event loop,
  потому что рендер видео и TTS — блокирующие и тяжёлые.

## Точки входа

```
Start Bot.bat ──┐
python bot.py ──┼──> bot_new.py ──> services/bot_lifecycle.run_bot_process
python -m main ─┘        │                        │
                         │                        └──> main.py: run_bot_async
                         ├── services/runtime_manifest (pre/post bootstrap)
                         └── services/database_migrations.apply_database_migrations
```

| Файл | Роль |
|---|---|
| `Start Bot.bat` | Windows-лаунчер: проверяет Python (3.12/3.13), пересоздаёт `.venv` при смене lock'а, ставит зависимости, запускает `bot_new.py` |
| `bot.py` | Тонкий совместимый шim для старых скриптов: выполняет `bot_new.py` как `__main__` |
| `bot_new.py` | Bootstrap: проверка версии Python, чек-лист рантайма, миграции, затем передача жизненного цикла сервису |
| `main.py` | Приложение: PTB-хендлеры, планировщик обслуживания (раз в час), Flask-хелсчек под `waitress` |
| `mp3bot` (консольная команда) | Тот же вход, что `python bot_new.py`, для установленного пакета (`pyproject.toml` → `[project.scripts]`) |

`--version` обрабатывается **до** bootstrap (в `bot_new.py`, сразу после настройки
stdio): версия — единственное, что обязано отвечать без зависимостей, БД и сети.

## Слои

Пакеты упорядочены по направлению зависимостей. Числа — количество импортов
между пакетами в прод-коде:

| Пакет | Модулей | Роль | Импортирует |
|---|---|---|---|
| `handlers/` | 11 | Telegram-слой: команды, колбэки, визарды дубляжа | `core` (33), `services` (25), `tools` (3), `pipelines` (2), `converters` (1) |
| `pipelines/` | 9 | Сценарии из нескольких сервисов | `services` (85), `core` (34), `converters` (6), `handlers` (1) |
| `services/` | 129 | Доменные операции: Gemini, Telegraph, дубляж, TTS, PDF | `core` (166), `converters` (11), `tools` (5), `pipelines` (3), `handlers` (9) |
| `converters/` | 3 | Рендеринг текста: Markdown → Telegraph/PDF, субтитры | `core` (15), `services` (6) |
| `core/` | 44 | Фундамент: конфиг, БД, модели, промпты, утилиты | `services` (4) |
| `tools/` | 97 | CLI, миграции, экспериментальные прогоны моделей | `services` (49), `core` (11), `converters` (1) |

Целевое направление: `handlers → pipelines → services → converters → core`, причём
`core` не импортирует ничего из остальных пакетов. **Сейчас есть известные
исключения** — они перечислены ниже, чтобы «нарушение» не приходилось искать
grep'ом.

### Известные нарушения направления

| Нарушение | Где | Почему существует |
|---|---|---|
| `core → services` | `core/globals.py` (`gemini_capacity_control`) | процесс-wide конфиг исторически знает о capacity-контроллере; чистые health/quota-контракты уже перенесены в `core/` |
| `converters → services` | `converters/md_telegraph.py` (`pdf_generator`) | после успешного editPage конвертер инвалидирует PDF-кэш напрямую |
| `services → handlers` | `services/dub_studio_runtime.py` (7 handler-модулей) | Dub Studio поднимает хендлеры, чтобы переиспользовать меню и регистрацию команд |
| `services → pipelines` | `services/shorts_factory_execution_guard.py:275`, `services/shorts_factory_source.py:489`, `services/translation_editorial_runner.py:38` | циклическая зависимость Shorts: сервис запускает пайплайн |

Исполняемый контракт (`import-linter`) уже подключён через
`tools/check_import_layers.py`: направление
`handlers → pipelines → services → converters → core` проверяется на реальном
графе импортов. Перечисленные legacy-рёбра временно записаны как **точные**
`ignore_imports` в `.import-linter`; wildcard-исключения запрещены. Это burn-down,
а не постоянное разрешение: новые нарушения падают, а после удаления legacy-ребра
его запись обязательно удаляется в том же PR (устаревшая запись тоже падает).
Целевое состояние P1-6 — пустой список исключений.

## Путь запроса

```
Telegram update
   │
   ├─ handlers/commands.py · callbacks.py · mode_command.py   (разбор и меню)
   │
   ├─ pipelines/main_pipeline.py                              (сценарий анализа)
   │     ├─ services/youtube_* · services/search.py           (получение источника)
   │     ├─ services/gemini_* · core/prompts.py               (анализ, промпты, кэш)
   │     ├─ converters/md_telegraph.py                        (рендеринг разметки)
   │     ├─ services/telegraph*.py                            (публикация страниц)
   │     └─ services/pdf_generator.py · converters/caption.py (PDF и субтитры)
   │
   ├─ pipelines/shorts_factory.py → services/shorts_*          (Shorts)
   └─ handlers/dub_wizard.py → services/dub_*                  (дубляж)
```

Тяжёлые стадии выполняются в пуле потоков; прогресс и статусы — в SQLite.

## Данные

| Что | Где | Особенности |
|---|---|---|
| Метаданные, кэши, статусы | `core/database.py` (SQLite) | WAL + `busy_timeout`, миграции в `services/database_migrations.py`, горячий бэкап нативным `sqlite3.backup` раз в сутки |
| Медиа и промежуточные файлы | каталог `downloads/` | чистится планировщиком (`cleanup_stale_downloads`, `cleanup_nosub_files`), TTL по типам |
| Опубликованные страницы | Telegraph (внешний сервис) + локальный архив `docs/generated_pages_archive.md` | архив содержит только названия и публичные ссылки |
| Логи | `bot.log` (ротация 5 МБ × 3) | формат и путь настраиваются (см. §Наблюдаемость в аудите) |

## Внешние сервисы

| Сервис | Модули | Ограничение |
|---|---|---|
| Gemini (free tier) | `services/gemini_*`, `core/model_catalog.py` | только бесплатная квота: при исчерпании — ожидание, а не платный fallback ([`dependency-policy.md`](dependency-policy.md)) |
| Telegram Bot API | `main.py`, `handlers/`, локальный `telegram-bot-api` | файлы >20 МБ требуют локального сервера |
| Telegraph | `services/telegraph*.py` | лимиты на размер узла и глубину вложенности |
| YouTube / VK | `services/youtube_*`, `services/search.py`, `tools/ensure_bgutil_provider.py` | PO-token рантайм пинится вне pip |
| Yandex | `services/yandex_live_dub.py` | OAuth-токен, LiveDub |

## Гейты качества

Всё это выполняется на каждый PR и агрегируется в `Required PR Gate`:

| Гейт | Что ловит |
|---|---|
| `pytest` (3.12, 3.13) + `ruff` | регрессии и синтаксические ошибки |
| `Package build` | wheel собирается, `mp3bot --version` работает без зависимостей |
| `Dependency audit` | lock == замыкание, известные CVE, отсутствие вторых владельцев версий |
| `gitleaks`, `CodeQL` | секреты в истории, SAST-находки |
| `tools/verify_repo.py` | compileall, генерируемые артефакты (env, модели, **индекс документации**), pytest, ruff, `git diff --check` |
| Windows-джобы | рантайм-паритет: пути, кодировки, `.bat`-лаунчер |

## Куда добавлять код

- новая команда бота → `handlers/`, регистрация в `main.py`;
- сценарий из нескольких сервисов → `pipelines/`;
- работа с внешним API, файлами, моделями → `services/`;
- чистая функция без I/O (разметка, форматирование, правила) → `core/` или `converters/`;
- разовый CLI, миграция, эксперимент → `tools/` (прод-код не должен импортировать `tools`, кроме одного известного места `services/dub_worker.py`).

Правило, которое проверяется тестами: **любая новая env-переменная** попадает в
`docs/env-reference.md` (генерируется), **любой новый документ** — в индекс
`docs/README.md` (генерируется).
