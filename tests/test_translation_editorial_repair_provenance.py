from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.translation_editorial_repair_provenance as provenance
from services.translation_editorial import sha256_file
from services.translation_editorial_repair_provenance import (
    REPAIR_PROVENANCE_SCHEMA_NAME,
    remap_timestamp_from_review_timeline,
    validate_repair_provenance_document,
    verify_repair_provenance,
    write_repair_provenance,
)


def _document(tmp_path: Path) -> tuple[Path, dict]:
    output = tmp_path / "clean.mp4"
    output.write_bytes(b"clean-output" * 300)
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    document = {
        "schema_name": REPAIR_PROVENANCE_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": "sha256:" + "a" * 64,
        "review_sha256": sha256_file(review),
        "source": {
            "local_path": str(tmp_path / "source.mp4"),
            "sha256": "sha256:" + "b" * 64,
            "bytes": 9999,
            "duration_seconds": 100.0,
        },
        "repairs": [
            {"type": "drop_span", "start_seconds": 10.0, "end_seconds": 12.0},
            {"type": "drop_span", "start_seconds": 20.0, "end_seconds": 21.0},
            {"type": "mute_span", "start_seconds": 40.0, "end_seconds": 41.0},
        ],
        "drop_spans": [[10.0, 12.0], [20.0, 21.0]],
        "timeline": {
            "input": "review/translated-video timeline",
            "output": "cleaned-master timeline",
            "mapping": "output_t = input_t - removed_drop_duration_before_input_t",
            "mute_span_changes_duration": False,
        },
        "output": {
            "local_path": str(output.resolve(strict=False)),
            "sha256": sha256_file(output),
            "bytes": output.stat().st_size,
            "duration_seconds": 97.0,
        },
    }
    document["repair_result_id"] = provenance._canonical_sha256(document)
    return output, document


def test_repair_provenance_maps_old_timestamps_after_drop_spans(tmp_path: Path) -> None:
    _output, document = _document(tmp_path)

    assert remap_timestamp_from_review_timeline(5.0, document) == 5.0
    assert remap_timestamp_from_review_timeline(15.0, document) == 13.0
    assert remap_timestamp_from_review_timeline(25.0, document) == 22.0
    assert remap_timestamp_from_review_timeline(40.0, document) == 37.0


def test_repair_provenance_is_self_binding_and_immutable(tmp_path: Path) -> None:
    _output, document = _document(tmp_path)
    sidecar = tmp_path / "clean.editorial-repair.json"

    assert validate_repair_provenance_document(document) == []
    assert write_repair_provenance(sidecar, document) == sidecar
    assert write_repair_provenance(sidecar, document) == sidecar

    changed = json.loads(json.dumps(document))
    changed["repairs"][0]["end_seconds"] = 13.0
    with pytest.raises(ValueError, match="repair_result_id"):
        write_repair_provenance(tmp_path / "bad.json", changed)


@pytest.mark.asyncio
async def test_verify_repair_provenance_rehashes_and_rejects_tampered_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, document = _document(tmp_path)
    sidecar = tmp_path / "clean.editorial-repair.json"
    write_repair_provenance(sidecar, document)

    async def fake_probe(path: Path):
        assert Path(path) == output
        return SimpleNamespace(duration=97.0)

    monkeypatch.setattr(provenance, "probe_media_async", fake_probe)
    monkeypatch.setattr(provenance, "media_probe_is_deliverable", lambda probe: probe is not None)

    verified = await verify_repair_provenance(sidecar, expected_output_path=output)
    assert verified["repair_result_id"] == document["repair_result_id"]

    output.write_bytes(b"tampered-output" * 300)
    with pytest.raises(RuntimeError, match="output size changed|output bytes changed"):
        await verify_repair_provenance(sidecar, expected_output_path=output)
