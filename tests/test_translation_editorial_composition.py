from __future__ import annotations

from pathlib import Path

from services.translation_editorial_composition import (
    COMPOSITION_SCHEMA_NAME,
    HANDOFF_SCHEMA_NAME,
    build_composition_template,
    build_release_handoff,
    composition_id,
    piece_duration,
    refresh_composition_id,
    validate_composition_document,
)


def _template(tmp_path: Path, duration: float = 600.0) -> dict:
    source = tmp_path / "clean.mp4"
    source.write_bytes(b"x" * 2048)
    return build_composition_template(
        source_video_path=source,
        source_duration=duration,
        title="Reviewed sermon",
        performer="Preacher",
        source_review_pack_id="sha256:" + "a" * 64,
        source_review_sha256="sha256:" + "b" * 64,
        project_key="sermon-project",
        youtube_account_alias="channel-alias",
        youtube_channel_id="UC123",
    )


def _refresh(document: dict) -> dict:
    return refresh_composition_id(document)


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


def test_editorial_sequence_requires_rationale_and_chronology(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-sequence",
            "kind": "short",
            "assembly_mode": "editorial_sequence",
            "segments": [
                {"start_seconds": 100.0, "end_seconds": 120.0},
                {"start_seconds": 40.0, "end_seconds": 60.0},
            ],
        }
    ]
    document = _refresh(document)

    errors = validate_composition_document(document)
    assert any("chronological and non-overlapping" in item for item in errors)
    assert any("requires editorial_rationale" in item for item in errors)


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


def test_composition_id_binds_publication_copy_too(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
            "publication": {"title": "Первый заголовок"},
        }
    ]
    first = _refresh(document)
    old_id = first["composition_id"]
    first["pieces"][0]["publication"]["title"] = "Другой заголовок"

    assert composition_id(first) != old_id
    assert any("composition_id does not match" in item for item in validate_composition_document(first))


def test_release_handoff_is_explicitly_provider_inert(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
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
    ]
    document = _refresh(document)
    results = [
        {
            "piece_id": "short-1",
            "output_path": "C:/media/short-1.mp4",
            "provenance_path": "C:/media/short-1.provenance.json",
            "output_sha256": "sha256:" + "c" * 64,
            "duration_seconds": 30.0,
        }
    ]

    handoff = build_release_handoff(document, results)

    assert handoff["schema_name"] == HANDOFF_SCHEMA_NAME
    assert handoff["provider_write_authorized"] is False
    assert handoff["target_system"] == "video-channel-manager"
    assert handoff["release_target"]["youtube_channel_id"] == "UC123"
    assert handoff["outputs"][0]["source_segments"] == [
        {"start_seconds": 10.0, "end_seconds": 40.0}
    ]
    assert handoff["outputs"][0]["publication"]["title"] == "Вера и дела"
    assert handoff["handoff_id"].startswith("sha256:")


def test_handoff_refuses_missing_rendered_piece(tmp_path: Path) -> None:
    document = _template(tmp_path)
    document["pieces"] = [
        {
            "piece_id": "short-1",
            "kind": "short",
            "assembly_mode": "continuous",
            "segments": [{"start_seconds": 10.0, "end_seconds": 40.0}],
        }
    ]
    document = _refresh(document)

    try:
        build_release_handoff(document, [])
    except ValueError as exc:
        assert "missing rendered result for short-1" in str(exc)
    else:
        raise AssertionError("missing rendered piece must fail closed")
