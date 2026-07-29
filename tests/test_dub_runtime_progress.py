from __future__ import annotations

from pathlib import Path

from services.dub_studio_runtime import (
    _permanent_edit_failure,
    _progress_message_ref,
)


ROOT = Path(__file__).resolve().parents[1]


def test_progress_ref_is_scoped_to_current_job() -> None:
    project = {
        "metadata": {
            "dub_progress_message_v1": {
                "job_id": 12,
                "chat_id": 123,
                "message_id": 456,
            }
        }
    }
    assert _progress_message_ref(project, {"job_id": 12}) == (123, 456)
    assert _progress_message_ref(project, {"job_id": 13}) is None


def test_only_permanent_edit_errors_create_replacement() -> None:
    assert _permanent_edit_failure(RuntimeError("Message to edit not found"))
    assert _permanent_edit_failure(RuntimeError("Message can't be edited"))
    assert not _permanent_edit_failure(RuntimeError("Timed out while connecting"))


def test_runtime_edits_one_progress_card_and_finalizes_it() -> None:
    source = (ROOT / "services" / "dub_studio_runtime.py").read_text(encoding="utf-8")
    assert "edit_message_text" in source
    assert "dub_progress_message_v1" in source
    assert "_store_progress_message_ref" in source
    assert "_finalize_progress_card" in source
    assert "проценты обновляются в этом сообщении" in source
    progress_section = source[source.index("async def _notify_progress_milestone"):source.index("async def _finalize_progress_card")]
    assert progress_section.index("edit_message_text") < progress_section.index("send_message")


def test_progress_is_integrated_without_second_installer() -> None:
    bot = (ROOT / "bot_new.py").read_text(encoding="utf-8")
    runtime = (ROOT / "services" / "dub_studio_runtime.py").read_text(encoding="utf-8")
    assert "install_dub_progress_updates" not in bot
    assert "dub_progress_updates.py" not in runtime
    assert 'dub-worker-quality-v4.4' in runtime
