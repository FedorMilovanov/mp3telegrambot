from __future__ import annotations

from handlers.dub_commands import _default_render_action
from handlers.dub_wizard import _decode_text_file, _request_payload


def _project(mode: str) -> dict:
    return {
        "id": "dub-1234567890",
        "recipe_id": "generic_short_v1",
        "metadata": {"translation_mode": mode},
    }


def test_each_mode_has_its_own_render_action() -> None:
    assert _default_render_action(_project("gemini")) == "render_gemini"
    assert _default_render_action(_project("direct")) == "render_direct"


def test_both_modes_keep_approved_audio_defaults() -> None:
    for mode in ("gemini", "direct"):
        payload = _request_payload("tNlIoCeGyLk", "https://youtube.com/watch?v=tNlIoCeGyLk", mode)
        assert payload["original_level"] == 0.18
        assert payload["russian_delay_ms"] == 420
        assert payload["translation_mode"] == mode


def test_ready_srt_accepts_windows_1251() -> None:
    source = "1\n00:00:00,000 --> 00:00:02,000\nГотовый перевод.\n"
    assert _decode_text_file(source.encode("cp1251")) == source
