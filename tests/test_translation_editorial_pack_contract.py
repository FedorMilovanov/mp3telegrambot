from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import services.translation_editorial as editorial
from services.translation_editorial import build_review_pack, sha256_file
from services.translation_editorial_pack_contract import load_verified_review_pack


def _current_pack(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    original = tmp_path / "original.srt"
    russian = tmp_path / "russian.srt"
    original.write_text("1\n00:00:00,000 --> 00:00:01,000\nSource\n", encoding="utf-8")
    russian.write_text("1\n00:00:00,000 --> 00:00:01,000\nПеревод\n", encoding="utf-8")
    return build_review_pack(
        output_dir=tmp_path,
        media_id="video",
        source_url="",
        title="",
        performer="",
        duration=5.0,
        source_video_path=source,
        original_srt_path=original,
        russian_whisper_srt_path=russian,
        timeline_metadata={"original_srt": "source", "russian_whisper": "translated"},
    )


def _rewrite_zip(source: Path, target: Path, *, mutate, extra: tuple[str, bytes] | None = None) -> None:
    with zipfile.ZipFile(source, "r") as input_zip, zipfile.ZipFile(target, "w") as output_zip:
        for info in input_zip.infolist():
            output_zip.writestr(info, mutate(info.filename, input_zip.read(info.filename)))
        if extra is not None:
            output_zip.writestr(extra[0], extra[1])


def test_current_pack_contract_and_instructions_verify(tmp_path: Path) -> None:
    pack = _current_pack(tmp_path)

    manifest = load_verified_review_pack(pack)

    assert "timeline" in manifest
    assert manifest["review_pack_id"].startswith("sha256:")


def test_pack_contract_rejects_modified_editor_instructions(tmp_path: Path) -> None:
    pack = _current_pack(tmp_path)
    tampered = tmp_path / "tampered-instructions.zip"
    _rewrite_zip(
        pack,
        tampered,
        mutate=lambda name, payload: (
            payload + b"\nCHANGED\n" if name == "REVIEW_INSTRUCTIONS.md" else payload
        ),
    )

    with pytest.raises(ValueError, match="REVIEW_INSTRUCTIONS"):
        load_verified_review_pack(tampered)


def test_pack_contract_rejects_unexpected_zip_member(tmp_path: Path) -> None:
    pack = _current_pack(tmp_path)
    tampered = tmp_path / "extra-member.zip"
    _rewrite_zip(pack, tampered, mutate=lambda _name, payload: payload, extra=("extra.txt", b"x"))

    with pytest.raises(ValueError, match="non-canonical"):
        load_verified_review_pack(tampered)


def test_legacy_pr113_pack_contract_remains_supported(tmp_path: Path) -> None:
    source_video = tmp_path / "legacy-source.mp4"
    source_video.write_bytes(b"legacy-source")
    original = b"1\n00:00:00,000 --> 00:00:01,000\nSource\n"
    russian = b"1\n00:00:00,000 --> 00:00:01,000\nRussian\n"
    candidates = {"shorts": [], "long_clips": []}
    source = {
        "url": "",
        "media_id": "legacy",
        "title": "",
        "performer": "",
        "duration_seconds": 5.0,
        "translated_video": {
            "local_path": str(source_video.resolve(strict=False)),
            "sha256": sha256_file(source_video),
            "bytes": source_video.stat().st_size,
        },
    }
    transcripts = {
        "original": {
            "file": "original.srt",
            "role": "source_original_srt",
            "sha256": editorial._sha256_bytes(original),
            "bytes": len(original),
        },
        "russian_whisper": {
            "file": "russian_whisper.srt",
            "role": "heard_russian_asr",
            "sha256": editorial._sha256_bytes(russian),
            "bytes": len(russian),
        },
    }
    identity = {
        "schema_name": editorial.PACK_SCHEMA_NAME,
        "schema_version": 1,
        "source": source,
        "transcripts": transcripts,
        "candidates": candidates,
    }
    contract = {
        "verdicts": sorted(editorial.VERDICTS),
        "issue_severities": sorted(editorial.ISSUE_SEVERITIES),
        "action_types": sorted(editorial.ACTION_TYPES),
        "automatically_executable_actions": sorted(editorial.EXECUTABLE_ACTIONS),
        "borrow_span_policy": (
            "review-only in v1; donor media is never synthesized or inserted automatically"
        ),
    }
    manifest = {
        **identity,
        "review_pack_id": editorial._canonical_sha256(identity),
        "review_contract": contract,
    }
    instructions = (
        "# Translation Editorial Review v1\n\n"
        "Upload this ZIP to the editor. Compare `original.srt` with the words actually "
        "heard in `russian_whisper.srt`. Minor stylistic roughness is not a defect. "
        "Return a `review.json` bound to the exact `review_pack_id` in manifest.json.\n\n"
        "Safe automatic repairs in v1: `drop_span` and `mute_span`. `borrow_span` may "
        "identify a same-voice donor candidate, but requires explicit human/editor approval "
        "and is intentionally not auto-executed.\n"
    )
    pack = tmp_path / "legacy.zip"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("candidates.json", json.dumps(candidates, ensure_ascii=False))
        archive.writestr("original.srt", original)
        archive.writestr("russian_whisper.srt", russian)
        archive.writestr("REVIEW_INSTRUCTIONS.md", instructions)

    loaded = load_verified_review_pack(pack)

    assert loaded["review_pack_id"] == manifest["review_pack_id"]
