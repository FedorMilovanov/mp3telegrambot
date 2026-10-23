# Документация MP3Bot

Этот файл **сгенерирован**: `python tools/render_docs_index.py`.
Руками не правится — `--check` в CI и в `pre-push` падает, если список
разошёлся с каталогом `docs/`.

С чего начинать:

- **что проект умеет и как его запустить** — [`../README.md`](../README.md);
- **устройство и правила слоёв** — [`../ARCHITECTURE.md`](../ARCHITECTURE.md);
- **как сообщить об уязвимости и что считается секретом** — [`../SECURITY.md`](../SECURITY.md);
- **как менять код и какие гейты гонять локально** — [`../CONTRIBUTING.md`](../CONTRIBUTING.md);
- **какие переменные окружения существуют** — [`env-reference.md`](env-reference.md) (тоже генерируется);
- **что делать дальше по качеству** — [`AUDIT_2026-10-23_NEXT.md`](AUDIT_2026-10-23_NEXT.md).

Правила именования: имя файла описывает **область и дату**, а не «новизну».
Суффикс `_latest` запрещён: файл с таким именем устаревает молча, и читатель
не может понять, что перед ним старое.

Всего файлов: **75**.

## Эксплуатация и контракты

| Файл | О чём |
|---|---|
| [`AUDIT_2026-10-23_NEXT.md`](AUDIT_2026-10-23_NEXT.md) | `verify_repo.py` 6/6, lock == closure (73 = 73, включая dev-инструменты). |
| [`dependency-policy.md`](dependency-policy.md) | Три контура, каждый со своей ролью. Ни один из них не заменяет остальные. |
| [`env-reference.md`](env-reference.md) | Сгенерировано `scripts/render_env_reference.py` из кода — не редактируйте вручную. |
| [`gemini-operations.md`](gemini-operations.md) | This document describes the production operating contract for Gemini in MP3Bot. It is intentionally stricter than a generic API integration guide because… |
| [`generated_pages_archive.md`](generated_pages_archive.md) | Санитизированный публичный индекс: только названия, авторы и публичные ссылки. |
| [`local-bot-api-repair.md`](local-bot-api-repair.md) | The local `telegram-bot-api.exe` process opens port `8081`, but |
| [`model-catalog.md`](model-catalog.md) | Policy: `project-gemini-routing-2026-10-22`. Сгенерировано scripts/render_model_catalog.py из core/model_catalog.py — не редактируйте вручную. |
| [`runtime-reliability.md`](runtime-reliability.md) | LOCAL_BOT_API_EXE=C:\Program Files\TelegramBotAPI\telegram-bot-api.exe |
| [`tts-weighted-runner.md`](tts-weighted-runner.md) | Этот runbook относится только к manual workflow `TTS Weighted Smoke`. Он не нужен hosted CI и не должен использоваться для запуска кода из pull request. |
| [`youtube-gvs-acceptance.md`](youtube-gvs-acceptance.md) | `tools/check_youtube_gvs.py` is a manual end-to-end check for the exact failure |
| [`youtube-po-token-runtime.md`](youtube-po-token-runtime.md) | In August 2026 YouTube tightened Google Video Server (GVS) authorization for |

## Dub Studio

| Файл | О чём |
|---|---|
| [`dub_quality_v41_validation.md`](dub_quality_v41_validation.md) | — |
| [`dub_studio_audio_quality_and_repair.md`](dub_studio_audio_quality_and_repair.md) | Сравнение исходного MP4 и результата бота показало два независимых класса дефектов. |
| [`dub_studio_audio_repair_validation.md`](dub_studio_audio_repair_validation.md) | — |
| [`dub_studio_clean_direct_production.md`](dub_studio_clean_direct_production.md) | Telegram-бот должен давать тот же аудиорезультат, что и проверенный ручной запуск из PowerShell. Бот является диспетчером: он готовит входные данные, запускает… |
| [`dub_studio_professional_audio_v45.md`](dub_studio_professional_audio_v45.md) | Professional Audio v4.5 is the release gate above the proven NoChew Quality v4.2 core. |
| [`dub_studio_quality_repair_final_gate.md`](dub_studio_quality_repair_final_gate.md) | — |
| [`dub_studio_quality_v42_final_validation.md`](dub_studio_quality_v42_final_validation.md) | — |
| [`dub_studio_quality_v42_validation.md`](dub_studio_quality_v42_validation.md) | регрессии «30 мс чирп» и «одиночный клик перед речью». |
| [`dub_studio_repair_race_validation.md`](dub_studio_repair_race_validation.md) | — |
| [`dub_studio_runtime_fix.md`](dub_studio_runtime_fix.md) | — |
| [`dub_studio_runtime_v3_validation.md`](dub_studio_runtime_v3_validation.md) | — |
| [`dub_studio_runtime_v42_final_validation.md`](dub_studio_runtime_v42_final_validation.md) | ................................F                                        [100%] |
| [`dub_studio_srt_repair_final_revalidation.md`](dub_studio_srt_repair_final_revalidation.md) | — |
| [`dub_studio_srt_repair_final_validation.md`](dub_studio_srt_repair_final_validation.md) | ..................................F.........                             [100%] |
| [`dub_studio_validation.md`](dub_studio_validation.md) | — |

## VoxCPM2 и TTS

| Файл | О чём |
|---|---|
| [`shorts_factory_max.md`](shorts_factory_max.md) | `SHORTS FACTORY MAX` is a standalone `/mode` option for extraction-only jobs. It intentionally skips the normal Synopsis, Telegraph pages, questions, source… |
| [`voxcpm2-universal-hardening.md`](voxcpm2-universal-hardening.md) | This runtime is project-wide. It contains no video ID, speaker name, quotation, |
| [`voxcpm2/CUDA_PROBATION.md`](voxcpm2/CUDA_PROBATION.md) | The local RTX 3060 has a confirmed history of `nvlddmkm` Event ID 153, LiveKernelEvent/WATCHDOG, CUBLAS failures, illegal memory access and BF16/FP16 failures.… |
| [`voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md`](voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md) | На текущем компьютере VoxCPM2 разрешена только на CPU. Production-launchers обязаны скрывать GPU через `CUDA_VISIBLE_DEVICES=-1` и проверять… |
| [`voxcpm2/CURRENT_STATE.md`](voxcpm2/CURRENT_STATE.md) | V3.2 NoChew: owner-approved technical baseline |
| [`voxcpm2/EXPERIMENT_LOG.md`](voxcpm2/EXPERIMENT_LOG.md) | This is the append-only laboratory log for the local CPU dubbing work. Keep observed facts separate from hypotheses. Do not rewrite failed runs as successes. |
| [`voxcpm2/HANDOFF_FOR_AI.md`](voxcpm2/HANDOFF_FOR_AI.md) | Read this file before changing, running or advising on the local VoxCPM2 pipeline. |
| [`voxcpm2/INTEGRATION_PLAN.md`](voxcpm2/INTEGRATION_PLAN.md) | This document defines how the laboratory CPU dubbing path should enter the bot without destabilizing the current LiveDub pipeline. |
| [`voxcpm2/MODEL_COMPARISON_2026-07-26.md`](voxcpm2/MODEL_COMPARISON_2026-07-26.md) | Этот документ отвечает на практический вопрос проекта: действительно ли VoxCPM2 является лучшей или хотя бы одной из наиболее перспективных открытых моделей… |
| [`voxcpm2/MODEL_COMPARISON_2026-08-01.md`](voxcpm2/MODEL_COMPARISON_2026-08-01.md) | 1. русский язык и cross-lingual cloning действительно работают, а не только заявлены в демо; |
| [`voxcpm2/PRODUCTION_RUNBOOK.md`](voxcpm2/PRODUCTION_RUNBOOK.md) | This runbook turns the MacArthur laboratory work into a repeatable Windows workflow. VoxCPM2 synthesis stays CPU-only. The damaged RTX 3060 may be used only as… |
| [`voxcpm2/QUALITY_RESEARCH_2026-07-26.md`](voxcpm2/QUALITY_RESEARCH_2026-07-26.md) | This note records a focused review of more than 50 primary pages: official VoxCPM2 documentation, current source files, model configuration, release notes,… |
| [`voxcpm2/README.md`](voxcpm2/README.md) | VoxCPM2 пока не импортируется основным Telegram bot runtime. Текущий этап — стабильный лабораторный production-контур для Shorts и подготовка архитектуры… |
| [`voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`](voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md) | Reference quality is a first-order model input, not a cosmetic post-processing detail. VoxCPM2 can transfer room tone, compression, breath pattern, mic… |
| [`voxcpm2/SEMANTIC_BLOCK_PRODUCTION.md`](voxcpm2/SEMANTIC_BLOCK_PRODUCTION.md) | `semantic-block-continuation-v1` is the direct ready-SRT production policy for |
| [`voxcpm2/SOURCES.md`](voxcpm2/SOURCES.md) | Curated primary-source reading list for the CPU dubbing pipeline. This is not a generic link dump: each item is tied to an implementation decision, failure… |
| [`voxcpm2/SPEECH_BACKEND_CONTRACT.md`](voxcpm2/SPEECH_BACKEND_CONTRACT.md) | Production orchestration owns timing, semantic QA, speaker QA, checkpoints, |
| [`voxcpm2_clean_expressive_design.md`](voxcpm2_clean_expressive_design.md) | Short synthesis windows are retained for timing stability, but they are not |
| [`voxcpm2_cpu_operations.md`](voxcpm2_cpu_operations.md) | Этот runbook фиксирует рабочую конфигурацию Dub Studio и правила, которые предотвращают повторение ошибок с путями, `request.json`, лишними загрузками и… |
| [`voxcpm2_dub_studio.md`](voxcpm2_dub_studio.md) | Dub Studio превращает Telegram-бота в пульт управления локальным |
| [`voxcpm2_gold_audit_round2_2026-07-29.md`](voxcpm2_gold_audit_round2_2026-07-29.md) | This round was performed against the live `main` branch while another agent was |
| [`voxcpm2_max_quality_audit_2026-07.md`](voxcpm2_max_quality_audit_2026-07.md) | This audit checks the actual MP3Bot/Dub Studio production path against official |
| [`voxcpm2_quality_audit_addendum_2026-07-29.md`](voxcpm2_quality_audit_addendum_2026-07-29.md) | This addendum records high-value defects found after the original 90-check, |
| [`voxcpm2_quality_v4_audit.md`](voxcpm2_quality_v4_audit.md) | This audit compares the supplied original `The Strength of a Godly Woman \| John Piper Clip.mp4` |

## Перевод и редактура

| Файл | О чём |
|---|---|
| [`translation_editorial_composition.md`](translation_editorial_composition.md) | This stage begins after translation review and, when needed, after the full Yandex master has been repaired. It is deliberately separate from semantic QA:… |
| [`translation_editorial_review.md`](translation_editorial_review.md) | This workflow treats Yandex LiveDub as an editorial source that may be repaired surgically instead of requiring a perfect machine translation. |

## Аудиты и отчёты о качестве

| Файл | О чём |
|---|---|
| [`AUDIT_2026-10-22.md`](AUDIT_2026-10-22.md) | Проект в хорошем инженерном состоянии (3102 теста зелёные, `ruff` чистый, dependency-lock проверяется на старте), но отстал от экосистемы Gemini на одну… |
| [`AUDIT_YOUTUBE_GVS_403.md`](AUDIT_YOUTUBE_GVS_403.md) | `FAIL_HTTP_403` after PR #170 (`2a1c8cf`, main HEAD). |
| [`prompt-intelligence-audit.md`](prompt-intelligence-audit.md) | Система должна не просто выпускать грамотно оформленный богословский текст. Она должна помогать человеку: |
| [`quality_audit_2026-07-23_anatomy_conspects.md`](quality_audit_2026-07-23_anatomy_conspects.md) | Source: operator-provided production log and screenshots for three John MacArthur |
| [`quality_audit_2026-07-23_static_shorts_blur.md`](quality_audit_2026-07-23_static_shorts_blur.md) | The marathon log showed both paths working, but one static-looking cover was classified as moving and rendered with `crop_zoom(medium)`. Other covers from the… |
| [`quality_audit_2026-07-23_teacherly_study.md`](quality_audit_2026-07-23_teacherly_study.md) | A short free-form request produced a visibly stronger Study text than the accumulated legacy rubric prompt. The better sample had material-specific headings,… |
| [`quality_audit_history.md`](quality_audit_history.md) | Source: operator runtime log excerpt, 10:42–15:34 Europe/Moscow. |
| [`quality_audit_reports/2026-08-04-cut-modes-recheck.md`](quality_audit_reports/2026-08-04-cut-modes-recheck.md) | Repository: `FedorMilovanov/mp3telegrambot` |
| [`quality_audit_reports/2026-08-04-shorts-factory-max.md`](quality_audit_reports/2026-08-04-shorts-factory-max.md) | 1. Default model is `gemini-3.1-pro-preview`; Flash and Lite are not accepted as Factory overrides. |
| [`quality_audits/2026-08-15-explicit-livedub-delivery.md`](quality_audits/2026-08-15-explicit-livedub-delivery.md) | Production LiveDub delivery had accumulated a stack of runtime adapters that |
| [`quality_audits/2026-08-15-gemini36-resilience.md`](quality_audits/2026-08-15-gemini36-resilience.md) | Production Shorts Factory log showed repeated `503 UNAVAILABLE / high demand` while a ~54 minute source had been converted to a ~262.6 MB FLAC solely for… |
| [`quality_audits/2026-08-15-source-owned-gemini36-polish.md`](quality_audits/2026-08-15-source-owned-gemini36-polish.md) | Re-audited the current production `main` after the previous Gemini 3.6 resilience merge instead of treating green CI as proof that every source owner encoded… |
| [`quality_audits/bgutil-node-runtime-parity-20260820.md`](quality_audits/bgutil-node-runtime-parity-20260820.md) | A real Windows/TUN Shorts Factory request reached the exact-source bgutil runtime but failed before token generation. Startup had reported: |
| [`quality_audits/gemini-503-capacity-audit-20260820.md`](quality_audits/gemini-503-capacity-audit-20260820.md) | A client application cannot truthfully guarantee that Google will never return |
| [`quality_audits/latency-stage-measurement-20260820.md`](quality_audits/latency-stage-measurement-20260820.md) | Gemini `503 UNAVAILABLE` occurred on the former Gemini 3.6 route as well as the current 3.7 route. Google documents 503 as temporary service overload/capacity… |
| [`quality_audits/README.md`](quality_audits/README.md) | Каждый файл в этом каталоге — снимок состояния на дату в его имени (`20260820`, |
| [`quality_audits/youtube-bgutil-offline-integrity-20260820.md`](quality_audits/youtube-bgutil-offline-integrity-20260820.md) | On the production Windows machine, exact-source checkout, `npm ci`, and TypeScript build completed, but bootstrap failed on the post-build command: |
| [`quality_audits/ytdlp-freshness-policy-20260820.md`](quality_audits/ytdlp-freshness-policy-20260820.md) | The production lock currently pins `yt-dlp==2026.7.4`. As of 2026-08-20 this is the latest stable yt-dlp release, so the installed stable line is not presently… |
| [`telegraph_repair_targets_2026-06-16.md`](telegraph_repair_targets_2026-06-16.md) | Technical pages from the live Shepherds' Conference run that DOM audit found worth repairing. |
| [`unified_mode_menu_validation.md`](unified_mode_menu_validation.md) | — |
| [`V3_PROGRESS.md`](V3_PROGRESS.md) | 4. usage/finish_reason extraction helpers |

## Прочее

| Файл | О чём |
|---|---|
| [`ops/deploy_windows.md`](ops/deploy_windows.md) | Документ описывает воспроизводимую установку с нуля: от чистой машины до |
| [`ops/observability.md`](ops/observability.md) | Что смотреть, когда «бот не отвечает», и как это увидеть раньше, чем напишет |
