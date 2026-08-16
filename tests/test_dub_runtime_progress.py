from __future__ import annotations

import ast
from pathlib import Path

from services import dub_studio_runtime, dub_worker
from services.dub_worker_release import WORKER_RUNTIME


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "services" / "dub_studio_runtime.py"
WORKER = ROOT / "services" / "dub_worker.py"
WORKER_ENTRY = ROOT / "tools" / "voxcpm2" / "dub_worker.py"


def _source(path: Path) -> str:
    value = path.read_text(encoding="utf-8")
    ast.parse(value)
    return value


def test_runtime_builds_one_job_scoped_progress_card() -> None:
    event = {
        "job_id": 17,
        "project_title": "Проверка",
        "payload": {"progress": 47, "stage": "Реплика 3/6"},
    }
    project = {
        "id": "dub-0123456789",
        "progress": 41,
        "stage": "synthesis",
    }

    text = dub_studio_runtime._progress_text(event, project)

    assert "Dub Studio — 47%" in text
    assert "Реплика 3/6" in text
    assert "проценты обновляются в этом сообщении" in text
    assert "/dubstatus dub-0123456789" in text

    source = _source(RUNTIME)
    assert '_PROGRESS_METADATA_KEY = "dub_progress_message_v1"' in source
    assert "ref_job_id != event_job_id" in source
    assert "_store_progress_message_ref" in source
    progress_section = source[
        source.index("async def _notify_progress_milestone"):
        source.index("async def _finalize_progress_card")
    ]
    assert progress_section.index("edit_message_text") < progress_section.index("send_message")
    assert "message is not modified" in progress_section


def test_runtime_finalizes_progress_card_at_terminal_event() -> None:
    source = _source(RUNTIME)
    assert "async def _finalize_progress_card" in source
    assert '"job_succeeded": ("✅", "готово")' in source
    assert '"job_failed": ("❌", "ошибка")' in source
    assert "await _finalize_progress_card(application, event, project)" in source


def test_progress_is_integrated_with_source_owned_supervisor() -> None:
    bot = _source(ROOT / "bot_new.py")
    runtime = _source(RUNTIME)
    assert "install_dub_progress_updates" not in bot
    assert "dub_progress_updates.py" not in runtime
    assert "from services.dub_worker_release import WORKER_RUNTIME" in runtime
    assert "_WORKER_RUNTIME = WORKER_RUNTIME" in runtime
    assert '"tools.voxcpm2.dub_worker"' in runtime
    assert "_legacy" not in runtime
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME


def test_worker_stage_parser_ignores_master_substrings_in_tracebacks() -> None:
    progress, stage = dub_worker._progress_from_line(
        '  File "clean.py", line 7, in master_constant_mix.py',
        42,
    )
    assert progress == 42
    assert stage == ""

    worker = _source(WORKER)
    entry = _source(WORKER_ENTRY)
    assert "from services.dub_worker_release import WORKER_RUNTIME" in worker
    assert "def _progress_from_line" in worker
    assert 'if "master" in text.lower()' not in worker
    assert "from services.dub_worker import main" in entry


def test_worker_contains_terminal_preflight_and_delivery_guards() -> None:
    worker = _source(WORKER)
    assert "def recover_abandoned_jobs" in worker
    assert "_FINAL_WORKER_JOB_STATES" in worker
    assert "_FINISH_LOCK = threading.RLock()" in worker
    assert "recovered_after_worker_stop" in worker
    assert "from tools.voxcpm2 import dub_job_preflight" in worker
    assert 'DELIVERY_RESILIENCE_POLICY = "cadence-tail-fit-adaptive-resume-v1"' in worker
    assert dub_worker.WORKER_RUNTIME == WORKER_RUNTIME
