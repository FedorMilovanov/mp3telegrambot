from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from services import livedub_ru_provenance as provenance


def _audio(path: Path, payload: bytes = b"audio" * 500) -> Path:
    path.write_bytes(payload)
    return path


def test_atomic_marker_roundtrip_selects_exact_older_vot_file(tmp_path: Path):
    exact = _audio(tmp_path / "video.live.mp3")
    unrelated = _audio(tmp_path / "newer-unrelated.mp3", b"other" * 500)
    os.utime(exact, (10, 10))
    os.utime(unrelated, (20, 20))

    assert provenance.write_ru_audio_provenance(exact, voice_style="live") is True
    assert provenance.read_ru_audio_provenance(tmp_path) == exact
    assert list(tmp_path.glob(".livedub_ru_audio.json.*.tmp")) == []


def test_marker_rejects_path_traversal_and_absolute_paths(tmp_path: Path):
    outside = _audio(tmp_path.parent / "outside.mp3")
    marker = tmp_path / ".livedub_ru_audio.json"

    marker.write_text(
        json.dumps({
            "schema_version": 1,
            "filename": "../outside.mp3",
            "size_bytes": outside.stat().st_size,
            "mtime_ns": outside.stat().st_mtime_ns,
        }),
        encoding="utf-8",
    )
    assert provenance.read_ru_audio_provenance(tmp_path) is None

    marker.write_text(
        json.dumps({
            "schema_version": 1,
            "filename": str(outside.resolve()),
            "size_bytes": outside.stat().st_size,
            "mtime_ns": outside.stat().st_mtime_ns,
        }),
        encoding="utf-8",
    )
    assert provenance.read_ru_audio_provenance(tmp_path) is None


def test_symlinked_audio_is_rejected(tmp_path: Path):
    target = _audio(tmp_path.parent / "outside-target.mp3")
    link = tmp_path / "translation.live.mp3"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable on this platform")

    assert provenance.write_ru_audio_provenance(link) is False


def test_changed_file_invalidates_provenance(tmp_path: Path):
    exact = _audio(tmp_path / "video.live.mp3")
    assert provenance.write_ru_audio_provenance(exact)
    exact.write_bytes(b"changed" * 600)
    assert provenance.read_ru_audio_provenance(tmp_path) is None


def test_derived_audio_cannot_be_recorded_or_selected(tmp_path: Path):
    derived = _audio(tmp_path / "video.final-mix.mp3")
    assert provenance.write_ru_audio_provenance(derived) is False

    marker = tmp_path / ".livedub_ru_audio.json"
    marker.write_text(
        json.dumps({
            "schema_version": 1,
            "filename": derived.name,
            "size_bytes": derived.stat().st_size,
            "mtime_ns": derived.stat().st_mtime_ns,
        }),
        encoding="utf-8",
    )
    assert provenance.read_ru_audio_provenance(tmp_path) is None


def test_track_reader_prefers_exact_marker_over_legacy_fallback(tmp_path: Path, monkeypatch):
    from services import livedub_mix as mix

    exact = _audio(tmp_path / "video.live.mp3")
    fallback = _audio(tmp_path / "fallback.mp3")
    assert provenance.write_ru_audio_provenance(exact)

    monkeypatch.setattr(mix, "find_pro_tracks", lambda _workdir: (Path("original.mp4"), fallback))
    provenance._install_track_reader()
    original, selected = mix.find_pro_tracks(tmp_path)

    assert original == Path("original.mp4")
    assert selected == exact


def test_track_reader_preserves_fallback_without_valid_marker(tmp_path: Path, monkeypatch):
    from services import livedub_mix as mix

    fallback = _audio(tmp_path / "fallback.mp3")
    monkeypatch.setattr(mix, "find_pro_tracks", lambda _workdir: (None, fallback))
    provenance._install_track_reader()
    assert mix.find_pro_tracks(tmp_path) == (None, fallback)


def test_vot_wrapper_records_file_created_by_current_call(tmp_path: Path, monkeypatch):
    from services import yandex_live_dub as yandex

    exact = tmp_path / "returned.live.mp3"

    async def fake_get(*args, **kwargs):
        _audio(exact)
        return exact

    monkeypatch.setattr(yandex, "get_live_dub_audio", fake_get)
    provenance._install_vot_recorder()

    result = asyncio.run(
        yandex.get_live_dub_audio(
            "https://example.test/video",
            tmp_path,
            voice_style="live",
        )
    )
    assert result == exact
    assert provenance.read_ru_audio_provenance(tmp_path) == exact
    payload = json.loads((tmp_path / ".livedub_ru_audio.json").read_text(encoding="utf-8"))
    assert payload["voice_style"] == "live"


def test_vot_wrapper_does_not_fossilize_unchanged_old_mp3(tmp_path: Path, monkeypatch):
    from services import yandex_live_dub as yandex

    stale = _audio(tmp_path / "stale.live.mp3")

    async def fake_get(*args, **kwargs):
        return stale

    monkeypatch.setattr(yandex, "get_live_dub_audio", fake_get)
    provenance._install_vot_recorder()

    assert asyncio.run(yandex.get_live_dub_audio("url", tmp_path)) == stale
    assert not (tmp_path / ".livedub_ru_audio.json").exists()


def test_vot_wrapper_rejects_return_outside_requested_directory(tmp_path: Path, monkeypatch):
    from services import yandex_live_dub as yandex

    outside = tmp_path.parent / "outside-return.live.mp3"

    async def fake_get(*args, **kwargs):
        _audio(outside)
        return outside

    monkeypatch.setattr(yandex, "get_live_dub_audio", fake_get)
    provenance._install_vot_recorder()

    assert asyncio.run(yandex.get_live_dub_audio("url", tmp_path)) == outside
    assert not (tmp_path / ".livedub_ru_audio.json").exists()
