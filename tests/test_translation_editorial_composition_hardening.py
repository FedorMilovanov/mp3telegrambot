from __future__ import annotations

import inspect
from pathlib import Path

import services.translation_editorial_composition as composition
from services.translation_editorial_composition import (
    build_composition_template,
    refresh_composition_id,
    validate_composition_document,
)


def _plan(tmp_path: Path) -> dict:
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"x" * 2048)
    plan = build_composition_template(
        source_video_path=source,
        source_duration=120.0,
        project_key="project",
        youtube_channel_id="UC123",
    )
    plan["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
            "publication": {"title": "Title"},
        }
    ]
    return refresh_composition_id(plan)


def test_publication_cannot_smuggle_provider_authority(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan["pieces"][0]["publication"]["provider_write_authorized"] = True
    plan = refresh_composition_id(plan)

    errors = validate_composition_document(plan)

    assert any("unsupported publication fields: provider_write_authorized" in item for item in errors)


def test_release_target_cannot_smuggle_secondary_execution_gate(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    plan["release_target"]["provider_write_authorized"] = True
    plan = refresh_composition_id(plan)

    errors = validate_composition_document(plan)

    assert "unsupported release_target fields: provider_write_authorized" in errors


def test_renderer_temp_sidecars_are_unique_and_copy_fallback_cleans_partial() -> None:
    source = inspect.getsource(composition)

    assert 'suffix=".provenance.tmp"' in source
    assert "final_path.unlink(missing_ok=True)" in inspect.getsource(composition._publish_new_file)
    assert "await asyncio.to_thread(sha256_file, path)" in source
    assert "await asyncio.to_thread(sha256_file, temp_output)" in source
