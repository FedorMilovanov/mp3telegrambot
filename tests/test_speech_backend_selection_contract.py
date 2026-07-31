from __future__ import annotations

import pytest

from tools.voxcpm2 import clean_runtime_contract


def _request(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "video_id": "z20py4yqhyq",
        "threads": 4,
        "steps": 16,
        "cfg": 1.8,
        "base_seed": 2026072800,
        "original_level": 0.18,
    }
    payload.update(updates)
    return payload


def test_default_backend_is_explicit_in_normalized_settings() -> None:
    settings = clean_runtime_contract.normalize_settings(
        _request(),
        duration=60.0,
    )
    assert settings["speech_backend"] == "voxcpm2"
    assert settings["speech_backend_policy"] == "explicit-request-speech-backend-v1"


def test_backend_alias_is_normalized_without_changing_renderer() -> None:
    settings = clean_runtime_contract.normalize_settings(
        _request(speech_backend="OpenBMB"),
        duration=60.0,
    )
    assert settings["speech_backend"] == "voxcpm2"


def test_unknown_backend_never_silently_falls_back() -> None:
    with pytest.raises(RuntimeError, match="Неизвестный speech backend"):
        clean_runtime_contract.normalize_settings(
            _request(speech_backend="future-neural-engine"),
            duration=60.0,
        )
