from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "services" / "shorts_static_runtime.py"


def _load_runtime():
    spec = importlib.util.spec_from_file_location("shorts_static_runtime_test", RUNTIME_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trailing_freeze_counts_only_from_real_start() -> None:
    runtime = _load_runtime()
    assert runtime._freeze_coverage("freeze_start: 0.2", 8.0) == 0.975
    assert runtime._freeze_coverage("freeze_start: 7.6", 8.0) < 0.10


def test_closed_freeze_duration_is_clamped_and_counted() -> None:
    runtime = _load_runtime()
    output = "\n".join(
        [
            "lavfi.freezedetect.freeze_start: 0.1",
            "lavfi.freezedetect.freeze_end: 3.1 | lavfi.freezedetect.freeze_duration: 3.0",
            "lavfi.freezedetect.freeze_start: 4.0",
        ]
    )
    assert runtime._freeze_coverage(output, 8.0) == 0.875


def test_ydif_parser_accepts_ffmpeg_metadata_forms() -> None:
    runtime = _load_runtime()
    output = "lavfi.signalstats.YDIF=0.12\nlavfi.signalstats.YDIF: 1.50"
    assert runtime._parse_ydif_values(output) == [0.12, 1.5]


def test_static_slide_requires_enough_low_motion_evidence() -> None:
    runtime = _load_runtime()
    is_static, metrics = runtime._classify_static_metrics(
        freeze_ratio=0.96,
        ydif_values=[0.10] * 24,
        probe_seconds=8.0,
    )
    assert is_static is True
    assert metrics["reason"] == "dominant-freeze+low-motion"


def test_moving_or_uncertain_video_keeps_crop_zoom() -> None:
    runtime = _load_runtime()
    moving, _ = runtime._classify_static_metrics(
        freeze_ratio=0.15,
        ydif_values=[2.0, 3.5, 5.0] * 8,
        probe_seconds=8.0,
    )
    sparse, sparse_metrics = runtime._classify_static_metrics(
        freeze_ratio=1.0,
        ydif_values=[0.0, 0.0],
        probe_seconds=8.0,
    )
    assert moving is False
    assert sparse is False
    assert sparse_metrics["reason"] == "insufficient-motion-samples"


def test_probe_suppresses_noise_and_emphasizes_central_motion() -> None:
    runtime = _load_runtime()
    vf = runtime._probe_filter(-50.0, 1.5)
    assert "crop=trunc(iw*0.56/2)*2" in vf
    assert "scale=96:96:flags=area" in vf
    assert "format=gray" in vf
    assert "setpts=PTS-STARTPTS" in vf
    assert "signalstats" in vf
    assert "freezedetect=n=-50.0dB:d=1.50" in vf


def test_runtime_probe_static_and_fail_safe(monkeypatch, tmp_path: Path) -> None:
    runtime = _load_runtime()
    source = tmp_path / "slide.mp4"
    source.write_bytes(b"not-a-real-video")

    ydif = "\n".join("lavfi.signalstats.YDIF=0.10" for _ in range(24))
    output = "lavfi.freezedetect.freeze_start: 0.0\n" + ydif
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr=output)

    monkeypatch.setattr(runtime.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    runtime._CACHE.clear()

    assert asyncio.run(runtime._is_static_video_confident(source, 12.0)) is True
    assert seen
    command = seen[0]
    assert command[command.index("-ss") + 1] == "12.750"
    assert "scale=96:96:flags=area" in command[command.index("-vf") + 1]

    def failed_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="decode failed")

    monkeypatch.setattr(runtime.subprocess, "run", failed_run)
    runtime._CACHE.clear()
    assert asyncio.run(runtime._is_static_video_confident(source, 15.0)) is False


def test_installation_precedes_shorts_import_contract() -> None:
    init_source = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
    assert "install_short_static_runtime()" in init_source
    assert init_source.index("install_short_static_runtime()") < init_source.index(
        "install_conspect_quality_contract()"
    )
    assert "moving=crop_zoom" in (ROOT / "services" / "shorts_static_runtime.py").read_text(
        encoding="utf-8"
    )
