from pathlib import Path
from types import SimpleNamespace

import pytest

import pipelines.montage as montage
from services.media_delivery_probe import MediaProbe


def _probe(duration: float = 90.0) -> MediaProbe:
    return MediaProbe(
        duration=duration,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        size_mb=1.0,
    )


def _candidate() -> dict:
    return {
        "title": "Монтаж",
        "total_dur": 90.0,
        "fragments": [
            {"start_seconds": 0.0, "end_seconds": 30.0},
            {"start_seconds": 60.0, "end_seconds": 90.0},
            {"start_seconds": 120.0, "end_seconds": 150.0},
        ],
    }


def test_extras_speed_validation_is_finite_positive_and_explicit():
    assert montage._validated_extras_speed(1.0) == (1.0, False)
    assert montage._validated_extras_speed(1.5) == (1.5, True)
    assert montage._validated_extras_speed(0.99) == (0.99, True)
    for value in (0, -1, float("nan"), float("inf"), "broken"):
        assert montage._validated_extras_speed(value) is None


@pytest.mark.asyncio
async def test_required_speed_transform_failure_never_delivers_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(montage, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(montage, "get_shorts_visual_mode", lambda value: "crop_zoom")
    settings = {
        "shorts_audio_normalize": True,
        "shorts_snapshot": False,
        "shorts_subtitles": False,
        "shorts_title_poster": False,
    }

    async def get_setting(key):
        return settings[key]

    async def get_speed():
        return 1.5

    async def render(source_video_path, output_path, fragments, *, visual_mode):
        output_path.write_bytes(b"raw")
        return True

    async def probe(path):
        return _probe()

    post_calls = []

    async def failed_post(input_path, output_path, *, normalize_audio, speed):
        post_calls.append((Path(input_path), Path(output_path), normalize_audio, speed))
        return False

    monkeypatch.setattr(montage, "asettings_get", get_setting)
    monkeypatch.setattr(montage, "ashorts_speed_get", get_speed)
    monkeypatch.setattr(montage, "render_montage_short", render)
    monkeypatch.setattr(montage, "probe_media_async", probe)
    monkeypatch.setattr(montage, "file_size_mb", lambda path: 1.0)
    monkeypatch.setattr(montage, "get_max_file_size_mb", lambda: 100.0)
    monkeypatch.setattr(montage, "postprocess_short", failed_post)

    result = await montage._run_montage_or_highlights_pipeline(
        cand=_candidate(),
        video_path=tmp_path / "source.mp4",
        media_id="abc",
        prefix="montage_1",
        ai_data={},
        performer="Author",
        url="https://example.invalid",
        rutube_url="",
        vk_url="",
        update=SimpleNamespace(message=SimpleNamespace()),
        caption_fn=lambda: "caption",
    )

    assert result is False
    assert len(post_calls) == 1
    assert not (tmp_path / "abc_montage_1_raw.mp4").exists()
