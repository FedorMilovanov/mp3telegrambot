"""Regression coverage for PTB user-scoped Dub Wizard routing and Gemini defaults."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from handlers import dub_wizard


@pytest.mark.asyncio
async def test_text_handler_ignores_update_without_user_context() -> None:
    update = SimpleNamespace(
        effective_user=None,
        effective_message=SimpleNamespace(text="channel post"),
    )
    context = SimpleNamespace(user_data=None)

    await dub_wizard.handle_dub_wizard_text(update, context)


@pytest.mark.asyncio
async def test_document_handler_ignores_update_without_user_context() -> None:
    update = SimpleNamespace(
        effective_user=None,
        effective_message=SimpleNamespace(document=SimpleNamespace(file_name="x.srt")),
    )
    context = SimpleNamespace(user_data=None)

    await dub_wizard.handle_dub_wizard_document(update, context)


def test_message_handlers_are_limited_to_regular_messages() -> None:
    source = inspect.getsource(dub_wizard.register_dub_wizard_handlers)
    assert "MessageHandler(_MSG_ONLY & filters.Document.ALL" in source
    assert "MessageHandler(_MSG_ONLY & filters.TEXT & ~filters.COMMAND" in source


def test_dub_translation_defaults_to_gemini38(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DUB_TRANSLATION_MODEL", raising=False)
    monkeypatch.setattr(
        dub_wizard,
        "production_tts_profile_choice",
        lambda _profile_id: SimpleNamespace(profile_id="test/profile", backend_id="test"),
    )
    monkeypatch.setattr(
        dub_wizard,
        "normalize_new_production_tts_request",
        lambda payload, _profile_id: payload,
    )

    payload = dub_wizard._request_payload(
        "abcdefghijk",
        "https://youtube.com/watch?v=abcdefghijk",
        dub_wizard._GEMINI_MODE,
        "test/profile",
    )

    assert payload["translation_model"] == "gemini-3.8-flash"


def test_dub_wizard_source_has_no_stale_gemini37_default() -> None:
    source = inspect.getsource(dub_wizard)
    assert '"gemini-3.7-flash"' not in source
