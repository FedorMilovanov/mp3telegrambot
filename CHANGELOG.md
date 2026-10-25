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
  (`tools/check_types.py`), шаг в CI, проверка в `verify_repo.py` и тест
  `tests/test_typecheck_gate.py`, который ловит ослабление настроек и сверяет
  срез с `docs/typechecking.md`. Срез: `core/` и `pipelines/` (кроме
  `main_pipeline.py`, см. P0-3).

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
- Стадия подписи вынесена в `pipelines/caption_stage.py` (`CaptionSource`,
  `fit_caption`, `CaptionFit`): сборка и обрезка до 1024 видимых символов —
  одна реализация на обычный путь и на кэш-хит.
- Стадия анализа вынесена в `pipelines/analysis_stage.py`
  (`AnalysisRequest`, `AnalysisDeps`, `AnalysisOutcome`, `run_analysis`):
  прогресс → Gemini → аудит темы заголовка → ранние alt-links → конспект,
  либо честная деградация. Порядок шагов закреплён тестами на фейковых
  зависимостях; боевые зависимости собирает `production_deps()`.
- Стадия рендера вынесена в `pipelines/telegraph_render.py`
  (`RenderRequest`, `RenderDeps`, `RenderedPages`, `render_pages`): пять
  публикующих функций, взаимные исключения, opt-in объединённый
  Study+Reflection с откатом на раздельные вызовы и перенос компактной
  замены «Вопросы» из навигации. Сбой одной страницы не отменяет остальные.
- Навигация между страницами вынесена в `pipelines/telegraph_nav.py`:
  сборка блока, снятие предыдущего, компактный вариант у лимита размера,
  обход частей по «➡ Дальше», защита от циклов и две попытки при сетевом
  сбое. HTTP заменён на `PageIO`, поэтому правила проверяются без сети.
- Автопочинка после публикации вынесена в
  `pipelines/telegraph_repair_stage.py`: разворот цепочки частей, изоляция
  страниц (сбой одной не отменяет остальные) и структурный отчёт
  (`changed_count`, `errors`) для архива. Раньше это был один `try` на все
  страницы внутри пайплайна.
- Входные данные подписи вынесены в `pipelines/caption_inputs.py`: приоритет
  новых страниц над legacy compact, скрытие дубля Терминов, статус
  публикации, покрытие таймкодами и предупреждение о неполной публикации.
  Чистые функции — 20 тестов на таблицах случаев.
- Долговременный архив публикации вынесен в `pipelines/archive_stage.py`:
  нормализация записи, сохранение страницы → статуса автопочинки → сегментов.
  Сбой любой операции возвращается в `ArchiveResult` и не отменяет отправку
  готового MP3; стадия покрыта поведенческими тестами на 100 %.
- Свежая отправка результата вынесена в `pipelines/delivery_stage.py`: ID3,
  три попытки MP3 с `RetryAfter`/backoff и LiveDub deferral, сохранение
  `audio_file_id`, полное описание, PDF и quiz. Пользовательский порядок,
  сообщения и некритичность дополнений закреплены тестами; стадия покрыта на
  100 %, отдельный cache-hit путь не объединялся с ней.
- Cache-hit отправка перенесена в `deliver_cached_result`: мгновенный resend
  по `file_id`, очистка протухшего ID и файловый fallback теперь используют
  общую покрытую стадию. Различия fresh/cache в ID3, PDF boundaries/messages,
  chat action и quiz сохранены явно и проверены тестами.
- Решение о cache-hit вынесено в `pipelines/cache_stage.py`: актуальность
  записи и dirty-метаданные проверяются отдельной стадией с 35 тестами и
  100 % statement/branch coverage; thumbnail, audio, caption и delivery не
  связаны с этим решением внутри оркестратора.
- Получение обложки fresh/cache-hit объединено в
  `pipelines/thumbnail_stage.py`: reuse файла, yt-dlp с timeout 30 секунд,
  использование появившегося файла после rc≠0 и некритичный `None` при ошибке
  закреплены 17 тестами; различия текстов логов сохранены.
- Ратчет покрытия по файлам (`tools/check_coverage_ratchet.py`,
  `docs/coverage_baseline.json`): покрытие объявленных файлов может только
  расти. Компенсирует `diff-cover` там, где изменения — проводка в
  разрезаемом файле.
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
- `pipelines/` приведён к `mypy --strict`: сигнатуры пайплайнов Shorts, Clips,
  Montage, Highlights, плейлиста и Shorts Factory размечены (`Any` — только для
  telegram-объектов), `frames` и «Task» получили параметры. Длительность в
  `create_shorts_candidates`/`create_clips_candidates` и
  `send_factory_full_translation_if_enabled` объявлена как `int | float`:
  ffprobe отдаёт доли секунды, и приведение к `int` на стороне вызова меняло
  бы текст промпта; `short_trim_save` принимает `float` — колонки имеют
  INTEGER affinity, дробные значения и раньше сохранялись как REAL.
- `pipelines/shorts.py` импортирует `InlineKeyboardButton`/`InlineKeyboardMarkup`
  из `telegram`, а не через `core.globals`: неявный реэкспорт ломался при
  любом изменении в globals и запрещён strict-режимом.

### Fixed

- Повреждённая структура кэша (`ai_data` не dict, `questions` не list или
  нестроковое metadata-поле) больше не роняет обработку: запись отклоняется
  как `dirty_meta`, а результат пересоздаётся по обычному fresh-пути.
- Без `py-modules` в wheel не попадали `bot.py`, `bot_new.py`, `main.py` — консольная
  команда падала бы с `ImportError` уже после установки.
- `mp3bot = "bot_new:main"` ссылался на несуществующую функцию: `bot_new` исполнял
  bootstrap на импорте и не имел реальной точки входа.
- Тест, фиксировавший число пинов литералом (69), заменён сверкой с самим lock-файлом.
- Кэш-хит собирал футер подписи в обратном порядке и без заголовков секций:
  одна и та же запись выглядела по-разному при первом прогоне и при повторной
  отправке из кэша. Футер теперь один на оба пути, порядок «Читать Подробный
  Разбор → Смотреть Видео» закреплён тестом.
- `ts_in_cap` считался по копии строки таймкодов, а не по финальному состоянию
  подписи; в архив уходит значение из `CaptionFit.timestamps_shown`.
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
