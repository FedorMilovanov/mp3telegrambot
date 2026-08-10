from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.translation_editorial_composition as composition
import services.translation_editorial_repair_provenance as repair_provenance
from services.translation_editorial import sha256_file
from services.translation_editorial_composition import (
    build_composition_template,
    refresh_composition_id,
    validate_composition_document,
)
from services.translation_editorial_repair_provenance import (
    REPAIR_PROVENANCE_SCHEMA_NAME,
    write_repair_provenance,
)


def _bound_plan(tmp_path: Path) -> tuple[dict, Path]:
    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"clean-media" * 300)
    review = tmp_path / "review.json"
    review.write_text("{}", encoding="utf-8")
    provenance = {
        "schema_name": REPAIR_PROVENANCE_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": "sha256:" + "a" * 64,
        "review_sha256": sha256_file(review),
        "source": {
            "local_path": str(tmp_path / "translated.mp4"),
            "sha256": "sha256:" + "b" * 64,
            "bytes": 5000,
            "duration_seconds": 100.0,
        },
        "repairs": [{"type": "drop_span", "start_seconds": 20.0, "end_seconds": 23.0}],
        "drop_spans": [[20.0, 23.0]],
        "timeline": {
            "input": "review/translated-video timeline",
            "output": "cleaned-master timeline",
            "mapping": "output_t = input_t - removed_drop_duration_before_input_t",
            "mute_span_changes_duration": False,
        },
        "output": {
            "local_path": str(clean.resolve(strict=False)),
            "sha256": sha256_file(clean),
            "bytes": clean.stat().st_size,
            "duration_seconds": 97.0,
        },
    }
    provenance["repair_result_id"] = repair_provenance._canonical_sha256(provenance)
    sidecar = tmp_path / "clean.editorial-repair.json"
    write_repair_provenance(sidecar, provenance)

    plan = build_composition_template(
        source_video_path=clean,
        source_duration=97.0,
        source_review_pack_id=provenance["review_pack_id"],
        source_review_sha256=provenance["review_sha256"],
    )
    plan["source"]["repair_provenance"] = {
        "local_path": str(sidecar.resolve(strict=False)),
        "sha256": sha256_file(sidecar),
        "repair_result_id": provenance["repair_result_id"],
    }
    plan["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 5.0, "end_seconds": 35.0}],
        }
    ]
    return refresh_composition_id(plan), sidecar


def test_composition_validates_repair_binding_shape(tmp_path: Path) -> None:
    plan, _sidecar = _bound_plan(tmp_path)
    assert validate_composition_document(plan) == []

    plan["source"]["repair_provenance"]["sha256"] = "sha256:not-a-digest"
    plan = refresh_composition_id(plan)
    assert any(
        "repair_provenance.sha256" in item
        for item in validate_composition_document(plan)
    )


@pytest.mark.asyncio
async def test_service_rehashes_repair_sidecar_before_accepting_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, sidecar = _bound_plan(tmp_path)
    clean = Path(plan["source"]["local_path"])

    async def fake_probe(path: Path):
        assert Path(path) == clean
        return SimpleNamespace(duration=97.0)

    monkeypatch.setattr(composition, "probe_media_async", fake_probe)
    monkeypatch.setattr(composition, "media_probe_is_deliverable", lambda probe: probe is not None)
    monkeypatch.setattr(repair_provenance, "probe_media_async", fake_probe)
    monkeypatch.setattr(repair_provenance, "media_probe_is_deliverable", lambda probe: probe is not None)

    verified_path, duration = await composition._verify_source(plan)
    assert verified_path == clean
    assert duration == 97.0

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["timeline"]["mapping"] = "tampered"
    sidecar.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError)):
        await composition._verify_source(plan)
