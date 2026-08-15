from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_disk_guard as disk_guard
import services.shorts_factory_source as source

_GIB = 1024**3


def test_selection_payload_sums_selected_video_and_audio_streams():
    payload = {
        "duration": 600,
        "requested_downloads": [{
            "requested_formats": [
                {"filesize": 900_000_000, "duration": 600},
                {"filesize_approx": 80_000_000, "duration": 600},
            ]
        }],
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


def test_audio_disk_model_includes_native_stream_and_pcm_working_peak():
    estimate = 500 * 1024**2
    duration = 3 * 3600
    required = disk_guard.required_factory_free_bytes("audio", estimate, duration)
    pcm_bound = duration * 48_000 * 2 * 2
    expected_modeled = estimate + pcm_bound * 1.10 + 512 * 1024**2
    assert required >= expected_modeled
    assert required >= 2 * _GIB


def test_unknown_audio_and_video_sizes_use_conservative_floors():
    assert disk_guard.required_factory_free_bytes("audio", 0, 0) >= 4 * _GIB
    assert disk_guard.required_factory_free_bytes("video", 0, 0) >= 6 * _GIB


def test_video_disk_model_includes_separate_streams_and_merged_output():
    estimate = 4 * _GIB
    required = disk_guard.required_factory_free_bytes("video", estimate, 3600)
    assert required >= estimate * 2.20 + _GIB


def test_unknown_video_size_uses_duration_based_model():
    duration = 2 * 3600
    required = disk_guard.required_factory_free_bytes("video", 0, duration)
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


def test_free_space_guard_checks_every_target_and_rejects_shortage(monkeypatch, tmp_path):
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


def test_source_owns_delivery_sort_and_pre_download_capacity_checks():
    source_code = Path(source.__file__).read_text(encoding="utf-8")
    assert "factory_delivery_sort_args(" in source_code
    assert "ensure_factory_audio_space(" in source_code
    assert "ensure_factory_video_space(" in source_code
    assert "expected_duration" in source_code
    assert "hdr:0,res,fps" in Path(disk_guard.__file__).read_text(encoding="utf-8")


def test_factory_pipeline_is_audio_plan_first_without_event_state():
    pipeline = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    audio_pos = pipeline.index("mp3_path = await _download_factory_audio(")
    plan_pos = pipeline.index("plan = await create_factory_plan(")
    source_task_pos = pipeline.index("source_task = asyncio.create_task(", plan_pos)
    assert audio_pos < plan_pos < source_task_pos

    guard = Path(disk_guard.__file__).read_text(encoding="utf-8")
    assert "ContextVar" not in guard
    assert "asyncio.Event" not in guard
    assert "sys.modules" not in guard
    assert "install_factory_disk_guard" not in guard
    assert "register_factory_source_info" not in guard
    assert "mark_factory_analysis_audio_skipped" not in guard


def test_translation_source_checks_capacity_with_known_duration_before_download():
    source_code = Path(source.__file__).read_text(encoding="utf-8")
    function = source_code[source_code.index("async def prepare_factory_translation_video") :]
    ensure_pos = function.index("ensure_factory_video_space(")
    download_pos = function.index("download_factory_video_source(")
    assert ensure_pos < download_pos
    assert "duration_seconds=float(duration)" in function
    assert "expected_duration=float(duration)" in function
