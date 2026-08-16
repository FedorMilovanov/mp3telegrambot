from __future__ import annotations

from handlers import dub_health
from services import dub_studio_runtime
from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import dub_worker_hardened
from tools.voxcpm2.semantic_tts_guard_v4 import _GUARD_VERSION


def test_worker_release_is_shared_while_audio_guard_remains_versioned() -> None:
    assert WORKER_RUNTIME.startswith("dub-worker-quality-v")
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_worker_hardened._RUNTIME_VERSION == WORKER_RUNTIME
    assert dub_worker_hardened._RUNTIME_VERSION == WORKER_RUNTIME
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    assert _GUARD_VERSION == "semantic-tts-guard-v4.2"


def test_health_checks_current_production_actions_behaviorally() -> None:
    ok, detail = dub_health._recipe_contract()
    assert ok, detail
    ok, detail = dub_health._backend_contract()
    assert ok, detail
    ok, detail = dub_health._runtime_safety_contract()
    assert ok, detail


def test_worker_parses_structured_progress_and_qa_rounds() -> None:
    parser = dub_worker_hardened._progress_from_line_v44
    line = 'DUB_PROGRESS {"progress": 47, "stage": "Реплика 3/6, генерация варианта 2"}'
    progress, stage = parser(line, 31)
    assert progress == 47
    assert stage == "Реплика 3/6, генерация варианта 2"

    progress, stage = parser(
        "[TTS-QA] reference-only, QA round 2/3, seed=123",
        88,
    )
    assert progress == 88
    assert stage == "Независимая QA: раунд 2/3"


def test_worker_progress_ignores_traceback_function_names() -> None:
    progress, stage = dub_worker_hardened._progress_from_line_v44(
        '  File "clean.py", line 7, in master_constant_mix',
        42,
    )
    assert progress == 42
    assert stage == ""


def test_progress_milestones_are_sparse() -> None:
    assert dub_worker_hardened._highest_crossed_milestone(24, 26) == 25
    assert dub_worker_hardened._highest_crossed_milestone(26, 77) == 75
    assert dub_worker_hardened._highest_crossed_milestone(77, 89) is None
    assert dub_worker_hardened._highest_crossed_milestone(89, 92) == 90
