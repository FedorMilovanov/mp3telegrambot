from __future__ import annotations

from pathlib import Path

from services.dub_progress_updates import (
    _permanent_edit_failure,
    _progress_message_ref,
)


ROOT = Path(__file__).resolve().parents[1]


def test_progress_ref_is_scoped_to_the_current_job() -> None:
    project = {
        "metadata": {
            "dub_progress_message_v1": {
                "job_id": 11,
                "chat_id": 123,
                "message_id": 456,
            }
        }
    }
    assert _progress_message_ref(project, {"job_id": 11}) == (123, 456)
    assert _progress_message_ref(project, {"job_id": 12}) is None


def test_only_permanent_edit_errors_create_a_replacement() -> None:
    assert _permanent_edit_failure(RuntimeError("Message to edit not found"))
    assert _permanent_edit_failure(RuntimeError("Message can't be edited"))
    assert not _permanent_edit_failure(RuntimeError("Timed out while connecting"))


def test_progress_implementation_edits_before_fallback_send() -> None:
    source = (ROOT / "services" / "dub_progress_updates.py").read_text(encoding="utf-8")
    assert "edit_message_text" in source
    assert source.index("edit_message_text") < source.index("send_message")
    assert "message is not modified" in source
    assert "_store_progress_message_ref" in source
    assert "store.mark_event_delivered" in source


def test_bot_installs_progress_policy_after_dub_runtime() -> None:
    source = (ROOT / "bot_new.py").read_text(encoding="utf-8")
    runtime = source.index("install_dub_studio_runtime()")
    progress = source.index("install_dub_progress_updates()")
    assert runtime < progress
