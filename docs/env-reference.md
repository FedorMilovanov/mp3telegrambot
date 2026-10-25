# Справочник переменных окружения

Сгенерировано `scripts/render_env_reference.py` из кода — не редактируйте вручную.
Курируемая инструкция «что стоит трогать» — `.env.example`; здесь полный перечень,
включая внутренние и диагностические переменные.

Всего переменных: **177**; описано в `.env.example`: **103**; не описано: **74**; управляется стартовой политикой: **15**.

| Переменная | Где читается | Дефолт в коде | В `.env.example` | Политика |
|---|---|---|---|---|
| `ADMIN_IDS` | `core/database.py` | — | да | — |
| `ALL_PROXY` | `services/ffmpeg.py` | `` | — | — |
| `APP_ENV` | `main.py`, `services/bot_lifecycle.py` | — | — | — |
| `AUDIO_ANALYSIS_MODE` | `core/database.py`, `core/prompts.py` | `deep` | да | — |
| `AUDIO_ANALYSIS_STRUCTURED` | `services/gemini_analyze.py` | `1` | да | — |
| `AUDIO_CACHE_TTL_DAYS` | `services/audio_cache_maintenance.py` | `` | — | — |
| `AUDIO_STRUCTURED_TIMEOUT` | `services/gemini_analyze.py` | `180` | да | — |
| `AUDIO_TIMESTAMP_REPAIR` | `services/gemini_analyze.py` | `1` | да | — |
| `AUDIO_TIMESTAMP_REPAIR_TIMEOUT` | `services/gemini_analyze.py` | `240` | да | — |
| `AUDIT_BLOCK_PUBLICATION` | `core/page_audit.py` | `0` | да | — |
| `BGUTIL_HTTP_LOG` | `services/bgutil_http_runtime.py` | `` | — | — |
| `BOT_TOKEN` | `bot_new.py`, `core/globals.py`, `services/livedub_audio_companion.py` и ещё 3 | `` | да | — |
| `CACHE_TTL_DAYS` | `core/database.py` | `45` | да | — |
| `CACHE_VERSION` | `core/database.py` | `2026-03-22-v5` | да | — |
| `CHANNEL_MAPPINGS` | `core/database.py` | `{}` | — | — |
| `CLIPS_CANDIDATE_BUDGET_SECONDS` | `pipelines/clips.py` | `90` | — | — |
| `CLOUD_MEDIA_AUTO_COMPRESS` | `services/local_botapi_bootstrap.py`, `services/local_botapi_required.py` | `1` | да | — |
| `CODE_HEALTH_REGEX_THRESHOLD` | `core/code_health.py` | `300` | — | — |
| `COMBINED_STUDY_REFLECTION_MAX_PROMPT_CHARS` | `services/telegraph_pages.py` | `90000` | — | — |
| `COMBINE_STUDY_REFLECTION` | `services/telegraph_pages.py` | `0` | да | — |
| `CONTENT_AUDIT_BLOCK_PUBLICATION` | `core/content_audit.py` | `0` | да | — |
| `CONTENT_AUDIT_MODE` | `core/content_audit.py`, `core/page_audit.py` | `warn` | да | — |
| `CONTENT_AUDIT_STRICT_CODES` | `core/content_audit.py` | `` | да | — |
| `CUDA_DEVICE_ORDER` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `CUDA_LAUNCH_BLOCKING` | `tools/voxcpm2/cuda_probation_probe.py` | — | — | — |
| `DEGRADED_TRANSCRIPT_TIMESTAMPS` | `pipelines/degraded_mode.py` | `1` | — | — |
| `DISABLE_HEALTH_CHECK` | `handlers/commands.py`, `main.py`, `services/bot_lifecycle.py` | `` | да | — |
| `DUB_BACKEND_EXECUTION_PLAN_LOG` | `services/speech_backends/execution_plan.py` | `` | — | — |
| `DUB_CPU_VENV` | `handlers/dub_health.py` | — | — | — |
| `DUB_DEFAULT_TTS_PROFILE` | `services/speech_backends/builtin_profiles.py` | — | — | — |
| `DUB_STUDIO_POWERSHELL` | `services/dub_worker.py` | `` | — | — |
| `DUB_STUDIO_PROJECT_ID` | `tools/voxcpm2/generic_project_runtime.py` | `` | — | — |
| `DUB_STUDIO_ROOT` | `services/dub_studio.py` | `` | — | — |
| `DUB_TITLE_MODEL` | `handlers/dub_wizard.py` | `` | — | — |
| `DUB_TRANSLATION_MODEL` | `handlers/dub_wizard.py`, `tools/voxcpm2/generic_short_production.py` | `` | — | — |
| `DUB_TTS_MAX_ONSET_MS` | `tools/voxcpm2/semantic_tts_guard_v4.py` | `220` | — | — |
| `DUB_TTS_MIN_TRAILING_MS` | `tools/voxcpm2/semantic_tts_guard_v4.py` | `45` | — | — |
| `DUB_TTS_QA_MAX_ROUNDS` | `tools/voxcpm2/semantic_tts_guard_v4.py` | `3` | — | — |
| `DUB_TTS_QA_MODEL` | `handlers/dub_health.py`, `tools/voxcpm2/semantic_tts_guard.py` | `small` | — | — |
| `DUB_VOX_ARCHIVE` | `handlers/dub_health.py` | — | — | — |
| `DUB_VOX_MODEL_REVISION` | `services/speech_backends/builtin_profiles.py` | `` | — | — |
| `DUB_WHISPER_MODEL` | `handlers/dub_wizard.py` | `large-v3` | — | — |
| `ENV` | `main.py`, `services/bot_lifecycle.py` | — | — | — |
| `EXPANDED_CONTENT_AUDIT_RETRY` | `services/telegraph_pages.py` | `1` | да | — |
| `EXPANDED_PAGES_STRUCTURED` | `services/telegraph_pages.py` | `1` | да | — |
| `FORCE_EXIT_ON_STOP` | `handlers/commands.py` | `` | — | — |
| `GEMINI_API_KEY` | `core/globals.py` | `` | да | — |
| `GEMINI_API_KEY_2` | `core/globals.py` | `` | да | — |
| `GEMINI_API_KEY_3` | `core/globals.py` | `` | да | — |
| `GEMINI_API_KEY_4` | `core/globals.py` | `` | да | — |
| `GEMINI_MODEL` | `services/runtime_build_identity.py` | — | да | да |
| `GEMINI_PROXY_URL` | `core/globals.py`, `services/livedub_quality_runtime.py` | `` | да | — |
| `GEMINI_SCHEMA_THINKING` | `core/globals.py` | `1` | да | да |
| `GEMINI_SERVICE_TIER` | `core/globals.py` | `standard` | да | — |
| `GENERATED_PAGES_DIR` | `core/generated_pages.py` | `data/generated_pages` | да | — |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` | `bot.py`, `bot_new.py` | — | да | — |
| `HF_HUB_OFFLINE` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `HIGHLIGHTS_FINAL_MAX_SILENCE_SECONDS` | `services/media_delivery_probe.py` | `2.8` | — | — |
| `LIVEDUB_AUDIO_BITRATE` | `services/livedub_audio_companion.py` | `160k` | — | — |
| `LIVEDUB_AUDIO_CACHE_MAX_BYTES` | `services/livedub_audio_cache_recovery.py` | `` | — | — |
| `LIVEDUB_AUDIO_DEDUPE_TIMEOUT` | `services/livedub_delivery_coordinator.py` | `2100` | — | — |
| `LIVEDUB_AUTOFIX_MAX_INTERVALS` | `services/livedub_mix.py` | `0` | — | — |
| `LIVEDUB_CLEAN_INFO_CARD` | `services/livedub_info_presentation_policy.py` | `1` | — | — |
| `LIVEDUB_DELAY_MS` | `core/database.py`, `services/translation_editorial_factory.py` | `600` | да | — |
| `LIVEDUB_HARDSUB` | `services/eng_subtitles.py` | `1` | да | — |
| `LIVEDUB_INFO_CARD` | `services/livedub_info.py` | `1` | да | — |
| `LIVEDUB_INFO_FALLBACK_MODELS` | `services/livedub_info.py` | `` | да | да |
| `LIVEDUB_INFO_MODEL` | `services/livedub_info.py`, `services/livedub_publication_core.py` | — | да | да |
| `LIVEDUB_LONG_QA` | `services/livedub_long_qa.py` | `1` | — | — |
| `LIVEDUB_LONG_QA_MODEL` | `services/livedub_long_qa.py` | `` | да | да |
| `LIVEDUB_LONG_QA_THINKING` | `services/livedub_long_qa.py` | `high` | — | да |
| `LIVEDUB_ORIG_VOLUME` | `core/database.py` | `0.45` | да | — |
| `LIVEDUB_PUBLICATION_CARD` | `services/livedub_publication.py` | `1` | — | — |
| `LIVEDUB_PUBLICATION_FALLBACK_MODELS` | `services/livedub_publication_core.py` | `` | да | да |
| `LIVEDUB_QA_AUDIO_TRUST` | `services/gemini_qa_policy.py`, `services/livedub_qa_trust.py` | `1` | — | — |
| `LIVEDUB_QA_CONFIRM_ISSUES` | `services/gemini_qa_policy.py`, `services/livedub_qa_trust.py` | `1` | — | — |
| `LIVEDUB_QA_VERIFY_MODEL` | `services/livedub_qa_trust.py` | `` | да | да |
| `LIVEDUB_QA_VERIFY_THINKING` | `services/livedub_qa_trust.py` | `low` | — | да |
| `LIVEDUB_QUICK_QA_MAX_DURATION` | `pipelines/main_pipeline.py`, `services/pre_main_policy.py` | `10800`, `120` | да | — |
| `LIVEDUB_QUICK_QA_MODEL` | `pipelines/main_pipeline.py` | `` | да | да |
| `LIVEDUB_QUICK_QA_THINKING` | `pipelines/main_pipeline.py` | `minimal` | да | да |
| `LIVEDUB_RNNOISE_MODEL` | `core/database.py`, `services/livedub_mix.py` | `` | да | — |
| `LIVEDUB_SEND_AUDIO` | `services/livedub_audio_companion.py` | `1` | — | — |
| `LIVEDUB_SEND_DUAL_AUDIO` | `services/livedub_audio_companion.py` | `1` | — | — |
| `LIVEDUB_TAIL_FREEZE_MAX_SEC` | `core/database.py` | `180` | да | — |
| `LIVEDUB_TAIL_MARGIN_MS` | `core/database.py` | `1000` | да | — |
| `LIVEDUB_TITLE_CACHE_MAX` | `pipelines/livedub_title.py` | `256` | — | — |
| `LIVEDUB_TITLE_TRANSLATE` | `pipelines/livedub_title.py` | `1` | да | — |
| `LIVEDUB_TRANS_VOLUME` | `core/database.py` | `1.3` | да | — |
| `LIVEDUB_TTS_FALLBACK` | `services/livedub_mix.py` | `0` | да | да |
| `LIVEDUB_VOICE_EQ` | `core/database.py` | `1` | — | — |
| `LOCAL_BOT_API_AUTOSTART` | `main.py` | `1` | да | — |
| `LOCAL_BOT_API_CLOUD_FALLBACK` | `main.py`, `services/local_botapi_required.py` | `1` | да | — |
| `LOCAL_BOT_API_DATA_DIR` | `core/utils.py`, `main.py`, `services/local_botapi_bootstrap.py` и ещё 1 | `` | да | — |
| `LOCAL_BOT_API_EXE` | `main.py` | — | да | — |
| `LOCAL_BOT_API_GETME_TIMEOUT_SEC` | `main.py` | `60` | — | — |
| `LOCAL_BOT_API_PROXY_LOGIN` | `main.py` | `` | да | — |
| `LOCAL_BOT_API_PROXY_PASSWORD` | `main.py`, `services/local_botapi_runtime.py` | `` | да | — |
| `LOCAL_BOT_API_PROXY_PORT` | `main.py` | `` | да | — |
| `LOCAL_BOT_API_PROXY_SERVER` | `handlers/commands.py`, `main.py` | `` | да | — |
| `LOCAL_BOT_API_PROXY_URL` | `core/globals.py`, `handlers/commands.py`, `main.py` и ещё 2 | `` | да | — |
| `LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC` | `services/local_botapi_required.py` | `300` | да | — |
| `LOCAL_BOT_API_TDLIB_PROXY_TYPE` | `main.py` | `` | да | — |
| `LOCAL_BOT_API_URL` | `core/globals.py`, `services/local_botapi_bootstrap.py`, `services/local_botapi_required.py` и ещё 1 | `` | да | — |
| `LOCAL_BOT_API_WAIT_LOCAL` | `main.py`, `services/local_botapi_bootstrap.py`, `services/local_botapi_required.py` | `` | — | — |
| `LOG_FILE` | `core/logging_setup.py` | `bot.log` | да | — |
| `LOG_FORMAT` | `core/logging_setup.py` | `text` | да | — |
| `LOG_LEVEL` | `core/logging_setup.py` | `INFO` | да | — |
| `MAINTENANCE_ALERT_INTERVAL_HOURS` | `main.py` | `6` | да | — |
| `MAX_FILE_SIZE_MB` | `core/database.py` | `` | да | — |
| `MAX_VIDEO_DURATION_SEC` | `pipelines/media_guards.py` | `10800` | да | — |
| `MKL_NUM_THREADS` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `MP3BOT_BUILD_SHA` | `services/runtime_build_identity.py` | — | — | — |
| `MP3BOT_EFFECTIVE_BOT_API` | `services/local_botapi_bootstrap.py`, `services/local_botapi_diagnostics.py`, `services/local_botapi_required.py` | `` | — | — |
| `MP3_LOUDNORM` | `services/ffmpeg.py` | `1` | да | — |
| `OMP_NUM_THREADS` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `PAGE_AUDIT_BLOCK_PUBLICATION` | `core/page_audit.py` | `0` | да | — |
| `PAGE_AUDIT_MODE` | `core/page_audit.py` | — | да | — |
| `PAGE_AUDIT_STRICT_CODES` | `core/page_audit.py` | `` | да | — |
| `PDF_BODY_FONT` | `services/pdf_generator.py` | `Merriweather` | да | — |
| `PDF_FONTS_DIR` | `services/pdf_generator.py` | `` | да | — |
| `PDF_SANS_FONT` | `services/pdf_generator.py` | `Manrope` | да | — |
| `PDF_TELEGRAPH_CACHE_SIZE` | `services/pdf_generator.py` | `64` | — | — |
| `PLAYLIST_DELAY_SEC` | `pipelines/playlist.py` | `1.0` | да | — |
| `PORT` | `handlers/commands.py`, `main.py`, `services/bot_lifecycle.py` | `10000` | — | — |
| `PROMPT_EXPERIMENT_TAG` | `core/generated_pages.py`, `pipelines/fresh_caption_stage.py` | `` | — | — |
| `PROMPT_SCHEMA_VERSION` | `core/database.py` | `analysis-deep-v7` | — | — |
| `PYTHONUTF8` | `handlers/dub_health.py`, `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | `` | — | — |
| `RENDER_GIT_COMMIT` | `services/runtime_build_identity.py` | — | — | — |
| `RUTUBE_LISTING_CACHE_TTL` | `services/search.py` | `900` | — | — |
| `SHORTS_FACTORY_EDITORIAL_GEMINI_MAX_ATTEMPTS` | `services/translation_editorial_factory.py` | `1` | — | — |
| `SHORTS_FACTORY_EDITORIAL_GEMINI_TIMEOUT_SEC` | `services/translation_editorial_factory.py` | `300` | — | — |
| `SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC` | `services/translation_editorial_factory.py` | `0.15` | — | — |
| `SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC` | `services/shorts_factory_source.py` | `` | — | — |
| `SHORTS_FACTORY_MAX_SOURCE_SEC` | `pipelines/shorts_factory.py` | `10800` | — | — |
| `SHORTS_FACTORY_MIN_FREE_GB` | `services/shorts_factory_execution_guard.py` | `2.0` | — | — |
| `SHORTS_FACTORY_MODEL` | `services/runtime_build_identity.py`, `services/shorts_factory_candidates.py` | `` | да | да |
| `SHORTS_FACTORY_PUBLICATION_DESCRIPTION` | `services/shorts_factory_publication.py` | `1` | — | — |
| `SHORTS_FACTORY_PUBLICATION_DESCRIPTION_TIMEOUT_SEC` | `services/shorts_factory_publication.py` | `12` | — | — |
| `SHORTS_FACTORY_RETRY_CACHE_MAX_ITEMS` | `services/shorts_factory_retry_cache.py` | `2` | — | — |
| `SHORTS_FACTORY_SOURCE_RETENTION_HOURS` | `pipelines/shorts_factory.py` | `24` | — | — |
| `SHORTS_FACTORY_TRANSLATION_BACKEND` | `pipelines/shorts_factory.py` | `yandex_live` | — | — |
| `SHORTS_FACTORY_WHISPER_MODEL` | `services/shorts_transcription.py` | `` | да | да |
| `SPONSORBLOCK_REMOVE` | `pipelines/main_pipeline.py` | `` | да | — |
| `SYNOPSIS_DENSITY_RETRY` | `services/telegraph.py` | `1` | да | — |
| `SYNOPSIS_STRUCTURED` | `services/telegraph.py` | `0` | да | — |
| `SYNOPSIS_VERBATIM_PROMPT` | `services/telegraph.py` | `1` | да | — |
| `SYNOPSIS_YT_TRANSCRIPT_LANGS` | `services/youtube_transcript.py` | `` | да | — |
| `SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS` | `services/telegraph.py` | `120000` | да | — |
| `SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE` | `services/youtube_transcript.py` | `0.70` | да | — |
| `TELEGRAM_API_HASH` | `main.py`, `services/local_botapi_bootstrap.py`, `services/local_botapi_required.py` и ещё 1 | `` | да | — |
| `TELEGRAM_API_ID` | `main.py`, `services/local_botapi_bootstrap.py`, `services/local_botapi_required.py` и ещё 1 | `` | да | — |
| `TELEGRAM_PROXY_HTTP_FALLBACK` | `main.py` | `1` | да | — |
| `TELEGRAM_PROXY_URL` | `core/globals.py`, `handlers/commands.py`, `main.py` и ещё 5 | `` | да | — |
| `TELEGRAPH_TOKEN` | `core/globals.py` | `` | да | — |
| `TOKENIZERS_PARALLELISM` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `TRANSFORMERS_OFFLINE` | `tools/voxcpm2/quality_sweep.py`, `tools/voxcpm2/segmented_cpu_dub.py` | — | — | — |
| `VIDEO_CPU_PRESET` | `services/ffmpeg.py`, `services/shorts_factory_video_quality.py` | ``, `veryfast` | да | — |
| `VIDEO_FORCE_CPU` | `services/ffmpeg.py`, `services/shorts_factory_video_quality.py`, `tools/run_factory_media_benchmark.py` и ещё 1 | `0` | да | — |
| `VIDEO_LOCK_TTL_SEC` | `core/globals.py` | `3600` | да | — |
| `VIDEO_LOCK_WAIT_TIMEOUT_SEC` | `handlers/commands.py` | `300` | да | — |
| `VK_API_TOKEN` | `core/globals.py`, `services/search.py` | `` | да | — |
| `VOT_API_TOKEN` | `handlers/commands.py`, `main.py`, `services/shorts_factory_execution_guard.py` и ещё 1 | `` | да | — |
| `VOXCPM_LEGACY_RENDERER` | `tools/voxcpm2/voxcpm2_professional_adapter_v45.py`, `tools/voxcpm2/voxcpm2_quality_v45_renderer.py` | `` | — | — |
| `VOXCPM_ORIGINAL_RENDERER` | `tools/voxcpm2/voxcpm2_quality_v45_renderer.py`, `tools/voxcpm2/voxcpm2_quality_v4_renderer.py` | `` | — | — |
| `WHISPER_DEVICE` | `services/shorts_video_impl.py` | `cpu` | да | — |
| `WHISPER_FORCE_CPU` | `services/shorts_video_impl.py` | `0` | да | — |
| `WHISPER_GPU_SAFE_MODE` | `services/shorts_video_impl.py` | `0` | да | — |
| `WHISPER_MODEL` | `services/shorts_video_impl.py` | `large-v3` | да | да |
| `WHITELIST_IDS` | `core/database.py` | `` | да | — |
| `WKHTMLTOPDF_PATH` | `services/pdf_generator.py` | `` | да | — |
| `YANDEX_OAUTH_TOKEN` | `handlers/commands.py`, `main.py`, `services/shorts_factory_execution_guard.py` и ещё 1 | `` | да | — |
| `YTDLP_COOKIES_FROM_BROWSER` | `services/ffmpeg.py` | `` | да | — |
| `YTDLP_FRAGMENTS` | `services/ffmpeg.py` | `4` | да | — |
| `YTDLP_INFO_TIMEOUT` | `pipelines/media_guards.py` | `180` | да | — |
| `YTDLP_PROXY_FORCE_SOCKS` | `services/ffmpeg.py` | `0` | — | — |
| `YTDLP_PROXY_URL` | `services/ffmpeg.py` | `` | да | — |
