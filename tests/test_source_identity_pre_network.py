from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import clean_source_download as source_download


def test_project_url_mismatch_fails_before_metadata(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "source" / "source.mp4"
    source.parent.mkdir(parents=True)
    (root / "request.json").write_text(
        json.dumps({"video_id": "ProjectId11"}),
        encoding="utf-8",
    )
    calls = 0

    def forbidden_download(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("legacy downloader/yt-dlp must not run")

    monkeypatch.setattr(source_download, "_legacy_download_source", forbidden_download)
    with pytest.raises(RuntimeError, match="до yt-dlp"):
        source_download.download_source(
            "https://youtu.be/SourceId999",
            source,
        )
    assert calls == 0


def test_matching_project_identity_delegates_to_verified_downloader(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "source" / "source.mp4"
    source.parent.mkdir(parents=True)
    (root / "request.json").write_text(
        json.dumps({"video_id": "AbCdEf12345"}),
        encoding="utf-8",
    )
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, path: Path):
        calls.append((url, Path(path)))
        return {"id": "AbCdEf12345"}

    monkeypatch.setattr(source_download, "_legacy_download_source", fake_download)
    result = source_download.download_source(
        "https://youtube.com/watch?v=AbCdEf12345",
        source,
    )
    assert result == {"id": "AbCdEf12345"}
    assert calls == [
        ("https://youtube.com/watch?v=AbCdEf12345", source),
    ]


def test_source_download_facade_patches_legacy_entrypoint() -> None:
    assert Path(source_download.__file__).name == "__init__.py"
    assert source_download._legacy.download_source is source_download.download_source
