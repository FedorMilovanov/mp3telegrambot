from __future__ import annotations

import json
from pathlib import Path

import services.translation_editorial_composition as composition
from services.translation_editorial import sha256_file
from services.translation_editorial_composition import (
    COMPOSITION_SCHEMA_NAME,
    HANDOFF_SCHEMA_NAME,
    RESULT_SCHEMA_NAME,
    build_composition_template,
    build_release_handoff,
    composition_id,
    piece_duration,
    refresh_composition_id,
    validate_composition_document,
)


def _template(tmp_path: Path, duration: float = 600.0, *, target: bool = True) -> dict:
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"x" * 2048)
    return build_composition_template(
        source_video_path=source,
        source_duration=duration,
        title="Reviewed sermon",
        performer="Preacher",
        source_review_pack_id="sha256:" + "a" * 64,
        source_review_sha256="sha256:" + "b" * 64,
        project_key="sermon-project" if target else "",
        youtube_account_alias="channel-alias" if target else "",
        youtube_channel_id="UC123" if target else "",
    )


def _refresh(document: dict) -> dict:
    return refresh_composition_id(document)


def _rendered_result(tmp_path: Path, document: dict, piece: dict) -> dict:
    output = tmp_path / f"{piece['piece_id']}.mp4"
    output.write_bytes(b"rendered" * 400)
    sidecar = tmp_path / f"{piece['piece_id']}.provenance.json"
    provenance = {
        "schema_name": RESULT_SCHEMA_NAME,
        "schema_version": 1,
        "composition_id": document["composition_id"],
        "piece": piece,
        "source": {
            "local_path": document["source"]["local_path"],
            "sha256": document["source"]["sha256"],
            "duration_seconds": document["source"]["duration_seconds"],
        },
        "output": {
            "local_path": str(output.resolve(strict=False)),
            "sha256": sha256_file(output),
            "bytes": output.stat().st_size,
            "duration_seconds": piece_duration(piece),
        },
    }
    provenance["result_id"] = composition._canonical_sha256(provenance)
    sidecar.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "piece_id": piece["piece_id"],
        "output_path": str(output),
        "provenance_path": str(sidecar),
        "output_sha256": provenance["output"]["sha256"],
        "output_bytes": provenance["output"]["bytes"],
        "duration_seconds": provenance["output"]["duration_seconds"],
        "result_id": provenance["result_id"],
        "provenance_sha256": sha256_file(sidecar),
    }


def test_template_binds_exact_source_and_is_provider_neutral(tmp_path: Path) -> None:
    document = _template(tmp_path)

    assert document["schema_name"] == COMPOSITION_SCHEMA_NAME
    assert document["source"]["sha256"].startswith("sha256:")
    assert document["source"]["duration_seconds"] == 600.0
    assert document["release_target"] == {
        "project_key": "sermon-project",
        "youtube_account_alias": "channel-alias",
        "youtube_channel_id": "UC123",
    }
    assert document["composition_id"] == composition_id(document)


def test_template_rejects_nonfinite_duration_and_bad_review_digest(tmp_path: Path) -> None:
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"x" * 2048)
    for duration in (float("nan"), float("inf"), -1.0):
        try:
            build_composition_template(source_video_path=source, source_duration=duration)
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite/non-positive duration must fail")

    try:
        build_composition_template(
            source_video_path=source,
            source_duration=10.0,
            source_review_pack_id="sha256:" + "z" * 64,
        )
    except ValueError as exc:
        assert "source_review_pack_id" in str(exc)
    else:
        raise AssertionError("non-hex review pack digest must fail")


def test_continuous_short_and_excerpt_validate(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-faith-works",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 30.0, "end_seconds": 75.0}],
            "publication": {"title": "Вера и дела", "hashtags": ["#Вера", "#Дела"]},
        },
        {
            "piece_id": "excerpt-justification",
            "kind": "excerpt",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 100.0, "end_seconds": 340.0}],
            "publication": {"title": "Оправдание"},
        },
    ]
    document = _refresh(document)

    assert validate_composition_document(document) == []
    assert piece_duration(document["pieces"][0]) == 45.0
    assert piece_duration(document["pieces"][1]) == 240.0


def test_editorial_sequence_requires_meaningful_rationale_and_chronology(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-sequence",
            "kind": "short",
            "assembly_mode": "editorial_sequence",
            "editorial_rationale": ".",
            "segments": [
                {"start_seconds": 100.0, "end_seconds": 120.0},
                {"start_seconds": 40.0, "end_seconds": 60.0},
            ],
        }
    ]
    document = _refresh(document)

    errors = validate_composition_document(document)
    assert any("chronological and non-overlapping" in item for item in errors)
    assert any("meaningful rationale" in item for item in errors)


def test_editorial_sequence_allows_multiple_source_regions_when_explicit(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-problem-example-conclusion",
            "kind": "short",
            "assembly_mode": "editorial_sequence",
            "editorial_rationale": (
                "Три фрагмента продолжают одну и ту же мысль; порядок исходной проповеди сохранён."
            ),
            "segments": [
                {"start_seconds": 10.0, "end_seconds": 25.0},
                {"start_seconds": 100.0, "end_seconds": 120.0},
                {"start_seconds": 300.0, "end_seconds": 325.0},
            ],
        }
    ]
    document = _refresh(document)

    assert validate_composition_document(document) == []
    assert piece_duration(document["pieces"][0]) == 60.0


def test_full_output_cannot_masquerade_as_one_minute_of_hour_sermon(tmp_path: Path) -> None:
    document = _template(tmp_path, duration=3600.0)
    document["pieces"] = [
        {
            "piece_id": "full",
            "kind": "full",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 0.0, "end_seconds": 60.0}],
        }
    ]
    document = _refresh(document)
    errors = validate_composition_document(document)

    assert any("retain at least 85%" in item for item in errors)
    assert any("ends too far" in item for item in errors)


def test_full_output_allows_reviewed_small_cuts_across_sermon(tmp_path: Path) -> None:
    document = _template(tmp_path, duration=600.0)
    document["pieces"] = [
        {
            "piece_id": "full-reviewed",
            "kind": "full",
            "assembly_mode": "editorial_sequence",
            "editorial_rationale": "Удалены два коротких подтверждённых дефекта перевода.",
            "segments": [
                {"start_seconds": 0.0, "end_seconds": 200.0},
                {"start_seconds": 205.0, "end_seconds": 400.0},
                {"start_seconds": 405.0, "end_seconds": 600.0},
            ],
        }
    ]
    document = _refresh(document)

    assert validate_composition_document(document) == []


def test_nonfinite_segments_are_rejected_without_crashing(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-nan",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": float("nan"), "end_seconds": 40.0}],
        }
    ]
    document["composition_id"] = "sha256:" + "0" * 64

    errors = validate_composition_document(document)
    assert any("non-finite" in item for item in errors)
    assert any("start/end must be finite" in item for item in errors)


def test_piece_id_must_be_filesystem_safe_before_render(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "bad/name",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
        }
    ]
    document = _refresh(document)

    assert any(
        "piece_id must already be filesystem-safe" in item
        for item in validate_composition_document(document)
    )


def test_partial_release_target_is_rejected(tmp_path: Path) -> None:
    document = _template(tmp_path, target=False)
    document["release_target"]["youtube_account_alias"] = "alias-only"
    document["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
        }
    ]
    document = _refresh(document)
    errors = validate_composition_document(document)

    assert "release_target.project_key is required when target identity is supplied" in errors
    assert "release_target.youtube_channel_id is required when target identity is supplied" in errors


def test_publication_metadata_shape_is_guarded(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
            "publication": {"title": ["not", "text"], "hashtags": "#WrongShape"},
        }
    ]
    document = _refresh(document)
    errors = validate_composition_document(document)

    assert any("publication.title must be a string" in item for item in errors)
    assert any("publication.hashtags must be a list" in item for item in errors)


def test_release_copy_changes_handoff_not_media_composition_id(tmp_path: Path) -> None:
    document = _template(tmp_path)
    piece = {
        "piece_id": "short-1",
        "kind": "short",
        "assembly_mode": "continuous",
        "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
        "publication": {"title": "Первый заголовок"},
    }
    document["pieces"] = [piece]
    document = _refresh(document)
    media_id = document["composition_id"]
    result = _rendered_result(tmp_path, document, piece)
    first_handoff = build_release_handoff(document, [result])

    document["pieces"][0]["publication"] = {
        "title": "Другой заголовок",
        "description": "Изменена только публикационная карточка.",
    }

    assert composition_id(document) == media_id
    assert validate_composition_document(document) == []
    second_handoff = build_release_handoff(document, [result])
    assert second_handoff["handoff_id"] != first_handoff["handoff_id"]
    assert second_handoff["outputs"][0]["publication"]["title"] == "Другой заголовок"


def test_release_handoff_rehashes_real_output_and_sidecar(tmp_path: Path) -> None:
    document = _template(tmp_path)
    piece = {
        "piece_id": "short-1",
        "kind": "short",
        "assembly_mode": "continuous",
        "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
        "publication": {
            "title": "Вера и дела",
            "description": "Короткая редакционная версия.",
            "hashtags": ["#Вера", "#Дела"],
            "playlist": "Проповеди",
            "schedule_at": "2026-08-12T19:30:00+03:00",
        },
    }
    document["pieces"] = [piece]
    document = _refresh(document)
    result = _rendered_result(tmp_path, document, piece)

    handoff = build_release_handoff(document, [result])

    assert handoff["schema_name"] == HANDOFF_SCHEMA_NAME
    assert handoff["provider_write_authorized"] is False
    assert handoff["target_system"] == "video-channel-manager"
    assert handoff["release_target"]["youtube_channel_id"] == "UC123"
    assert handoff["outputs"][0]["source_segments"] == [
        {"start_seconds": 10.0, "end_seconds": 40.0}
    ]
    assert handoff["outputs"][0]["publication"]["title"] == "Вера и дела"
    assert handoff["outputs"][0]["media"]["bytes"] == result["output_bytes"]
    assert handoff["outputs"][0]["media"]["result_id"] == result["result_id"]
    assert handoff["handoff_id"].startswith("sha256:")


def test_handoff_rejects_tampered_rendered_bytes(tmp_path: Path) -> None:
    document = _template(tmp_path)
    piece = {
        "piece_id": "short-1",
        "kind": "short",
        "assembly_mode": "continuous",
        "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
    }
    document["pieces"] = [piece]
    document = _refresh(document)
    result = _rendered_result(tmp_path, document, piece)
    Path(result["output_path"]).write_bytes(b"tampered" * 400)

    try:
        build_release_handoff(document, [result])
    except RuntimeError as exc:
        assert "output" in str(exc)
    else:
        raise AssertionError("tampered output must fail closed")


def test_handoff_refuses_missing_or_extra_rendered_piece(tmp_path: Path) -> None:
    document = _template(tmp_path)
    piece = {
        "piece_id": "short-1",
        "kind": "short",
        "assembly_mode": "continuous",
        "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
    }
    document["pieces"] = [piece]
    document = _refresh(document)

    try:
        build_release_handoff(document, [])
    except ValueError as exc:
        assert "missing rendered result for short-1" in str(exc)
    else:
        raise AssertionError("missing rendered piece must fail closed")

    result = _rendered_result(tmp_path, document, piece)
    extra = dict(result)
    extra["piece_id"] = "unknown"
    try:
        build_release_handoff(document, [result, extra])
    except ValueError as exc:
        assert "unexpected rendered results" in str(exc)
    else:
        raise AssertionError("unexpected rendered piece must fail closed")


def test_renderer_uses_no_overwrite_ffmpeg_and_resume_is_sidecar_bound() -> None:
    source = Path(composition.__file__).read_text(encoding="utf-8")
    assert '"-n"' in source
    assert '"-y"' not in source
    assert "_load_verified_existing_result" in source
    assert "output bytes changed" in source
