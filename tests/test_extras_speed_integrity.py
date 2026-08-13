from pathlib import Path
from types import SimpleNamespace

import pytest

import pipelines.montage as montage
import services.shorts_video as shorts_video
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


@pytest.mark.asyncio
async def test_generic_normalize_only_packet_copies_video(monkeypatch, tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    input_path.write_bytes(b"input")
    commands = []

    monkeypatch.setattr(shorts_video.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    async def fake_run(command, *, timeout, text):
        commands.append((list(command), timeout, text))
        output_path.write_bytes(b"output")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video, "run_cancellable_process", fake_run)

    assert shorts_video._normalize_only_can_copy_video(
        normalize_audio=True,
        speed=1.0,
    )
    assert not shorts_video._normalize_only_can_copy_video(
        normalize_audio=True,
        speed=1.2,
    )
    assert not shorts_video._normalize_only_can_copy_video(
        normalize_audio=False,
        speed=1.0,
    )

    assert await shorts_video._normalize_audio_copy_video(input_path, output_path)
    assert len(commands) == 1
    command, timeout, text = commands[0]
    assert timeout == 600
    assert text is True
    video_codec_index = command.index("-c:v")
    assert command[video_codec_index + 1] == "copy"
    audio_filter_index = command.index("-af")
    assert command[audio_filter_index + 1] == "loudnorm=I=-16:TP=-1.5:LRA=11"
    assert "h264_nvenc" not in command
    assert "libx264" not in command


def test_generic_transform_uses_copy_only_for_audio_only_unity_path():
    source = Path("services/shorts_video.py").read_text(encoding="utf-8")
    start = source.index("async def _unowned_short_transform")
    end = source.index("async def _unowned_create_short_title_poster", start)
    transform = source[start:end]

    assert "_normalize_only_can_copy_video(" in transform
    assert "_normalize_audio_copy_video(input_path, output_path)" in transform
    assert "result = await _LEGACY_SHORT_TRANSFORM(" in transform
    assert "normalize_audio=normalize_audio" in transform
    assert "speed=speed_value" in transform


def test_montage_encodes_parts_but_packet_copies_final_video_concat():
    source = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    render_start = source.index("async def render_montage_short")
    render_end = source.index("def _extras_text_config", render_start)
    render = source[render_start:render_end]

    marker = "# All parts were rendered by the same encoder/filter contract above"
    concat_start = render.index(marker)
    before_concat = render[:concat_start]
    concat = render[concat_start:]

    assert '"-c:v", _enc, *_preset, *_quality' in before_concat
    assert '"-c:v", "copy"' in concat
    assert '"-c:a", "aac", "-b:a", "128k"' in concat
    assert '"-af", "aresample=async=1"' in concat
    assert '"-c:v", _enc' not in concat
    assert "async with _sched.gpu_render" not in concat
    assert '"-vsync", "vfr"' not in concat


def test_factory_and_verified_highlights_keep_their_existing_quality_owners():
    factory = Path("services/shorts_factory_video_quality.py").read_text(encoding="utf-8")
    highlights = Path("services/highlights_quality.py").read_text(encoding="utf-8")

    assert '"-c:v",\n        "copy"' in factory
    assert "Render all verified cuts from source in one owned encode" in highlights
