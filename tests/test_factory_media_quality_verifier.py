from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from tools import run_factory_media_benchmark as benchmark
from tools.verify_factory_media_quality import _assert_quality, _timestamp


def _passing_report() -> dict[str, Any]:
    return {
        "short": {
            "old_ssim": 0.91,
            "new_ssim": 0.95,
            "old_video_encode_stages_pre_subtitle": 2,
            "new_video_encode_stages_pre_subtitle": 1,
            "new_video_stream_copy_preserved": True,
        },
        "long": {
            "old_ssim": 0.92,
            "new_ssim": 0.97,
            "new": {
                "width": 1920,
                "height": 1080,
                "video_codec": "h264",
            },
        },
    }


def test_factory_media_evidence_timestamp_is_stable() -> None:
    assert _timestamp(0) == "00:00:00"
    assert _timestamp(2309) == "00:38:29"
    assert _timestamp(3661) == "01:01:01"


def test_factory_media_evidence_gate_requires_real_quality_gain_and_stream_copy() -> None:
    report = _passing_report()
    _assert_quality(report)

    broken = _passing_report()
    broken["short"]["new_video_stream_copy_preserved"] = False
    with pytest.raises(RuntimeError, match="bitstream"):
        _assert_quality(broken)

    broken = _passing_report()
    broken["short"]["new_ssim"] = broken["short"]["old_ssim"]
    with pytest.raises(RuntimeError, match="Short live quality"):
        _assert_quality(broken)

    broken = _passing_report()
    broken["long"]["new_ssim"] = broken["long"]["old_ssim"]
    with pytest.raises(RuntimeError, match="LONG live quality"):
        _assert_quality(broken)


def test_factory_media_evidence_gate_requires_h264_and_1080_ceiling() -> None:
    broken = _passing_report()
    broken["long"]["new"]["height"] = 1440
    with pytest.raises(RuntimeError, match="1080p"):
        _assert_quality(broken)

    broken = _passing_report()
    broken["long"]["new"]["video_codec"] = "av1"
    with pytest.raises(RuntimeError, match="H.264"):
        _assert_quality(broken)


def test_factory_language_prompt_typo_does_not_regress() -> None:
    source = Path("services/shorts_factory_source.py").read_text(encoding="utf-8")
    assert "доминирующий фактически услышанный язык речи" in source
    assert "услышаннный" not in source


def test_factory_benchmark_uses_resolved_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "source.mp4"

    def complete(command: list[str], **_kwargs: object) -> SimpleNamespace:
        output.write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(benchmark, "resolve_required_executable", lambda _name: "/resolved/ffmpeg")
    monkeypatch.setattr(benchmark.subprocess, "run", run)

    benchmark._generate_source(output)

    assert run.call_args.args[0][0] == "/resolved/ffmpeg"
    assert run.call_args.args[0][-1] == str(output)


def test_durable_factory_benchmark_is_provider_independent() -> None:
    source = Path("tools/run_factory_media_benchmark.py").read_text(encoding="utf-8")

    assert "testsrc2=size=1920x1080:rate=30:duration=8" in source
    assert "verify(" in source
    assert "https://" not in source
    assert "http://" not in source
