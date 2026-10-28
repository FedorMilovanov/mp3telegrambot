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
- `.editorconfig` с UTF-8/LF, 4-space indentation и CRLF для PowerShell;
  зарегистрирован opt-in pytest marker `slow` для двух тестов с реальными
  ожиданиями. Полный pytest и CI по-прежнему выполняют их; быстрый локальный
  режим — явный `-m "not slow"`.
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
  `main_pipeline.py`, см. P0-3). Срез расширен на `services/speech_backends/`:
  14 modules доведены до strict-zero; annotation `VoxCPM2Backend.aliases`
  соответствует `SpeechBackend` без изменения runtime-значения. Срез также
  включает `services/telegraph_repair.py`: typed transport helper сохраняет
  точные Telegraph URL, timeout и нефатальный suffix-probe fallback. Общий
  `services/process_executables.py` также входит в strict gate.
- Исполняемый контракт production-слоёв через `import-linter`: новые обратные
  импорты блокируются, а известные legacy-рёбра сокращаются по точному
  burn-down без wildcard-исключений (`tools/check_import_layers.py`).
- AST-классификатор formatting-only PR (`tools/check_formatting_only.py`):
  строковый `diff-cover` пропускается только при неизменных executable AST;
  полный pytest и coverage ratchet остаются обязательными.

### Changed

- Весь Python-код отформатирован Ruff отдельным коммитом `67463dc` без смены
  executable AST; SHA внесён в `.git-blame-ignore-revs` и проверяется выполняемым
  тестом. Исторический LiveDub fixture и Markdown-примеры не перезаписывались;
  `verify_repo.py` теперь требует `ruff format --check .`.
- Первый import-layer burn-down: 20 → 17 legacy-рёбер. `QUIZ_PROMPT` живёт в
  `core.prompts`, caption-link builders — в `converters.caption`; неиспользуемый
  `core.utils → services.ffmpeg` удалён. Тексты и результаты builders не изменились.
- Второй import-layer burn-down: 17 → 15 legacy-рёбер. Чистые `health_report` и
  `gemini_quota_domains` перенесены в `core`; прежние `services`-пути сохранены
  compatibility-экспортами, поэтому вызывающий код и контракты не изменились.
- Третий import-layer burn-down: 15 → 13 legacy-рёбер. Режимы пользователя и
  флаг полного Shorts-видео вынесены в `core.user_modes`; handler сохраняет
  прежние имена, а pipeline и Shorts service не зависят от Telegram UI.
- Четвёртый import-layer burn-down: 13 → 12 legacy-рёбер. Markdown→Telegraph и
  node-cleaning принадлежат `converters.telegraph_nodes`; цикл publisher ↔
  converter разорван, а прежние `services.telegraph` имена сохранены.
- Пятый import-layer burn-down: 12 → 11 legacy-рёбер. Общий Telegraph HTML
  cache перенесён в `core.telegraph_cache`; PDF сохраняет прежние exports, а
  converter больше не импортирует PDF service ради invalidation.
- Шестой import-layer burn-down: 11 → 4 legacy-рёбра. Dub Studio registration
  принадлежит `handlers.dub_studio_registration`, а output discovery/delivery —
  `services.dub_delivery`; runtime больше не импортирует UI handlers.
- Седьмой import-layer burn-down: 4 → 3 legacy-ребра. Общий Gemini transport,
  client rotation и transient retry routing перенесены из `core.globals` в
  `services.gemini_router`; process-wide config больше не импортирует services.
- Восьмой import-layer burn-down: 3 → 0 legacy-рёбер. Standalone editorial
  orchestration перенесён в pipelines, Factory env/backend policy принадлежит
  execution-guard, неиспользуемый guarded wrapper удалён. `ignore_imports`
  полностью убран: новые обратные зависимости падают без исключений.
- Extended Ruff, волна 2a: безопасные `UP009` и `UP006` исправлены во всём
  дереве (142 находки), а `UP009`, `UP006` и `W293` включены в обычный gate.
- Extended Ruff, волна 2b: все 73 `Optional[...]` переведены на PEP 604
  `... | None`; освободившиеся typing imports удалены, `UP045` включён в gate.
- Extended Ruff, волна 2c: все 35 вызовов `subprocess.run` получили явную
  non-throwing политику `check=False`; generic wrapper сохраняет переданный
  caller `check`, а `PLW1510` включён в обычный gate.
- Extended Ruff, волна 2d: 28 redundant assignments перед `return` заменены
  прямым возвратом того же выражения; `RET504` включён в обычный gate.
- Extended Ruff, волна 2e: 10 runtime raises получили явный `from cause`;
  ещё 14 CLI sites используют `from cause` для аргументов и `from None` для
  уже обработанных exits. Все 24 `B904` закрыты, правило включено в gate.
- Extended Ruff, волна 2f (tools/tests slice): 11 `try/except/pass` заменены
  `contextlib.suppress`; оставшиеся 134 production findings разбираются по слоям.
- Extended Ruff, волна 2f (entry/core/handlers): ещё 27 `SIM105` заменены;
  после двух slices остаются 107 findings в pipelines/services.
- Extended Ruff, волна 2f (pipelines): ещё 40 `SIM105` заменены, включая
  cancellation-safe teardown; после трёх slices остаются 67 service findings.
- Extended Ruff, волна 2f (Shorts/PDF/transcript services): ещё 25 `SIM105`
  заменены; остаются 42 LiveDub/process service findings до включения gate.
- Extended Ruff, волна 2f (LiveDub/process services): последние 42 `SIM105`
  заменены; findings = 0, правило включено в обязательный Ruff gate.
- Async I/O: все 12 `ASYNC230` разобраны вручную; blocking open/write перенесены
  в worker threads без изменения Telegram API contract, правило включено в gate.
- Async I/O, первый `ASYNC240` slice: 7 локализованных path-I/O sites перенесены
  в worker threads; остаются 202 findings для следующих модульных slices.
- Async I/O, второй `ASYNC240` slice: Factory source/deferral/static probes и
  cleanup переведены на worker threads; остаются 198 findings.
- Async I/O, третий `ASYNC240` slice: Clips/Shorts delivery больше не выполняет
  source/stat/thumbnail/cleanup file I/O в event loop; остаются 196 findings.
- Async I/O, четвёртый `ASYNC240` slice: thumbnail discovery и PDF size/cleanup
  переведены на worker threads; остаётся 191 finding.
- Async I/O, пятый `ASYNC240` slice: FFmpeg silence/crop probes проверяют
  исходник в worker thread; остаются 189 findings.
- Async I/O, шестой `ASYNC240` slice: Montage/Highlights проверяют borrowed
  LiveDub source в worker thread; остаются 187 findings.
- Async I/O, седьмой `ASYNC240` slice: Factory full-video delivery объединяет
  file/size validation в worker thread; остаются 185 findings.
- Async I/O, восьмой `ASYNC240` slice: Factory RU speech proof считывает
  file/size metadata в worker thread; остаются 183 findings.
- Async I/O, девятый `ASYNC240` slice: Factory long-fit stat/cleanup operations
  переведены в worker threads; остаётся 181 finding.
- Async I/O, десятый `ASYNC240` slice: Factory editorial optional documents
  проверяются в worker threads; остаются 179 findings.
- Async I/O, одиннадцатый `ASYNC240` slice: Full LiveDub QA workdir/SRT
  discovery переведено в worker threads; остаются 177 findings.
- Async I/O, двенадцатый `ASYNC240` slice: Quick LiveDub QA workdir/SRT/original
  discovery переведено в worker threads; остаются 174 findings.
- Async I/O, тринадцатый `ASYNC240` slice: Long LiveDub QA source-file checks
  переведены в worker threads; остаются 171 findings.
- Async I/O, четырнадцатый `ASYNC240` slice: LiveDub QA SRT/RU/original
  source-exists checks переведены в worker threads; остаются 168 findings.
- Async I/O, пятнадцатый `ASYNC240` slice: ENG subtitles workdir/audio
  discovery переведено в worker threads; остаются 166 findings.
- Async I/O, шестнадцатый `ASYNC240` slice: ENG original-video reuse/download
  discovery переведено в worker threads; остаются 163 findings.
- Async I/O, семнадцатый `ASYNC240` slice: MP3 source/path/cache preflight
  переведён в worker threads; остаются 154 findings.
- Async I/O, восемнадцатый `ASYNC240` slice: no-sub callback file check
  переведён в worker thread; остаются 153 findings.
- Async I/O, девятнадцатый `ASYNC240` slice: retrim source/no-sub checks
  переведены в worker threads; остаются 151 findings.
- Async I/O, двадцатый `ASYNC240` slice: archive/segment export file checks
  переведены в worker threads; остаются 148 findings.
- Async I/O, двадцать первый `ASYNC240` slice: PDF output preflight
  переведён в worker threads; остаются 146 findings.
- Async I/O, двадцать второй `ASYNC240` slice: disk diagnostics
  переведены в worker threads; остаются 145 findings.
- Async I/O, двадцать третий `ASYNC240` slice: status DB/backup и PDF cleanup
  переведены в worker threads; `handlers/commands.py` очищен, остаются 143 findings.
- Async I/O, двадцать четвёртый `ASYNC240` slice: LiveDub download discovery
  переведён в worker threads; `services/yandex_live_dub.py` очищен, остаются 140 findings.
- Async I/O, двадцать пятый `ASYNC240` slice: Shorts Factory audio preflight/input
  переведён в worker threads; `services/shorts_factory_candidates.py` очищен,
  остаются 136 findings.
- Async I/O, двадцать шестой `ASYNC240` slice: Shorts/Clips candidate audio input
  переведён в worker threads; `services/shorts_candidates.py` очищен,
  остаются 132 findings.
- Async I/O, двадцать седьмой `ASYNC240` slice: Shorts subtitle burn file I/O
  переведён в worker threads; `services/shorts_subtitle_burn.py` очищен,
  остаются 128 findings.
- Async I/O, двадцать восьмой `ASYNC240` slice: LiveDub mix output validation
  переведена в worker threads; остаются 126 findings.
- Async I/O, двадцать девятый `ASYNC240` slice: LiveDub QA-autofix file I/O
  переведён в worker threads; `services/livedub_mix.py` очищен, остаются 124 findings.
- Async I/O, тридцатый `ASYNC240` slice: LiveDub QA temporary-original cleanup
  подтверждён тестом; cancellation-safe synchronous unlink сохранён с точечным
  обоснованным `noqa`, остаются 123 findings.
- Async I/O, тридцать первый `ASYNC240` slice: Shorts normalize/transform
  input/output и same-path no-op checks переведены в worker threads;
  nonempty-output commit и empty-output cleanup подтверждены тестами,
  остаются 116 findings.
- Async I/O, тридцать второй `ASYNC240` slice: общий Shorts poster/snapshot
  output validator переведён в worker thread; success/empty paths обоих API
  подтверждены тестами, `services/shorts_video.py` очищен, остаются 114 findings.
- Async I/O, тридцать третий `ASYNC240` slice: Russian Whisper WAV validation
  и temporary-text writes переведены в owned workers; cancellation-safe cleanup
  сохранён с точечными `noqa` и тестами, `services/translation_editorial.py`
  очищен, остаются 107 findings.
- Async I/O, тридцать четвёртый `ASYNC240` slice: editorial CLI path resolution,
  source preflight и fail-closed output/provenance pair checks переведены в
  worker threads; `tools/translation_editorial.py` очищен, остаётся 101 finding.
- Async I/O, тридцать пятый `ASYNC240` slice: Highlights audio-probe/candidate
  source preflights переведены в worker threads; missing/existing-source paths
  подтверждены тестами, остаются 99 findings.
- Async I/O, тридцать шестой `ASYNC240` slice: Highlights render validation и
  failure cleanup переведены в owned workers; cancellation cleanup сохранён
  синхронным, все postconditions покрыты тестами, `services/highlights_quality.py`
  очищен, остаются 90 findings.
- Async I/O, тридцать седьмой `ASYNC240` slice: composition source size/missing
  checks используют один worker snapshot; missing/threshold/size-drift paths
  подтверждены тестами, остаются 87 findings.
- Async I/O, тридцать восьмой `ASYNC240` slice: composition render validation,
  provenance paths и атомарная sidecar+media публикация переведены в workers;
  cancellation-safe temp cleanup сохранён, `services/translation_editorial_composition.py`
  очищен, остаются 77 findings.
- Async I/O, тридцать девятый `ASYNC240` slice: Clips/Montage source, part и
  output validation используют worker-side size snapshots; signal-2, nonempty
  gates и cancellation cleanup сохранены, `services/render_clips_montage.py`
  очищен, остаются 64 findings.
- Async I/O, сороковой `ASYNC240` slice: Factory evidence и download candidate
  discovery используют owned workers; mtime ordering, <=1080p validation и
  cancellation cleanup подтверждены тестами, остаются 61 finding.
- Async I/O, сорок первый `ASYNC240` slice: Factory normalize/LONG source checks
  и output cleanup используют owned workers; `-c:v copy`, NVENC→CPU fallback и
  cancellation cleanup сохранены, `services/shorts_factory_video_quality.py`
  очищен, остаются 47 findings.
- Async I/O, сорок второй `ASYNC240` slice: Shorts download reuse/discovery,
  legacy render и transform используют workers; 10,240-byte gate и no-op copy
  подтверждены тестами, остаются 34 findings.
- Async I/O, сорок третий `ASYNC240` slice: legacy Shorts transcribe/burn source
  checks, ASS cleanup и output validation используют workers; Russian Whisper и
  WAV/ASS cleanup подтверждены тестами, остаются 27 findings.
- Async I/O, сорок четвёртый `ASYNC240` slice: Shorts poster/snapshot source,
  frame cleanup и output validation используют workers;
  `services/shorts_video_impl.py` и весь production/tools census очищены,
  остаются 21 test finding.
- Async I/O, сорок пятый `ASYNC240` slice: conversion/render async test doubles
  выносят generated-output writes, audio probe и temporary-glob в workers;
  assertions и cancellation cleanup не изменены, остаются 11 test findings.
- Async I/O, сорок шестой `ASYNC240` slice: оставшиеся delivery/segment/Shorts/
  Factory/editorial async test doubles используют workers для filesystem I/O;
  assertions и cancellation behavior сохранены, общий `ASYNC240` census = 0.
- Extended Ruff, первый ручной `F401` slice: удалены 12 явно неиспользуемых
  stdlib imports в девяти test modules; import-time contracts не затронуты,
  `F401` снижен до 182, общий extended census — 693.
- Extended Ruff, второй ручной `F401` slice: удалены ещё 17 stdlib imports
  в 14 test modules; project imports оставлены для отдельной проверки,
  `F401` снижен до 165, общий extended census — 676.
- Extended Ruff, третий ручной `F401` slice: test-level census очищен;
  устаревшие runtime-layout imports удалены, prompt constants связаны с явными
  assertions. Общий `F401` — 152, extended census — 663.
- Extended Ruff, четвёртый ручной `F401` slice: удалены шесть unused
  stdlib/typing imports в services после проверки external references;
  `F401` — 146, extended census — 657.
- Extended Ruff, пятый ручной `F401` slice: удалены семь unused stdlib/typing
  imports в Dub/LiveDub/speech/TTS modules после behavioral checks;
  `F401` — 139, extended census — 650.
- Extended Ruff, шестой ручной `F401` slice: удалены 14 unused imports в
  adaptive-generation, Gemini, Shorts и Telegraph modules; Telegram re-exports
  в `core.globals` сохранены. `F401` — 125, extended census — 636.
- Extended Ruff, седьмой ручной `F401` slice: удалены 15 unused imports в
  Caption/Telegraph converters и трёх service modules; добавлен behavioral test
  для LiveDub audio fields, 447 focused tests зелёные. `F401` — 110,
  extended census — 621.
- Extended Ruff, восьмой ручной `F401` slice: удалены семь unused imports в
  entrypoint, Shorts и Clips modules; два используемых module API оформлены как
  explicit re-exports. `F401` — 101, extended census — 612.
- Extended Ruff, девятый ручной `F401` slice: удалены 20 unused imports из
  `core.database` и `core.utils`; 305 focused tests зелёные. `F401` — 81,
  extended census — 592.
- Extended Ruff, десятый ручной `F401` slice: удалены 18 unused imports из
  command/callback handlers; 351 focused test зелёный. `F401` — 63,
  extended census — 574.
- Extended Ruff, одиннадцатый ручной `F401` slice: удалены 12 unused imports в
  пяти VoxCPM2 runtime modules; три используемых surgical facade-импорта
  сохранены. `F401` — 51, `E402` — 108, extended census — 560.
- Extended Ruff, двенадцатый ручной `F401` slice: удалены семь unused imports в
  repo/VoxCPM2 tools; публичный `clean_production_core.subprocess` сохранён как
  explicit re-export. `F401` — 43, extended census — 552.
- Extended Ruff, тринадцатый ручной `F401` slice: удалены десять late unused
  imports в восьми VoxCPM2 modules; 146 focused tests зелёные. `F401` — 33,
  `E402` — 98, extended census — 532.
- Extended Ruff, четырнадцатый ручной `F401` slice: три re-exported globals
  (`InlineKeyboardButton`, `InlineKeyboardMarkup`, `html_mod`) заменены прямыми
  stdlib/telegram imports во всех production-consumers; 960 focused tests
  зелёные. `F401` — 30, `E402` — 97, extended census — 528.
- Extended Ruff, пятнадцатый ручной `F401` slice: три consumed module-имени
  surgical runtime facade сохранены через explicit re-export/assignment;
  executed identity-contract подтверждает их связь с owner modules. 73 focused
  tests зелёные. `F401` — 27, extended census — 525.
- Extended Ruff, шестнадцатый ручной `F401` slice: удалены 18 unused imports
  из `main_pipeline`; девять LiveDub compatibility-имён сохранены explicit
  re-exports и executed identity-contract. `F401` = 0 и включён в обычный gate;
  969 focused tests зелёных, extended census — 498.
- Extended Ruff, первый ручной `E501` slice: перенесены 24 overlong lines в
  десяти test modules; JSON fixtures сохранили parsed values, остальные strings
  проверены literal-equivalence. 83 focused tests зелёные. `E501` — 148,
  extended census — 474.
- Extended Ruff, второй ручной `E501` slice: перенесены девять string/f-string
  lines в пяти `core` modules; executable AST не изменился, 372 focused tests
  зелёные. `E501` — 139, extended census — 465.
- Extended Ruff, третий ручной `E501` slice: перенесены десять lines в
  converters/handlers/main; HTML и сообщения сохранены, executable AST не
  изменился, 375 focused tests зелёные. `E501` — 129, extended census — 455.
- Extended Ruff, четвёртый ручной `E501` slice: перенесены 11 overlong lines в
  Telegraph publisher/pages; prompt/logger strings сохранены, executable AST не
  изменился, 969 focused tests зелёные. `E501` — 118, extended census — 444.
- Extended Ruff, пятый ручной `E501` slice: устранены 78 findings в 19 prompt
  string tokens; prompt values, source-substring контракт и executable AST
  сохранены, 573 focused tests зелёные. `E501` — 40, extended census — 366.
- Extended Ruff, шестой ручной `E501` slice: последние 40 findings устранены в
  27 string tokens в 14 modules; SQL/prompt/f-string values, source-substring
  контракты и executable AST сохранены, 1099 focused tests зелёные (1 platform
  skip). `E501` = 0, extended census — 326.
- `E501` включён в обязательный Ruff gate после достижения repository-wide
  нуля; classifier и diff-cover не ослаблены.
- Extended Ruff, первый ручной `S110` slice: удалены 14 silent exception
  handlers в `core`; fallback-returns сохранены, JSON parsing сужен до
  `JSONDecodeError`, logging filter при сбое маскирования стал fail-closed.
  708 focused tests зелёные. `S110` — 42, extended census — 312.
- Extended Ruff, diagnostic fallback slice: пять `S110` в encoder/language
  probes и `/status` заменены явной debug-диагностикой; NVENC→CPU, `None`
  и обязательные секции статуса сохранены. 75 focused tests зелёные.
  `S110` — 37, extended census — 307.
- Extended Ruff, Whisper CUDA cleanup: три дублированных silent handler
  объединены в `_clear_torch_cuda_cache`; сбой GPU cleanup остаётся нефатальным,
  порядок reset/model/transcribe сохранён. 121 focused test зелёный.
  `S110` — 34, extended census — 304.
- Extended Ruff, LiveDub stage fallbacks: четыре silent handler в cached
  invalidation, QA asset discovery, failure notification и cover selection
  получили диагностику; cleanup/reuse/delivery contracts сохранены. 203 focused
  tests (202 passed, 1 skipped) зелёные. `S110` — 30, extended census — 300.
- Extended Ruff, LiveDub publication/QA: шесть silent handlers заменены явной
  диагностикой; malformed metadata сохраняет fallback, remote-file cleanup
  использует общий best-effort helper без подавления cancellation. 213 focused
  tests (212 passed, 1 skipped) зелёные. `S110` — 24, extended census — 294.
- Extended Ruff, Telegraph fallbacks: семь silent handlers заменены явной
  диагностикой в legacy/service edit, Synopsis grounding, chain repair и DOM
  audit. Cache invalidation, suffix probes и Playwright→requests fallback
  остаются нефатальными; grounding сохраняет границу 24 000 символов. 88 focused
  tests зелёные. `S110` — 17, extended census — 287.
- Extended Ruff, service fallbacks: семь silent handlers в segment callback,
  Gemini, Local Bot API, PDF, Quiz и owned Shorts transcription получили явную
  диагностику. Segment lock release не маскирует результат рендера, malformed
  proxy label не раскрывает credentials, Whisper использует общий CUDA cleanup.
  99 focused tests зелёные. `S110` — 10, extended census — 280.
- Extended Ruff, финальный `S110` slice: последние десять silent handlers в
  entrypoint и pipelines получили диагностику. Mode fallback, delivery/cache
  cleanup и приоритет poster→snapshot→none сохранены; thumbnail file reads
  выполняются в owned worker. 169 pipeline-focused и 47 async-focused tests
  зелёные. `S110` = 0 и включён в обязательный Ruff gate; extended census — 270.
- `ASYNC240` включён в обязательный Ruff gate после повторного repository-wide
  нулевого census; `ASYNC230` остаётся включён, новых suppressions нет.
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
- Post-delivery дополнения вынесены в `pipelines/extras_stage.py`: порядок
  Shorts → Clips → Montage → Highlights, общий candidates-запрос и выбор
  LiveDub-файла для ENG сохранены; сбой одного необязательного рендера не
  отменяет остальные, а отмена задачи по-прежнему не поглощается. Стадия
  покрыта 23 тестами со 100 % statement/branch coverage.
- Cache-hit подготовка ссылок и подписи вынесена в
  `pipelines/cached_presentation_stage.py`: сохранённые RuTube/VK не вызывают
  повторный поиск, Термины скрываются при обеих новых страницах, а подпись
  собирается общей `caption_stage`. Стадия покрыта 15 тестами со 100 %
  statement/branch coverage; порядок чтения Telegraph-ссылок до thumbnail/audio
  и критичность ошибок внешнего поиска сохранены.
- LiveDub title/author helpers вынесены в `pipelines/livedub_title.py`:
  нормализация, bounded cache, light-info перевод и основной Gemini fallback
  с прежними env-флагами и timeout 30 секунд покрыты 100 % statement/branch
  тестами. Совместимый wrapper в `main_pipeline` передаёт текущий
  `main_pipeline.GEMINI_CLIENTS`, поэтому исторические monkeypatch-контракты
  продолжают выполняться без переписывания. Дополнительные поведенческие тесты
  закрепили in-process yt-dlp и ранние failure/cleanup ветки оркестратора.
- Завершение ENG Quick вынесено в `pipelines/livedub_quick_stage.py`:
  статусные сообщения, OAuth/generic failure-hint, playlist silent mode,
  cleanup и честный `bool` доставки проверяются девятью поведенческими
  тестами со 100 % statement/branch coverage. Вложенная LiveDub отправка
  остаётся в оркестраторе и передаётся callback'ом без смены контракта.
- Финальная очистка ресурсов пайплайна вынесена в
  `pipelines/resource_cleanup_stage.py`: cancellation LiveDub task, удаление
  рабочего каталога и Gemini `audio_part`, закрытие thumbnail и прежние
  failure-семантики закреплены десятью тестами со 100 % statement/branch
  coverage; сквозной ENG Quick тест проверяет вызов из настоящего `finally`.
- Fresh и cache-hit обработки `AudioTooLarge` объединены в
  `pipelines/audio_failure_stage.py`: разные исторические сообщения, попытка
  LiveDub отправки, cleanup и возврат `False` сохранены. Шесть прямых и два
  сквозных теста покрывают обе ветки и их failure-семантику.
- Post-analysis контекст публикации вынесен в
  `pipelines/publication_context_stage.py`: search title/author, AI
  questions/terms, один batch-запрос настроек, caption format/timestamps,
  план страниц и прежняя диагностика сохраняют порядок и значения fresh-пути.
  Две прямые проверки и сквозной fresh-path тест дают стадии 100 %
  statement/branch coverage.
- Fresh caption fit, диагностика, metadata, cache save и durable archive
  объединены в `pipelines/fresh_caption_stage.py`. Сохранены порядок операций,
  пропуск persistence без анализа, существующий `duration`, приоритетные
  caption-links и исходные archive URLs; четыре прямых теста и расширенный
  integration до `deliver_result` проверяют проводку и failure-семантику.
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

- Генератор `docs/env-reference.md` больше не зависит от переносов строк:
  multiline `os.getenv`/`os.environ` собираются до и после форматирования одинаково;
  в справочник возвращены два пропущенных технических имени.
- `pre-commit` добавлен в `requirements-dev.txt` и точный lock вместе с транзитивным
  замыканием: чистая установка по документации теперь действительно предоставляет
  команду `pre-commit`. В инструкции убран небезопасный для легаси-кода запуск
  `pre-commit run --all-files` вместо проверки только изменённых файлов.
- Dependency audit читает уже resolved lock через `--no-deps --disable-pip`:
  собственное окружение `pip-audit` больше не может незаметно исключить из отчёта
  совпадающий установленный пин вроде `packaging`.
- Tag push больше не запускает записывающие pre-commit hooks на всём дереве:
  file hooks ограничены стадией commit, а на pre-push остаётся только read-only
  `verify_repo.py`.
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

- Пять git-subprocess вызовов в runtime identity, Required PR Gate и repository
  tools больше не используют partial executable path: `git` разрешается через
  `shutil.which`, отсутствие executable обрабатывается явно, shell не применяется.
  Extended `S607` census сокращён с 23 до 18.
- Четыре Windows `taskkill` вызова в Dub/bgutil используют общий resolver перед
  `subprocess`; missing executable остаётся `FileNotFoundError`, а terminate/kill
  и fail-closed orphan recovery fallbacks сохранены. `S607`: 18 → 14.
- Local Bot API разрешает `netstat`, `tasklist`, `ps`, `taskkill`, `kill` и
  `pkill` до subprocess launch; targeted/global termination policy, opt-in
  global kill и обработку ошибок это не меняет. `S607`: 14 → 9; три resolved
  dynamic argv остаются в отдельной ручной `S603` очереди без suppressions.
- Dub Studio и четыре media tool разрешают `taskkill`/`ffmpeg`/`ffprobe` до
  запуска. Standalone VoxCPM2 scripts сохраняют независимый запуск и прежний
  missing-executable error; постобработка и QA gate не изменены. `S607`: 9 → 4.
- Последние четыре test-only `S607` устранены resolved `git`/`node`/`taskkill`;
  фиксированные argv без shell прошли ручной `S603` review с узкими обоснованными
  исключениями. `S607` = 0 и включён в обычный Ruff gate; `S603`: 106 → 104.
- Пять resolved-git `S603` прошли ручной review: Required PR Gate принимает
  только полные 40-hex SHA, а revision аргументы diff/show отделены
  `--end-of-options`; shell не используется. `S603`: 104 → 99.
- Десять Dub/bgutil `S603` прошли ручной review с узкими обоснованиями для
  resolved PowerShell/Node/`taskkill` и validated runner argv. Variable-command
  `ffprobe` в artifact postcondition также переведён на общий resolver; shell,
  process ownership и fail-closed media validation не изменены. `S603`: 99 → 89.
- Все GitHub Actions workflow явно ограничивают `GITHUB_TOKEN`: семь оставшихся
  workflow получили верхнеуровневый `permissions: contents: read`, а contract-тест
  запрещает возврат к неявным repository-default permissions. Job-level разрешения
  сохранены без расширения.
- `services/pdf_generator.py`: классификация видеоссылок использует parsed hostname
  вместо substring-проверки; look-alike domains и userinfo больше не принимаются за
  YouTube/RuTube/VK. Очистка `script`/`style` учитывает пробелы и атрибуты в closing tag;
  оба контракта закреплены регрессиями, CodeQL-suppression не использовался.
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
