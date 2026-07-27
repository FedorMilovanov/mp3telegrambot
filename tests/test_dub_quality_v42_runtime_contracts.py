from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services import dub_studio_runtime
from tools.voxcpm2 import dub_worker_hardened
from tools.voxcpm2.semantic_tts_guard_v4 import _GUARD_VERSION


def test_worker_and_guard_runtime_markers_are_v42() -> None:
    assert dub_studio_runtime._WORKER_RUNTIME == "dub-worker-quality-v4.2"
    assert dub_worker_hardened._RUNTIME_VERSION == "dub-worker-quality-v4.2"
    assert dub_health._WORKER_RUNTIME == "dub-worker-quality-v4.2"
    assert _GUARD_VERSION == "semantic-tts-guard-v4.2"


def test_health_checks_all_three_production_actions() -> None:
    source = Path("handlers/dub_health.py").read_text(encoding="utf-8")
    assert "Recipe: Gemini MAX" in source
    assert "Recipe: готовый SRT" in source
    assert "Recipe: аудиоремонт без Gemini" in source
    assert "generic_audio_repair_runtime" in source
    assert 'str(repair_spec.get("kind") or "") == "utility"' in source


def test_health_checks_quality_v42_and_no_gemini_repair() -> None:
    source = Path("handlers/dub_health.py").read_text(encoding="utf-8")
    assert "NoChew Quality v4.2 + аудиоремонт" in source
    assert 'semantic-tts-guard-v4.2' in source
    assert 'voxcpm2-quality-v4.2' in source
    assert "sustained_activity_index" in source
    assert '"gemini_called": False' in source
    assert '"translate_groups_max" not in contract_text[audio_repair_entry_path]' in source


def test_runtime_upgrade_forces_old_worker_replacement() -> None:
    runtime_source = Path("services/dub_studio_runtime.py").read_text(encoding="utf-8")
    worker_source = Path("tools/voxcpm2/dub_worker_hardened.py").read_text(encoding="utf-8")
    assert 'dub-worker-quality-v4.1' not in runtime_source
    assert 'dub-worker-quality-v4.1' not in worker_source
    assert runtime_source.count('dub-worker-quality-v4.2') == 1
    assert worker_source.count('dub-worker-quality-v4.2') == 1
