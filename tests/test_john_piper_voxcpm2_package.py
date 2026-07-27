from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
)
PARTS = tuple(EXAMPLE / f"package.part{index:02d}.b64" for index in range(1, 8))
EXPECTED_ENCODED_LENGTH = 26336
EXPECTED_PART_LENGTHS = (4096, 4096, 4096, 4096, 4096, 4096, 1760)
EXPECTED_SHA256 = "e5247345d451cd00805d9157cf279a19aad6ee2ca9935a0167ee7b38fda9294f"
EXPECTED_FILES = {
    "README_RU.md",
    "Run-John-Piper-FINAL-CPU-Inner.ps1",
    "master_constant_mix.py",
    "segments_ru_final.json",
    "source_subtitles_en.srt",
    "subtitles_ru_final.srt",
    "translation_ru.txt",
    "voxcpm2_cpu_shorts_production.py",
}


def _encoded_package() -> str:
    chunks = tuple(path.read_text(encoding="ascii").strip() for path in PARTS)
    assert tuple(map(len, chunks)) == EXPECTED_PART_LENGTHS
    encoded = "".join(chunks)
    assert len(encoded) == EXPECTED_ENCODED_LENGTH
    return encoded


def _decoded_package() -> bytes:
    return base64.b64decode(_encoded_package(), validate=True)


def test_embedded_package_hash_and_members() -> None:
    package = _decoded_package()
    assert hashlib.sha256(package).hexdigest() == EXPECTED_SHA256

    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert set(archive.namelist()) == EXPECTED_FILES
        assert archive.testzip() is None


def test_embedded_python_modules_compile() -> None:
    package = _decoded_package()
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        for name in (
            "voxcpm2_cpu_shorts_production.py",
            "master_constant_mix.py",
        ):
            source = archive.read(name).decode("utf-8")
            compile(source, name, "exec")


def test_john_piper_segments_are_complete_and_non_overlapping() -> None:
    segments = json.loads(
        (EXAMPLE / "segments_ru_final.json").read_text(encoding="utf-8")
    )
    assert len(segments) == 5
    assert segments[0]["start"] == 0.24
    assert segments[-1]["end"] == 64.32
    assert [item["start_delay_ms"] for item in segments] == [220, 160, 100, 70, 40]

    previous_end = 0.0
    for item in segments:
        assert item["start"] >= previous_end
        assert item["end"] > item["start"]
        assert item["text"].strip()
        previous_end = item["end"]

    combined = " ".join(item["text"] for item in segments)
    assert "самым радикальным человеком в мире" in combined
    assert "Советник, Которому вы доверяете" in combined
    assert "Своей смертью и воскресением" in combined
