# Проверка типов: срез вместо «всё сразу»

Строгий `mypy` включён не на весь код, а на объявленный срез — и это осознанно.

Причина простая: `mypy --strict .` на текущем коде даёт 5 038 ошибок в 563
файлах, причём большая часть — в тестах и в легаси `services/`. Гейт, который
всегда красный, через неделю отключают, поэтому он был бы имитацией контроля,
а не контролем.

Срез — это контракт: внутри него ошибок **ноль**, и он расширяется только
вперёд, по одному пакету за PR.

## Что уже проверяется

| Срез | Ошибок `mypy --strict` | Что отложено |
|---|---|---|
| `core/` | 0 | — |
| `pipelines/` | 0 | — |
| `converters/__init__.py` | 0 | — |
| `converters/caption.py` | 0 | — |
| `converters/telegraph_nodes.py` | 0 | — |
| `handlers/__init__.py` | 0 | — |
| `handlers/callbacks.py` | 0 | — |
| `handlers/dub_audio_repair.py` | 0 | — |
| `handlers/dub_delivery.py` | 0 | — |
| `handlers/dub_health.py` | 0 | — |
| `handlers/dub_multicommand.py` | 0 | — |
| `handlers/dub_quickstart.py` | 0 | — |
| `handlers/dub_studio_registration.py` | 0 | — |
| `handlers/mode_command.py` | 0 | — |
| `handlers/dub_wizard.py` | 0 | — |
| `services/__init__.py` | 0 | — |
| `services/async_process.py` | 0 | — |
| `services/async_worker.py` | 0 | — |
| `services/audio_cache_maintenance.py` | 0 | — |
| `services/bgutil_http_runtime.py` | 0 | — |
| `services/bgutil_orphan_recovery.py` | 0 | — |
| `services/bot_lifecycle.py` | 0 | — |
| `services/clip_renderer.py` | 0 | — |
| `services/conspect_audit_runtime.py` | 0 | — |
| `services/conspect_quality_contract.py` | 0 | — |
| `services/database_migrations.py` | 0 | — |
| `services/dub_delivery.py` | 0 | — |
| `services/dub_execution.py` | 0 | — |
| `services/dub_preflight.py` | 0 | — |
| `services/dub_reference_strategies.py` | 0 | — |
| `services/dub_release_health_v64.py` | 0 | — |
| `services/dub_rendering.py` | 0 | — |
| `services/dub_studio.py` | 0 | — |
| `services/dub_studio_runtime.py` | 0 | — |
| `services/dub_worker.py` | 0 | — |
| `services/dub_worker_release.py` | 0 | — |
| `services/eng_subtitles.py` | 0 | — |
| `services/env_policy.py` | 0 | — |
| `services/ffmpeg.py` | 0 | — |
| `services/gemini_analyze.py` | 0 | — |
| `services/gemini_capacity_control.py` | 0 | — |
| `services/gemini_error_policy.py` | 0 | — |
| `services/gemini_max_quality.py` | 0 | — |
| `services/gemini_model_status.py` | 0 | — |
| `services/gemini_qa_policy.py` | 0 | — |
| `services/gemini_quota_domains.py` | 0 | — |
| `services/gemini_router.py` | 0 | — |
| `services/health_report.py` | 0 | — |
| `services/highlights_candidate_gate.py` | 0 | — |
| `services/highlights_quality.py` | 0 | — |
| `services/latency_trace.py` | 0 | — |
| `services/livedub_audio_cache_recovery.py` | 0 | — |
| `services/livedub_audio_companion.py` | 0 | — |
| `services/livedub_audio_quality_guard.py` | 0 | — |
| `services/livedub_delivery_coordinator.py` | 0 | — |
| `services/livedub_info.py` | 0 | — |
| `services/livedub_info_evidence.py` | 0 | — |
| `services/livedub_info_presentation_policy.py` | 0 | — |
| `services/livedub_long_qa.py` | 0 | — |
| `services/livedub_mix.py` | 0 | — |
| `services/livedub_output_policy.py` | 0 | — |
| `services/livedub_publication.py` | 0 | — |
| `services/livedub_publication_core.py` | 0 | — |
| `services/livedub_qa.py` | 0 | — |
| `services/livedub_qa_hardening.py` | 0 | — |
| `services/livedub_qa_trust.py` | 0 | — |
| `services/livedub_quality_runtime.py` | 0 | — |
| `services/livedub_ru_provenance.py` | 0 | — |
| `services/local_botapi_bootstrap.py` | 0 | — |
| `services/local_botapi_diagnostics.py` | 0 | — |
| `services/local_botapi_required.py` | 0 | — |
| `services/local_botapi_runtime.py` | 0 | — |
| `services/media_delivery_probe.py` | 0 | — |
| `services/media_masters.py` | 0 | — |
| `services/mp3_chapters.py` | 0 | — |
| `services/mp3_conversion.py` | 0 | — |
| `services/operator_runtime_status.py` | 0 | — |
| `services/pdf_generator.py` | 0 | — |
| `services/polling_reliability_runtime.py` | 0 | — |
| `services/pre_main_policy.py` | 0 | — |
| `services/process_executables.py` | 0 | — |
| `services/process_singleton.py` | 0 | — |
| `services/quiz_generator.py` | 0 | — |
| `services/quiz_sessions.py` | 0 | — |
| `services/render_clips_montage.py` | 0 | — |
| `services/restart_state_runtime.py` | 0 | — |
| `services/runtime_build_identity.py` | 0 | — |
| `services/runtime_dependency_lock.py` | 0 | — |
| `services/runtime_manifest.py` | 0 | — |
| `services/search.py` | 0 | — |
| `services/segment_render.py` | 0 | — |
| `services/shorts_duration_safety.py` | 0 | — |
| `services/shorts_candidates.py` | 0 | — |
| `services/shorts_factory_candidates.py` | 0 | — |
| `services/shorts_factory_capacity.py` | 0 | — |
| `services/shorts_factory_capacity_runtime.py` | 0 | — |
| `services/shorts_factory_disk_guard.py` | 0 | — |
| `services/shorts_factory_execution_guard.py` | 0 | — |
| `services/shorts_factory_full_video.py` | 0 | — |
| `services/shorts_factory_long_fit.py` | 0 | — |
| `services/shorts_factory_media.py` | 0 | — |
| `services/shorts_factory_publication.py` | 0 | — |
| `services/shorts_factory_quality_gate.py` | 0 | — |
| `services/shorts_factory_retry_cache.py` | 0 | — |
| `services/shorts_factory_source.py` | 0 | — |
| `services/shorts_factory_timing.py` | 0 | — |
| `services/shorts_factory_video_quality.py` | 0 | — |
| `services/shorts_static_policy.py` | 0 | — |
| `services/shorts_subtitle_burn.py` | 0 | — |
| `services/shorts_subtitle_integrity.py` | 0 | — |
| `services/shorts_transcription.py` | 0 | — |
| `services/shorts_video.py` | 0 | — |
| `services/shorts_video_impl.py` | 0 | — |
| `services/speech_backends/` | 0 | — |
| `services/study_synthesis_policy.py` | 0 | — |
| `services/study_synthesis_runtime.py` | 0 | — |
| `services/telegraph_edit.py` | 0 | — |
| `services/telegraph_repair.py` | 0 | — |
| `services/translation_editorial.py` | 0 | — |
| `services/translation_editorial_composition.py` | 0 | — |
| `services/translation_editorial_factory.py` | 0 | — |
| `services/translation_editorial_pack_contract.py` | 0 | — |
| `services/translation_editorial_pack_v2.py` | 0 | — |
| `services/translation_editorial_repair_provenance.py` | 0 | — |
| `services/tts_profile_selection.py` | 0 | — |
| `services/tts_weighted_runner_provisioning.py` | 0 | — |
| `services/tts_weighted_smoke.py` | 0 | — |
| `services/tts_weighted_smoke_attestation.py` | 0 | — |
| `services/tts_weighted_smoke_runner.py` | 0 | — |
| `services/yandex_live_dub.py` | 0 | — |
| `services/youtube_po_token_runtime.py` | 0 | — |
| `services/youtube_transcript.py` | 0 | — |

Текущий срез — 236/241 production modules (97,9 %); вне него остаются
5 modules (2,1 %).

Измерено после расширения среза на `pipelines/`: полный `mypy --strict .`
(включая `tests/`) — 5 038 ошибок в 563 файлах; по пакетам без тестов:
`services/` 673, `tools/` 255, `handlers/` 158, `converters/` 143, `main.py` 49,
`bot_new.py` 1.

Отложенные файлы перечислены в `TYPED_SLICES` (`tools/check_types.py`) с
причиной и передаются в mypy как `--exclude`; тест проверяет, что каждый
такой путь существует и что список совпадает с этой таблицей.

## Как запускать

```bash
python tools/check_types.py             # объявленный срез (то же, что в CI)
python tools/check_types.py core tools # произвольный набор каталогов
python tools/check_types.py --verbose   # команда и полный вывод mypy
```

Локально `python -m pytest -q` тоже падает при ошибке типа в срезе:
`tests/test_typecheck_gate.py` запускает mypy тем же кодом, что и CI. Это
специально: ошибку надо видеть до push, а не после.

## Где живёт строгость

Настройки — в `[tool.mypy]` (`pyproject.toml`), а не в командной строке CI:

| Настройка | Зачем |
|---|---|
| `strict = true` | без неё проверка декоративна: непроверенные тела функций дают `Any` на входе |
| `explicit_package_bases = true` | иначе корневой `__init__.py` даёт `Duplicate module named 'database'` и mypy не стартует |
| `follow_imports = "silent"` | граница среза: ошибки вне среза не шумят, ошибки вызывающего кода внутри среза видны всегда |
| `enable_error_code = ["ignore-without-code"]` | голый `# type: ignore` скрывает все классы ошибок сразу; разрешён только адресный |
| `python_version = "3.12"` | цель совпадает с floor проекта |

`tests/test_typecheck_gate.py` проверяет, что эти значения не «поехали»:
снять `strict` или вернуть `follow_imports = "normal"` можно только вместе с
осознанной правкой теста, а не строчкой в конфиге.

## Политика подавлений

`# type: ignore` — не способ пройти гейт, а утверждение о третьей стороне.

1. Код ошибки обязателен: `# type: ignore[attr-defined]`, а не `# type: ignore`.
2. Рядом — причина словами. Пример из `core/globals.py`:

   ```python
   # google-genai — опциональная зависимость: ниже весь код конфигурации
   # проверяет HAS_GEMINI перед обращением, поэтому подмена модулей на None
   # безопасна и намеренна.
   genai = None  # type: ignore[assignment]
   ```

3. Если ошибка указывает на **настоящий** баг — исправляется код, а не
   подавляется. Так было с `core/source_packs.py`: объявления
   `negative=(("молитва",), ("ангел",),)` были кортежем кортежей, хотя тип —
   `tuple[str, ...]`; работало это лишь потому, что рядом стояла защита
   «support accidental tuple entries». Защиту убрали, объявления выпрямили.

## Как расширять срез

1. Привести пакет к нулю ошибок: `python tools/check_types.py <пакет>`.
2. Добавить его в `TYPED_SLICES` (`tools/check_types.py`) — это единственный
   список того, что проверяется.
3. Обновить таблицу в этом документе и запись в `CHANGELOG.md`.

Расширять «частично», с известными ошибками, нельзя: тогда срез перестаёт
отвечать на вопрос «зелёный он или нет». Единственное допустимое исключение —
файл, который в ближайших PR переезжает в другие модули: он попадает в
`exclude` своего среза с причиной, и тест требует, чтобы исключение было
объявлено явно.

## Чего здесь сознательно нет

- `--strict` на `tests/`: тесты выигрывают от свободы в фикстурах, а strict
  на них даёт тысячи ошибок без роста доверия к продукту.
- `redundant-expr`: `mypy` считает защитные `isinstance(section, dict)` в
  comprehensions «всегда истинными» — для данных из JSON это нормальная
  защита, а не мусор.
- Типизация `main_pipeline.py` до декомпозиции (см. `docs/AUDIT_2026-10-23_NEXT.md`,
  P0-3): 3 000 строк переезжают в отдельные модули, и сигнатуры пришлось бы
  переписывать дважды. Файл указан в `exclude` среза `pipelines/`.
