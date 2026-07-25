from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import livedub_audio_companion as companion
from services.livedub_delivery_hardening import (
    build_major_fix_intervals,
    normalise_js_runtime_args,
)


def _parse_mmss(value: str):
    try:
        minutes, seconds = value.split(":")
        return int(minutes) * 60 + int(seconds)
    except Exception:
        return None


def test_js_runtimes_are_repeated_and_deduplicated():
    args = [
        "python", "-m", "yt_dlp",
        "--js-runtimes", "deno,node",
        "--quiet",
        "--js-runtimes=node",
    ]
    fixed = normalise_js_runtime_args(args)
    assert "deno,node" not in fixed
    assert fixed.count("--js-runtimes") == 2
    assert fixed[-4:] == ["--js-runtimes", "deno", "--js-runtimes", "node"]


def test_all_major_issues_are_covered_not_only_first_six():
    issues = [
        {"time": f"{index:02d}:00", "severity": "major"}
        for index in range(9)
    ]
    intervals = build_major_fix_intervals(
        issues,
        parse_time=_parse_mmss,
        delay_s=0.6,
    )
    assert len(intervals) == 9
    for index in range(9):
        moment = index * 60
        assert any(start <= moment <= end for start, end in intervals)


def test_explicit_autofix_limit_refuses_partial_result():
    issues = [
        {"time": f"{index:02d}:00", "severity": "major"}
        for index in range(9)
    ]
    with pytest.raises(RuntimeError, match="refusing a partial auto-fix"):
        build_major_fix_intervals(
            issues,
            parse_time=_parse_mmss,
            delay_s=0.6,
            max_intervals=6,
        )


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_audio(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(audio=SimpleNamespace(file_id=f"fid-{len(self.calls)}"))


def test_new_delivery_sends_clean_and_final_mix(monkeypatch, tmp_path: Path):
    video = tmp_path / "final.mp4"
    clean = tmp_path / "clean.mp3"
    mixed = tmp_path / "final.final-mix.mp3"
    for path in (video, clean, mixed):
        path.write_bytes(b"x" * 2048)

    monkeypatch.setattr(companion, "_probe_audio", lambda path: (True, 120))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda path: clean)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda path: mixed)
    monkeypatch.setattr(companion, "_cache_put_variant", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)

    bot = FakeBot()
    ok = asyncio.run(
        companion._send_new_audio(
            bot,
            chat_id=1,
            video_path=video,
            caption="<b>Название - Автор</b>\n🎬 Живые голоса Яндекса",
            reply_to=2,
            thumbnail=None,
            video_file_id="video-fid",
        )
    )

    assert ok is True
    assert len(bot.calls) == 2
    assert "Чистая аудиодорожка" in bot.calls[0]["caption"]
    assert "финального дубляжа" in bot.calls[1]["caption"]
    assert bot.calls[0]["filename"].endswith("чистый RU.mp3")
    assert bot.calls[1]["filename"].endswith("финальный микс.mp3")
    assert bot.calls[0]["audio"] == clean
    assert bot.calls[1]["audio"] == mixed


def test_incomplete_legacy_cache_forces_rebuild(monkeypatch):
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _fid: {
            "variants": {
                "clean": {"audio_file_id": "old-clean"},
            }
        },
    )
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    bot = FakeBot()
    ok = asyncio.run(
        companion._send_cached_audio(
            bot,
            chat_id=1,
            video_file_id="video-fid",
            reply_to=2,
        )
    )
    assert ok is False
    assert bot.calls == []


def test_complete_cache_resends_both_variants(monkeypatch):
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _fid: {
            "variants": {
                "clean": {"audio_file_id": "clean-fid", "title": "T"},
                "mixed": {"audio_file_id": "mixed-fid", "title": "T"},
            }
        },
    )
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    bot = FakeBot()
    ok = asyncio.run(
        companion._send_cached_audio(
            bot,
            chat_id=1,
            video_file_id="video-fid",
            reply_to=2,
        )
    )
    assert ok is True
    assert [call["audio"] for call in bot.calls] == ["clean-fid", "mixed-fid"]
