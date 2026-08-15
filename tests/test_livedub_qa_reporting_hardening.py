from pathlib import Path

from services import livedub_qa_hardening as qa


def test_notes_are_inserted_after_report_heading() -> None:
    assert qa._insert_after_head("HEAD\nBODY", ["NOTE 1", "NOTE 2"]) == "HEAD\nNOTE 1\nNOTE 2\nBODY"


def test_annotate_availability_tracks_original_and_russian_separately(tmp_path: Path) -> None:
    original = tmp_path / "original.mp3"; original.write_bytes(b"original")
    russian = tmp_path / "dub.mp3"; russian.write_bytes(b"russian")
    data = qa.annotate_qa_availability({}, {"original_audio_path": original, "dub_audio_path": russian, "dub_video_path": russian, "existing_audio_part": None, "existing_client": None}, None)
    assert data["_qa_original_reference_available"] is True
    assert data["_qa_local_original_available"] is True
    assert data["_qa_russian_audio_available"] is True


def test_exact_uncut_original_replaces_edited_reference(monkeypatch, tmp_path: Path) -> None:
    exact = tmp_path / "original_video.mp4"; exact.write_bytes(b"video")
    monkeypatch.setattr(qa, "_exact_original_in_workdir", lambda _value: exact)
    prepared, found = qa.prepare_exact_timeline_inputs({"dub_video_path": tmp_path / "dub.mp4", "original_audio_path": tmp_path / "edited.mp3", "existing_audio_part": object(), "existing_client": object()})
    assert found == exact
    assert prepared["original_audio_path"] == exact
    assert prepared["existing_audio_part"] is None
    assert prepared["existing_client"] is None


def test_hardened_report_marks_unverified_limit_without_publishing_candidates() -> None:
    rendered = qa.decorate_hardened_report("HEAD\nBODY", {"issues": [], "_qa_verification_limit_dropped": 3})
    assert "За пределами настроенного лимита осталось замечаний: 3" in rendered
    assert "они не опубликованы" in rendered


def test_one_validation_issue_cannot_confirm_two_primary_findings() -> None:
    primary = {"issues": [{"time":"00:10","heard":"same important phrase","problem":"wrong meaning"}, {"time":"00:11","heard":"same important phrase","problem":"wrong meaning"}]}
    validation = {"issues": [{"time":"00:10","heard":"same important phrase","problem":"wrong meaning"}]}
    result = qa.confirmed_result_one_to_one(primary, validation)
    assert len(result["issues"]) == 1
