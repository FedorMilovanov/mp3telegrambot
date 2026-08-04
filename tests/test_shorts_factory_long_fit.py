from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_long_fit as long_fit


_GIB = 1024**3


def _probe(duration):
    return SimpleNamespace(
        duration=duration,
        has_video=True,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
    )


def test_long_fit_bitrate_uses_highest_budget_after_audio_and_reserve():
    bitrate = long_fit.factory_long_target_video_kbps(2000, 600)
    assert bitrate > 25000
    assert long_fit.factory_long_target_video_kbps(1000, 600) < bitrate


def test_long_fit_rejects_invalid_or_unacceptably_low_bitrate():
    with pytest.raises(RuntimeError, match="positive file-size"):
        long_fit.factory_long_target_video_kbps(0, 600)
    with pytest.raises(RuntimeError, match="positive duration"):
        long_fit.factory_long_target_video_kbps(2000, 0)
    with pytest.raises(RuntimeError, match="unacceptable video bitrate"):
        long_fit.factory_long_target_video_kbps(1, 900)


def test_long_fit_disk_requirement_reserves_target_and_overhead():
    required = long_fit.factory_long_fit_required_free_bytes(2000)
    assert required >= int(2000 * 1024**2 * 1.10)
    assert required >= 2 * _GIB


def test_long_fit_disk_guard_rejects_shortage(monkeypatch, tmp_path):
    monkeypatch.setattr(
        long_fit.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1 * _GIB),
    )

    with pytest.raises(RuntimeError, match="недостаточно места"):
        long_fit.ensure_factory_long_fit_space(tmp_path, 2000)


def test_two_pass_commands_preserve_resolution_and_exact_interval(tmp_path):
    first, second = long_fit._two_pass_commands(
        "ffmpeg",
        tmp_path / "source.mkv",
        tmp_path / "fitted.mp4",
        tmp_path / "passlog",
        start_seconds=123.456,
        duration_seconds=600.125,
        video_kbps=12000,
    )

    for command in (first, second):
        assert command[command.index("-ss") + 1] == "123.456"
        assert command[command.index("-t") + 1] == "600.125"
        assert command[command.index("-c:v") + 1] == "libx264"
        assert command[command.index("-preset") + 1] == "slow"
        assert command[command.index("-b:v") + 1] == "12000k"
        assert command[command.index("-pix_fmt") + 1] == "yuv420p"
        assert "-vf" not in command
        assert "-s" not in command

    assert first[first.index("-pass") + 1] == "1"
    assert "-an" in first
    assert first[first.index("-f") + 1] == "null"
    assert second[second.index("-pass") + 1] == "2"
    assert second[second.index("-c:a") + 1] == "aac"
    assert second[second.index("-b:a") + 1] == "192k"
    assert second[second.index("-movflags") + 1] == "+faststart"


@pytest.mark.asyncio
async def test_two_pass_fit_replaces_oversized_output_after_proof(
    monkeypatch,
    tmp_path,
):
    source_path = tmp_path / "source.mkv"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"s" * 4096)
    output_path.write_bytes(b"o" * 4096)
    commands = []

    async def fake_run(command, **kwargs):
        commands.append(list(command))
        pass_number = command[command.index("-pass") + 1]
        if pass_number == "2":
            Path(command[-1]).write_bytes(b"f" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        assert path.name.endswith("_factory_fit.mp4")
        return _probe(600.0)

    monkeypatch.setattr(long_fit, "run_cancellable_process", fake_run)
    monkeypatch.setattr(long_fit, "probe_media_async", fake_probe)
    monkeypatch.setattr(
        long_fit,
        "media_probe_is_deliverable",
        lambda value: value is not None,
    )
    monkeypatch.setattr(
        long_fit,
        "ensure_factory_long_fit_space",
        lambda *args, **kwargs: None,
    )

    result = await long_fit.fit_factory_long_clip_to_limit(
        source_path,
        output_path,
        100.0,
        700.0,
        max_file_size_mb=100,
        ffmpeg="ffmpeg",
    )

    assert result is True
    assert output_path.read_bytes() == b"f" * 4096
    assert len(commands) == 2
    assert not list(tmp_path.glob("clip_factory_x264*"))
    assert not (tmp_path / "clip_factory_fit.mp4").exists()


@pytest.mark.asyncio
async def test_two_pass_fit_rejects_truncated_result(
    monkeypatch,
    tmp_path,
):
    source_path = tmp_path / "source.mkv"
    output_path = tmp_path / "clip.mp4"
    source_path.write_bytes(b"s" * 4096)
    output_path.write_bytes(b"original")

    async def fake_run(command, **kwargs):
        if command[command.index("-pass") + 1] == "2":
            Path(command[-1]).write_bytes(b"f" * 4096)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(long_fit, "run_cancellable_process", fake_run)
    monkeypatch.setattr(
        long_fit,
        "probe_media_async",
        lambda _path: __import__("asyncio").sleep(0, result=_probe(597.0)),
    )
    monkeypatch.setattr(
        long_fit,
        "media_probe_is_deliverable",
        lambda value: value is not None,
    )
    monkeypatch.setattr(
        long_fit,
        "ensure_factory_long_fit_space",
        lambda *args, **kwargs: None,
    )

    result = await long_fit.fit_factory_long_clip_to_limit(
        source_path,
        output_path,
        100.0,
        700.0,
        max_file_size_mb=100,
        ffmpeg="ffmpeg",
    )

    assert result is False
    assert output_path.read_bytes() == b"original"
    assert not (tmp_path / "clip_factory_fit.mp4").exists()


def test_long_fit_is_required_by_disk_guard_before_execution():
    disk_guard = Path("services/shorts_factory_disk_guard.py").read_text(
        encoding="utf-8"
    )
    long_fit_source = Path(
        "services/shorts_factory_long_fit.py"
    ).read_text(encoding="utf-8")
    quality = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )

    assert "if not install_factory_long_fit_policy():" in disk_guard
    disk_pos = quality.index("if not install_factory_disk_guard():")
    execution_pos = quality.index(
        "if not install_shorts_factory_execution_guard():"
    )
    assert disk_pos < execution_pos
    assert "render_module.render_clip = factory_size_safe_render_clip" in long_fit_source
    assert "clips_module.render_clip = factory_size_safe_render_clip" in long_fit_source
    assert "\ninstall_factory_long_fit_policy()\n" not in long_fit_source
