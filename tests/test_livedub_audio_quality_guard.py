from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from services import livedub_audio_companion as companion
from services import livedub_audio_quality_guard as guard
from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


def test_clean_selector_ignores_newer_derived_mp3s(tmp_path: Path):
    clean = tmp_path / "translation.live.mp3"
    clean.write_bytes(b"clean" * 400)

    stale_mix = tmp_path / "sermon.final-mix.mp3"
    stale_mix.write_bytes(b"mixed" * 400)
    stale_legacy = tmp_path / "sermon.ru-audio.mp3"
    stale_legacy.write_bytes(b"legacy" * 400)

    os.utime(clean, (10, 10))
    os.utime(stale_mix, (30, 30))
    os.utime(stale_legacy, (20, 20))

    assert guard.is_derived_audio_artifact(stale_mix)
    assert guard.is_derived_audio_artifact(stale_legacy)
    assert not guard.is_derived_audio_artifact(clean)
    assert guard.select_clean_translation_mp3(tmp_path) == clean


def test_clean_selector_rejects_qa_and_original_audio(tmp_path: Path):
    (tmp_path / "original_audio.mp3").write_bytes(b"original" * 300)
    (tmp_path / "translation_qa.mp3").write_bytes(b"qa" * 700)
    assert guard.select_clean_translation_mp3(tmp_path) is None


def test_dual_mode_reports_partial_result_when_clean_track_is_missing(
    monkeypatch, tmp_path: Path
):
    guard._install_complete_dual_delivery()

    video = tmp_path / "final.mp4"
    mixed = tmp_path / "final.final-mix.mp3"
    video.write_bytes(b"video" * 400)
    mixed.write_bytes(b"mixed" * 400)

    sent = []

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 120))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: None)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)

    async def fake_send_variant(_self, **kwargs):
        sent.append(kwargs["variant"])
        return True

    monkeypatch.setattr(companion, "_send_variant", fake_send_variant)

    with pytest.raises(RuntimeError, match=r"неполный комплект MP3 \(1/2\)"):
        asyncio.run(
            companion._send_new_audio(
                object(),
                chat_id=1,
                video_path=video,
                caption="<b>Название - Автор</b>",
                reply_to=2,
                thumbnail=None,
                video_file_id="video-id",
            )
        )
    assert sent == ["mixed"]


def test_same_physical_file_cannot_be_sent_as_both_variants(monkeypatch, tmp_path: Path):
    guard._install_complete_dual_delivery()

    video = tmp_path / "final.mp4"
    shared = tmp_path / "translation.live.mp3"
    video.write_bytes(b"video" * 400)
    shared.write_bytes(b"audio" * 400)

    sent = []
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 120))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: shared)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: shared)

    async def fake_send_variant(_self, **kwargs):
        sent.append(kwargs["variant"])
        return True

    monkeypatch.setattr(companion, "_send_variant", fake_send_variant)

    with pytest.raises(RuntimeError, match=r"неполный комплект MP3 \(1/2\)"):
        asyncio.run(
            companion._send_new_audio(
                object(),
                chat_id=1,
                video_path=video,
                caption="<b>Название - Автор</b>",
                reply_to=2,
                thumbnail=None,
                video_file_id="video-id",
            )
        )
    assert sent == ["clean"]


def test_manifest_installs_quality_before_dedupe_and_deep_audit():
    order = {
        feature.feature_id: index
        for index, feature in enumerate(DEFAULT_RUNTIME_FEATURES)
    }
    assert (
        order["livedub-audio-quality"]
        < order["livedub-audio-dedupe"]
        < order["livedub-deep-audit"]
    )
    quality = next(
        feature
        for feature in DEFAULT_RUNTIME_FEATURES
        if feature.feature_id == "livedub-audio-quality"
    )
    assert quality.required is True


def test_runtime_patch_applies_same_clean_selector_to_mix_and_vot(tmp_path: Path):
    from services import livedub_mix as mix
    from services import yandex_live_dub as yandex

    clean = tmp_path / "translation.live.mp3"
    clean.write_bytes(b"clean" * 400)
    stale_mix = tmp_path / "translation.final-mix.mp3"
    stale_mix.write_bytes(b"mixed" * 400)
    os.utime(clean, (10, 10))
    os.utime(stale_mix, (20, 20))

    guard._install_clean_track_selection()

    _original, selected = mix.find_pro_tracks(tmp_path)
    assert selected == clean
    assert yandex._find_latest_file(tmp_path, "*.mp3") == clean
