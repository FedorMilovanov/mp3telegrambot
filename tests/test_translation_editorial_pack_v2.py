from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from services import translation_editorial_pack_v2 as pack_v2


def _fake_v1_builder(*args, **kwargs) -> Path:
    output_dir = Path(kwargs.get("output_dir") or args[0])
    output_dir.mkdir(parents=True, exist_ok=True)
    media_id = str(kwargs.get("media_id") or "media")
    original = b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"
    russian = b"1\n00:00:00,600 --> 00:00:01,600\nPrivet\n"
    candidates = {"shorts": [], "long_clips": []}
    manifest = {
        "schema_name": pack_v2.PACK_SCHEMA_NAME,
        "schema_version": 1,
        "source": {
            "url": "https://example.invalid/video",
            "translated_video": {
                "local_path": "C:/machine/specific/file.mp4",
                "sha256": "sha256:" + "a" * 64,
                "size": 1234,
            },
        },
        "transcripts": {
            "original": {
                "file": "original.srt",
                "role": "source_original_srt",
                "sha256": pack_v2._sha256_bytes(original),
            },
            "russian_whisper": {
                "file": "russian_whisper.srt",
                "role": "heard_russian_asr",
                "sha256": pack_v2._sha256_bytes(russian),
            },
        },
        "candidates": candidates,
        "timeline": {"russian_whisper": "translated_video_timeline"},
    }
    manifest["review_pack_id"] = pack_v2._canonical_sha256(
        pack_v2._v1_evidence_identity(manifest)
    )
    destination = output_dir / f"{media_id}_translation_editorial_v1.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("original.srt", original)
        archive.writestr("russian_whisper.srt", russian)
        archive.writestr("candidates.json", json.dumps(candidates, ensure_ascii=False))
        archive.writestr("REVIEW_INSTRUCTIONS.md", "legacy instructions")
    return destination


def _build(monkeypatch, output_dir: Path) -> Path:
    monkeypatch.setattr(pack_v2, "_build_review_pack_v1", _fake_v1_builder)
    return pack_v2.build_review_pack(
        output_dir=output_dir,
        media_id="abc",
        source_url="https://example.invalid/video",
        title="Title",
        performer="Author",
        duration=120.0,
        source_video_path=output_dir / "source.mp4",
        original_srt_path=output_dir / "original.srt",
        russian_whisper_srt_path=output_dir / "russian.srt",
        russian_words_path=None,
        shorts_candidates=[],
        long_candidates=[],
        timeline_metadata={"russian_whisper": "translated_video_timeline"},
    )


def test_v2_identity_binds_review_contract_and_exact_instructions(monkeypatch, tmp_path):
    first = _build(monkeypatch, tmp_path / "first")
    first_manifest = pack_v2.load_verified_review_pack(first)
    assert first_manifest["schema_version"] == 2
    assert first_manifest["evidence_pack_id_v1"].startswith("sha256:")
    assert first_manifest["review_instructions_sha256"].startswith("sha256:")
    assert first_manifest["review_contract"] == pack_v2.review_contract()
    assert "_translation_editorial_v2_" in first.name

    original = pack_v2.review_instructions
    monkeypatch.setattr(
        pack_v2,
        "review_instructions",
        lambda *, has_timeline: original(has_timeline=has_timeline) + "\ncontract revision\n",
    )
    second = _build(monkeypatch, tmp_path / "second")
    second_manifest = pack_v2.load_verified_review_pack(second)
    assert second_manifest["review_pack_id"] != first_manifest["review_pack_id"]
    assert second_manifest["review_instructions_sha256"] != first_manifest["review_instructions_sha256"]


def test_v2_builder_leaves_no_legacy_pack_in_output(monkeypatch, tmp_path):
    output_dir = tmp_path / "packs"
    pack = _build(monkeypatch, output_dir)
    assert pack.exists()
    assert list(output_dir.glob("*_translation_editorial_v1*.zip")) == []
    assert list(output_dir.glob("*_translation_editorial_v2_*.zip")) == [pack]


def test_v2_verifier_rejects_modified_instructions(monkeypatch, tmp_path):
    pack = _build(monkeypatch, tmp_path / "packs")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(pack, "r") as source, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_DEFLATED
    ) as destination:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "REVIEW_INSTRUCTIONS.md":
                payload += b"\nmodified\n"
            destination.writestr(info.filename, payload)

    with pytest.raises(ValueError, match="REVIEW_INSTRUCTIONS"):
        pack_v2.load_verified_review_pack(tampered)
