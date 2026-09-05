from pathlib import Path

import pytest

import services.shorts_factory_candidates as candidates
import services.shorts_factory_execution_guard as execution
import services.shorts_factory_quality_gate as gate
import services.shorts_factory_source as factory_source
import services.shorts_factory_timing as timing
from services.shorts_transcription import factory_subtitle_profile


def test_factory_model_floor_accepts_only_gemini_38_flash(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_MODEL", raising=False)
    assert candidates.DEFAULT_SHORTS_FACTORY_MODEL == "gemini-3.8-flash"
    assert candidates.shorts_factory_model() == "gemini-3.8-flash"

    for model in (
        "gemini-3.7-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemini-3.7-flash-lite",
        "gemini-3.6-pro",
        "gemini-pro",
        "my-gemini-3.8-flash",
    ):
        monkeypatch.setenv("SHORTS_FACTORY_MODEL", model)
        with pytest.raises(RuntimeError, match="requires gemini-3.8-flash"):
            candidates.shorts_factory_model()


def test_factory_timestamps_preserve_millisecond_precision():
    assert candidates._seconds(10.3754) == 10.375
    assert candidates._seconds("10.3754") == 10.375
    assert candidates._seconds("1:40.625") == 100.625
    assert candidates._seconds("1:02:03.125") == 3723.125
    assert candidates._seconds(-5) == 0.0
    assert candidates._seconds("nan") == 0.0
    assert candidates._seconds("inf") == 0.0
    assert candidates._seconds("broken") == 0.0


def test_factory_score_floors_can_only_be_tightened(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "70")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "10")
    assert gate._score_threshold(
        "SHORTS_FACTORY_MIN_SHORT_SCORE",
        gate.DEFAULT_MIN_SHORT_SCORE,
    ) == gate.DEFAULT_MIN_SHORT_SCORE
    assert gate._score_threshold(
        "SHORTS_FACTORY_MIN_LONG_SCORE",
        gate.DEFAULT_MIN_LONG_SCORE,
    ) == gate.DEFAULT_MIN_LONG_SCORE

    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "95")
    assert gate._score_threshold(
        "SHORTS_FACTORY_MIN_SHORT_SCORE",
        gate.DEFAULT_MIN_SHORT_SCORE,
    ) == 95.0


def test_factory_subtitle_profile_requires_exact_large_v3(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_WHISPER_MODEL", raising=False)
    assert factory_subtitle_profile() == {
        "model_name": "large-v3",
        "karaoke": True,
        "word_timestamps": True,
        "light": False,
        "gemini_hints": True,
    }

    for model in ("large-v3-turbo", "medium", "small"):
        monkeypatch.setenv("SHORTS_FACTORY_WHISPER_MODEL", model)
        with pytest.raises(RuntimeError, match="quality downgrade"):
            factory_subtitle_profile()


def test_factory_disk_floor_cannot_be_lowered(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MIN_FREE_GB", "0.5")
    assert execution.MIN_FACTORY_FREE_GB == 2.0
    assert execution._min_free_gb() == 2.0
    monkeypatch.setenv("SHORTS_FACTORY_MIN_FREE_GB", "4.5")
    assert execution._min_free_gb() == 4.5


def test_factory_livedub_timeout_cannot_be_lowered(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "60")
    assert factory_source._factory_livedub_timeout_seconds() == 1800
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_TIMEOUT_SEC", "2400")
    assert factory_source._factory_livedub_timeout_seconds() == 2400


def test_factory_timing_quality_bounds_are_direction_aware(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_RU_MIN_SILENCE_SEC", "0.05")
    assert timing._env_float(
        "SHORTS_FACTORY_RU_MIN_SILENCE_SEC",
        0.25,
        0.10,
        1.50,
    ) == 0.10

    monkeypatch.setenv("SHORTS_FACTORY_RU_MIN_COVERAGE", "0.10")
    assert timing._env_quality_min(
        "SHORTS_FACTORY_RU_MIN_COVERAGE",
        0.45,
        0.15,
        0.98,
    ) == 0.45
    monkeypatch.setenv("SHORTS_FACTORY_RU_MIN_COVERAGE", "0.70")
    assert timing._env_quality_min(
        "SHORTS_FACTORY_RU_MIN_COVERAGE",
        0.45,
        0.15,
        0.98,
    ) == 0.70

    monkeypatch.setenv("SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC", "1.5")
    assert timing._env_quality_max(
        "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC",
        4.0,
        1.0,
        20.0,
    ) == 1.5
    monkeypatch.setenv("SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC", "8.0")
    assert timing._env_quality_max(
        "SHORTS_FACTORY_MAX_UNTRANSLATED_SOURCE_BURST_SEC",
        4.0,
        1.0,
        20.0,
    ) == 4.0


def test_factory_exact_audited_ends_are_explicit_not_ambient():
    short_delivery = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")
    factory_pipeline = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    short_renderer = Path("services/shorts_video.py").read_text(encoding="utf-8")
    clip_renderer = Path("services/clip_renderer.py").read_text(encoding="utf-8")

    assert "snap_to_silence=False" in short_delivery
    assert "snap_to_silence=False" in factory_pipeline
    assert "snap_to_silence: bool = True" in short_renderer
    assert "snap_to_silence: bool = True" in clip_renderer
    assert "_FACTORY_SETTINGS" not in short_renderer
    assert "_FACTORY_SETTINGS" not in clip_renderer


def test_no_downgrade_is_not_a_runtime_installer_stack():
    assert not Path("services/shorts_factory_no_downgrade.py").exists()
    quality = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    execution_source = Path("services/shorts_factory_execution_guard.py").read_text(
        encoding="utf-8"
    )
    manifest = Path("services/runtime_manifest.py").read_text(encoding="utf-8")
    assert "install_factory_no_downgrade_policy" not in quality
    assert "install_factory_no_downgrade_policy" not in execution_source
    assert "shorts_factory_no_downgrade" not in manifest


def test_source_owned_dispatcher_has_no_deleted_factory_runtime():
    dispatcher = Path("pipelines/video_dispatch.py").read_text(encoding="utf-8")
    assert 'if mode == "shorts_max":' in dispatcher
    assert "from pipelines.shorts_factory import process_shorts_factory" in dispatcher
    assert "shorts_factory_runtime" not in dispatcher
