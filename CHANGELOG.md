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

### Changed

- **Python floor 3.11 → 3.12**; матрица CI `[3.12, 3.13]`, ruff `target-version = py312`.
- `numpy` объявлен как `>=2.4,<3.0` (снята граница `<2.5`, которая блокировала мажор).
- `dependabot.yml`: сняты оба `ignore` для мажоров numpy/av — они больше не «заведомо красные».
- Проект устанавливается как пакет: `[build-system]`, `[project]`, `[project.scripts]`,
  `py-modules = ["bot", "bot_new", "main"]`.
- Логика установки зависимостей в CI и в `Start Bot.bat` — только из lock'а.

### Fixed

- Без `py-modules` в wheel не попадали `bot.py`, `bot_new.py`, `main.py` — консольная
  команда падала бы с `ImportError` уже после установки.
- `mp3bot = "bot_new:main"` ссылался на несуществующую функцию: `bot_new` исполнял
  bootstrap на импорте и не имел реальной точки входа.
- Тест, фиксировавший число пинов литералом (69), заменён сверкой с самим lock-файлом.

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
