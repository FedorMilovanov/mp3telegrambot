from __future__ import annotations

from pathlib import Path

import pytest

from tools.voxcpm2 import strict_translation_payload as strict


ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    {"id": 1, "start": 0.0, "end": 2.0, "english": "One"},
    {"id": 2, "start": 2.0, "end": 4.0, "english": "Two"},
]


def test_full_payload_accepts_reordered_unique_ids_and_restores_source_order() -> None:
    result = strict.validate_full(
        {
            "segments": [
                {"id": "2", "russian": "  Вторая   фраза  "},
                {"id": 1, "russian": "Первая фраза"},
            ]
        },
        GROUPS,
    )
    assert result == [
        {"id": 1, "russian": "Первая фраза"},
        {"id": 2, "russian": "Вторая фраза"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"segments": [{"id": 1, "russian": "А"}, {"id": 1, "russian": "Б"}, {"id": 2, "russian": "В"}]},
        {"segments": [{"id": 1, "russian": "А"}]},
        {"segments": [{"id": 1, "russian": "А"}, {"id": 2, "russian": "Б"}, {"id": 3, "russian": "В"}]},
        {"segments": [{"id": True, "russian": "А"}, {"id": 2, "russian": "Б"}]},
        {"segments": [{"id": 1.5, "russian": "А"}, {"id": 2, "russian": "Б"}]},
        {"segments": [{"id": float("nan"), "russian": "А"}, {"id": 2, "russian": "Б"}]},
        {"segments": [{"id": 1, "russian": "А"}, "not-an-object", {"id": 2, "russian": "Б"}]},
        {"segments": [{"id": 1, "russian": "А"}, {"id": 2, "russian": "   "}]},
    ],
)
def test_full_payload_rejects_ambiguous_or_incomplete_segments(payload) -> None:
    with pytest.raises(RuntimeError):
        strict.validate_full(payload, GROUPS)


def test_subset_requires_exact_allowed_ids_without_last_write_wins() -> None:
    assert strict.validate_subset(
        {"segments": [{"id": 4, "russian": "Четыре"}, {"id": 2, "russian": "Два"}]},
        [2, 4],
    ) == [
        {"id": 2, "russian": "Два"},
        {"id": 4, "russian": "Четыре"},
    ]
    with pytest.raises(RuntimeError, match="повторяющийся ID"):
        strict.validate_subset(
            {"segments": [{"id": 2, "russian": "Один"}, {"id": 2, "russian": "Два"}]},
            [2],
        )


def test_clean_translation_routes_use_strict_validator() -> None:
    expressive = (ROOT / "tools" / "voxcpm2" / "expressive_translation.py").read_text(encoding="utf-8")
    custom = (ROOT / "tools" / "voxcpm2" / "generic_clean_custom_runtime.py").read_text(encoding="utf-8")
    assert "strict_translation_payload.validate_full(value, groups)" in expressive
    assert "strict_translation_payload.validate_subset(" in expressive
    assert "production._validate_translation_payload = strict_translation_payload.validate_full" in custom
