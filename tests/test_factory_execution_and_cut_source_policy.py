from pathlib import Path
from types import SimpleNamespace

import pytest

import services.cut_mode_source_policy as source_policy
from services.cut_mode_source_policy import (
    cached_record_for_cut_source,
    cut_cache_validity,
    cut_replay_setting_value,
    cut_replay_settings,
    cut_source_is_usable,
    cut_source_mode_context,
    translated_source_required,
)
from services.shorts_factory_execution_guard import (
    factory_language_needs_translation,
    factory_preflight_issues,
    factory_translation_preflight_issues,
    normalize_factory_language,
    resolve_factory_spoken_language,
)
from services.shorts_factory_quality_gate import validated_factory_plan_language


def test_factory_quality_gate_requires_proven_dominant_spoken_language():
    assert validated_factory_plan_language({"metadata": {"language": "en"}}) == "en"
    with pytest.raises(RuntimeError, match="доминирующий язык речи"):
        validated_factory_plan_language({"metadata": {"language": "mixed"}})


def test_factory_spoken_language_prefers_audio_plan_over_title_metadata():
    plan = {"metadata": {"language": "English"}}
    info = {"language": "ru", "title": "Русский заголовок"}
    assert resolve_factory_spoken_language(plan, info) == "en"
    assert factory_language_needs_translation("en") is True


def test_factory_russian_audio_skips_translation_even_with_english_title():
    plan = {"metadata": {"language": "русский"}}
    info = {"language": "", "title": "An English SEO title"}
    assert resolve_factory_spoken_language(plan, info) == "ru"
    assert factory_language_needs_translation("ru") is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("en-US", "en"),
        ("English", "en"),
        ("русский", "ru"),
        ("ukr", "uk"),
        ("Belarusian", "be"),
        ("fr-FR", "fr"),
    ],
)
def test_factory_language_normalization(value, expected):
    assert normalize_factory_language(value) == expected


@pytest.mark.parametrize("language", ["uk", "be", "en", "fr", "de"])
def test_every_proven_non_russian_language_requires_russian_livedub(language):
    assert factory_language_needs_translation(language) is True


def test_factory_unknown_language_is_fail_closed_without_title_guessing():
    with pytest.raises(RuntimeError, match="Не удалось доказать язык речи"):
        resolve_factory_spoken_language(
            {"metadata": {"language": "unknown"}},
            {"language": "", "title": "Евангелие"},
        )


def test_enabled_legacy_cuts_convert_valid_cache_to_replay():
    assert cut_cache_validity(True, "ok", pipeline_requested=True) == (
        False,
        "cut_cache_replay",
    )
    assert cut_cache_validity(True, "ok", pipeline_requested=False) == (True, "ok")


def test_eng_cut_ignores_cached_file_id_but_keeps_other_cache_fields():
    cached = {
        "ai_data": {"real_title": "T"},
        "livedub_file_id": "telegram-file-id",
        "livedub_file_id_version": "v1",
    }
    adjusted = cached_record_for_cut_source(
        cached,
        pipeline_requested=True,
        translated_required=True,
    )
    assert adjusted is not cached
    assert adjusted["ai_data"] == cached["ai_data"]
    assert adjusted["livedub_file_id"] == ""
    assert adjusted["livedub_file_id_version"] == ""
    assert cached["livedub_file_id"] == "telegram-file-id"
    assert cached_record_for_cut_source(
        cached,
        pipeline_requested=True,
        translated_required=False,
    ) is cached


def test_cached_cut_replay_disables_publication_features_only_task_locally():
    base = {
        "synopsis": True,
        "analytics": True,
        "shorts": True,
        "clips": True,
    }
    with cut_source_mode_context("rus", pipeline_requested=True):
        token = source_policy._CUT_CACHE_REPLAY.set(True)
        try:
            replay = cut_replay_settings(base)
            assert replay["synopsis"] is False
            assert replay["analytics"] is False
            assert replay["shorts"] is True
            assert replay["clips"] is True
            assert cut_replay_setting_value("generate_pdf", True) is False
        finally:
            source_policy._CUT_CACHE_REPLAY.reset(token)
    assert cut_replay_settings(base) == base
    assert cut_replay_setting_value("synopsis", True) is True


@pytest.mark.asyncio
async def test_cached_cut_replay_suppresses_only_main_mp3(tmp_path):
    main_mp3 = tmp_path / "main.mp3"
    companion = tmp_path / "main_livedub_ru.mp3"
    main_mp3.write_bytes(b"main")
    companion.write_bytes(b"ru")

    class Message:
        def __init__(self):
            self.audios = []

        async def reply_audio(self, *args, **kwargs):
            self.audios.append(kwargs.get("audio") or args[0])
            return "sent"

    message = Message()
    proxy = source_policy._CutReplayMessageProxy(message)
    replay_token = source_policy._CUT_CACHE_REPLAY.set(True)
    mp3_token = source_policy._CUT_MAIN_MP3_PATH.set(main_mp3)
    try:
        suppressed = await proxy.reply_audio(audio=main_mp3)
        delivered = await proxy.reply_audio(audio=companion)
    finally:
        source_policy._CUT_MAIN_MP3_PATH.reset(mp3_token)
        source_policy._CUT_CACHE_REPLAY.reset(replay_token)
    assert suppressed.audio is None
    assert delivered == "sent"
    assert message.audios == [companion]


def test_legacy_eng_cut_source_policy_is_task_local_and_fail_closed(tmp_path):
    translated = tmp_path / "translated.mp4"
    translated.write_bytes(b"x")
    assert translated_source_required("rus") is False
    assert translated_source_required("eng") is True
    with cut_source_mode_context("eng", pipeline_requested=True):
        assert cut_source_is_usable(None) is False
        assert cut_source_is_usable(translated) is True
    with cut_source_mode_context("rus"):
        assert cut_source_is_usable(None) is True


@pytest.mark.asyncio
async def test_clip_delivery_proxy_uses_actual_probed_duration(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=612.6)

    async def fake_probe(path):
        assert path == video
        return probe

    monkeypatch.setattr(source_policy, "probe_media_async", fake_probe)
    monkeypatch.setattr(source_policy, "media_probe_is_deliverable", lambda value: value is probe)

    class Message:
        def __init__(self):
            self.kwargs = None

        async def reply_video(self, *args, **kwargs):
            self.kwargs = kwargs
            return "sent"

    message = Message()
    proxy = source_policy._ClipMessageProxy(message)
    assert await proxy.reply_video(video=video, duration=600) == "sent"
    assert message.kwargs["duration"] == 613


def test_factory_translation_preflight_requires_route_and_oauth_by_default():
    assert factory_translation_preflight_issues(
        oauth_present=False,
        helper_available=False,
        cli_available=False,
        require_oauth=True,
    ) == (
        "Yandex LiveDub client route is unavailable",
        "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN is missing",
    )


def test_factory_translation_preflight_allows_explicit_cached_only_opt_out():
    assert factory_translation_preflight_issues(
        oauth_present=False,
        helper_available=True,
        cli_available=False,
        require_oauth=False,
    ) == ()


def test_factory_preflight_reports_every_missing_runtime_dependency():
    issues = factory_preflight_issues(
        gemini_available=False,
        whisper_available=False,
        ffmpeg_available=False,
        ffprobe_available=False,
        free_gb=0.4,
        min_free_gb=2.0,
    )
    assert issues == (
        "Gemini API clients are unavailable",
        "faster-whisper is unavailable",
        "ffmpeg is unavailable",
        "ffprobe is unavailable",
        "free disk 0.4 GB is below 2.0 GB",
    )


def test_factory_preflight_accepts_complete_runtime():
    assert factory_preflight_issues(
        gemini_available=True,
        whisper_available=True,
        ffmpeg_available=True,
        ffprobe_available=True,
        free_gb=8.0,
        min_free_gb=2.0,
    ) == ()


def test_factory_owner_proves_language_before_selecting_source_backend():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    plan_pos = source.index("plan = await create_factory_plan(")
    language_pos = source.index("spoken_language = resolve_factory_spoken_language(plan, info)")
    source_task_pos = source.index("source_task = asyncio.create_task(", language_pos)
    assert plan_pos < language_pos < source_task_pos
    assert "_source_needs_translation" not in source
    assert "factory_language_needs_translation(spoken_language)" in source


def test_factory_delivery_counts_are_return_values_not_context_proxies():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    short_source = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")
    assert "shorts_sent = await process_and_send_factory_shorts(" in source
    assert "longs_sent = await process_and_send_clips(" in source
    assert "factory_completed_delivery_counts" not in source
    assert "factory_render_context" not in source
    assert "sent += 1" in short_source
    assert "SHORTS FACTORY MAX завершён" in source


def test_cut_mode_context_preserves_each_existing_entrypoint_chain():
    source = Path("services/cut_mode_source_policy.py").read_text(encoding="utf-8")
    assert "original_main_process = main_pipeline_module.process_single_video" in source
    assert "original_commands_process = commands_module.process_single_video" in source
    assert "original_playlist_process = playlist_module.process_single_video" in source
    assert "commands_process_with_mode = _wrap_process_entry(" in source
    assert "playlist_process_with_mode = _wrap_process_entry(" in source


def test_cached_cut_replay_preserves_cache_and_archive_surfaces():
    source = Path("services/cut_mode_source_policy.py").read_text(encoding="utf-8")
    assert "Cached cut replay: preserving existing video_cache record" in source
    assert "main_pipeline_module.adb_save = cut_aware_adb_save" in source
    assert "main_pipeline_module.asave_generated_page_record" in source
    assert "main_pipeline_module.asave_segment_plan_export" in source
    assert "main_pipeline_module.gemini_analyze_audio = cut_aware_gemini_analyze" in source


def test_factory_quality_and_execution_contracts_are_not_installer_stacks():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    execution = Path("services/shorts_factory_execution_guard.py").read_text(encoding="utf-8")
    assert "install_factory_plan_quality_gate" not in quality
    assert "install_shorts_factory_execution_guard" not in execution
    assert "shorts_factory_runtime" not in execution
    assert "process_shorts_factory_guarded" in execution
