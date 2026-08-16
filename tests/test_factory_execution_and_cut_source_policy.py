from pathlib import Path

import pytest

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
    assert resolve_factory_spoken_language({"metadata": {"language": "English"}}, {"title": "Русский"}) == "en"
    assert factory_language_needs_translation("en") is True


def test_factory_russian_audio_skips_translation_even_with_english_title():
    assert resolve_factory_spoken_language({"metadata": {"language": "русский"}}, {"title": "English title"}) == "ru"
    assert factory_language_needs_translation("ru") is False


@pytest.mark.parametrize(("value", "expected"), [("en-US", "en"), ("English", "en"), ("русский", "ru"), ("ukr", "uk"), ("Belarusian", "be"), ("fr-FR", "fr")])
def test_factory_language_normalization(value, expected):
    assert normalize_factory_language(value) == expected


@pytest.mark.parametrize("language", ["uk", "be", "en", "fr", "de"])
def test_every_proven_non_russian_language_requires_russian_livedub(language):
    assert factory_language_needs_translation(language) is True


def test_factory_unknown_language_is_fail_closed_without_title_guessing():
    with pytest.raises(RuntimeError, match="Не удалось доказать язык речи"):
        resolve_factory_spoken_language({"metadata": {"language": "unknown"}}, {"title": "Евангелие"})


def test_factory_translation_preflight_requires_route_and_oauth_by_default():
    assert factory_translation_preflight_issues(oauth_present=False, helper_available=False, cli_available=False, require_oauth=True) == (
        "Yandex LiveDub client route is unavailable",
        "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN is missing",
    )


def test_factory_translation_preflight_allows_explicit_cached_only_opt_out():
    assert factory_translation_preflight_issues(oauth_present=False, helper_available=True, cli_available=False, require_oauth=False) == ()


def test_factory_preflight_reports_every_missing_runtime_dependency():
    assert factory_preflight_issues(gemini_available=False, whisper_available=False, ffmpeg_available=False, ffprobe_available=False, free_gb=0.4, min_free_gb=2.0) == (
        "Gemini API clients are unavailable",
        "faster-whisper is unavailable",
        "ffmpeg is unavailable",
        "ffprobe is unavailable",
        "free disk 0.4 GB is below 2.0 GB",
    )


def test_factory_owner_proves_language_before_selecting_source_backend():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    plan_pos = source.index("plan = await create_factory_plan(")
    language_pos = source.index("spoken_language = resolve_factory_spoken_language(plan, info)")
    source_task_pos = source.index("source_task = asyncio.create_task(", language_pos)
    assert plan_pos < language_pos < source_task_pos
    assert "_source_needs_translation" not in source
    assert "factory_language_needs_translation(spoken_language)" in source


def test_factory_quality_and_execution_contracts_are_not_installer_stacks():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    execution = Path("services/shorts_factory_execution_guard.py").read_text(encoding="utf-8")
    assert "install_factory_plan_quality_gate" not in quality
    assert "install_shorts_factory_execution_guard" not in execution
    assert "shorts_factory_runtime" not in execution
