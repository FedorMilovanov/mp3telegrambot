from __future__ import annotations

import json
from pathlib import Path


def test_atomic_json_retries_transient_permission_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tools.voxcpm2 import dub_job_preflight as preflight

    destination = tmp_path / "production_preflight.json"
    original_replace = preflight.os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(13, "temporary Windows sharing violation")
        return original_replace(source, target)

    monkeypatch.setattr(preflight.os, "replace", flaky_replace)
    preflight._atomic_json(
        destination,
        {"schema_version": 2, "value": "ok"},
    )

    assert attempts == 3
    assert json.loads(destination.read_text(encoding="utf-8"))["value"] == "ok"
    assert not list(tmp_path.glob("production_preflight.json.tmp.*"))


def test_browser_profile_parser_preserves_windows_drive() -> None:
    from services import ffmpeg

    windows_profile = r"C:\Users\Fedor\AppData\Roaming\Mozilla\Firefox\Profiles\abc.default"
    assert (
        ffmpeg._browser_profile_from_spec(f"firefox:{windows_profile}")
        == windows_profile
    )
    assert (
        ffmpeg._browser_profile_from_spec("firefox:/tmp/profile::basictext")
        == "/tmp/profile"
    )
    assert ffmpeg._browser_profile_from_spec("firefox") == ""


def test_single_chapter_without_metadata_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    from mutagen.id3 import ID3

    from services.mp3_chapters import embed_chapters

    mp3 = tmp_path / "metadata.mp3"
    mp3.write_bytes(b"")

    assert not embed_chapters(
        mp3,
        [{"time": "0:00", "topic": "Одна точка"}],
        duration_sec=30,
    )
    assert embed_chapters(mp3, [], title="Только метаданные")
    assert ID3(str(mp3)).getall("TIT2")[0].text[0] == "Только метаданные"
