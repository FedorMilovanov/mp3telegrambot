from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "services" / "shorts_static_policy.py"


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
    assert "format=yuv420p" in vf
    assert "setpts=PTS-STARTPTS" in vf
    assert "signalstats" in vf
    assert "freezedetect=n=-50.0dB:d=1.50" in vf


def _static_probe_output() -> str:
    ydif = "\n".join("lavfi.signalstats.YDIF=0.10" for _ in range(24))
    return "lavfi.freezedetect.freeze_start: 0.0\n" + ydif


def _moving_probe_output() -> str:
    return "\n".join("lavfi.signalstats.YDIF=4.00" for _ in range(24))


def test_runtime_uses_two_static_probes_and_fail_safe(monkeypatch, tmp_path: Path) -> None:
    runtime = _load_runtime()
    source = tmp_path / "slide.mp4"
    source.write_bytes(b"not-a-real-video")
    seen: list[list[str]] = []

    async def fake_owner(cmd, **kwargs):
        seen.append(list(cmd))
        assert kwargs == {"timeout": 45, "text": True}
        return SimpleNamespace(returncode=0, stdout="", stderr=_static_probe_output())

    monkeypatch.setattr(runtime.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(runtime, "run_cancellable_process", fake_owner)
    runtime._CACHE.clear()

    assert asyncio.run(runtime._is_static_video_confident(source, 12.0)) is True
    assert len(seen) == 2
    assert seen[0][seen[0].index("-ss") + 1] == "12.750"
    assert seen[1][seen[1].index("-ss") + 1] == "24.750"
    assert "scale=96:96:flags=area" in seen[0][seen[0].index("-vf") + 1]

    async def failed_owner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="decode failed")

    monkeypatch.setattr(runtime, "run_cancellable_process", failed_owner)
    runtime._CACHE.clear()
    assert asyncio.run(runtime._is_static_video_confident(source, 15.0)) is False


def test_opening_slide_then_moving_footage_keeps_crop(monkeypatch, tmp_path: Path) -> None:
    runtime = _load_runtime()
    source = tmp_path / "talk.mp4"
    source.write_bytes(b"not-a-real-video")
    outputs = iter([_static_probe_output(), _moving_probe_output()])

    async def fake_owner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr=next(outputs))

    monkeypatch.setattr(runtime.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(runtime, "run_cancellable_process", fake_owner)
    runtime._CACHE.clear()

    assert asyncio.run(runtime._is_static_video_confident(source, 30.0)) is False


def test_ffmpeg_uses_source_owned_shorts_visual_policy() -> None:
    ffmpeg_source = (ROOT / "services" / "ffmpeg.py").read_text(encoding="utf-8")
    policy_source = RUNTIME_PATH.read_text(encoding="utf-8")
    assert "from services.shorts_static_policy import _is_static_video_confident" in ffmpeg_source
    assert "moving/uncertain→crop_zoom" in policy_source
    assert "SHORTS_STATIC_SECOND_PROBE_OFFSET" in policy_source
    assert "await run_cancellable_process(" in policy_source
    assert "run_in_executor" not in policy_source
    assert "subprocess.run(" not in policy_source
