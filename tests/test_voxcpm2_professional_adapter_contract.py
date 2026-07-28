from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def _write_reference(path: Path) -> None:
    sample_rate = 16_000
    time = np.arange(sample_rate, dtype=np.float32) / sample_rate
    reference = (0.08 * np.sin(2.0 * np.pi * 120.0 * time)).astype(np.float32)
    sf.write(path, reference, sample_rate)


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

    extended = tmp_path / "extended.wav"
    composite = tmp_path / "composite.wav"
    _write_reference(extended)
    _write_reference(composite)

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
    adapter_globals = namespace["main"].__globals__

    for hook in ("candidate_score", "fit_without_slowdown", "log", "set_seed", "main"):
        assert callable(adapter_globals.get(hook)), hook

    legacy_globals = adapter_globals["legacy_globals"]
    events = legacy_globals["events"]

    def wrapped_log(message: str) -> None:
        events.append(("wrapped_log", message))

    def wrapped_seed(seed: int, _torch_module) -> None:
        events.append(("wrapped_seed", seed))

    adapter_globals["log"] = wrapped_log
    adapter_globals["set_seed"] = wrapped_seed
    namespace["main"]()

    assert ("wrapped_log", "renderer entered") in events
    assert ("wrapped_seed", 77) in events
    assert not any(name.startswith("legacy_") for name, _value in events)


def test_quality_v4_patches_the_globals_used_by_runpy_functions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    marker = tmp_path / "hooks.json"
    original = tmp_path / "fake_renderer.py"
    original.write_text(
        """
import json
import os
from pathlib import Path


def candidate_score(candidate, speech_slot):
    return 0.0


def fit_without_slowdown(clean_path, fitted_path, target_duration, tail_guard):
    return {}


def log(message):
    pass


def set_seed(seed, torch_module):
    pass


def main():
    Path(os.environ["HOOK_MARKER"]).write_text(
        json.dumps({
            "candidate_score": candidate_score.__name__,
            "fit_without_slowdown": fit_without_slowdown.__name__,
            "log": log.__name__,
            "set_seed": set_seed.__name__,
        }),
        encoding="utf-8",
    )
    log("=== VOXCPM2 FINAL PRODUCTION CPU RENDER ===")
    set_seed(77, None)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("VOXCPM_ORIGINAL_RENDERER", str(original))
    monkeypatch.setenv("HOOK_MARKER", str(marker))

    renderer_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "voxcpm2"
        / "voxcpm2_quality_v4_renderer.py"
    )
    namespace = runpy.run_path(str(renderer_path), run_name="quality_v4_contract_test")
    namespace["main"]()

    hooks = json.loads(marker.read_text(encoding="utf-8"))
    assert hooks == {
        "candidate_score": "quality_score",
        "fit_without_slowdown": "quality_fit",
        "log": "progress_log",
        "set_seed": "progress_set_seed",
    }
