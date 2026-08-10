from __future__ import annotations

import inspect
import json
from pathlib import Path

import services.translation_editorial as editorial
import services.translation_editorial_composition as composition
import services.translation_editorial_factory as factory
import services.translation_editorial_repair_provenance as repair_provenance
from services.translation_editorial import build_review_pack, load_pack_manifest
from services.translation_editorial_composition import (
    build_composition_template,
    refresh_composition_id,
    validate_composition_document,
)


def _srt(path: Path, text: str) -> Path:
    path.write_text(
        f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n",
        encoding="utf-8",
    )
    return path


def test_review_pack_identity_is_path_stable_for_timeline_aware_v1(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    oa = tmp_path / "oa"
    ob = tmp_path / "ob"
    for item in (a, b, oa, ob):
        item.mkdir()
    source_a = a / "translated.mp4"
    source_b = b / "translated.mp4"
    source_a.write_bytes(b"same-source" * 400)
    source_b.write_bytes(source_a.read_bytes())
    timeline = {"original_srt": "source", "russian_whisper": "translated"}
    kwargs = dict(
        media_id="video",
        source_url="https://example.invalid/video",
        title="Title",
        performer="Speaker",
        duration=10.0,
        timeline_metadata=timeline,
    )
    pack_a = build_review_pack(
        output_dir=oa,
        source_video_path=source_a,
        original_srt_path=_srt(a / "original.srt", "Source"),
        russian_whisper_srt_path=_srt(a / "russian.srt", "Перевод"),
        **kwargs,
    )
    pack_b = build_review_pack(
        output_dir=ob,
        source_video_path=source_b,
        original_srt_path=_srt(b / "original.srt", "Source"),
        russian_whisper_srt_path=_srt(b / "russian.srt", "Перевод"),
        **kwargs,
    )
    ma = load_pack_manifest(pack_a)
    mb = load_pack_manifest(pack_b)
    assert ma["review_pack_id"] == mb["review_pack_id"]
    assert ma["source"]["translated_video"]["local_path"] != mb["source"]["translated_video"]["local_path"]


def test_atomic_writers_do_not_delete_fileexists_winners() -> None:
    targets = [
        inspect.getsource(editorial._publish_new_file),
        inspect.getsource(composition._publish_new_file),
        inspect.getsource(factory._copy_without_overwrite),
        inspect.getsource(repair_provenance.write_repair_provenance),
    ]
    for source in targets:
        assert "except FileExistsError" in source
        assert "if created" in source


def test_media_composition_identity_ignores_paths_release_copy_and_target(tmp_path: Path) -> None:
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"clean" * 500)
    plan = build_composition_template(source_video_path=source, source_duration=120.0)
    plan["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
            "publication": {"title": "A"},
        }
    ]
    plan = refresh_composition_id(plan)
    original = plan["composition_id"]
    plan["source"]["local_path"] = str(tmp_path / "moved" / "clean.mp4")
    plan["source"]["title"] = "display change"
    plan["release_target"] = {
        "project_key": "project",
        "youtube_account_alias": "alias",
        "youtube_channel_id": "UC123",
    }
    plan["pieces"][0]["publication"] = {"title": "B", "description": "changed"}
    assert composition.composition_id(plan) == original
    plan["pieces"][0]["segments"][0]["end_seconds"] = 41.0
    assert composition.composition_id(plan) != original


def test_repair_provenance_contract_binds_drop_map_and_rejects_unknown_actions() -> None:
    document = {
        "schema_name": repair_provenance.REPAIR_PROVENANCE_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": "sha256:" + "a" * 64,
        "review_sha256": "sha256:" + "b" * 64,
        "source": {
            "local_path": "source.mp4",
            "sha256": "sha256:" + "c" * 64,
            "bytes": 5000,
            "duration_seconds": 100.0,
        },
        "repairs": [{"type": "borrow_span", "start_seconds": 10.0, "end_seconds": 11.0}],
        "drop_spans": [],
        "timeline": {
            "input": "review/translated-video timeline",
            "output": "cleaned-master timeline",
            "mapping": "output_t = input_t - removed_drop_duration_before_input_t",
            "mute_span_changes_duration": False,
        },
        "output": {
            "local_path": "clean.mp4",
            "sha256": "sha256:" + "d" * 64,
            "bytes": 4500,
            "duration_seconds": 100.0,
        },
    }
    document["repair_result_id"] = repair_provenance._canonical_sha256(
        repair_provenance._identity_payload(document)
    )
    errors = repair_provenance.validate_repair_provenance_document(document)
    assert any("unsupported action" in item for item in errors)


def test_audit_history_records_final_adversarial_pass() -> None:
    history = Path("docs/quality_audit_history.md").read_text(encoding="utf-8")
    assert "Translation Editorial Review/Composition adversarial re-audit" in history
    assert "provider_write_authorized=false" in history
