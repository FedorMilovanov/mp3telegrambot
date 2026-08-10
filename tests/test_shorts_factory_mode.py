from types import SimpleNamespace

import pytest

from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES
import pipelines.shorts_factory as factory
from pipelines.shorts_factory import (
    _persist_factory_source,
    _source_needs_translation,
    _translation_backend,
)
import services.shorts_factory_media as factory_media
import services.shorts_factory_runtime as factory_runtime
from services.shorts_factory_media import validated_factory_source_duration
from services.shorts_factory_runtime import (
    DEFAULT_FACTORY_WHISPER_MODEL,
    factory_completed_delivery_counts,
    factory_long_delivery_count,
    factory_render_context,
    factory_short_delivery_count,
    factory_shorts_speed,
    factory_subtitle_profile,
    is_subtitled_factory_delivery,
)
from services.shorts_factory_timing import (
    align_candidates_to_ru_speech,
    align_factory_livedub_candidates,
)


@pytest.fixture(autouse=True)
def _factory_runtime_delivery_probe(monkeypatch):
    async def fake_probe(_path):
        return SimpleNamespace(duration=90.0)

    monkeypatch.setattr(factory_runtime, "probe_media_async", fake_probe)
    monkeypatch.setattr(
        factory_runtime,
        "media_probe_is_deliverable",
        lambda value: value is not None,
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


def test_livedub_alignment_snaps_semantic_range_to_proved_ru_speech():
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
    assert aligned[0]["livedub_ru_boundary_proof"] == "exact-vot-ru-silencedetect-v2"
    assert aligned[0]["livedub_ru_speech_coverage"] > 0.99
    assert candidates[0]["start_seconds"] == 10


def test_livedub_alignment_uses_proved_ru_speech_near_source_tail():
    candidates = [
        {
            "start_seconds": 860,
            "end_seconds": 900,
            "duration_seconds": 40,
            "start": "14:20",
            "end": "15:00",
            "title": "Последняя Фраза",
        }
    ]

    aligned = align_candidates_to_ru_speech(
        candidates,
        source_duration=903,
        speech_intervals=[(860.6, 901.1)],
        delay_seconds=0.0,
    )

    assert aligned[0]["start_seconds"] == pytest.approx(860.6)
    assert aligned[0]["end_seconds"] == pytest.approx(901.18)
    assert aligned[0]["end_seconds"] <= 903
    assert aligned[0]["livedub_ru_boundary_proof"] == "exact-vot-ru-silencedetect-v2"


def test_livedub_alignment_rejects_unproved_english_timeline_fallback():
    candidates = [
        {
            "start_seconds": 10,
            "end_seconds": 100,
            "duration_seconds": 90,
            "title": "Без Русского Доказательства",
        }
    ]

    with pytest.raises(RuntimeError, match="refusing unverified English-timeline cuts"):
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
    monkeypatch.setattr(
        factory_media,
        "media_probe_is_deliverable",
        lambda value: value is probe,
    )

    assert await validated_factory_source_duration(source, expected_duration=900) == 901.2


@pytest.mark.asyncio
async def test_factory_rejects_truncated_source(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=890.0)

    async def fake_probe(_path):
        return probe

    monkeypatch.setattr(factory_media, "probe_media_async", fake_probe)
    monkeypatch.setattr(
        factory_media,
        "media_probe_is_deliverable",
        lambda value: value is probe,
    )

    with pytest.raises(RuntimeError, match="обрезан"):
        await validated_factory_source_duration(source, expected_duration=900)


@pytest.mark.asyncio
async def test_factory_rejects_source_without_video_and_audio(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)

    async def fake_probe(_path):
        return None

    monkeypatch.setattr(factory_media, "probe_media_async", fake_probe)
    monkeypatch.setattr(
        factory_media,
        "media_probe_is_deliverable",
        lambda _value: False,
    )

    with pytest.raises(RuntimeError, match="media probe"):
        await validated_factory_source_duration(source, expected_duration=900)


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


@pytest.mark.asyncio
async def test_factory_delivery_counters_increment_only_after_success(tmp_path):
    class Message:
        async def reply_video(self, *args, **kwargs):
            return {"args": args, "kwargs": kwargs}

    subtitled = tmp_path / "video_short_1_sub.mp4"
    raw = tmp_path / "video_short_1_raw.mp4"
    short_proxy = factory_runtime._FactoryMessageProxy(Message())
    long_proxy = factory_runtime._FactoryLongMessageProxy(Message())

    completed_token = factory_runtime._FACTORY_COMPLETED_DELIVERIES.set(None)
    try:
        with factory_render_context([], []):
            assert factory_short_delivery_count() == 0
            assert factory_long_delivery_count() == 0

            await short_proxy.reply_video(video=subtitled)
            assert factory_short_delivery_count() == 1
            with pytest.raises(RuntimeError, match="subtitle-less"):
                await short_proxy.reply_video(video=raw)
            assert factory_short_delivery_count() == 1

            await long_proxy.reply_video(video=raw)
            assert factory_long_delivery_count() == 1

        assert factory_short_delivery_count() == 0
        assert factory_long_delivery_count() == 0
        assert factory_completed_delivery_counts() == (1, 1)
    finally:
        factory_runtime._FACTORY_COMPLETED_DELIVERIES.reset(completed_token)


@pytest.mark.asyncio
async def test_factory_final_delivery_enforces_caps_and_removes_trim_controls(
    monkeypatch,
    tmp_path,
):
    durations = {"short": 179.96, "long": 899.96}

    async def fake_probe(path):
        key = "short" if "short" in path.name else "long"
        return SimpleNamespace(duration=durations[key])

    monkeypatch.setattr(factory_runtime, "probe_media_async", fake_probe)

    class Message:
        def __init__(self):
            self.calls = []

        async def reply_video(self, *args, **kwargs):
            self.calls.append(dict(kwargs))
            return "sent"

    message = Message()
    short_proxy = factory_runtime._FactoryMessageProxy(message)
    long_proxy = factory_runtime._FactoryLongMessageProxy(message)
    short_path = tmp_path / "video_short_1_sub.mp4"
    long_path = tmp_path / "video_long_1.mp4"

    await short_proxy.reply_video(
        video=short_path,
        duration=177,
        reply_markup="unsafe-generic-trim-controls",
    )
    await long_proxy.reply_video(video=long_path, duration=897)

    assert message.calls[0]["duration"] == 180
    assert "reply_markup" not in message.calls[0]
    assert message.calls[1]["duration"] == 900

    durations["short"] = 180.06
    with pytest.raises(RuntimeError, match="exceeds 180s"):
        await short_proxy.reply_video(video=short_path)

    durations["long"] = 900.06
    with pytest.raises(RuntimeError, match="exceeds 900s"):
        await long_proxy.reply_video(video=long_path)


@pytest.mark.asyncio
async def test_factory_final_status_uses_actual_delivery_counts():
    class StatusMessage:
        def __init__(self):
            self.text = ""

        async def edit_text(self, text, *args, **kwargs):
            self.text = text
            return {"args": args, "kwargs": kwargs}

    status = StatusMessage()
    proxy = factory_runtime._FactoryStatusProxy(status)
    token = factory_runtime._FACTORY_COMPLETED_DELIVERIES.set((2, 1))
    try:
        await proxy.edit_text(
            "✅ SHORTS FACTORY MAX завершён: 5 Shorts, 3 длинных фрагмента."
        )
    finally:
        factory_runtime._FACTORY_COMPLETED_DELIVERIES.reset(token)

    assert status.text == (
        "✅ SHORTS FACTORY MAX завершён: 2 Shorts, 1 длинных фрагмента."
    )
