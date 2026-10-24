# Изменения

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [SemVer](https://semver.org/lang/ru/).

Правила:

- версия объявлена **только** в `core/version.py` и печатается командой `mp3bot --version`;
- раздел `Unreleased` пополняется в том же PR, который меняет поведение;
- дата ставится в момент тега, а не заранее;
- `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security` — по назначению,
  а не «всё в Changed».

## [Unreleased]

### Added

- Генерируемый индекс документации `docs/README.md` (`tools/render_docs_index.py`),
  проверка в `verify_repo.py` и в `pre-push`.
- Корневые документы: `ARCHITECTURE.md`, `SECURITY.md`, `CONTRIBUTING.md`.
- `Package build` в CI: сборка wheel, установка `--no-deps`, `mp3bot --version`,
  проверка метаданных.
- `Secret scan` (gitleaks, полная история + weekly) и `CodeQL` (python +
  javascript-typescript, `security-extended`).
- pre-commit: ruff из lock'а, `pre-commit-hooks`, `verify_repo.py` на `pre-push`.
- Измерение покрытия: `branch = true`, порог 52 %, `diff-cover` (75 % на изменённые строки).
- `--version` / `-V` у `bot_new.py` и консольная команда `mp3bot`.
- `/healthz`: JSON с причинами нездоровья (`problems[]`) и `503`, когда бот не
  обрабатывает апдейты, БД недоступна, мало места или нет ключей Gemini.
  Старый `/health` (`OK` / `503 STALE`) не изменился.
- `/metrics` в формате Prometheus text без новых зависимостей: апдейты, отказы,
  возраст последнего апдейта, активные тяжёлые задачи, падения обслуживания,
  свободный диск, занятость слотов планировщика.
- `LOG_FORMAT=text|json`, `LOG_LEVEL`, `LOG_FILE`: JSON-строки для сборщиков,
  `update_id` в каждой строке апдейта, маскирование значений секретных
  переменных в готовом сообщении.
- Алерт владельцу в Telegram при падении ежечасного обслуживания, с антиспамом
  `MAINTENANCE_ALERT_INTERVAL_HOURS` (по умолчанию раз в 6 часов).
- Гейт статической типизации: `mypy --strict` для объявленного среза
  (`tools/check_types.py`, сейчас `core/`), шаг в CI, проверка в
  `verify_repo.py` и тест `tests/test_typecheck_gate.py`, который ловит
  ослабление настроек. Документ — `docs/typechecking.md`.

### Changed

- Проверки допуска в `pipelines/main_pipeline.py` вынесены в
  `pipelines/media_guards.py` (`live_skip`, `duration_skip`, `disk_skip`,
  `env_int`, `info_timeout_seconds`, `first_entry`, `parse_info_stdout`):
  тексты отказов и пороги закреплены тестами, `MAX_VIDEO_DURATION_SEC` больше
  не роняет пайплайн на нечисловом значении, а проверка свободного места
  снова учитывает `%TEMP%` — теперь под тестом, а не поиском строки в исходнике.
- Стадия «получить mp3» вынесена в `pipelines/audio_stage.py` (`prepare_audio`):
  одна реализация вместо двух разошедшихся копий в обычном пути и в кэш-хите.
- Сообщение «файл слишком большой» показывает итоговый размер после сжатия,
  а не исходный.
- Честная деградация вынесена в `pipelines/degraded_mode.py`: пометка
  «Gemini недоступен» в подписи и необязательные таймкоды из субтитров
  YouTube — с тестами вместо проверки «когда кончится квота».
- Матрица публикации вынесена в `pipelines/publication_plan.py`: какие
  Telegraph-страницы выпускать (включая три взаимных исключения) проверяется
  на всех 32 комбинациях флагов, а «анализа нет» больше не путается с
  «пользователь всё выключил».
- **Python floor 3.11 → 3.12**; матрица CI `[3.12, 3.13]`, ruff `target-version = py312`.
- `numpy` объявлен как `>=2.4,<3.0` (снята граница `<2.5`, которая блокировала мажор).
- `dependabot.yml`: сняты оба `ignore` для мажоров numpy/av — они больше не «заведомо красные».
- Проект устанавливается как пакет: `[build-system]`, `[project]`, `[project.scripts]`,
  `py-modules = ["bot", "bot_new", "main"]`.
- Логика установки зависимостей в CI и в `Start Bot.bat` — только из lock'а.
- `core/` приведён к `mypy --strict` без единого подавления нового класса
  ошибок: разнотипные `kwargs` конфигов Gemini аннотированы как
  `dict[str, Any]`, `None`-дефолты и `int | None` больше не протекают в
  арифметику, `Image.Resampling` вместо устаревшего `Image.LANCZOS`,
  `CREATE_NO_WINDOW` читается через `getattr` (константа только для Windows).
- `mypy 2.4` и его зависимости (`ast-serialize`, `librt`, `mypy-extensions`,
  `pathspec`) добавлены в `requirements-dev.txt` и lock.

### Fixed

- Без `py-modules` в wheel не попадали `bot.py`, `bot_new.py`, `main.py` — консольная
  команда падала бы с `ImportError` уже после установки.
- `mp3bot = "bot_new:main"` ссылался на несуществующую функцию: `bot_new` исполнял
  bootstrap на импорте и не имел реальной точки входа.
- Тест, фиксировавший число пинов литералом (69), заменён сверкой с самим lock-файлом.
- `core/source_packs.py`: `negative=(("молитва",), ("ангел",))` — кортеж
  кортежей там, где тип требует `tuple[str, ...]`. Работало только за счёт
  защитного `neg[0] if isinstance(neg, tuple)`; объявления выпрямлены,
  защита удалена.
- `core/synopsis_quality.py`: `max()` и деление на `None` в аудите плотности
  конспекта — времена секций собираются отдельной функцией, которая их
  отбрасывает, вместо фильтра в генераторе списка (его `mypy` не сужает).
- `services/quiz_generator.py`: `len(questions)` при `questions is None`
  после `enough` — проверка сузилась до `if enough and questions`.
- `core/observability.py`: `int(cur.lastrowid)` падал бы на `None`; теперь
  отсутствие вставки возвращает `None`, как и объявлено в сигнатуре.
- `services/ffmpeg.py`, `services/gemini_quota_domains.py`: `subprocess.CREATE_NO_WINDOW`
  и `int(getattr(exc, "code", None))` больше не падают на не-Windows и на
  исключениях без кода.

### Security

- `gitleaks` + `.gitleaks.toml` с узким allowlist (пути целиком не разрешены).
- `filelock 3.32.2 → 4.2.4` (CVE-2026-112940).
- `setuptools 84.0.0 → 84.2.0` (GHSA-xmj3-9gf7-h52w).
- Убран литерал образца ключа AWS из документации аудита — документ сам становился
  находкой сканера.

### Removed

- Шесть файлов `docs/*_latest.md`: переименованы по области и дате, суффикс запрещён
  проверкой.

## [4.0.0] - 2026-10-24

Первый релиз с версией и лицензией: до него проект не объявлял ни того, ни другого.

### Added

- `LICENSE` (MIT), `license = "MIT"` и `license-files` в метаданных пакета.
- `core/version.py` как единственный источник версии.
- `docs/AUDIT_2026-10-23_NEXT.md` — измеренный аудит и план до профессионального уровня.

### Changed

- Lock пересобран: brotli 1.2.1, `undici` 7.30.1 в VOT-хелпере, `pdfkit` удалён,
  8 мёртвых env-переменных удалены, `docs/env-reference.md` (174 переменные) генерируется.
- Зависимости обновлены: `python-telegram-bot 22.8`, `pytest 9.1.1`, `ruff 0.16.1`,
  `yt-dlp` — по политике ежедневных сгруппированных обновлений.

### Fixed

- Аудит зависимостей стал fail-closed: пустой отчёт больше не считается чистым.
- `npm audit` для VOT-хелпера и smoke-тест его API-контракта — в CI.
- Утечка `docs/page_dumps/` (108 файлов чужого текста) из репозитория.
