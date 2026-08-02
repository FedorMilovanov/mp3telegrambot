from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services import dub_studio_runtime
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
from tools.voxcpm2 import dub_worker_hardened


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
    assert Path(dub_worker_hardened.__file__).name == "__init__.py"
    assert dub_worker_hardened._RUNTIME_VERSION == WORKER_RUNTIME
    assert dub_worker_hardened._legacy._RUNTIME_VERSION == WORKER_RUNTIME
    assert dub_worker_hardened.CANCELLATION_POLICY == "preflight-cancel-before-runner-v1"
    assert dub_worker_hardened.JOB_QUALITY_RETRY_POLICY == (
        "worker-checkpoint-quality-restart-v1"
    )
    assert dub_worker_hardened.MAX_JOB_QUALITY_RESTARTS == 3
    assert callable(dub_worker_hardened._execute_job_with_cancellable_preflight)
    assert callable(dub_worker_hardened._run_with_quality_restarts)
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
    for check in (
        dub_health._backend_contract,
        dub_health._recipe_contract,
        dub_health._worker_contract,
        dub_health._runtime_safety_contract,
        dub_health._quality_runtime_contract,
    ):
        ok, detail = check()
        assert ok, f"{check.__name__}: {detail}"

    source = (
        ROOT / "tools" / "voxcpm2" / "clean_runtime_contract" / "__init__.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "services/speech_backends/base.py",
        "services/speech_backends/control_plane.py",
        "tools/voxcpm2/generic_project_runtime/__init__.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
        "tools/voxcpm2/direct_max_quality_render/__init__.py",
        "tools/voxcpm2/final_media_qa/__init__.py",
    ):
        assert marker in source
    assert callable(clean_runtime_contract.build_fingerprints)
