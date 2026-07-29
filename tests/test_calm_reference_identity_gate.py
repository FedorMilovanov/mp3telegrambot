from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from tools.voxcpm2 import controlled_reference_gate as gate


def _wav(path: Path, *, frequency: float = 120.0, seconds: float = 6.0) -> None:
    sample_rate = 16_000
    time = np.arange(int(sample_rate * seconds), dtype=np.float32) / sample_rate
    audio = (0.12 * np.sin(2.0 * np.pi * frequency * time)).astype(np.float32)
    sf.write(path, audio, sample_rate, subtype="PCM_24")


def _calm_report(path: Path) -> None:
    path.with_suffix(".selection.json").write_text(
        json.dumps(
            {
                "reference_mode": "single-continuous-window",
                "selected": [{"start": 0.0, "end": 6.0}],
            }
        ),
        encoding="utf-8",
    )


def test_mismatched_calm_composite_is_rejected_before_expression(
    monkeypatch,
    tmp_path: Path,
) -> None:
    identity = tmp_path / "extended_reference.wav"
    composite = tmp_path / "composite_reference.wav"
    _wav(identity, frequency=110.0)
    _wav(composite, frequency=220.0)
    _calm_report(composite)
    monkeypatch.setattr(gate, "spectral_similarity", lambda *_args: 0.10)

    called = False

    def expressive_should_not_run(**_kwargs):
        nonlocal called
        called = True
        return False

    monkeypatch.setattr(
        gate.expressive_continuity,
        "build_controlled_expressive_reference",
        expressive_should_not_run,
    )
    with pytest.raises(RuntimeError, match="Calm composite identity gate"):
        gate.build_or_keep_calm(
            source=tmp_path / "source.mp4",
            segments=[{"id": 1}],
            output=composite,
            identity_reference=identity,
        )
    assert called is False


def test_safe_calm_fallback_carries_identity_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    identity = tmp_path / "extended_reference.wav"
    composite = tmp_path / "composite_reference.wav"
    _wav(identity)
    _wav(composite)
    _calm_report(composite)
    monkeypatch.setattr(gate, "spectral_similarity", lambda *_args: 0.91)
    monkeypatch.setattr(
        gate.expressive_continuity,
        "build_controlled_expressive_reference",
        lambda **_kwargs: False,
    )

    built, detail = gate.build_or_keep_calm(
        source=tmp_path / "source.mp4",
        segments=[{"id": 1}],
        output=composite,
        identity_reference=identity,
    )
    assert built is False
    assert "identity similarity=0.9100" in detail
    payload = json.loads(
        composite.with_suffix(".selection.json").read_text(encoding="utf-8")
    )
    assert payload["identity_policy"] == gate.IDENTITY_POLICY
    assert payload["identity_spectral_similarity"] == pytest.approx(0.91)
    assert payload["identity_spectral_floor"] == gate.MIN_IDENTITY_SPECTRAL_SIMILARITY


def test_nonfinite_expressive_report_duration_is_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    identity = tmp_path / "extended_reference.wav"
    expressive = tmp_path / "composite_reference.wav"
    _wav(identity)
    _wav(expressive)
    expressive.with_suffix(".selection.json").write_text(
        json.dumps(
            {
                "profile": "controlled_expressive",
                "duration_seconds": "NaN",
                "selected": [{"start": 0.0, "end": 6.0}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "spectral_similarity", lambda *_args: 0.91)
    valid, detail = gate._valid_expressive_reference(
        expressive,
        identity_reference=identity,
    )
    assert valid is False
    assert "конечным числом" in detail
