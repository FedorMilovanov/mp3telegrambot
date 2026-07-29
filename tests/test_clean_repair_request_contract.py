from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair


def _root(tmp_path: Path, request: dict, *, segment_ids=(1, 2)) -> Path:
    root = tmp_path / "project"
    (root / "input").mkdir(parents=True)
    (root / "source").mkdir(parents=True)
    (root / "source" / "source.mp4").write_bytes(b"source-placeholder")
    segments_path = root / "segments_ru_final.json"
    segments_path.write_text(
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
        "segments_sha256": repair._legacy.legacy_repair._sha256(segments_path),
        **request,
    }
    (root / "input" / "audio_repair.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def _refresh_request_hash(root: Path) -> None:
    repair_path = root / "input" / "audio_repair.json"
    payload = json.loads(repair_path.read_text(encoding="utf-8"))
    payload["segments_sha256"] = repair._legacy.legacy_repair._sha256(
        root / "segments_ru_final.json"
    )
    repair_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_valid_partial_and_full_repair_scopes_are_accepted(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(repair._legacy.pipeline, "ffprobe_duration", lambda _path: 3.0)
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
        {"segments_sha256": "bad"},
    ],
)
def test_ambiguous_repair_scope_fails_closed(tmp_path: Path, override: dict) -> None:
    root = _root(tmp_path, override)
    with pytest.raises(RuntimeError):
        repair._validate_repair_request(root, "project-1")


@pytest.mark.parametrize(
    ("request", "marker", "manifest"),
    [
        ({"base_seed": True}, {}, {}),
        ({"base_seed": 1.5}, {}, {}),
        ({"base_seed": 1}, {"base_seed": True}, {}),
        ({"base_seed": 1}, {"base_seed": 2.5}, {}),
        ({"base_seed": 1}, {}, {"audio_repairs": {"bad": True}}),
    ],
)
def test_ambiguous_repair_seed_fields_fail_closed(
    request: dict,
    marker: dict,
    manifest: dict,
) -> None:
    with pytest.raises(RuntimeError):
        repair._next_seed(request, marker, manifest)


def test_stale_segment_hash_stops_before_migration(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        {"repair_all": True, "segment_ids": [1, 2]},
    )
    segments_path = root / "segments_ru_final.json"
    before_request_hash = json.loads(
        (root / "input" / "audio_repair.json").read_text(encoding="utf-8")
    )["segments_sha256"]
    segments_path.write_text(
        segments_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    assert repair._legacy.legacy_repair._sha256(segments_path) != before_request_hash
    with pytest.raises(RuntimeError, match="изменился после создания repair request"):
        repair._validate_repair_request(root, "project-1")
    assert not (root / "segments_ru_final.pre_v45.json").exists()


def test_selective_repair_rejects_bad_timing_before_planner(tmp_path: Path) -> None:
    root = _root(tmp_path, {})
    segments_path = root / "segments_ru_final.json"
    payload = json.loads(segments_path.read_text(encoding="utf-8"))
    payload[0]["start"] = float("nan")
    segments_path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=True),
        encoding="utf-8",
    )
    _refresh_request_hash(root)
    with pytest.raises(RuntimeError, match="конечным числом"):
        repair._validate_repair_request(root, "project-1")


def test_ambiguous_checkpoint_report_id_fails_closed(tmp_path: Path) -> None:
    work = tmp_path / "work"
    checkpoints = work / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "segment_01.json").write_text(
        json.dumps({"report": {"id": True}}),
        encoding="utf-8",
    )
    ready, detail = repair._checkpoint_ready(work, 1)
    assert ready is False
    assert "bool" in detail


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


def test_repair_facade_patches_all_legacy_validation_hooks() -> None:
    assert repair._legacy._next_seed is repair._next_seed
    assert repair._legacy._checkpoint_ready is repair._checkpoint_ready
    assert repair._legacy._update_manifest is repair._update_manifest
    assert repair._legacy.legacy_repair._load_segments is repair._load_segments
