from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services import livedub_ru_provenance as provenance


def _audio(path: Path, payload: bytes = b"audio" * 500) -> Path:
    path.write_bytes(payload)
    return path


def test_atomic_marker_roundtrip_selects_exact_older_vot_file(tmp_path: Path) -> None:
    exact = _audio(tmp_path / "video.live.mp3")
    unrelated = _audio(tmp_path / "newer-unrelated.mp3", b"other" * 500)
    os.utime(exact, (10, 10)); os.utime(unrelated, (20, 20))
    assert provenance.write_ru_audio_provenance(exact, voice_style="live") is True
    assert provenance.read_ru_audio_provenance(tmp_path) == exact
    assert list(tmp_path.glob(".livedub_ru_audio.json.*.tmp")) == []


def test_marker_rejects_path_traversal_and_changed_files(tmp_path: Path) -> None:
    exact = _audio(tmp_path / "video.live.mp3")
    assert provenance.write_ru_audio_provenance(exact)
    exact.write_bytes(b"changed" * 600)
    assert provenance.read_ru_audio_provenance(tmp_path) is None
    marker = tmp_path / ".livedub_ru_audio.json"
    marker.write_text(json.dumps({"schema_version": 1, "filename": "../outside.mp3", "size_bytes": 1, "mtime_ns": 1}), encoding="utf-8")
    assert provenance.read_ru_audio_provenance(tmp_path) is None


def test_symlinked_and_derived_audio_are_rejected(tmp_path: Path) -> None:
    derived = _audio(tmp_path / "video.final-mix.mp3")
    assert provenance.write_ru_audio_provenance(derived) is False
    target = _audio(tmp_path.parent / "outside-target.mp3")
    link = tmp_path / "translation.live.mp3"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    assert provenance.write_ru_audio_provenance(link) is False


def test_record_returned_ru_audio_accepts_only_current_changed_file(tmp_path: Path) -> None:
    stale = _audio(tmp_path / "stale.live.mp3")
    before = provenance.snapshot_ru_audio_candidates(tmp_path)
    assert provenance.record_returned_ru_audio(stale, workdir=tmp_path, before=before) is False
    fresh = _audio(tmp_path / "fresh.live.mp3", b"fresh" * 500)
    assert provenance.record_returned_ru_audio(fresh, workdir=tmp_path, before=before, voice_style="live") is True
    assert provenance.read_ru_audio_provenance(tmp_path) == fresh


def test_record_returned_ru_audio_rejects_outside_workdir(tmp_path: Path) -> None:
    outside = _audio(tmp_path.parent / "outside-return.live.mp3")
    assert provenance.record_returned_ru_audio(outside, workdir=tmp_path, before={}) is False
    assert not (tmp_path / ".livedub_ru_audio.json").exists()


def test_mix_consumer_prefers_exact_provenance(tmp_path: Path) -> None:
    from services import livedub_mix as mix

    exact = _audio(tmp_path / "exact.live.mp3")
    fallback = _audio(tmp_path / "fallback.mp3", b"fallback" * 500)
    os.utime(exact, (10, 10)); os.utime(fallback, (20, 20))
    assert provenance.write_ru_audio_provenance(exact)
    _original, selected = mix.find_pro_tracks(tmp_path)
    assert selected == exact
