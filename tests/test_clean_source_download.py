from __future__ import annotations

import json
from types import SimpleNamespace

from tools.voxcpm2 import clean_source_download as source_cache


def test_verified_source_cache_reuses_only_same_video(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.mp4"
    current = {"id": "video-one", "byte": b"A"}
    metadata_calls = 0
    download_calls = 0

    monkeypatch.setattr(source_cache.hardened, "_ytdlp_base", lambda: ["yt-dlp"])
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

    monkeypatch.setattr(source_cache.hardened, "_ytdlp_base", lambda: ["yt-dlp"])
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
