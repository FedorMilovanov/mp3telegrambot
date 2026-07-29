from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "services" / "dub_studio_runtime.py"
WORKER = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened.py"


def _source(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    ast.parse(value)
    return value


def test_runtime_edits_one_job_scoped_progress_card() -> None:
    source = _source(RUNTIME)
    assert '_PROGRESS_METADATA_KEY = "dub_progress_message_v1"' in source
    assert "event_job_id" in source
    assert "ref_job_id != event_job_id" in source
    assert "_store_progress_message_ref" in source
    progress_section = source[
        source.index("async def _notify_progress_milestone"):
        source.index("async def _finalize_progress_card")
    ]
    assert progress_section.index("edit_message_text") < progress_section.index("send_message")
    assert "message is not modified" in progress_section
    assert "проценты обновляются в этом сообщении" in progress_section


def test_runtime_finalizes_progress_card_at_terminal_event() -> None:
    source = _source(RUNTIME)
    assert "async def _finalize_progress_card" in source
    assert '"job_succeeded": ("✅", "готово")' in source
    assert '"job_failed": ("❌", "ошибка")' in source
    assert "await _finalize_progress_card(application, event, project)" in source


def test_progress_is_integrated_without_second_installer() -> None:
    bot = _source(ROOT / "bot_new.py")
    runtime = _source(RUNTIME)
    assert "install_dub_progress_updates" not in bot
    assert "dub_progress_updates.py" not in runtime
    assert 'dub-worker-quality-v4.5' in runtime
    assert "worker v4.5 autostart requested" in runtime


def test_worker_stage_parser_never_uses_master_substring_fallback() -> None:
    worker = _source(WORKER)
    assert 'dub-worker-quality-v4.5' in worker
    assert "def _progress_from_line_v44" in worker
    assert "render_and_master" in worker
    assert "master_constant_mix.py" in worker
    assert "return current, \"\"" in worker
    assert 'if "master" in text.lower()' not in worker


def test_worker_v45_contains_durable_terminal_guards() -> None:
    worker = _source(WORKER)
    assert "_recover_abandoned_with_terminal_events" in worker
    assert "_FINAL_JOB_STATES" in worker
    assert "status in _FINAL_JOB_STATES" in worker
    assert "_FINISH_LOCK = threading.RLock()" in worker
    assert "finished_at=''" in worker
    assert "recovered_after_worker_stop" in worker
