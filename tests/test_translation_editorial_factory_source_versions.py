from __future__ import annotations

from pathlib import Path

import services.translation_editorial_factory as factory
from services.translation_editorial import sha256_file


def test_durable_factory_sources_are_versioned_by_exact_bytes(tmp_path: Path) -> None:
    root = tmp_path / "editorial"
    root.mkdir()
    first = tmp_path / "video_factory_source_a.mp4"
    second = tmp_path / "video_factory_source_b.mp4"
    first.write_bytes(b"a" * 4096)
    second.write_bytes(b"b" * 4096)

    first_durable = factory._durable_review_source(first, root, "video")
    second_durable = factory._durable_review_source(second, root, "video")

    assert first_durable != second_durable
    assert first_durable.exists() and second_durable.exists()
    assert sha256_file(first_durable) == sha256_file(first)
    assert sha256_file(second_durable) == sha256_file(second)
    assert sha256_file(first)[7:19] in first_durable.name
    assert sha256_file(second)[7:19] in second_durable.name


def test_same_factory_source_reuses_exact_durable_version(tmp_path: Path) -> None:
    root = tmp_path / "editorial"
    root.mkdir()
    source = tmp_path / "video_factory_source.mp4"
    source.write_bytes(b"same" * 2048)

    first = factory._durable_review_source(source, root, "video")
    second = factory._durable_review_source(source, root, "video")

    assert first == second
    assert sha256_file(first) == sha256_file(source)
