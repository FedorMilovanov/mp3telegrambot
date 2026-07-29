from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.voxcpm2 import controlled_reference_gate

ROOT = Path(__file__).resolve().parents[1]


def _write_wav(path: Path, *, seconds: float, value: float) -> None:
    sample_rate = 16_000
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(
        path,
        np.full(int(sample_rate * seconds), value, dtype=np.float32),
        sample_rate,
        subtype="PCM_24",
    )


def _write_report(path: Path, *, profile: str, duration: float) -> None:
    path.write_text(
        json.dumps(
            {
                "profile": profile,
                "selected": [{"start": 1.0, "end": 1.0 + duration}],
                "duration_seconds": duration,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _calm_pair(root: Path) -> tuple[Path, Path, bytes, str]:
    output = root / "composite_reference.wav"
    report = output.with_suffix(".selection.json")
    _write_wav(output, seconds=8.0, value=0.02)
    report.write_text(
        json.dumps(
            {
                "policy": "professional-audio-v4.5",
                "denoise": False,
                "selected": [{"start": 2.0, "end": 10.0}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output, report, output.read_bytes(), report.read_text(encoding="utf-8")


def test_short_expressive_wav_restores_calm_pair(monkeypatch, tmp_path: Path) -> None:
    output, report, calm_wav, calm_report = _calm_pair(tmp_path)

    def build(**kwargs):
        _write_wav(kwargs["output"], seconds=3.8, value=0.15)
        _write_report(
            kwargs["output"].with_suffix(".selection.json"),
            profile="controlled_expressive",
            duration=3.8,
        )
        return True

    monkeypatch.setattr(
        controlled_reference_gate.expressive_continuity,
        "build_controlled_expressive_reference",
        build,
    )
    built, detail = controlled_reference_gate.build_or_keep_calm(
        source=tmp_path / "source.mp4",
        segments=[{"id": 1}],
        output=output,
    )
    assert built is False
    assert "fallback" in detail
    assert output.read_bytes() == calm_wav
    assert report.read_text(encoding="utf-8") == calm_report


def test_false_builder_result_restores_even_partial_write(monkeypatch, tmp_path: Path) -> None:
    output, report, calm_wav, calm_report = _calm_pair(tmp_path)

    def build(**kwargs):
        kwargs["output"].write_bytes(b"partial")
        kwargs["output"].with_suffix(".selection.json").write_text(
            "partial",
            encoding="utf-8",
        )
        return False

    monkeypatch.setattr(
        controlled_reference_gate.expressive_continuity,
        "build_controlled_expressive_reference",
        build,
    )
    built, _detail = controlled_reference_gate.build_or_keep_calm(
        source=tmp_path / "source.mp4",
        segments=[{"id": 1}],
        output=output,
    )
    assert built is False
    assert output.read_bytes() == calm_wav
    assert report.read_text(encoding="utf-8") == calm_report


def test_valid_expressive_pair_commits(monkeypatch, tmp_path: Path) -> None:
    output, report, calm_wav, _calm_report = _calm_pair(tmp_path)

    def build(**kwargs):
        _write_wav(kwargs["output"], seconds=6.0, value=0.15)
        _write_report(
            kwargs["output"].with_suffix(".selection.json"),
            profile="controlled_expressive",
            duration=6.0,
        )
        return True

    monkeypatch.setattr(
        controlled_reference_gate.expressive_continuity,
        "build_controlled_expressive_reference",
        build,
    )
    built, detail = controlled_reference_gate.build_or_keep_calm(
        source=tmp_path / "source.mp4",
        segments=[{"id": 1}],
        output=output,
    )
    assert built is True
    assert "controlled expressive" in detail
    assert output.read_bytes() != calm_wav
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["profile"] == "controlled_expressive"


def test_builder_exception_restores_then_reraises(monkeypatch, tmp_path: Path) -> None:
    output, report, calm_wav, calm_report = _calm_pair(tmp_path)

    def build(**kwargs):
        kwargs["output"].write_bytes(b"partial")
        raise RuntimeError("builder failed")

    monkeypatch.setattr(
        controlled_reference_gate.expressive_continuity,
        "build_controlled_expressive_reference",
        build,
    )
    with pytest.raises(RuntimeError, match="builder failed"):
        controlled_reference_gate.build_or_keep_calm(
            source=tmp_path / "source.mp4",
            segments=[{"id": 1}],
            output=output,
        )
    assert output.read_bytes() == calm_wav
    assert report.read_text(encoding="utf-8") == calm_report


def test_every_production_route_uses_transactional_gate() -> None:
    for name in (
        "generic_clean_gemini_runtime.py",
        "generic_clean_direct_runtime.py",
        "generic_clean_custom_runtime.py",
        "generic_clean_audio_repair_runtime.py",
    ):
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "controlled_reference_gate.build_or_keep_calm" in source
        assert "expressive_continuity.build_controlled_expressive_reference(" not in source


def test_direct_renderer_remains_independent_from_reference_preparation() -> None:
    combined = "\n".join(
        (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        for name in (
            "direct_max_quality_io.py",
            "direct_max_quality_analysis.py",
            "direct_max_quality_render.py",
            "direct_max_quality_cli.py",
        )
    )
    assert "controlled_reference_gate" not in combined
    assert "expressive_continuity" not in combined
