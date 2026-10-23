# Аудит 2026-10-23: что сделать, чтобы проект был профессиональным

Статус: **аудит и рекомендации.** База: `main` @ `07ba4be`, 3 102 passed / 3 skipped,
`verify_repo.py` 6/6, lock == closure (73 = 73, включая dev-инструменты).

Решения владельца, принятые после первой редакции этого документа:

| Вопрос | Решение |
|---|---|
| Лицензия | **MIT** — внедрено в PR #205 (`LICENSE`, `license = "MIT"`) |
| Gemini | **только free tier**, платный ключ не покупаем — записано в `docs/dependency-policy.md` |
| Python floor 3.11 → 3.12 | **поднят** в PR #211: ignore для numpy/av сняты, граница `numpy<2.5` убрана |

Уже сделано: PR #205 — `LICENSE`, `[project]`, `core/version.py`, `mp3bot --version`, CI-job
`Package build`, 8 тестов упаковки; PR #208 — gitleaks, CodeQL, pre-commit; PR #209 — измерение
покрытия (branch, порог 52 %, diff-cover 75 % на изменённые строки).

---

## §0. Резюме

Внутренняя инженерия проекта уже выше среднего: хешированный lock, гейт «lock == closure»,
никаких вторых владельцев версий, fail-closed аудит зависимостей, 18 workflow с path-фильтрами
и агрегирующим `Required PR Gate`, 3 105 тестов, генерируемые артефакты с `--check`.
Это уровень зрелой команды, и переделывать это не нужно.

Не хватает **внешнего кольца**, которое отличает хороший личный проект от профессионального продукта:

| # | Чего нет | Почему это приоритет |
|---|---|---|
| 1 | ~~Нет `LICENSE`~~ | **закрыто** в PR #205 |
| 2 | **Покрытие не измеряется** (фактически 56 %, в оркестрации — 8 %) | 55,8k строк тестов создают иллюзию защищённости там, где её нет |
| 3 | **Нет сканирования секретов и SAST** | 174 переменные окружения, 5 внешних сервисов с токенами |
| 4 | ~~Нет упаковки и версии~~ | **закрыто** в PR #205 (осталось: `CHANGELOG`, теги, релизы — §P1-5) |
| 5 | **Нет проверки типов**, хотя 92 % функций аннотированы | mypy: 455 ошибок в 93 файлах — это 1–2 сессии |
| 6 | **Наблюдаемость неполная** | `/health` есть (binарный OK/503), но нет метрик, JSON-логов, correlation id, алертов |
| 7 | **Нет индекса документации** | 81 md-файл, 60+ россыпью в `docs/`, включая дубли `*_latest.md` |

Порядок работ — §7. Открытые вопросы — §8.

Главный содержательный вывод: **самое рискованное место проекта — не 786 модулей, а
`pipelines/main_pipeline.py`: 3 120 строк и 8 % покрытия.** Именно там ломается прод, и именно там
тесты почти ничего не проверяют. Это важнее любого косметического рефакторинга.

---

## §1. Измеренная база

Все цифры получены командами из §9 на чекауте `07ba4be`, не оценены на глаз.

### Код и тесты

| Метрика | Значение |
|---|---|
| Python-файлов (tracked) | 786, из них 484 — тесты |
| Строк прод-кода | 115 529 |
| Строк тестов | 55 801 (48 % от прода — хороший ratio) |
| Тестов | 3 105 (3 102 passed / 3 skipped) |
| Прогон с покрытием | 88 с |
| Модулей в 5 пакетах без входящих импортов | 5, суммарно ≈2 KB — мёртвого кода практически нет |

### Покрытие (впервые измерено, `coverage` 7.17 + `pytest-cov`)

Итого: **53 778 инструкций, 56 % покрыто.**

| Пакет | Инструкций | Покрытие |
|---|---|---|
| `core` | 5 526 | **79,1 %** |
| `converters` | 1 854 | 64,4 % |
| `tools` (вкл. voxcpm2) | 16 201 | 58,4 % |
| `services` | 23 824 | 57,6 % |
| `handlers` | 2 802 | **30,2 %** |
| `pipelines` | 2 949 | **16,1 %** |
| `main.py` | 622 | **8,0 %** |

Худшие крупные модули (непокрытых инструкций):

| Модуль | Непокрыто / всего | % |
|---|---|---|
| `pipelines/main_pipeline.py` | 1 599 / 1 743 | 8 |
| `services/telegraph_pages.py` | 788 / 937 | 16 |
| `services/telegraph.py` | 678 / 896 | 24 |
| `converters/md_telegraph.py` | 621 / 1 707 | 64 |
| `handlers/commands.py` | 612 / 797 | 23 |
| `services/shorts_video_impl.py` | 595 / 793 | 25 |
| `services/pdf_generator.py` | 468 / 751 | 38 |

### Статический анализ

| Проверка | Результат |
|---|---|
| ruff (текущий гейт: 8 правил) | чисто |
| mypy (default, прод-скоуп 198 файлов) | **455 ошибок / 93 файла**; `union-attr` 141, `arg-type` 128, `assignment` 47 |
| mypy `--strict core` (44 файла) | 247 ошибок / 31 файл |
| `ruff format --check` | **642 файла из 867** не отформатированы |
| Аннотированность функций | 3 269 / 3 551 (92 %) |
| Бэклог расширенных правил ruff | `E501` 464, `ASYNC240` 200, `F401` 184, `SIM105` 146, `S110` 143, `E402` 108, `S603` 97, `UP009` 93, `UP045` 73, `PLW0603` 46 |

### Безопасность (ручной замер)

`shell=True` — 0, `eval/exec` — 0, `pickle.load` — 0, `yaml.load` — 0, `verify=False` — 0,
`requests.*` без `timeout` — **0 из 16**. Единственное место, где рядом с логом упоминается токен, —
`services/search.py:548`, и она печатает только `token present=True/False`.

Секретов в репозитории не найдено (проверено паттернами и `.gitignore`). Проблема не в коде,
а в отсутствии автоматического контроля: ни `gitleaks`, ни `trufflehog`, ни `detect-secrets`,
ни CodeQL, ни semgrep.

### CI и процессы

18 workflow: 13 с path-фильтрами, 8 с `concurrency` + `cancel-in-progress`, 17 с `timeout-minutes`,
10 с явными `permissions:`, `Required PR Gate` агрегирует все PR-workflow. Матрица Python
3.11/3.12/3.13 + `windows-latest`. **Это сильная часть проекта — не переделывать.**

Документация: 81 md; `docs/` — 60+ файлов россыпью, включая `*_latest.md`-дубли; индекса нет.
Релизы: 0 тегов, 0 релизов, нет `CHANGELOG`. Развёртывание: ни `Dockerfile`, ни systemd-юнита,
ни конфига службы Windows — только 18 ad-hoc `.ps1`.
Точки входа: `bot.py` (77 строк, compat-шим) → `bot_new.py` (202) → `main.py` (1 105).

---

## §2. Что НЕ надо делать (анти-рекомендации)

1. **Не разбивать 786 модулей на микросервисы/пакеты.** Граф импортов чист (5 сирот, ≈2 KB).
   Реально болят три файла — §5.
2. **Не гнаться за 100 % покрытием.** Цель — 70 % по `core+services+converters` и ≥50 % в
   `main_pipeline`. Тесты на `tools/voxcpm2` в гейт не включать.
3. **Не заводить Docker/Kubernetes.** Прод — Windows-хост с GPU и локальным Bot API сервером;
   контейнер добавит слой несовместимости. Воспроизводимость даёт lock (уже есть) + документ установки.
4. **Не добавлять semgrep/Snyk поверх существующего аудита.** Хватит gitleaks + CodeQL.
5. **Не включать `S101` (assert) в гейт** — 8 901 срабатывание почти целиком в тестах.
6. **Не удалять `bot.py`.** Это документированный совместимый шим; теперь у него есть законная
   замена — консольная команда `mp3bot`.
7. **Не возвращать платный Gemini.** Решение зафиксировано в `docs/dependency-policy.md`.

---

## §3. P0 — первая очередь

### P0-1. Лицензия — ✅ сделано

`LICENSE` (MIT, © 2026 Fedor Milovanov), `license = "MIT"`, `license-files = ["LICENSE"]`,
секция «Лицензия» в README. Проверено: собранный wheel содержит `License-Expression: MIT`
и `licenses/LICENSE`. Тест: `tests/test_packaging_metadata.py::test_license_file_is_the_declared_licence`.

### P0-2. Покрытие: измерять и держать ratchet'ом — ✅ сделано (PR #209)

**Было:** покрытие не измерялось нигде; `pytest-cov` не было в зависимостях; фактическое — 56 %
(statement), при этом `pipelines` 16 %, `handlers` 30 %, `main.py` 8 %.

**Стало:** `coverage[toml] 7.17` + `pytest-cov 7.1` + `diff-cover 10.6` в dev-зависимостях и lock
(73 пина, замыкание сходится); `[tool.coverage.*]` в `pyproject.toml`; на job'е 3.12 — `--cov`,
markdown-сводка в `$GITHUB_STEP_SUMMARY` и `diff-cover --fail-under=75` на изменённые строки.
Измеренное значение с `branch = true` и без `tools/*` — **52,74 %**, поэтому `fail_under = 52`.

Дальше — только ratchet: +2 пункта раз в квартал (52 → 54 → 56 …), пока не дойдём до 70.

**Осталось (не в этом PR):** пункты 1–5 ниже описывают исходный план и теперь выполнены;

**Решение:**

1. `pytest-cov` + `coverage` в dev-зависимости (`requirements-dev.txt` + lock).
2. В `pyproject.toml`:

```toml
[tool.coverage.run]
branch = true
source = ["core", "services", "converters", "pipelines", "handlers"]
omit = ["tests/*", "tools/voxcpm2/*"]

[tool.coverage.report]
fail_under = 54          # измерено 56 — гейт не должен падать от шума
skip_covered = true
show_missing = true
```

3. В `ci.yml` на job `Python 3.12`: `--cov --cov-report=xml --cov-report=term-missing`,
   `coverage report --format=markdown >> $GITHUB_STEP_SUMMARY` (процент виден прямо в PR).
4. **Гейт на изменённые строки:** `diff-cover coverage.xml --fail-under=75 --compare-branch=origin/main`.
   Единственный способ поднимать покрытие без «тестов ради тестов».
5. Ratchet: `fail_under` +2 каждые 3 месяца (54 → 56 → 58 …) до 70.

**Объём:** 2–3 часа.
**Приёмка:** в PR виден процент; PR без тестов на новый код падает на `diff-cover`.

### P0-3. Тесты на три горячие точки (главная работа, не косметика)

**Сейчас:** `main_pipeline.py` — 1 599 непокрытых инструкций (8 %), `handlers/commands.py` — 612 (23 %),
`services/telegraph*.py` — 1 466 (16–24 %).

**Решение:** не писать тесты «на файл», а зайти через извлечение стадий:

1. В `main_pipeline.py` выделить 4–5 чистых функций-стадий (`prepare_input`, `analyze`, `render`,
   `publish`, `finalize`), каждая 40–80 строк, с явными аргументами и возвратом-результатом;
   оркестратор остаётся тонким (≤150 строк).
2. Каждую стадию покрыть 6–10 тестами с фейковыми клиентами (Gemini/Telegraph/Telegram уже
   мокаются — переиспользовать фикстуры из `tests/conftest.py`).
3. `handlers/commands.py`: тесты на маршрутизацию команд — дешёвые, 10–15 штук, сразу 23 % → ~50 %.
4. `services/telegraph*.py`: замокать `_telegraph_post` на границе; проверить сборку страниц,
   цепочки, ошибки API.

**Объём:** 10–16 часов, три PR.
**Приёмка:** `main_pipeline` ≥ 50 %, `handlers/*` ≥ 55 %, `telegraph*` ≥ 60 %;
`main_pipeline.py` ≤ 1 200 строк.

### P0-4. Сканирование секретов и SAST

**Решение:**

1. **gitleaks** — блокирующий, на `push` и `pull_request`:

```yaml
- uses: gitleaks/gitleaks-action@v2
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    GITLEAKS_ENABLE_SUMMARY: true
```
   Плюс локально — хук `gitleaks` в pre-commit (§P0-5).
2. **CodeQL** — `.github/workflows/codeql.yml`, языки `python` и `javascript-typescript`,
   `on: push/pull_request/schedule(weekly)`, `permissions: security-events: write`.
   Первые две недели — **не блокирующий** (алерты в Security-вкладку), затем в `Required PR Gate`
   при 0 открытых находок уровня `error`.
3. Включить **Secret scanning + Push protection** в настройках репозитория (бесплатно для публичных).

**Объём:** 1,5–2 часа.
**Приёмка:** в PR есть чек `gitleaks`; коммит с настоящим по форме ключом AWS
(образец из документации gitleaks, не печатаю его здесь, чтобы этот файл сам не стал находкой) — красный.

### P0-5. pre-commit: те же гейты, но до пуша

**Сейчас:** нет `.pre-commit-config.yaml`; цикл обратной связи — 5–10 минут вместо 3 секунд.

**Решение:** `ruff-check --fix`, `ruff-format`, `gitleaks`, `end-of-file-fixer`,
`trailing-whitespace`, `check-merge-conflict`, `check-added-large-files` и локальный хук
`python tools/verify_repo.py` на `pre-push` (ровно то, что гоняет CI).

**Объём:** 1 час.
**Приёмка:** `pre-commit run --all-files` зелёный; правка `docs/env-reference.md` руками без
`render_env_reference.py` падает на `pre-push`.

### P0-6. Упаковка — ✅ сделано (P0-6a), осталось P0-6b

Сделано в PR #205: `[build-system]` (setuptools>=77), `[project]` с `requires-python = ">=3.11"`,
`dynamic = ["version", "dependencies"]`, `[project.scripts] mp3bot = "bot_new:main"`,
`py-modules = ["bot", "bot_new", "main"]`, `core/version.py` (`4.0.0`), `mp3bot --version`
без зависимостей, CI-job `Package build` (сборка wheel → установка `--no-deps` → `--version` →
проверка метаданных).

Что осталось (P0-6b, отдельный PR): `CHANGELOG.md` + теги + релизный workflow — см. §P1-5.

Две ошибки, которые нашла именно сборка (их не видел ни один гейт): без `py-modules` в wheel
не попадали `bot.py`/`bot_new.py`/`main.py`, а `mp3bot = "bot_new:main"` ссылался на
несуществующую функцию. Обе закрыты тестами.

---

## §4. P1 — следующий уровень

### P1-1. Форматирование и расширение ruff (в 3 волны)

**Сейчас:** `ruff format --check` — 642 файла из 867; гейт из 8 правил.

**Решение:**

1. **Волна 1 (один коммит):** `ruff format .` + SHA в `.git-blame-ignore-revs`,
   `git config blame.ignoreRevsFile`. Навсегда убирает споры о переносах и делает `E501` неактуальным.
2. **Волна 2 (автофиксы):** `UP009` (93), `UP006` (49), `UP045` (73), `W293` (47),
   `SIM105` (146 → `contextlib.suppress`), `RET504` (28), `B904` (24 → `raise ... from e`),
   `PLW1510` (34 → `check=`), затем включить эти правила в гейт.
3. **Волна 3 (ручное):** `F401` (184 — осторожно с lazy-import циклами), `S110` (143 — каждый
   `except: pass` либо логируется, либо получает `# noqa: S110` с причиной), `S603/S607` (97/20),
   `PLW0603` (46 `global`).

**`ASYNC240` (200) — отдельно и по делу:** блокирующие файловые операции внутри `async def`;
на Windows/NTFS каждый `Path.exists()`/`stat()` — миллисекунды, и они останавливают весь event loop.
Худшие: `services/shorts_video_impl.py` (26), `services/shorts_factory_video_quality.py` (17),
`services/translation_editorial_composition.py` (13), `services/render_clips_montage.py` (13).
Лечение — `await asyncio.to_thread(...)` (в проекте уже есть `run_in_executor` в maintenance).

**Объём:** 1 ч + 3 ч + 8–12 ч, `ASYNC240` — 6–10 ч.
**Приёмка:** `ruff check` и `ruff format --check` зелёные на расширенном наборе; `ASYNC240` = 0.

### P1-2. mypy: 455 ошибок — выполнимо

**Решение:** `mypy` в dev-зависимости + `[tool.mypy]` с `explicit_package_bases = true`
(иначе корневой `__init__.py` даёт «file found twice»), `ignore_missing_imports = true`,
`check_untyped_defs = true`; burn-down по классам `union-attr` (141) → `arg-type` (128) →
`assignment` (47); затем блокирующий `mypy` в CI, `--strict` — только для `core/` после обнуления 247.

Не начинать с `--strict` на всё: 3 000+ ошибок превратят гейт в «всегда красный».

**Объём:** 10–14 часов.
**Приёмка:** `mypy` в `Required PR Gate`; `mypy --strict core` = 0.

### P1-3. Наблюдаемость (скорректировано после проверки кода)

**Что уже есть — не переделывать:** Flask-приложение в `core/globals.py` с маршрутами `/`
(«Bot is running!») и `/health` (`OK` / `503 STALE`, если `mark_bot_alive()` не вызывался >300 с),
которое отдаёт `waitress` на порту из окружения (`main.py:1091`, 4 потока) с graceful shutdown
по SIGTERM; для локального запуска есть `DISABLE_HEALTH_CHECK=1`.

**Чего нет:**

1. `/health` — бинарный и без деталей. Добавить `/healthz` с `{ok, last_maintenance_at, db_ok,
   disk_free_gb, queue_depth, gemini_keys_ok}`; старый `/health` оставить как есть — на него
   могут смотреть внешние мониторы.
2. Метрик нет. `/metrics` в формате Prometheus text (`mp3bot_updates_total`,
   `mp3bot_gemini_errors_total`, `mp3bot_jobs_active`, `mp3bot_maintenance_failures_total`),
   без новых зависимостей.
3. Логи текстовые (`basicConfig` + `RotatingFileHandler("bot.log", 5 MB × 3)` в `core/globals.py:552`),
   файл пишется в cwd. Нужен `LOG_FORMAT=text|json` и `LOG_FILE`.
4. Нет correlation id: `update_id` из хендлера должен попадать в каждую строку лога апдейта
   (через `contextvars`).
5. Нет маскирования: значения `TELEGRAM_BOT_TOKEN`, `GEMINI_*_KEY`, `VK_*`, `YANDEX_*` должны
   заменяться на `***` в любой записи лога (defense-in-depth).
6. `_periodic_maintenance` (`main.py:955`) глотает исключение в `logger.warning` — если maintenance
   падает каждую ночь, узнаёт только тот, кто читает `bot.log`. Нужен алерт владельцу в Telegram
   с антиспамом (не чаще 1 раза в 6 часов).

**Объём:** 6–8 часов.
**Приёмка:** `curl 127.0.0.1:<port>/healthz` отвечает JSON с деталями; строки одного апдейта
имеют общий `update_id`; искусственное падение maintenance даёт сообщение в Telegram и рост счётчика.

### P1-4. Документация: индекс и папки

**Сейчас:** 81 md; `docs/` — 60+ файлов россыпью, включая
`docs/dub_studio_quality_v42_final_validation.md` и `..._v42_final_validation_latest.md` рядом.

**Решение:**

1. Структура: `docs/ops/` (деплой, мониторинг, бэкапы, инциденты), `docs/quality/` (по одному файлу
   на область — `dub_studio.md` вместо 13 «final/latest»), `docs/audits/` (архив),
   `docs/README.md` — индекс с одной строкой описания на файл.
2. Дубли `*_latest.md` схлопнуть в исторический файл области.
3. Гейт `tools/check_docs_index.py --check` (каждый `docs/**/*.md` упомянут в индексе; имя не
   оканчивается на `_latest`) + 7-я проверка в `verify_repo.py` — тот же паттерн, что
   `render_env_reference.py --check`.
4. Три обязательных документа: `ARCHITECTURE.md` (слои `core → services → pipelines → handlers`,
   правила направления импортов, карта потоков), `SECURITY.md` (как сообщить об уязвимости,
   поддерживаемые версии, политика секретов), `CONTRIBUTING.md` (какие гейты гонять локально).

**Объём:** 4–6 часов.
**Приёмка:** `check_docs_index.py --check` в `verify_repo.py`; в `docs/` нет файлов с `_latest`.

### P1-5. Релизы: changelog, теги, артефакты, установка

**Сейчас:** 0 тегов, 0 релизов, нет `CHANGELOG`. Версия уже есть (`core/version.py`).

**Решение:**

1. `CHANGELOG.md` по Keep a Changelog; `Unreleased` пополняется в PR (мягкая проверка: PR с
   `feat:`/`fix:` обязан добавить строку).
2. Аннотированные теги `vX.Y.Z` + `.github/workflows/release.yml` на `push: tags: v*`:
   полный прогон тестов → `python -m build` → `SHA256SUMS` → GitHub Release с wheel и sdist.
3. `docs/ops/deploy_windows.md` + `scripts/install-service.ps1`: **Task Scheduler** (рекомендация —
   без сторонних бинарников, автозапуск при загрузке, рестарт при падении), параметры
   `pythonw bot_new.py`, лог, каталог данных, обновление (git pull → lock install → restart),
   откат по тегу.

**Объём:** 5–7 часов.
**Приёмка:** `v4.0.0` отмечен тегом с артефактами; `docs/ops/deploy_windows.md` позволяет поднять
бота с нуля на чистой Windows-машине.

### P1-6. Границы слоёв как исполняемый контракт

**Сейчас:** `services/telegraph_pages.py` импортирует **приватную** `_telegraph_post` из
`services/telegraph.py`; слои держатся на дисциплине.

**Решение:** `import-linter` с `layers`-контрактом (`handlers|pipelines → services → core`) и
`forbidden`-контрактом (`core` не импортирует `services|handlers|pipelines`). Единственный законный
обход — сделать `_telegraph_post` публичной (`telegraph_post`) и реэкспортировать.

**Объём:** 2–3 часа.
**Приёмка:** `lint-imports` в CI; `core → services` невозможен без падения.

### P1-7. Мелкое, но заметное

- Явные `permissions:` в 8 workflow, где их нет (по умолчанию — `contents: read`).
- `requirements-dev.txt` уже есть; добавить в него `pytest-cov`, `mypy`, `pre-commit`, `import-linter`.
- `.editorconfig` (LF, UTF-8, 4 пробела, `*.ps1` → CRLF).
- Маркер `slow` и `-m "not slow"` для быстрого локального цикла.

---

## §5. Структура: три файла, а не 786 модулей

| Файл | Строк | Покрытие | Что делать |
|---|---|---|---|
| `pipelines/main_pipeline.py` | 3 120 | 8 % | выделить стадии (§P0-3), цель ≤1 200 строк |
| `core/prompts.py` | 3 830 | — | превратить в пакет `core/prompts/` с текстами в шаблонах и загрузчиком: 3,8k строк литералов невозможно рецензировать |
| `services/telegraph*.py` (3 файла) | 1 624 + 1 897 + ~500 | 16–24 % | один пакет `services/telegraph/` с публичным API и общим HTTP-клиентом |

Больше ничего не трогать: `converters/md_telegraph.py` (2 934) и
`tools/voxcpm2/direct_max_quality_cli.py` (2 015) — связные алгоритмы/CLI, дробление даст переходы
по файлам вместо читаемости.

---

## §6. Снятие ограничений

### 6.1. Python floor: 3.11 → **3.12** — ✅ решено и сделано (PR #211)

**Рекомендация: поднять до 3.12, матрицу свести к `[3.12, 3.13]`, Windows — 3.13.**

1. Комментарий в `ci.yml`: «3.12 is the floor for numpy 2.5 / av 19 and the newest Pillow wheels;
   3.11 stays until the project decides to drop it».
2. `dependabot.yml` содержит два `ignore` для мажоров numpy и av с пометкой «снять обе записи
   вместе с подъёмом floor'а до 3.12». Пока floor 3.11, обновиться на актуальные numpy/av нельзя.
3. `target-version = "py311"` в ruff блокирует ~215 автофиксов `UP*` и новые API.
4. Минус один job в матрице — примерно −15 % времени CI на PR.

Цена: `requires-python`, `target-version`, матрица `ci.yml`, два `ignore` в dependabot,
`tests/test_lock_python_floor.py`, `tests/test_packaging_metadata.py`, проверка версии в
`bot_new.py`, раздел «Python floor» в `docs/dependency-policy.md`. Часа два.

### 6.2. Gemini: только free tier — ✅ решено

Платный ключ не покупаем. Зафиксировано в `docs/dependency-policy.md` («Gemini: только free tier»):
free-ключи — единственный источник квоты; бюджет-гардов не добавляем; Batch API и кэширование
допустимы только как квотные рычаги; сравнение моделей на платном ключе не проводится.

Следствие для эксплуатации: при всплеске нагрузки бот деградирует по квоте (429 → ожидание),
а не по качеству модели. Это ожидаемое поведение, а не баг.

### 6.3. Инфраструктура: никаких новых сервисов

Мониторинг, health, метрики — стандартная библиотека + локальный файл + SQLite. Prometheus/Grafana/Loki
для одного бота — второй проект, который надо администрировать. `import-linter`, `mypy`,
`pre-commit`, `pytest-cov` — dev-only, на прод не влияют.

---

## §7. Дорожная карта

| Волна | Содержание | Оценка | Результат |
|---|---|---|---|
| **0. Легализация** | ~~LICENSE~~ (PR #205), ~~gitleaks + CodeQL (P0-4), pre-commit (P0-5)~~ (PR #208) | — | секреты и SAST под контролем |
| **1. Измеримость** | ~~coverage + diff-cover (P0-2)~~ — сделано в PR #209 | — | «сколько покрыто» — отвечается командой |
| **2. Риск в оркестрации** | P0-3: три PR по стадиям `main_pipeline` + тесты handlers/telegraph | 10–16 ч | главная точка отказа покрыта, файл ≤1 200 строк |
| **3. Гигиена** | P1-1 волны 1–2, P1-7 | 4–5 ч | 642 файла отформатированы, 400+ находок закрыто |
| **4. Типы и слои** | P1-2 mypy burn-down, P1-6 import-linter | 12–17 ч | типы и границы слоёв проверяются CI |
| **5. Эксплуатация** | P1-3 наблюдаемость, P1-5 релизы + деплой | 11–15 ч | инцидент виден сразу, установка воспроизводима |
| **6. Долгий хвост** | P1-1 волна 3, `ASYNC240`, P1-4 документация | 18–24 ч | event loop не блокируется, docs навигируемы |

Итого ≈ 65–90 часов. Волны 0–2 (16–23 ч) дают 80 % эффекта: сканирование, покрытие, тесты
в главной точке риска.

Целевые метрики через 6 месяцев: покрытие 70 % (и ≥50 % в `main_pipeline`), `ASYNC240` = 0,
`mypy` зелёный, 100 % `docs/**` в индексе, 0 workflow без `permissions`, релизы по SemVer.

---

## §8. Открытые вопросы

1. ~~**Python floor:** поднимаем до 3.12?~~ — поднят, матрица `[3.12, 3.13]`.
2. **Следующий PR:** тесты и разбор `main_pipeline` (волна 2, P0-3) — главная точка риска.

Лицензия и Gemini закрыты.

---

## §9. Как воспроизвести замеры

```bash
# покрытие (нужны coverage + pytest-cov в venv)
python -m pytest -q --cov=core --cov=services --cov=pipelines \
  --cov=handlers --cov=converters --cov=tools --cov=main --cov-report=term

# типы
python -m mypy --explicit-package-bases --ignore-missing-imports \
  --exclude 'tests|tools/voxcpm2' core services pipelines handlers converters main.py bot_new.py
python -m mypy --explicit-package-bases --ignore-missing-imports --strict core

# форматирование и расширенные правила
python -m ruff format --check .
python -m ruff check --statistics --select E,W,F,B,S,RET,PLW,ASYNC,C4,SIM,UP .

# упаковка (то же, что делает job Package build)
python -m build --outdir dist
python -m venv /tmp/w && /tmp/w/bin/python -m pip install --no-deps --no-index dist/*.whl
/tmp/w/bin/mp3bot --version

# размеры и структура
git ls-files '*.py' | grep -v '^tests/' | xargs wc -l | sort -rn | head -13
git grep -n "shell=True\|verify=False\|pickle.load\|yaml.load(" -- '*.py' ':!tests/*'

# CI-гигиена
for f in .github/workflows/*.yml; do
  printf "%-45s perms=%s conc=%s timeout=%s\n" "$(basename $f)" \
    "$(grep -c '^permissions:' $f)" "$(grep -c '^concurrency:' $f)" "$(grep -c 'timeout-minutes' $f)"
done
```

Замеры сделаны в venv `/tmp/v` (Python 3.11, `coverage` 7.17, `mypy` 2.4.0). На момент первого
замера в репозитории этих инструментов не было; в PR #205 они тоже не добавлены.
