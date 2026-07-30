from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_supplemental_health_requires_full_repair_chain() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"strict-repair-request"',
        "def _validate_repair_request(",
        "def _validated_sha256(",
        "изменился после создания repair request",
        "manifest.audio_repairs должен быть списком",
        "_legacy._checkpoint_ready = _checkpoint_ready",
        "_legacy.legacy_repair._load_segments = _load_segments",
        '"serialized-repair-handler"',
        "_DUBFIX_LOCK = asyncio.Lock()",
        "async with _DUBFIX_LOCK",
        "os.O_CREAT | os.O_EXCL | os.O_WRONLY",
        '"transactional-repair-preprocess"',
        "strict_core._mark_and_validate_segments(",
        "allow_nan=False",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_requires_source_segment_and_full_preflight_identity() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"canonical-source-identity"',
        "Project request и скачиваемый YouTube-ролик имеют разные video ID",
        "clean_source_download._url_video_id(raw)",
        '"strict-segment-preflight"',
        "_legacy._mark_and_validate_segments = _mark_and_validate_segments",
        '"production-preflight-v2"',
        "generic_project_runtime.load_request(root)",
        "def _implementation_identity(",
        "PREFLIGHT_HEARTBEAT_SECONDS = 5.0",
        "def _preflight_heartbeat(",
        "clean_runtime_contract._model_manifest(",
        "clean_runtime_contract._voxcpm_runtime(",
        "uuid.uuid4().hex",
        "os.fsync(handle.fileno())",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_requires_clean_adapter_and_wizard_barriers() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"atomic-project-request"',
        'POLICY = "generic-project-runtime-write-through-v2"',
        "def validate_request_payload(",
        "class _WriteThroughModule",
        "_module.__class__ = _WriteThroughModule",
        "_legacy.validate_request_payload = validate_request_payload",
        "def _write_request(",
        "generic_project_runtime.validate_request_payload(payload)",
        "generic_project_runtime.save_json(destination, validated)",
        "_legacy._write_request = _write_request",
        "_legacy._create_generic_project = _create_generic_project",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_requires_cancellation_safe_worker_package() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"worker_facade"',
        '"worker_main"',
        'CANCELLATION_POLICY = "preflight-cancel-before-runner-v1"',
        'STORE_ROOT_POLICY = "explicit-worker-root-propagation-v2"',
        'DELIVERY_RESILIENCE_POLICY = "cadence-tail-fit-adaptive-resume-v1"',
        "def _execute_job_with_cancellable_preflight(",
        "with _store_root_environment(store):",
        "reason = _stop_reason(store, job_id)",
        "_run_with_quality_restarts(store, worker_id, job, project)",
        'JOB_QUALITY_RETRY_POLICY = "worker-checkpoint-quality-restart-v1"',
        "MAX_JOB_QUALITY_RESTARTS = 3",
        "_legacy.install_hardening()",
        "_legacy.worker.execute_job = _execute_job_with_cancellable_preflight",
        "from . import main",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_replaces_only_superseded_worker_v45_check() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "def _legacy_quality_without_superseded_worker(" in facade
    assert 'item != "worker-v45"' in facade
    assert '"dub-worker-quality-v4.8"' in facade
    assert "_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME" in facade
    assert "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME" in facade
    assert "def _v47_static_contract(" in facade
    assert "worker.execute_job = _execute_job_with_preflight" in facade


def test_supplemental_health_requires_long_form_delivery_resilience() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    required = (
        '"long-form-direct-resilience"',
        'SPEECH_SLOT_POLICY = "exact-srt-slot-minus-tail-v1"',
        "MIN_SPEECH_SLOT_SECONDS = 0.12",
        "def speech_slot_seconds(",
        'FIT_TEMPO_POLICY = "candidate-fit-tempo-hard-gate-v2"',
        'candidate.get("actual_speech_slot", speech_slot)',
        "speech_slot = speech_slot_seconds(target_duration, tail_guard)",
        'ADAPTIVE_RETRY_POLICY = "direct-candidate-adaptive-retry-v1"',
        'POLICY = "failed-segment-seed-epoch-v1"',
        "SEED_EPOCH_STRIDE = 1_000_000_000_000",
        "def load_retry_epoch(",
        "def seed_for_attempt(",
        "def invalidate_segment_for_retry(",
        "load_retry_epoch(work_dir, segment_id)",
        '"retry_epoch": retry_epoch',
        'DELIVERY_POLICY = "russian-ending-and-source-emphasis-hard-gate-v2"',
        '_SOURCE_PEAK_MIN_DOMINANCE = 0.18',
        'failures.append("fit_tempo_exceeds_hard_limit")',
        'failures.append("source_emphasis_misplaced_early")',
        "source_peak_dominance",
        "MAX_CANDIDATE_ATTEMPTS = 5",
        'POLICY = "assembled-russian-delivery-v3"',
        "LINKED_MAX_GAP_SECONDS = 0.55",
        '"linked_phrase_gap"',
        "seed epochs",
        "invalidated_for_retry",
        'POLICY = "late-broadband-tail-v2"',
        'POLICY = "post-aac-russian-delivery-v2"',
        "MAX_SEGMENT_WINDOW_SECONDS = 30.0",
        "def verify_final_encoded_russian(",
        'CHILD_PYTHON_POLICY = "repo-root-pythonpath-master-stderr-and-post-aac-v2"',
        "def _is_master_release_command(",
        "final_encoded_delivery_qa.verify_final_encoded_russian(",
        'DELIVERY_RETRY_POLICY = "bounded-checkpointed-delivery-retry-v1"',
        "MAX_AUTOMATIC_DELIVERY_RETRIES = 3",
        "def _retryable_delivery_failure(",
        "def _direct_failure_report(",
        "_legacy_render_and_master = _legacy.render_and_master",
        "def render_and_master(",
    )
    for item in required:
        assert item in facade


def test_supplemental_health_requires_write_through_service_hooks() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert '"supervisor_facade"' in facade
    assert '"title_facade"' in facade
    assert "class _WriteThroughModule" in facade
    assert "_module.__class__ = _WriteThroughModule" in facade
    assert "_legacy._patch_health = _patch_health" in facade
    assert "legacy_health.collect_dub_health = wrapped" in facade


def test_supplemental_health_composes_base_v47_and_facades() -> None:
    facade = (ROOT / "handlers" / "dub_health" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "base_ok, base_detail = _legacy_quality_without_superseded_worker(repo)" in facade
    assert "v47_ok, v47_detail = _v47_static_contract(repo)" in facade
    assert "supplemental_ok, supplemental_detail = _supplemental_quality_contract(repo)" in facade
    assert "bool(base_ok and v47_ok and supplemental_ok)" in facade
    assert "_legacy._quality_contract = _quality_contract" in facade
