from types import SimpleNamespace

import pytest

from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES
import pipelines.shorts_factory as factory
from pipelines.shorts_factory import (
    _persist_factory_source,
    _shift_candidates_for_livedub,
    _source_needs_translation,
    _translation_backend,
    _validated_source_duration,
)
from services.shorts_factory_runtime import (
    DEFAULT_FACTORY_WHISPER_MODEL,
    factory_shorts_speed,
    factory_subtitle_profile,
    is_subtitled_factory_delivery,
)


def test_shorts_factory_is_exposed_as_persistent_mode():
    assert "shorts_max" in VALID_MODES
    assert "SHORTS FACTORY MAX" in MODE_LABELS["shorts_max"]
    description = MODE_DESCRIPTIONS["shorts_max"]
    assert "Яндекс" in description
    assert "без собственного нейроперевода" in description


def test_non_russian_source_requires_translation():
    assert _source_needs_translation({"language": "en", "title": "A sermon"}) is True
    assert _source_needs_translation({"language": "fr", "title": "Un sermon"}) is True
    assert _source_needs_translation({"language": "ru", "title": "Проповедь"}) is False


def test_unknown_language_uses_title_script_as_conservative_signal():
    assert _source_needs_translation({"language": "", "title": "The Gospel"}) is True
    assert _source_needs_translation({"language": "", "title": "Евангелие"}) is False


def test_translation_backend_defaults_to_yandex_only(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_TRANSLATION_BACKEND", raising=False)
    assert _translation_backend() == "yandex_live"

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex")
    assert _translation_backend() == "yandex_live"

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "neural_future")
    assert _translation_backend() == "neural_future"


def test_livedub_shift_preserves_clip_duration(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC", "0.15")
    candidates = [
        {
            "start_seconds": 10,
            "end_seconds": 100,
            "duration_seconds": 90,
            "start": "0:10",
            "end": "1:40",
            "title": "Фрагмент",
        }
    ]

    shifted = _shift_candidates_for_livedub(candidates, source_duration=300)

    assert shifted[0]["start_seconds"] == 10.75
    assert shifted[0]["end_seconds"] == 100.75
    assert shifted[0]["duration_seconds"] == 90
    assert candidates[0]["start_seconds"] == 10


def test_livedub_shift_uses_extended_tail_timeline(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("SHORTS_FACTORY_LIVEDUB_SHIFT_EXTRA_SEC", "0.15")
    candidates = [
        {
            "start_seconds": 895,
            "end_seconds": 900,
            "duration_seconds": 5,
            "start": "14:55",
            "end": "15:00",
            "title": "Последняя Фраза",
        }
    ]

    shifted = _shift_candidates_for_livedub(candidates, source_duration=903)

    assert shifted[0]["start_seconds"] == 895.75
    assert shifted[0]["end_seconds"] == 900.75
    assert shifted[0]["duration_seconds"] == 5


def test_factory_source_is_moved_to_managed_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "DOWNLOAD_DIR", tmp_path)
    source = tmp_path / "temporary-source.mp4"
    source.write_bytes(b"x" * 2048)

    persisted = _persist_factory_source(source, "video123")

    assert persisted == tmp_path / "video123_factory_source.mp4"
    assert persisted.exists()
    assert not source.exists()


@pytest.mark.asyncio
async def test_factory_uses_real_probed_source_duration(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=901.2)

    async def fake_probe(path):
        assert path == source
        return probe

    monkeypatch.setattr(factory, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory, "media_probe_is_deliverable", lambda value: value is probe)

    assert await _validated_source_duration(source, expected_duration=900) == 902


@pytest.mark.asyncio
async def test_factory_rejects_truncated_source(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=890.0)

    async def fake_probe(_path):
        return probe

    monkeypatch.setattr(factory, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory, "media_probe_is_deliverable", lambda value: value is probe)

    with pytest.raises(RuntimeError, match="обрезан"):
        await _validated_source_duration(source, expected_duration=900)


@pytest.mark.asyncio
async def test_factory_rejects_source_without_video_and_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)

    async def fake_probe(_path):
        return None

    monkeypatch.setattr(factory, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory, "media_probe_is_deliverable", lambda _value: False)

    with pytest.raises(RuntimeError, match="media probe"):
        await _validated_source_duration(source, expected_duration=900)


def test_factory_forces_verified_timing_speed():
    assert factory_shorts_speed() == 1.0


def test_factory_subtitles_use_max_quality_profile(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_WHISPER_MODEL", raising=False)

    profile = factory_subtitle_profile()

    assert DEFAULT_FACTORY_WHISPER_MODEL == "large-v3"
    assert profile == {
        "model_name": "large-v3",
        "karaoke": True,
        "word_timestamps": True,
        "light": False,
        "gemini_hints": True,
    }


def test_factory_whisper_model_has_explicit_override(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_WHISPER_MODEL", "large-v3-turbo")

    assert factory_subtitle_profile()["model_name"] == "large-v3-turbo"


def test_factory_delivery_accepts_only_burned_subtitle_artifact(tmp_path):
    assert is_subtitled_factory_delivery(tmp_path / "video_short_1_sub.mp4") is True
    assert is_subtitled_factory_delivery(tmp_path / "video_short_1_post.mp4") is False
    assert is_subtitled_factory_delivery(tmp_path / "video_short_1_raw.mp4") is False
