from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

import services.translation_editorial as editorial
from services.translation_editorial import build_review_pack, load_pack_manifest, sha256_file


def _srt_payload(text: str) -> bytes:
    return f"1\n00:00:01,000 --> 00:00:03,000\n{text}\n\n".encode()


def test_pr113_legacy_v1_pack_without_timeline_still_verifies(tmp_path: Path) -> None:
    source_video = tmp_path / "legacy-source.mp4"
    source_video.write_bytes(b"legacy-source-bytes")
    original = _srt_payload("Faith apart from works.")
    russian = _srt_payload("Вера отдельно от дел.")
    candidates = {"shorts": [], "long_clips": []}
    source = {
        "url": "https://example.invalid/legacy",
        "media_id": "legacy",
        "title": "Legacy",
        "performer": "Speaker",
        "duration_seconds": 10.0,
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
    manifest = {
        **identity,
        "review_pack_id": editorial._canonical_sha256(identity),
        "review_contract": {},
    }
    pack = tmp_path / "legacy_v1.zip"
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("candidates.json", json.dumps(candidates, ensure_ascii=False))
        archive.writestr("original.srt", original)
        archive.writestr("russian_whisper.srt", russian)

    loaded = load_pack_manifest(pack)

    assert "timeline" not in loaded
    assert loaded["review_pack_id"] == manifest["review_pack_id"]


def test_review_pack_rejects_candidate_outside_exact_source_duration(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    original = tmp_path / "original.srt"
    russian = tmp_path / "russian.srt"
    original.write_bytes(_srt_payload("Source"))
    russian.write_bytes(_srt_payload("Перевод"))

    with pytest.raises(ValueError, match="candidate span outside source duration"):
        build_review_pack(
            output_dir=tmp_path,
            media_id="bad-candidate",
            source_url="",
            title="",
            performer="",
            duration=10.0,
            source_video_path=source,
            original_srt_path=original,
            russian_whisper_srt_path=russian,
            shorts_candidates=[{"start_seconds": 9.0, "end_seconds": 12.0}],
        )


def test_review_pack_rejects_nonfinite_candidate_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    original = tmp_path / "original.srt"
    russian = tmp_path / "russian.srt"
    original.write_bytes(_srt_payload("Source"))
    russian.write_bytes(_srt_payload("Перевод"))

    with pytest.raises(ValueError, match="candidate start/end must be finite"):
        build_review_pack(
            output_dir=tmp_path,
            media_id="nan-candidate",
            source_url="",
            title="",
            performer="",
            duration=10.0,
            source_video_path=source,
            original_srt_path=original,
            russian_whisper_srt_path=russian,
            shorts_candidates=[{"start_seconds": float("nan"), "end_seconds": 5.0}],
        )
