from __future__ import annotations

import pytest

from tools.voxcpm2 import clean_runtime_contract as contract


@pytest.mark.parametrize(
    ("request_payload", "message"),
    [
        ({"video_id": "x", "cfg": True}, "cfg не может быть bool"),
        ({"video_id": "x", "original_level": False}, "original_level не может быть bool"),
        ({"video_id": "x", "threads": True}, "threads не может быть bool"),
        ({"video_id": "x", "steps": False}, "steps не может быть bool"),
        ({"video_id": "x", "base_seed": True}, "base_seed не может быть bool"),
        ({"video_id": "x", "threads": 3.5}, "threads должен быть целым числом"),
        ({"video_id": "x", "steps": float("nan")}, "steps должен быть целым числом"),
        ({"video_id": "x", "base_seed": float("inf")}, "base_seed должен быть целым числом"),
    ],
)
def test_clean_runtime_rejects_bool_and_fractional_integer_settings(
    request_payload: dict,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        contract.normalize_settings(request_payload, duration=30.0)


def test_numeric_strings_remain_supported() -> None:
    result = contract.normalize_settings(
        {
            "video_id": "x",
            "cfg": "1.8",
            "original_level": "0",
            "threads": "10",
            "steps": "16",
            "base_seed": "0",
        },
        duration="30",
    )
    assert result["cfg"] == pytest.approx(1.8)
    assert result["original_level"] == 0.0
    assert result["threads"] == 10
    assert result["steps"] == 16
    assert result["base_seed"] == 0
