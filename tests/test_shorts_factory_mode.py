from pathlib import Path
from types import SimpleNamespace

import pytest

from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES
import pipelines.factory_short_delivery as short_delivery
import pipelines.shorts_factory as factory
from pipelines.shorts_factory import _persist_factory_source, _translation_backend
import pipelines.video_dispatch as video_dispatch
import services.shorts_factory_media as factory_media
from services.shorts_factory_execution_guard import (
    factory_language_needs_translation,
    resolve_factory_spoken_language,
)
from services.shorts_factory_media import validated_factory_source_duration
from services.shorts_factory_timing import (
    RU_ONLY_BOUNDARY_PROOF,
    align_candidates_to_ru_speech,
    align_factory_livedub_candidates,
)
from services.shorts_transcription import (
    DEFAULT_FACTORY_WHISPER_MODEL,
    factory_subtitle_profile,
)


def test_shorts_factory_is_exposed_as_persistent_mode():
    assert "shorts_max" in VALID_MODES
    assert "SHORTS FACTORY MAX" in MODE_LABELS["shorts_max"]
    description = MODE_DESCRIPTIONS["shorts_max"]
    assert "Яндекс" in description
    assert "без собственного нейроперевода" in description


def test_translation_decision_prefers_spoken_audio_over_title_or_metadata():
    plan = {"metadata": {"language": "en"}}
    info = {"language": "ru", "title": "Русский заголовок"}
    spoken = resolve_factory_spoken_language(plan, info)
    assert spoken == "en"
    assert factory_language_needs_translation(spoken) is True

    plan = {"metadata": {"language": "ru"}}
    info = {"language": "en", "title": "English title"}
    spoken = resolve_factory_spoken_language(plan, info)
    assert spoken == "ru"
    assert factory_language_needs_translation(spoken) is False


def test_translation_backend_defaults_to_yandex_only(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_TRANSLATION_BACKEND", raising=False)
    assert _translation_backend() == "yandex_live"
    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex")
    assert _translation_backend() == "yandex_live"
    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "neural_future")
    assert _translation_backend() == "neural_future"


def test_livedub_alignment_snaps_semantic_range_to_proved_ru_speech():
    candidates = [{
        "start_seconds": 10,
        "end_seconds": 100,
        "duration_seconds": 90,
        "start": "0:10",
        "end": "1:40",
        "title": "Фрагмент",
    }]
    aligned = align_candidates_to_ru_speech(
        candidates,
        source_duration=300,
        speech_intervals=[(10.8, 101.2)],
        delay_seconds=0.0,
    )
    assert aligned[0]["start_seconds"] == pytest.approx(10.8)
    assert aligned[0]["end_seconds"] == pytest.approx(101.28)
    assert aligned[0]["livedub_semantic_start_seconds"] == 10
    assert aligned[0]["livedub_semantic_end_seconds"] == 100
    assert aligned[0]["livedub_ru_boundary_proof"] == RU_ONLY_BOUNDARY_PROOF
    assert aligned[0]["livedub_ru_speech_coverage"] > 0.99
    assert candidates[0]["start_seconds"] == 10


def test_livedub_alignment_uses_proved_ru_speech_near_source_tail():
    candidates = [{
        "start_seconds": 860,
        "end_seconds": 900,
        "duration_seconds": 40,
        "start": "14:20",
        "end": "15:00",
        "title": "Последняя Фраза",
    }]
    aligned = align_candidates_to_ru_speech(
        candidates,
        source_duration=903,
        speech_intervals=[(860.6, 901.1)],
        delay_seconds=0.0,
    )
    assert aligned[0]["start_seconds"] == pytest.approx(860.6)
    assert aligned[0]["end_seconds"] == pytest.approx(901.18)
    assert aligned[0]["end_seconds"] <= 903
    assert aligned[0]["livedub_ru_boundary_proof"] == RU_ONLY_BOUNDARY_PROOF


def test_livedub_alignment_rejects_unproved_original_timeline_fallback():
    candidates = [{
        "start_seconds": 10,
        "end_seconds": 100,
        "duration_seconds": 90,
        "title": "Без Русского Доказательства",
    }]
    with pytest.raises(RuntimeError, match="refusing unverified original-timeline cuts"):
        align_factory_livedub_candidates(candidates, source_duration=300)


def test_factory_source_is_moved_to_managed_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(factory, "DOWNLOAD_DIR", tmp_path)
    source = tmp_path / "temporary-source.mp4"
    source.write_bytes(b"x" * 2048)
    persisted = _persist_factory_source(source, "video123")
    assert persisted == tmp_path / "video123_factory_source.mp4"
    assert persisted.exists()
    assert not source.exists()


@pytest.mark.asyncio
async def test_factory_uses_exact_probed_source_duration(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=901.2)

    async def fake_probe(path):
        assert path == source
        return probe

    monkeypatch.setattr(factory_media, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory_media, "media_probe_is_deliverable", lambda value: value is probe)
    assert await validated_factory_source_duration(source, expected_duration=900) == 901.2


@pytest.mark.asyncio
async def test_factory_rejects_truncated_source(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=890.0)

    async def fake_probe(_path):
        return probe

    monkeypatch.setattr(factory_media, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory_media, "media_probe_is_deliverable", lambda value: value is probe)
    with pytest.raises(RuntimeError, match="обрезан"):
        await validated_factory_source_duration(source, expected_duration=900)


@pytest.mark.asyncio
async def test_factory_rejects_source_without_video_and_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)

    async def fake_probe(_path):
        return None

    monkeypatch.setattr(factory_media, "probe_media_async", fake_probe)
    monkeypatch.setattr(factory_media, "media_probe_is_deliverable", lambda _value: False)
    with pytest.raises(RuntimeError, match="media probe"):
        await validated_factory_source_duration(source, expected_duration=900)


def test_factory_subtitles_use_explicit_max_quality_profile(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_WHISPER_MODEL", raising=False)
    assert DEFAULT_FACTORY_WHISPER_MODEL == "large-v3"
    assert factory_subtitle_profile() == {
        "model_name": "large-v3",
        "karaoke": True,
        "word_timestamps": True,
        "light": False,
        "gemini_hints": True,
    }


def test_factory_whisper_model_has_explicit_override(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_WHISPER_MODEL", "large-v3-turbo")
    assert factory_subtitle_profile()["model_name"] == "large-v3-turbo"


def test_factory_short_ceiling_is_absolute_start_plus_180_and_source_bounded():
    assert short_delivery.FACTORY_SHORT_PUBLIC_MAX_SEC == 180.0
    assert short_delivery._factory_snap_ceiling(250.0, 1000.0) == 430.0
    assert short_delivery._factory_snap_ceiling(900.0, 1000.0) == 1000.0


def test_factory_long_public_cap_is_source_owned():
    assert factory.FACTORY_LONG_PUBLIC_MAX_SEC == 900.0


def test_factory_short_delivery_has_no_runtime_proxy_or_trim_ui():
    source = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")
    assert "shorts_factory_runtime" not in source
    assert "reply_markup=" not in source
    assert "speed=1.0" in source
    assert "silence_snap_max_end=ceiling" in source
    assert "sent += 1" in source


def test_video_dispatch_routes_factory_without_runtime_rebinding():
    source = Path(video_dispatch.__file__).read_text(encoding="utf-8")
    assert 'if mode == "shorts_max":' in source
    assert "from pipelines.shorts_factory import process_shorts_factory" in source
    assert "shorts_factory_runtime" not in source
    assert "setattr(" not in source
