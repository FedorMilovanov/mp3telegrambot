from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import generic_project_runtime


def _request(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "direct",
    }
    payload.update(updates)
    return payload


def test_control_plane_normalizes_backend_without_loading_synthesis_core() -> None:
    result = generic_project_runtime.validate_request_payload(_request())

    assert result["speech_backend"] == "voxcpm2"
    # The wizard/request barrier must remain independent from numpy, torch and
    # the model runtime. Heavy synthesis is loaded only by the selected engine.
    source = Path(generic_project_runtime.__file__).read_text(encoding="utf-8")
    assert "from tools.voxcpm2 import clean_production_core" not in source


def test_control_plane_resolves_backend_alias() -> None:
    result = generic_project_runtime.validate_request_payload(
        _request(speech_backend="OpenBMB")
    )

    assert result["speech_backend"] == "voxcpm2"


def test_control_plane_rejects_unknown_backend_before_queueing() -> None:
    with pytest.raises(RuntimeError, match="Некорректный speech_backend"):
        generic_project_runtime.validate_request_payload(
            _request(speech_backend="future-neural-engine")
        )
