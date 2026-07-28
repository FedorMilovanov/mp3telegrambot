from __future__ import annotations

import runpy
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def test_professional_adapter_exports_and_forwards_v4_hooks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy_path = tmp_path / "legacy_renderer.py"
    legacy_path.write_text(
        """
events = []


def candidate_score(candidate, speech_slot):
    return 0.0


def fit_without_slowdown(clean_path, fitted_path, target_duration, tail_guard):
    return {}


def log(message):
    events.append(("legacy_log", message))


def set_seed(seed, torch_module):
    events.append(("legacy_seed", seed))


def main():
    log("renderer entered")
    set_seed(77, None)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    sample_rate = 16_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    reference = (0.08 * np.sin(2.0 * np.pi * 120.0 * time)).astype(np.float32)
    extended = tmp_path / "extended.wav"
    composite = tmp_path / "composite.wav"
    sf.write(extended, reference, sample_rate)
    sf.write(composite, reference, sample_rate)

    monkeypatch.setenv("VOXCPM_LEGACY_RENDERER", str(legacy_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "adapter",
            "--extended-reference",
            str(extended),
            "--composite-reference",
            str(composite),
        ],
    )

    adapter_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "voxcpm2"
        / "voxcpm2_professional_adapter_v45.py"
    )
    namespace = runpy.run_path(str(adapter_path), run_name="adapter_contract_test")

    for hook in ("candidate_score", "fit_without_slowdown", "log", "set_seed", "main"):
        assert callable(namespace.get(hook)), hook

    events = namespace["legacy"]["events"]

    def wrapped_log(message: str) -> None:
        events.append(("wrapped_log", message))

    def wrapped_seed(seed: int, _torch_module) -> None:
        events.append(("wrapped_seed", seed))

    namespace["log"] = wrapped_log
    namespace["set_seed"] = wrapped_seed
    namespace["main"]()

    assert ("wrapped_log", "renderer entered") in events
    assert ("wrapped_seed", 77) in events
    assert not any(name.startswith("legacy_") for name, _value in events)
