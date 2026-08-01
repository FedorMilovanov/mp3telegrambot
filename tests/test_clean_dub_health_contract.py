from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services.dub_worker_release import SOURCE_PROSODY_ROLE_POLICY, WORKER_RUNTIME
from services.speech_backends import (
    BACKEND_CONTRACT_POLICY,
    CONTROL_PLANE_POLICY,
    DEFAULT_BACKEND_ID,
    GENERATION_LENGTH_POLICY,
    GENERATION_LENGTH_REQUEST_POLICY,
    BackendAudioSpec,
    BackendGenerationLengthPlan,
    BackendGenerationLengthRequest,
    default_backend,
    select_production_backend,
)
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_clean_direct_runtime as direct_runtime
from tools.voxcpm2 import generic_project_runtime
from tools.voxcpm2 import source_prosody_policy


ROOT = Path(__file__).resolve().parents[1]


def test_quality_contract_accepts_current_strong_runtime_versions() -> None:
    ok, detail = dub_health._quality_contract(ROOT)

    assert ok, detail
    assert dub_health.QUALITY_CONTRACT_POLICY in detail
    assert CONTROL_PLANE_POLICY in detail
    assert GENERATION_LENGTH_POLICY in detail
    assert GENERATION_LENGTH_REQUEST_POLICY in detail
    assert "speech-backend" in detail
    assert "recipe-routing" in detail
    assert "runtime-safety" in detail
    assert "quality-runtime" in detail


def test_dub_health_checks_active_backend_and_safety_contracts() -> None:
    selection = select_production_backend(
        None,
        default_backend_id=DEFAULT_BACKEND_ID,
    )
    backend = default_backend()
    environment = backend.process_environment(
        {"threads": 1},
        base_environment={},
    ).as_dict()
    length_request = BackendGenerationLengthRequest(
        duration_budget=4.0,
        attempt=3,
        previous_output_durations=(1.0, 1.5),
    )
    plan = backend.plan_generation_length(
        BackendAudioSpec(16_000, 48_000, 0.08, 4096),
        length_request,
    )

    assert BACKEND_CONTRACT_POLICY.startswith("speech-backend-contract-v")
    assert CONTROL_PLANE_POLICY == "speech-backend-control-plane-v1"
    assert GENERATION_LENGTH_POLICY == "model-neutral-generation-length-plan-v1"
    assert GENERATION_LENGTH_REQUEST_POLICY == (
        "model-neutral-generation-length-request-v1"
    )
    assert selection.backend is backend
    assert backend.backend_id == "voxcpm2"
    assert backend.capabilities().missing() == ()
    assert isinstance(plan, BackendGenerationLengthPlan)
    assert plan.backend_id == backend.backend_id
    assert plan.duration_budget == length_request.duration_budget
    assert plan.attempt == length_request.attempt
    assert plan.backend_options
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["TRANSFORMERS_OFFLINE"] == "1"
    assert generic_project_runtime.POLICY == "generic-project-runtime-write-through-v4"
    assert direct_runtime.CHECKPOINT_MIGRATION_POLICY == (
        "signature-and-natural-tempo-checkpoint-adoption-v2"
    )
    assert source_prosody_policy.POLICY == SOURCE_PROSODY_ROLE_POLICY
    assert callable(repair_runtime._validate_repair_request)
    assert callable(repair_runtime._checkpoint_ready)
    assert callable(repair_runtime._delay_evidence)


def test_dub_health_keeps_environment_and_worker_checks() -> None:
    source = (ROOT / "handlers" / "dub_health.py").read_text(encoding="utf-8")
    labels = (
        "Recipe: Gemini MAX",
        "Recipe: готовый SRT",
        "Recipe: чистый аудиоремонт",
        "Whisper semantic QA",
        "SoundFile WAV I/O",
        "VoxCPM2 CPU Python",
        "VoxCPM2 archive",
        "Gemini MAX keys",
        "Dub Studio storage",
        "Worker",
        "Python UTF-8",
    )
    for label in labels:
        assert label in source
    assert 'for binary in ("ffmpeg", "ffprobe")' in source
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_health._legacy._WORKER_RUNTIME == WORKER_RUNTIME


def test_runtime_fingerprint_includes_active_facades() -> None:
    source = (
        ROOT / "tools" / "voxcpm2" / "clean_runtime_contract" / "__init__.py"
    ).read_text(encoding="utf-8")
    required = (
        "services/speech_backends/__init__.py",
        "services/speech_backends/base.py",
        "services/speech_backends/control_plane.py",
        "services/speech_backends/registry.py",
        "services/speech_backends/voxcpm2.py",
        "tools/voxcpm2/clean_runtime_contract/__init__.py",
        "tools/voxcpm2/clean_production_core/__init__.py",
        "tools/voxcpm2/generic_project_runtime/__init__.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py",
        "tools/voxcpm2/direct_max_quality_cli/__init__.py",
        "tools/voxcpm2/direct_max_quality_render/__init__.py",
        "tools/voxcpm2/final_media_qa/__init__.py",
    )
    for marker in required:
        assert marker in source
    assert callable(clean_runtime_contract.build_fingerprints)
