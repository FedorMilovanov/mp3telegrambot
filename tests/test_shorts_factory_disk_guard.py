from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_disk_guard as disk_guard
import services.shorts_factory_source as source


_GIB = 1024**3


def test_selection_payload_sums_selected_video_and_audio_streams():
    payload = {
        "duration": 600,
        "requested_downloads": [
            {
                "requested_formats": [
                    {"filesize": 900_000_000, "duration": 600},
                    {"filesize_approx": 80_000_000, "duration": 600},
                ]
            }
        ],
    }

    estimated, duration = disk_guard.estimate_factory_selection_payload(payload)

    assert estimated == 980_000_000
    assert duration == 600


def test_selection_payload_uses_bitrate_when_size_is_missing():
    payload = {
        "duration": 120,
        "requested_formats": [
            {"tbr": 8_000, "duration": 120},
            {"tbr": 160, "duration": 120},
        ],
    }

    estimated, duration = disk_guard.estimate_factory_selection_payload(payload)

    assert estimated == 122_400_000
    assert duration == 120


def test_audio_disk_model_includes_native_stream_and_lossless_pcm_peak():
    estimate = 500 * 1024**2
    duration = 3 * 3600

    required = disk_guard.required_factory_free_bytes(
        "audio",
        estimate,
        duration,
    )

    pcm_bound = duration * 48_000 * 2 * 2
    expected_modeled = estimate + pcm_bound * 1.10 + 512 * 1024**2
    assert required >= expected_modeled
    assert required >= 2 * _GIB


def test_unknown_audio_and_video_sizes_use_conservative_floors():
    assert disk_guard.required_factory_free_bytes("audio", 0, 0) >= 4 * _GIB
    assert disk_guard.required_factory_free_bytes("video", 0, 0) >= 6 * _GIB


def test_video_disk_model_includes_separate_streams_and_merged_output():
    estimate = 4 * _GIB

    required = disk_guard.required_factory_free_bytes(
        "video",
        estimate,
        3600,
    )

    assert required >= estimate * 2.20 + _GIB


def test_unknown_video_size_uses_duration_based_model():
    duration = 2 * 3600

    required = disk_guard.required_factory_free_bytes(
        "video",
        0,
        duration,
    )

    twelve_mbps_bytes = duration * 12_000_000 / 8
    assert required >= twelve_mbps_bytes * 2.20 + _GIB


def test_invalid_disk_estimate_kind_fails_closed():
    with pytest.raises(ValueError, match="Unsupported Factory disk estimate"):
        disk_guard.required_factory_free_bytes("unknown", 1, 1)


def test_factory_delivery_sort_maximizes_resolution_then_prefers_sdr():
    base = [
        "--format-sort-reset",
        "--no-format-sort-force",
        "--no-prefer-free-formats",
    ]

    result = disk_guard.factory_delivery_sort_args(base)

    assert result[:3] == base
    assert result[-2:] == ["--format-sort", "res,fps,hdr:0"]


def test_free_space_guard_checks_every_target_and_rejects_shortage(
    monkeypatch,
    tmp_path,
):
    checked = []

    def fake_usage(path):
        checked.append(Path(path))
        free = 8 * _GIB if Path(path).name == "enough" else 1 * _GIB
        return SimpleNamespace(free=free)

    enough = tmp_path / "enough"
    shortage = tmp_path / "shortage"
    monkeypatch.setattr(disk_guard.shutil, "disk_usage", fake_usage)

    with pytest.raises(RuntimeError, match="недостаточно места"):
        disk_guard.ensure_factory_free_space(
            [enough, shortage],
            required_bytes=6 * _GIB,
            label="тестового MAX-источника",
        )

    assert enough in checked
    assert shortage in checked


@pytest.mark.asyncio
async def test_estimate_uses_same_sdr_factory_sort_and_selected_format(
    monkeypatch,
):
    captured = []
    payload = {
        "duration": 300,
        "requested_formats": [
            {"filesize": 700_000_000, "duration": 300},
            {"filesize": 40_000_000, "duration": 300},
        ],
    }

    async def fake_run(command, **kwargs):
        captured.append((list(command), dict(kwargs)))
        return SimpleNamespace(
            returncode=0,
            stdout="diagnostic\n" + __import__("json").dumps(payload),
            stderr="",
        )

    monkeypatch.setattr(
        source,
        "YTDLP_BASE_ARGS",
        ["python", "-m", "yt_dlp", "--format-sort", "ext:mp4:m4a"],
    )
    original_reset = source._factory_quality_sort_reset
    monkeypatch.setattr(
        source,
        "_factory_quality_sort_reset",
        lambda: disk_guard.factory_delivery_sort_args(original_reset()),
    )
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    estimated, duration = await disk_guard.estimate_factory_selection(
        "https://youtu.be/example",
        "bestvideo+bestaudio/best",
    )

    command, kwargs = captured[0]
    assert estimated == 740_000_000
    assert duration == 300
    assert "--format-sort-reset" in command
    assert "--no-format-sort-force" in command
    assert "--no-prefer-free-formats" in command
    assert "res,fps,hdr:0" in command
    assert command[command.index("--format") + 1] == (
        "bestvideo+bestaudio/best"
    )
    assert "--simulate" in command
    assert "--dump-single-json" in command
    assert kwargs["text"] is True


@pytest.mark.asyncio
async def test_failed_estimate_returns_unknown_for_conservative_floor(monkeypatch):
    async def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="network error")

    monkeypatch.setattr(source, "run_cancellable_process", fake_run)

    assert await disk_guard.estimate_factory_selection(
        "https://youtu.be/example",
        "bestaudio/best",
    ) == (0, 0.0)


def test_disk_guard_installs_after_source_and_before_execution():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(
        encoding="utf-8"
    )
    guard = Path("services/shorts_factory_disk_guard.py").read_text(
        encoding="utf-8"
    )

    source_pos = quality.index("if not install_factory_source_quality_policy():")
    disk_pos = quality.index("if not install_factory_disk_guard():")
    execution_pos = quality.index(
        "if not install_shorts_factory_execution_guard():"
    )

    assert source_pos < disk_pos < execution_pos
    assert "source._factory_quality_sort_reset = output_safe_sort_reset" in guard
    assert "source.download_factory_audio_source = guarded_audio" in guard
    assert "source.download_factory_video_source = guarded_video" in guard
    assert "res,fps,hdr:0" in guard
    assert "\ninstall_factory_disk_guard()\n" not in guard
