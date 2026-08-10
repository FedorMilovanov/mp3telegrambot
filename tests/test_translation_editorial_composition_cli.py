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
