from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.translation_editorial import REVIEW_SCHEMA_NAME, build_review_pack, load_pack_manifest, sha256_file
from tools import translation_editorial_composition as cli


def _pack_and_review(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    original = tmp_path / "original.srt"
    russian = tmp_path / "russian.srt"
    original.write_text("1\n00:00:00,000 --> 00:00:01,000\nSource\n", encoding="utf-8")
    russian.write_text("1\n00:00:00,000 --> 00:00:01,000\nПеревод\n", encoding="utf-8")
    pack = build_review_pack(
        output_dir=tmp_path,
        media_id="video",
        source_url="",
        title="",
        performer="",
        duration=5.0,
        source_video_path=source,
        original_srt_path=original,
        russian_whisper_srt_path=russian,
    )
    manifest = load_pack_manifest(pack)
    review = {
        "schema_name": REVIEW_SCHEMA_NAME,
        "schema_version": 1,
        "review_pack_id": manifest["review_pack_id"],
        "full_sermon": {"verdict": "keep", "issues": []},
        "candidate_reviews": [],
    }
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return pack, review_path, review


def test_review_binding_revalidates_exact_pack_and_infers_id(tmp_path: Path) -> None:
    pack, review_path, review = _pack_and_review(tmp_path)
    args = SimpleNamespace(
        review=str(review_path),
        review_pack=str(pack),
        review_pack_id="",
    )

    pack_id, review_sha = cli._review_binding(args)

    assert pack_id == review["review_pack_id"]
    assert review_sha == sha256_file(review_path)


def test_review_binding_rejects_manual_id_mismatch(tmp_path: Path) -> None:
    pack, review_path, _review = _pack_and_review(tmp_path)
    args = SimpleNamespace(
        review=str(review_path),
        review_pack=str(pack),
        review_pack_id="sha256:" + "f" * 64,
    )

    with pytest.raises(ValueError, match="does not match review.json"):
        cli._review_binding(args)


def test_review_pack_without_review_is_rejected() -> None:
    args = SimpleNamespace(
        review=None,
        review_pack="pack.zip",
        review_pack_id="",
    )

    with pytest.raises(ValueError, match="requires --review"):
        cli._review_binding(args)


def test_review_execution_gate_blocks_rejected_full_sermon() -> None:
    review = {
        "full_sermon": {"verdict": "reject", "issues": []},
    }

    with pytest.raises(ValueError, match="rejected by review"):
        cli._enforce_review_execution_gate(review, None)


def test_review_execution_gate_requires_exact_full_sermon_repairs() -> None:
    review = {
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 10.0,
                    "end_seconds": 11.0,
                    "action": {"type": "drop_span"},
                }
            ],
        }
    }

    with pytest.raises(ValueError, match="supply the exact --repair-provenance"):
        cli._enforce_review_execution_gate(review, None)

    wrong = {
        "repairs": [
            {"type": "drop_span", "start_seconds": 20.0, "end_seconds": 21.0}
        ]
    }
    with pytest.raises(ValueError, match="do not exactly match"):
        cli._enforce_review_execution_gate(review, wrong)

    exact = {
        "repairs": [
            {"type": "drop_span", "start_seconds": 10.0, "end_seconds": 11.0}
        ]
    }
    cli._enforce_review_execution_gate(review, exact)


def test_review_execution_gate_blocks_unresolved_full_sermon_action() -> None:
    review = {
        "full_sermon": {
            "verdict": "repair",
            "issues": [
                {
                    "start_seconds": 10.0,
                    "end_seconds": 11.0,
                    "action": {"type": "reject_region"},
                }
            ],
        }
    }

    with pytest.raises(ValueError, match="unresolved non-executable"):
        cli._enforce_review_execution_gate(review, None)


def test_atomic_writer_never_deletes_concurrent_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "plan.json"

    def concurrent_winner(_source, destination) -> None:
        Path(destination).write_text("winner", encoding="utf-8")
        raise FileExistsError(destination)

    monkeypatch.setattr(cli.os, "link", concurrent_winner)

    with pytest.raises(FileExistsError):
        cli._write_atomic(target, {"value": 1}, overwrite=False)
    assert target.read_text(encoding="utf-8") == "winner"


def test_handoff_path_is_content_addressed_and_revision_safe(tmp_path: Path) -> None:
    first = {"handoff_id": "sha256:" + "a" * 64, "revision": 1}
    second = {"handoff_id": "sha256:" + "b" * 64, "revision": 2}

    first_path = cli._handoff_path(tmp_path, first)
    second_path = cli._handoff_path(tmp_path, second)

    assert first_path.name == "editorial-release-handoff_" + "a" * 64 + ".json"
    assert second_path.name == "editorial-release-handoff_" + "b" * 64 + ".json"
    assert first_path != second_path
    cli._write_handoff(first_path, first)
    cli._write_handoff(second_path, second)
    assert json.loads(first_path.read_text(encoding="utf-8")) == first
    assert json.loads(second_path.read_text(encoding="utf-8")) == second


def test_handoff_path_rejects_noncanonical_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical sha256"):
        cli._handoff_path(tmp_path, {"handoff_id": "sha256:ABC"})
