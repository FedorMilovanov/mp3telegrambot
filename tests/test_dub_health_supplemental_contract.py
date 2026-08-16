from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services import dub_studio_runtime, dub_worker
from services.dub_release_health_v64 import _v68_quality_contract
from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import clean_production_core
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import direct_max_quality_io
from tools.voxcpm2 import direct_max_quality_render
from tools.voxcpm2 import direct_retry_epoch
from tools.voxcpm2 import direct_timeline_delivery_qa
from tools.voxcpm2 import final_encoded_delivery_qa
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_project_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_active_health_requires_repair_and_project_barriers() -> None:
    assert callable(repair_runtime._validate_repair_request)
    assert callable(repair_runtime._validated_sha256)
    assert callable(repair_runtime._checkpoint_ready)
    assert callable(repair_runtime._delay_evidence)
    assert callable(repair_runtime._load_segments)
    assert callable(generic_project_runtime.validate_request_payload)
    assert callable(generic_project_runtime.save_json)
    assert generic_project_runtime.POLICY == "generic-project-runtime-write-through-v4"


def test_active_health_requires_current_worker_release() -> None:
    assert dub_worker.WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_worker.CANCELLATION_POLICY == "preflight-cancel-before-runner-v1"
    assert dub_worker.JOB_QUALITY_RETRY_POLICY == (
        "worker-checkpoint-quality-restart-v1"
    )
    assert dub_worker.MAX_JOB_QUALITY_RESTARTS == 3
    assert callable(dub_worker.execute_job)
    assert callable(dub_worker._run_with_quality_restarts)
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME


def test_active_health_requires_long_form_delivery_resilience() -> None:
    assert direct_max_quality_io.SPEECH_SLOT_POLICY == "exact-srt-slot-minus-tail-v1"
    assert callable(direct_max_quality_io.speech_slot_seconds)
    assert direct_max_quality_render.ADAPTIVE_RETRY_POLICY == (
        "stable-identity-candidate-retry-v2"
    )
    assert direct_retry_epoch.POLICY == "failed-segment-seed-epoch-scope-v2"
    assert callable(direct_retry_epoch.invalidate_segment_for_retry)
    assert direct_timeline_delivery_qa.POLICY == "assembled-monolithic-voice-v1"
    assert callable(direct_timeline_delivery_qa.verify_timeline_delivery)
    assert final_encoded_delivery_qa.POLICY == "post-aac-russian-delivery-v2"
    assert callable(final_encoded_delivery_qa.verify_final_encoded_russian)
    assert clean_production_core.DELIVERY_RETRY_POLICY == (
        "bounded-checkpointed-delivery-retry-v1"
    )


def test_active_health_composes_and_fingerprints_current_contracts() -> None:
    ok, detail = dub_health._quality_contract(ROOT)
    assert ok, detail
    ok, detail = _v68_quality_contract(ROOT)
    assert ok, detail

    assert clean_runtime_contract.POLICY == "clean-runtime-contract-v2"
    render_modules = set(clean_runtime_contract._RENDER_MODULES)
    release_modules = set(clean_runtime_contract._RELEASE_MODULES)
    for marker in (
        "tools/voxcpm2/generic_project_runtime.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
        "tools/voxcpm2/direct_max_quality_render.py",
    ):
        assert marker in render_modules
    assert "tools/voxcpm2/final_media_qa.py" in release_modules
    assert callable(clean_runtime_contract.build_fingerprints)
