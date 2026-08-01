from __future__ import annotations

import ast
from pathlib import Path

from services import dub_studio_runtime
from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import dub_worker_hardened


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "services" / "dub_studio_runtime.py"
RUNTIME_FACADE = ROOT / "services" / "dub_studio_runtime" / "__init__.py"
WORKER = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened.py"
WORKER_FACADE = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened" / "__init__.py"


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


def test_progress_is_integrated_with_active_supervisor_facade() -> None:
    bot = _source(ROOT / "bot_new.py")
    runtime = _source(RUNTIME)
    facade = _source(RUNTIME_FACADE)
    assert "install_dub_progress_updates" not in bot
    assert "dub_progress_updates.py" not in runtime
    assert "_WORKER_RUNTIME = WORKER_RUNTIME" in facade
    assert "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME" in facade
    assert "class _WriteThroughModule" in facade
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME


def test_worker_stage_parser_ignores_master_substrings_in_tracebacks() -> None:
    progress, stage = dub_worker_hardened._progress_from_line_v44(
        '  File "clean.py", line 7, in master_constant_mix.py',
        42,
    )
    assert progress == 42
    assert stage == ""

    worker = _source(WORKER)
    facade = _source(WORKER_FACADE)
    assert "_RUNTIME_VERSION = WORKER_RUNTIME" in facade
    assert "_legacy._RUNTIME_VERSION = _RUNTIME_VERSION" in facade
    assert "def _progress_from_line_v44" in worker
    assert 'if "master" in text.lower()' not in worker


def test_worker_contains_terminal_preflight_and_delivery_guards() -> None:
    worker = _source(WORKER)
    facade = _source(WORKER_FACADE)
    assert "_recover_abandoned_with_terminal_events" in worker
    assert "_FINAL_JOB_STATES" in worker
    assert "_FINISH_LOCK = threading.RLock()" in worker
    assert "recovered_after_worker_stop" in worker
    assert "from tools.voxcpm2 import dub_job_preflight" in worker
    assert 'DELIVERY_RESILIENCE_POLICY = "cadence-tail-fit-adaptive-resume-v1"' in facade
    assert dub_worker_hardened._RUNTIME_VERSION == WORKER_RUNTIME
