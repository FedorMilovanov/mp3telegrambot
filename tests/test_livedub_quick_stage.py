"""Behavioral contracts for the extracted ENG Quick finalization stage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from pipelines.livedub_quick_stage import finish_livedub_quick


class _Status:
    def __init__(self, events, *, fail_edit=False, fail_delete=False):
        self.events = events
        self.fail_edit = fail_edit
        self.fail_delete = fail_delete

    async def edit_text(self, text):
        self.events.append(("edit", text))
        if self.fail_edit:
            raise RuntimeError("edit failed")

    async def delete(self):
        self.events.append(("delete", None))
        if self.fail_delete:
            raise RuntimeError("delete failed")


class _Message:
    def __init__(self, events, *, fail_reply=False):
        self.events = events
        self.fail_reply = fail_reply

    async def reply_text(self, text):
        self.events.append(("reply", text))
        if self.fail_reply:
            raise RuntimeError("reply failed")


def _sender(events, delivered):
    async def send():
        events.append(("send", None))
        return delivered

    return send


def _cleanup(events):
    def cleanup(media_id):
        events.append(("cleanup", media_id))

    return cleanup


def _update(events, *, fail_reply=False):
    return SimpleNamespace(message=_Message(events, fail_reply=fail_reply))


def _kinds(events):
    return [event[0] for event in events]


def test_success_edits_and_deletes_status_before_cleanup():
    events = []
    status = _Status(events)

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=status,
            title="Название",
            send_result=_sender(events, True),
            silent_errors=False,
            workdir=None,
            media_id="media-1",
            cleanup=_cleanup(events),
        )
    )

    assert result is True
    assert _kinds(events) == ["edit", "send", "delete", "cleanup"]
    assert events[0][1] == "⚡ Режим ENG Quick: жду перевод «Живые голоса»...\n📝 Название"
    assert events[-1][1] == "media-1"


def test_success_without_status_skips_both_status_operations():
    events = []

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=None,
            title="Название",
            send_result=_sender(events, True),
            silent_errors=False,
            workdir=None,
            media_id="media-2",
            cleanup=_cleanup(events),
        )
    )

    assert result is True
    assert _kinds(events) == ["send", "cleanup"]


def test_status_edit_and_delete_failures_are_best_effort():
    events = []
    status = _Status(events, fail_edit=True, fail_delete=True)

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=status,
            title="Название",
            send_result=_sender(events, True),
            silent_errors=False,
            workdir=None,
            media_id="media-3",
            cleanup=_cleanup(events),
        )
    )

    assert result is True
    assert _kinds(events) == ["edit", "send", "delete", "cleanup"]


def test_generic_failure_is_explained_and_returns_false():
    events = []

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=None,
            title="Название",
            send_result=_sender(events, False),
            silent_errors=False,
            workdir=None,
            media_id="media-4",
            cleanup=_cleanup(events),
        )
    )

    assert result is False
    assert _kinds(events) == ["send", "reply", "cleanup"]
    assert "Перевод «Живые голоса» не получился" in events[1][1]
    assert "ENG Full (/mode)" in events[1][1]


def test_auth_marker_selects_actionable_hint(tmp_path):
    events = []
    (tmp_path / ".auth_required").write_text("required", encoding="utf-8")

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=None,
            title="Название",
            send_result=_sender(events, False),
            silent_errors=False,
            workdir=tmp_path,
            media_id="media-5",
            cleanup=_cleanup(events),
        )
    )

    assert result is False
    assert _kinds(events) == ["send", "reply", "cleanup"]
    hint = events[1][1]
    assert "VOT_API_TOKEN" in hint
    assert "YANDEX_OAUTH_TOKEN" in hint
    assert "rust-server-531j.onrender.com/v1/auth/handle" in hint


def test_playlist_failure_is_silent_but_still_cleans_up():
    events = []

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=None,
            title="Название",
            send_result=_sender(events, False),
            silent_errors=True,
            workdir=None,
            media_id="media-6",
            cleanup=_cleanup(events),
        )
    )

    assert result is False
    assert _kinds(events) == ["send", "cleanup"]


def test_marker_oserror_falls_back_to_generic_hint():
    class BrokenWorkdir:
        def __truediv__(self, _name):
            return self

        def exists(self):
            raise OSError("marker unavailable")

    events = []

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events),
            status_msg=None,
            title="Название",
            send_result=_sender(events, False),
            silent_errors=False,
            workdir=BrokenWorkdir(),
            media_id="media-7",
            cleanup=_cleanup(events),
        )
    )

    assert result is False
    assert "Перевод «Живые голоса» не получился" in events[1][1]


def test_reply_failure_does_not_mask_result_or_skip_cleanup():
    events = []

    result = asyncio.run(
        finish_livedub_quick(
            update=_update(events, fail_reply=True),
            status_msg=None,
            title="Название",
            send_result=_sender(events, False),
            silent_errors=False,
            workdir=None,
            media_id="media-8",
            cleanup=_cleanup(events),
        )
    )

    assert result is False
    assert _kinds(events) == ["send", "reply", "cleanup"]


def test_send_exception_propagates_before_delete_and_cleanup():
    events = []

    async def failing_send():
        events.append(("send", None))
        raise RuntimeError("delivery failed")

    with pytest.raises(RuntimeError, match="delivery failed"):
        asyncio.run(
            finish_livedub_quick(
                update=_update(events),
                status_msg=_Status(events),
                title="Название",
                send_result=failing_send,
                silent_errors=False,
                workdir=None,
                media_id="media-9",
                cleanup=_cleanup(events),
            )
        )

    assert _kinds(events) == ["edit", "send"]


# Replacements for source-owner assertions retained by test_livedub_qa.py.
def test_eng_quick_never_silent():
    main = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = Path("pipelines/livedub_quick_stage.py").read_text(encoding="utf-8")
    assert "return await finish_livedub_quick(" in main
    assert "send_result=_send_livedub_result" in main
    assert "delivered = await send_result()" in stage
    assert "Перевод «Живые голоса» не получился" in stage


def test_auth_required_marker_and_user_hint():
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert ".auth_required" in mix
    assert "LIVEDUB_AUTH_REQUIRED" in mix
    stage = Path("pipelines/livedub_quick_stage.py").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in stage
    assert "YANDEX_OAUTH_TOKEN" in stage
    assert ".auth_required" in stage
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in env
    assert "YANDEX_OAUTH_TOKEN" in env
    prefix = "#   1. Открой в браузере: "
    auth_line = next(line for line in env.splitlines() if line.startswith(prefix))
    auth_url = urlparse(auth_line.removeprefix(prefix))
    assert auth_url.scheme == "https"
    assert auth_url.hostname == "rust-server-531j.onrender.com"
    assert auth_url.path == "/v1/auth/handle"
