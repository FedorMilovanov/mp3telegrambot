"""Selected Manrope01 operator profile and CPU-only Large-v3 regression."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import shorts_video_impl
from services.shorts_transcription import factory_subtitle_profile


ROOT = Path(__file__).resolve().parents[1]


def test_manrope01_runbook_is_routed_to_agents_and_preserves_selected_style() -> None:
    contract = (ROOT / "docs/agent_direct_dubbing/SHORTS_MANROPE01_LARGEV3_CPU.md").read_text(encoding="utf8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf8")

    assert "SHORTS_MANROPE01_LARGEV3_CPU.md" in agents
    for required in (
        "Manrope 01",
        "ExtraBold 800",
        "#FFE17D",
        "#FFFAF1",
        "#F26F7E",
        "#101821",
        "540",
        "1426",
        "2",
        "вопрос",
        "16:9",
        "large-v3",
        'device="cpu"',
        'compute_type="int8"',
        "word_timestamps=True",
    ):
        assert required in contract, required


def test_existing_factory_cpu_example_is_consistent() -> None:
    example = (ROOT / "config/shorts_factory.env.example").read_text(encoding="utf8")
    assert "SHORTS_FACTORY_WHISPER_MODEL=large-v3" in example
    assert "WHISPER_FORCE_CPU=1" in example
    assert "WHISPER_DEVICE=cpu" in example


def test_factory_rejects_silent_small_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTS_FACTORY_WHISPER_MODEL", "small")
    with pytest.raises(RuntimeError, match="large-v3"):
        factory_subtitle_profile()


def test_force_cpu_overrides_stale_cuda_environment_without_gpu_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeWhisperModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            self.model = model
            self.device = device
            self.compute_type = compute_type

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))
    monkeypatch.setenv("WHISPER_FORCE_CPU", "1")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("WHISPER_GPU_SAFE_MODE", "0")
    monkeypatch.setattr(shorts_video_impl, "_whisper_model", None)
    monkeypatch.setattr(shorts_video_impl, "_whisper_model_name", None)

    instance = shorts_video_impl._get_whisper_model("large-v3")
    assert instance.model == "large-v3"
    assert instance.device == "cpu"
    assert instance.compute_type == "int8"


def test_passive_hold_policy_requires_no_early_word_advance() -> None:
    policy = (ROOT / "docs/agent_direct_dubbing/SHORTS_MANROPE01_LARGEV3_CPU.md").read_text(encoding="utf8")
    for required in (
        "BodyHold",
        "Body + BodyHold",
        "3,00",
        "Manrope Q54",
        "янтарного",
        "fade",
        "SUBTITLE_TIMING_HOLD",
        "FFprobe",
        "низкой вероятностью",
        "не вставлять",
    ):
        assert required in policy, required
