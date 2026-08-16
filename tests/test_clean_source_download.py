from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.voxcpm2 import clean_source_download as source_cache


ROOT = Path(__file__).resolve().parents[1]


def test_verified_source_cache_reuses_only_same_video(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.mp4"
    current = {"id": "video-one", "byte": b"A"}
    metadata_calls = 0
    download_calls = 0

    monkeypatch.setattr(source_cache.pipeline, "_ytdlp_base", lambda: ["yt-dlp"])
    monkeypatch.setattr(source_cache.pipeline, "log", lambda _message: None)

    def fake_run(command, *, capture=False, timeout=None, **_kwargs):
        nonlocal metadata_calls, download_calls
        assert timeout in {300, 1800}
        if "--dump-single-json" in command:
            metadata_calls += 1
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "id": current["id"],
                        "webpage_url": f"https://youtube.com/watch?v={current['id']}",
                    }
                ),
                returncode=0,
            )
        download_calls += 1
        output = command[command.index("-o") + 1]
        with open(output, "wb") as handle:
            handle.write(current["byte"] * 120_000)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(source_cache.pipeline, "run_checked", fake_run)

    first = source_cache.download_source("https://youtu.be/video-one", source)
    assert first["id"] == "video-one"
    assert metadata_calls == 1
    assert download_calls == 1

    second = source_cache.download_source("https://youtube.com/watch?v=video-one", source)
    assert second["id"] == "video-one"
    assert metadata_calls == 2
    assert download_calls == 1

    current.update(id="video-two", byte=b"B")
    third = source_cache.download_source("https://youtu.be/video-two", source)
    assert third["id"] == "video-two"
    assert metadata_calls == 3
    assert download_calls == 2
    assert source.read_bytes()[:8] == b"B" * 8

    manifest = json.loads(
        source.with_suffix(".mp4.download.json").read_text(encoding="utf-8")
    )
    assert manifest["policy"] == source_cache.POLICY
    assert manifest["video_id"] == "video-two"
    assert manifest["size_bytes"] == 120_000
    assert len(manifest["sampled_sha256"]) == 64


def test_same_size_source_tampering_forces_redownload(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.mp4"
    downloads = 0

    monkeypatch.setattr(source_cache.pipeline, "_ytdlp_base", lambda: ["yt-dlp"])
    monkeypatch.setattr(source_cache.pipeline, "log", lambda _message: None)

    def fake_run(command, *, capture=False, **_kwargs):
        nonlocal downloads
        if "--dump-single-json" in command:
            return SimpleNamespace(
                stdout=json.dumps({"id": "stable-video"}),
                returncode=0,
            )
        downloads += 1
        output = command[command.index("-o") + 1]
        with open(output, "wb") as handle:
            handle.write(b"C" * 120_000)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(source_cache.pipeline, "run_checked", fake_run)

    source_cache.download_source("https://youtu.be/stable-video", source)
    assert downloads == 1
    source.write_bytes(b"X" * 120_000)

    source_cache.download_source("https://youtu.be/stable-video", source)
    assert downloads == 2
    assert source.read_bytes()[:8] == b"C" * 8


def test_project_request_video_id_must_match_download(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    source = root / "source" / "source.mp4"
    source.parent.mkdir(parents=True)
    (root / "request.json").write_text(
        json.dumps({"video_id": "ProjectId11"}),
        encoding="utf-8",
    )
    downloads = 0

    monkeypatch.setattr(source_cache.pipeline, "_ytdlp_base", lambda: ["yt-dlp"])
    monkeypatch.setattr(source_cache.pipeline, "log", lambda _message: None)

    def fake_run(command, **_kwargs):
        nonlocal downloads
        if "--dump-single-json" in command:
            return SimpleNamespace(
                stdout=json.dumps({"id": "SourceId999"}),
                returncode=0,
            )
        downloads += 1
        raise AssertionError("video download must not begin after request mismatch")

    monkeypatch.setattr(source_cache.pipeline, "run_checked", fake_run)
    with pytest.raises(RuntimeError, match="Project request.*разные video ID"):
        source_cache.download_source("https://youtu.be/SourceId999", source)
    assert downloads == 0
    assert not source.exists()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://youtu.be/AbCdEf12345?t=3", "AbCdEf12345"),
        ("https://www.youtube.com/watch?v=AbCdEf12345&list=PL1", "AbCdEf12345"),
        ("https://m.youtube.com/shorts/AbCdEf12345", "AbCdEf12345"),
        ("https://music.youtube.com/watch?v=AbCdEf12345", "AbCdEf12345"),
        ("https://youtube.com/live/AbCdEf12345?feature=share", "AbCdEf12345"),
        ("https://youtube.com/embed/AbCdEf12345", "AbCdEf12345"),
        ("https://www.youtube-nocookie.com/embed/AbCdEf12345", "AbCdEf12345"),
    ],
)
def test_canonical_youtube_id_extraction(url: str, expected: str) -> None:
    assert source_cache._url_video_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=AbCdEf12345",
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/@channel",
        "ftp://youtube.com/watch?v=AbCdEf12345",
        "not-a-url",
    ],
)
def test_non_single_video_urls_fail_before_ytdlp(monkeypatch, url: str) -> None:
    calls = 0

    def should_not_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("yt-dlp must not run for an unsupported source URL")

    monkeypatch.setattr(source_cache.pipeline, "run_checked", should_not_run)
    with pytest.raises(RuntimeError, match="канонической ссылкой на один YouTube-ролик"):
        source_cache._metadata(url)
    assert calls == 0


def test_redirected_metadata_id_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(source_cache.pipeline, "_ytdlp_base", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        source_cache.pipeline,
        "run_checked",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"id": "OtherId9999"}),
            returncode=0,
        ),
    )
    with pytest.raises(RuntimeError, match="разные ролики"):
        source_cache._metadata("https://youtu.be/AbCdEf12345")


def test_invalid_metadata_video_id_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(source_cache.pipeline, "_ytdlp_base", lambda: ["yt-dlp"])
    monkeypatch.setattr(
        source_cache.pipeline,
        "run_checked",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({"id": "bad id"}),
            returncode=0,
        ),
    )
    with pytest.raises(RuntimeError, match="корректный YouTube video ID"):
        source_cache._metadata("https://youtu.be/AbCdEf12345")


