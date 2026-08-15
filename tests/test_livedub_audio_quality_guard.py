from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from services import livedub_audio_companion as companion
from services import livedub_audio_quality_guard as guard
from services import livedub_delivery_coordinator as delivery
from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


def test_clean_selector_ignores_newer_derived_mp3s(tmp_path: Path) -> None:
    clean = tmp_path / "translation.live.mp3"; clean.write_bytes(b"clean" * 400)
    mixed = tmp_path / "sermon.final-mix.mp3"; mixed.write_bytes(b"mixed" * 400)
    legacy = tmp_path / "sermon.ru-audio.mp3"; legacy.write_bytes(b"legacy" * 400)
    os.utime(clean, (10, 10)); os.utime(mixed, (30, 30)); os.utime(legacy, (20, 20))
    assert guard.is_derived_audio_artifact(mixed) and guard.is_derived_audio_artifact(legacy)
    assert guard.select_clean_translation_mp3(tmp_path) == clean


def test_clean_selector_rejects_qa_and_original_audio(tmp_path: Path) -> None:
    (tmp_path / "original_audio.mp3").write_bytes(b"original" * 300)
    (tmp_path / "translation_qa.mp3").write_bytes(b"qa" * 700)
    assert guard.select_clean_translation_mp3(tmp_path) is None


def test_dual_delivery_fails_closed_when_clean_track_missing(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "final.mp4"; video.write_bytes(b"video" * 400)
    mixed = tmp_path / "final.final-mix.mp3"; mixed.write_bytes(b"mixed" * 400)
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 120))
    monkeypatch.setattr(guard, "select_clean_translation_mp3", lambda _path: None)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)
    with pytest.raises(RuntimeError, match="чистая русская дорожка не найдена"):
        asyncio.run(delivery.deliver_new_companions(object(), chat_id=1, video_path=video, publication_card={}, reply_to=2, thumbnail=None, video_file_id="video-id"))


def test_dual_delivery_rejects_same_physical_file_for_both_roles(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "final.mp4"; video.write_bytes(b"video" * 400)
    shared = tmp_path / "translation.live.mp3"; shared.write_bytes(b"audio" * 400)
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 120))
    monkeypatch.setattr(guard, "select_clean_translation_mp3", lambda _path: shared)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: shared)
    with pytest.raises(RuntimeError, match="один файл"):
        asyncio.run(delivery.deliver_new_companions(object(), chat_id=1, video_path=video, publication_card={}, reply_to=2, thumbnail=None, video_file_id="video-id"))


def test_mix_and_vot_use_source_owned_clean_selector(tmp_path: Path) -> None:
    from services import livedub_mix as mix
    from services import yandex_live_dub as yandex
    clean = tmp_path / "translation.live.mp3"; clean.write_bytes(b"clean" * 400)
    stale = tmp_path / "translation.final-mix.mp3"; stale.write_bytes(b"mixed" * 400)
    os.utime(clean, (10, 10)); os.utime(stale, (20, 20))
    assert mix.find_pro_tracks(tmp_path)[1] == clean
    assert yandex._find_latest_file(tmp_path, "*.mp3") == clean


def test_manifest_uses_delivery_contract_not_quality_installers() -> None:
    ids = {feature.feature_id for feature in DEFAULT_RUNTIME_FEATURES}
    assert "livedub-delivery-contract" in ids
    assert "livedub-audio-quality" not in ids
    assert "livedub-audio-dedupe" not in ids
