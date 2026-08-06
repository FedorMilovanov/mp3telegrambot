from pathlib import Path
from types import ModuleType, SimpleNamespace
import asyncio
import sys

import pytest

import services.shorts_factory_disk_guard as disk_guard


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


def test_factory_delivery_sort_prefers_sdr_then_maximizes_resolution():
    base = [
        "--format-sort-reset",
        "--no-format-sort-force",
        "--no-prefer-free-formats",
    ]

    result = disk_guard.factory_delivery_sort_args(base)

    assert result[:3] == base
    assert result[-2:] == ["--format-sort", "hdr:0,res,fps"]


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
async def test_estimate_reuses_metadata_without_launching_ytdlp(monkeypatch):
    url = "https://youtu.be/example"
    disk_guard._DURATION_HINTS.clear()
    disk_guard._ACTIVE_REQUESTS.clear()

    async def forbidden_process(*args, **kwargs):
        raise AssertionError("disk estimate must not launch a child process")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_process)
    disk_guard.register_factory_source_info(url, {"duration": 1234})

    assert await disk_guard.estimate_factory_selection(
        url,
        "bestvideo+bestaudio/best",
    ) == (0, 1234)


@pytest.mark.asyncio
async def test_factory_orders_audio_before_video_and_uses_local_metadata(
    monkeypatch,
    tmp_path,
):
    events = []
    url = "https://youtu.be/example"

    pipeline = ModuleType("pipelines.shorts_factory")
    source = ModuleType("services.shorts_factory_source")
    long_fit = ModuleType("services.shorts_factory_long_fit")

    async def load_info(request_url):
        assert request_url == url
        return {"duration": 600}

    async def audio_download(request_url, media_id):
        events.append("audio-start")
        await asyncio.sleep(0.01)
        events.append("audio-end")
        return tmp_path / f"{media_id}.flac"

    async def video_download(request_url, media_id, workdir=None):
        events.append("video-start")
        assert events.index("audio-end") < events.index("video-start")
        return tmp_path / f"{media_id}.mkv"

    pipeline._load_video_info = load_info
    pipeline._download_factory_audio = audio_download
    pipeline.download_video_for_shorts = video_download
    source.DOWNLOAD_DIR = tmp_path
    source.download_factory_audio_source = audio_download
    source.download_factory_video_source = video_download
    source._factory_quality_sort_reset = lambda: [
        "--format-sort-reset",
        "--no-format-sort-force",
        "--no-prefer-free-formats",
    ]
    long_fit.install_factory_long_fit_policy = lambda: True

    pipelines_package = ModuleType("pipelines")
    pipelines_package.shorts_factory = pipeline
    monkeypatch.setitem(sys.modules, "pipelines", pipelines_package)
    monkeypatch.setitem(sys.modules, "pipelines.shorts_factory", pipeline)
    monkeypatch.setitem(sys.modules, "services.shorts_factory_source", source)
    monkeypatch.setitem(sys.modules, "services.shorts_factory_long_fit", long_fit)
    monkeypatch.setattr(disk_guard, "_INSTALLED", False)
    disk_guard._DURATION_HINTS.clear()
    disk_guard._ACTIVE_REQUESTS.clear()

    assert disk_guard.install_factory_disk_guard()
    assert await pipeline._load_video_info(url) == {"duration": 600}

    video_task = asyncio.create_task(
        pipeline.download_video_for_shorts(url, "video", workdir=tmp_path)
    )
    await asyncio.sleep(0)
    assert "video-start" not in events

    audio_path = await pipeline._download_factory_audio(url, "audio")
    video_path = await video_task

    assert audio_path.name == "audio.flac"
    assert video_path.name == "video.mkv"
    assert events == ["audio-start", "audio-end", "video-start"]


def test_duration_hint_cache_is_bounded():
    disk_guard._DURATION_HINTS.clear()
    disk_guard._ACTIVE_REQUESTS.clear()

    for index in range(disk_guard._MAX_DURATION_HINTS + 20):
        disk_guard.register_factory_source_info(
            f"https://youtu.be/{index}",
            {"duration": index + 1},
        )

    assert len(disk_guard._DURATION_HINTS) == disk_guard._MAX_DURATION_HINTS
    assert len(disk_guard._ACTIVE_REQUESTS) <= disk_guard._MAX_ACTIVE_REQUESTS


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
    assert "factory_pipeline._load_video_info = load_info_with_disk_hint" in guard
    assert "source.download_factory_audio_source = guarded_audio" in guard
    assert "source.download_factory_video_source = guarded_video" in guard
    assert "await state.audio_done.wait()" in guard
    assert "state.audio_done.set()" in guard
    assert '"--simulate"' not in guard
    assert "run_cancellable_process(" not in guard
    assert "hdr:0,res,fps" in guard
    assert "\ninstall_factory_disk_guard()\n" not in guard
