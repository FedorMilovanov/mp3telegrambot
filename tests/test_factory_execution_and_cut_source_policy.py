from pathlib import Path
from types import SimpleNamespace

import pytest

import services.cut_mode_source_policy as source_policy
from services.cut_mode_source_policy import (
    cached_record_for_cut_source,
    cut_cache_validity,
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
from services.shorts_factory_quality_gate import (
    validated_factory_plan_language,
)


def test_factory_quality_gate_requires_proven_dominant_spoken_language():
    assert validated_factory_plan_language(
        {"metadata": {"language": "en"}}
    ) == "en"

    with pytest.raises(RuntimeError, match="доминирующий язык речи"):
        validated_factory_plan_language(
            {"metadata": {"language": "mixed"}}
        )


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


def test_enabled_legacy_cuts_bypass_early_analysis_cache():
    assert cut_cache_validity(
        True,
        "ok",
        pipeline_requested=True,
    ) == (False, "cut_pipeline_requested")
    assert cut_cache_validity(
        True,
        "ok",
        pipeline_requested=False,
    ) == (True, "ok")


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
async def test_clip_delivery_proxy_uses_actual_probed_duration(
    monkeypatch,
    tmp_path,
):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=612.6)

    async def fake_probe(path):
        assert path == video
        return probe

    monkeypatch.setattr(source_policy, "probe_media_async", fake_probe)
    monkeypatch.setattr(
        source_policy,
        "media_probe_is_deliverable",
        lambda value: value is probe,
    )

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


def test_factory_executor_analyzes_audio_before_selecting_source_backend():
    source = Path("services/shorts_factory_execution_guard.py").read_text(
        encoding="utf-8"
    )

    plan_pos = source.index(
        "plan = await factory_module.create_factory_plan("
    )
    source_task_pos = source.index(
        "source_task = asyncio.create_task(",
        plan_pos,
    )

    assert plan_pos < source_task_pos
    assert "_source_needs_translation" not in source
    assert "resolve_factory_spoken_language(plan, info)" in source


def test_factory_partial_delivery_keeps_trim_source_and_reports_actual_counts():
    source = Path("services/shorts_factory_execution_guard.py").read_text(
        encoding="utf-8"
    )

    assert "shorts_sent, longs_sent = factory_completed_delivery_counts()" in source
    assert "keep_source_for_trim = shorts_sent > 0" in source
    assert "SHORTS FACTORY MAX частично завершён" in source
    assert "if total_sent <= 0:" in source


def test_cut_mode_context_preserves_each_existing_entrypoint_chain():
    source = Path("services/cut_mode_source_policy.py").read_text(
        encoding="utf-8"
    )

    assert "original_main_process = main_pipeline_module.process_single_video" in source
    assert "original_commands_process = commands_module.process_single_video" in source
    assert "original_playlist_process = playlist_module.process_single_video" in source
    assert "commands_process_with_mode = _wrap_process_entry(" in source
    assert "playlist_process_with_mode = _wrap_process_entry(" in source


def test_required_runtime_installs_new_guards_without_import_side_effects():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    execution = Path("services/shorts_factory_execution_guard.py").read_text(
        encoding="utf-8"
    )
    source_policy = Path("services/cut_mode_source_policy.py").read_text(
        encoding="utf-8"
    )

    assert "if not install_cut_mode_source_policy():" in quality
    assert "if not install_shorts_factory_execution_guard():" in quality
    assert "\ninstall_shorts_factory_execution_guard()\n" not in execution
    assert "\ninstall_cut_mode_source_policy()\n" not in source_policy
