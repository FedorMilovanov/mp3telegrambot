from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair


def _root(tmp_path: Path, request: dict, *, segment_ids=(1, 2)) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "segments_ru_final.json").write_text(
        json.dumps(
            [
                {
                    "id": item_id,
                    "start": float(index),
                    "end": float(index + 1),
                    "start_delay_ms": 0,
                    "text": f"Реплика {item_id}",
                }
                for index, item_id in enumerate(segment_ids)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    payload = {
        "schema_version": 1,
        "project_id": "project-1",
        "repair_all": False,
        "segment_ids": [1],
        **request,
    }
    (root / "input" / "audio_repair.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def test_valid_partial_and_full_repair_scopes_are_accepted(tmp_path: Path) -> None:
    partial = _root(tmp_path / "partial", {})
    assert repair._validate_repair_request(partial, "project-1")["segment_ids"] == [1]

    full = _root(
        tmp_path / "full",
        {"repair_all": True, "segment_ids": [1, 2]},
    )
    assert repair._validate_repair_request(full, "project-1")["repair_all"] is True


@pytest.mark.parametrize(
    "override",
    [
        {"schema_version": True},
        {"project_id": "other-project"},
        {"repair_all": "false"},
        {"segment_ids": [True]},
        {"segment_ids": [1.5]},
        {"segment_ids": [1, 1]},
        {"segment_ids": [99]},
        {"repair_all": True, "segment_ids": [1]},
        {"repair_all": False, "segment_ids": [1, 2]},
    ],
)
def test_ambiguous_repair_scope_fails_closed(tmp_path: Path, override: dict) -> None:
    root = _root(tmp_path, override)
    with pytest.raises(RuntimeError):
        repair._validate_repair_request(root, "project-1")


@pytest.mark.parametrize(
    ("request", "marker"),
    [
        ({"base_seed": True}, {}),
        ({"base_seed": 1.5}, {}),
        ({"base_seed": 1}, {"base_seed": True}),
        ({"base_seed": 1}, {"base_seed": 2.5}),
    ],
)
def test_ambiguous_repair_seed_fields_fail_closed(request: dict, marker: dict) -> None:
    with pytest.raises(RuntimeError):
        repair._next_seed(request, marker, {})


def test_invalid_request_stops_before_legacy_main(monkeypatch, tmp_path: Path) -> None:
    root = _root(tmp_path, {"repair_all": "false"})
    calls = 0

    monkeypatch.setattr(repair._legacy.production, "current_project_id", lambda: "project-1")
    monkeypatch.setattr(repair._legacy.production, "project_root", lambda _project_id: root)

    def forbidden_main() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(repair._legacy, "main", forbidden_main)
    with pytest.raises(RuntimeError, match="repair_all должен быть bool"):
        repair.main()
    assert calls == 0


def test_repair_facade_patches_legacy_seed_and_manifest_hooks() -> None:
    assert repair._legacy._next_seed is repair._next_seed
    assert repair._legacy._update_manifest is repair._update_manifest
