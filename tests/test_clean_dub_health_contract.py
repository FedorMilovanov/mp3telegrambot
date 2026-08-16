from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services.dub_worker_release import WORKER_RUNTIME
from services.speech_backends import DEFAULT_BACKEND_ID, default_backend, select_production_backend
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_direct_runtime
from tools.voxcpm2 import generic_project_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_quality_contract_accepts_current_source_owned_runtime() -> None:
    ok, detail = dub_health._quality_contract(ROOT)
    assert ok, detail
    assert "runtime-safety" in detail
    assert "recipe-routing" in detail


def test_active_backend_and_source_owned_routes_are_callable() -> None:
    selection = select_production_backend(None, default_backend_id=DEFAULT_BACKEND_ID)
    backend = default_backend()
    assert selection.backend is backend
    assert backend.capabilities().missing() == ()
    assert callable(generic_project_runtime.main)
    assert callable(generic_direct_runtime.main)
    assert callable(repair_runtime._validate_repair_request)
    assert callable(repair_runtime._checkpoint_ready)


def test_runtime_fingerprint_includes_real_source_owners() -> None:
    required = {
        "tools/voxcpm2/clean_runtime_contract.py",
        "tools/voxcpm2/clean_production_core.py",
        "tools/voxcpm2/generic_project_runtime.py",
        "tools/voxcpm2/generic_direct_runtime.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
        "tools/voxcpm2/master_direct_russian_only.py",
        "services/speech_backends/voxcpm2.py",
    }
    active = set(clean_runtime_contract._RENDER_MODULES) | set(clean_runtime_contract._RELEASE_MODULES)
    assert required <= active
    retired_facades = {
        "tools/voxcpm2/generic_project_runtime/__init__.py",
        "tools/voxcpm2/generic_direct_runtime/__init__.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
        "tools/voxcpm2/generic_gemini_runtime/__init__.py",
    }
    assert active.isdisjoint(retired_facades)
    assert callable(clean_runtime_contract.build_fingerprints)


def test_worker_runtime_is_directly_owned() -> None:
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    source = Path(dub_health.__file__).read_text(encoding="utf-8")
    assert "from services.dub_worker import build_command" in source
