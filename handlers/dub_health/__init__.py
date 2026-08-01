#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Active-contract facade for the durable Dub Studio health command.

Environment, executable and worker-liveness checks remain in the sibling
``handlers/dub_health.py`` module.  This facade replaces its historical
source-string release gate with behavioural checks against the modules Python
actually imports.  A refactor can therefore move implementation details
without making ``/dubcheck`` lie, while missing capabilities, routes or safety
transactions still fail closed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from services.dub_worker_release import WORKER_RUNTIME

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_health.py"
_SPEC = importlib.util.spec_from_file_location(
    "handlers._dub_health_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый Dub health: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_previous_legacy = sys.modules.get(_SPEC.name)
sys.modules[_SPEC.name] = _legacy
try:
    _SPEC.loader.exec_module(_legacy)
except BaseException:
    if _previous_legacy is None:
        sys.modules.pop(_SPEC.name, None)
    else:
        sys.modules[_SPEC.name] = _previous_legacy
    raise

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_WORKER_RUNTIME = WORKER_RUNTIME
from services import dub_studio_runtime as _supervisor  # noqa: E402

_supervisor._WORKER_RUNTIME = _WORKER_RUNTIME
_supervisor._legacy._WORKER_RUNTIME = _WORKER_RUNTIME
_legacy._WORKER_RUNTIME = _WORKER_RUNTIME

QUALITY_CONTRACT_POLICY = "active-dub-production-contract-v1"


def _module_path(module: Any) -> Path:
    value = getattr(module, "__file__", None)
    return Path(value).resolve() if value else Path()


def _package_facade(module: Any) -> bool:
    path = _module_path(module)
    return path.name == "__init__.py" and path.is_file()


def _record(
    checks: dict[str, tuple[bool, str]],
    name: str,
    predicate: bool,
    detail: str,
) -> None:
    checks[name] = (bool(predicate), str(detail))


def _safe_check(
    checks: dict[str, tuple[bool, str]],
    name: str,
    callback: Callable[[], tuple[bool, str]],
) -> None:
    try:
        ok, detail = callback()
    except Exception as exc:
        checks[name] = (False, f"{type(exc).__name__}: {exc}")
    else:
        checks[name] = (bool(ok), str(detail))


def _backend_contract() -> tuple[bool, str]:
    from services.speech_backends import (
        BACKEND_CONTRACT_POLICY,
        GENERATION_REQUEST_POLICY,
        SESSION_CONFIG_POLICY,
        BackendAudioSpec,
        BackendGenerationRequest,
        REQUIRED_PRODUCTION_CAPABILITIES,
        backend_ids,
        default_backend,
    )

    backend = default_backend()
    capabilities = backend.capabilities()
    missing = capabilities.missing(REQUIRED_PRODUCTION_CAPABILITIES)
    neutral_spec = BackendAudioSpec(
        encode_sample_rate=None,
        output_sample_rate=48_000,
        seconds_per_step=None,
        cache_length=None,
    )
    request = BackendGenerationRequest(
        text="Проверка backend-контракта.",
        reference_audio=Path("reference.wav"),
        seed=1,
        backend_options={},
    )
    ok = bool(
        BACKEND_CONTRACT_POLICY == "speech-backend-contract-v2"
        and GENERATION_REQUEST_POLICY == "model-neutral-generation-request-v1"
        and SESSION_CONFIG_POLICY == "model-neutral-session-config-v1"
        and backend_ids() == ("voxcpm2",)
        and backend.backend_id == "voxcpm2"
        and not missing
        and neutral_spec.output_sample_rate == 48_000
        and request.text
    )
    detail = (
        f"{BACKEND_CONTRACT_POLICY}; backend={backend.backend_id}; "
        f"missing={list(missing)}"
    )
    return ok, detail


def _recipe_contract() -> tuple[bool, str]:
    from services.dub_studio import load_recipe

    recipe = load_recipe("generic_short_v1")
    expected = {
        "render": "tools.voxcpm2.generic_clean_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_clean_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_clean_direct_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
        "prepare_custom": "tools.voxcpm2.generic_clean_custom_runtime",
        "render_custom": "tools.voxcpm2.generic_clean_custom_runtime",
    }
    actual = {
        name: str(recipe.action(name).get("module") or "")
        for name in expected
    }
    runners = {
        name: str(recipe.action(name).get("runner") or "")
        for name in expected
    }
    ok = actual == expected and set(runners.values()) == {"python_module"}
    return ok, f"routes={actual}"


def _worker_contract() -> tuple[bool, str]:
    from tools.voxcpm2 import dub_worker_hardened

    values = {
        "release": WORKER_RUNTIME,
        "health": _WORKER_RUNTIME,
        "supervisor": _supervisor._WORKER_RUNTIME,
        "supervisor_legacy": _supervisor._legacy._WORKER_RUNTIME,
        "worker": dub_worker_hardened._RUNTIME_VERSION,
        "worker_legacy": dub_worker_hardened._legacy._RUNTIME_VERSION,
    }
    ok = len(set(values.values())) == 1 and WORKER_RUNTIME.startswith(
        "dub-worker-quality-v"
    )
    return ok, ", ".join(f"{key}={value}" for key, value in values.items())


def _runtime_safety_contract() -> tuple[bool, str]:
    from services.speech_backends import default_backend
    from tools.voxcpm2 import clean_runtime_contract
    from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair
    from tools.voxcpm2 import generic_clean_direct_runtime as direct
    from tools.voxcpm2 import generic_project_runtime as project
    from tools.voxcpm2 import source_prosody_policy
    from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
        voxcpm2_cpu_shorts_production as wrapper,
    )

    environment = default_backend().process_environment(
        {"threads": 1},
        base_environment={},
    ).as_dict()
    required_callables = (
        clean_runtime_contract.build_fingerprints,
        project.validate_request_payload,
        project.save_json,
        repair._validate_repair_request,
        repair._checkpoint_ready,
        repair._delay_evidence,
        direct._signature_valid_checkpoint_set,
        source_prosody_policy.ranking_view,
        wrapper.run,
    )
    ok = bool(
        all(callable(value) for value in required_callables)
        and project.POLICY == "generic-project-runtime-write-through-v3"
        and direct.CHECKPOINT_MIGRATION_POLICY
        == "signature-and-natural-tempo-checkpoint-adoption-v2"
        and environment.get("HF_HUB_OFFLINE") == "1"
        and environment.get("TRANSFORMERS_OFFLINE") == "1"
        and wrapper.MARKER_POLICY == "direct-cli-runtime-marker-v2"
        and wrapper.SUCCESS_MARKER_POLICY == "direct-cli-success-marker-v1"
    )
    return ok, (
        f"project={project.POLICY}; checkpoints={direct.CHECKPOINT_MIGRATION_POLICY}; "
        f"marker={wrapper.MARKER_POLICY}; offline="
        f"{environment.get('HF_HUB_OFFLINE')}/{environment.get('TRANSFORMERS_OFFLINE')}"
    )


def _quality_runtime_contract() -> tuple[bool, str]:
    from services.speech_backends import default_backend
    from tools.voxcpm2 import direct_max_quality_cli
    from tools.voxcpm2 import direct_max_quality_io
    from tools.voxcpm2 import direct_max_quality_render
    from tools.voxcpm2 import direct_timeline_delivery_qa
    from tools.voxcpm2 import final_media_qa
    from tools.voxcpm2 import generic_clean_audio_repair_runtime
    from tools.voxcpm2 import generic_clean_direct_runtime
    from tools.voxcpm2 import generic_project_runtime
    from tools.voxcpm2 import source_prosody_policy

    facades = (
        direct_max_quality_cli,
        direct_max_quality_render,
        final_media_qa,
        generic_clean_audio_repair_runtime,
        generic_clean_direct_runtime,
        generic_project_runtime,
    )
    backend = default_backend()
    ok = bool(
        all(_package_facade(module) for module in facades)
        and direct_max_quality_io.PREFERRED_MAX_TEMPO
        <= direct_max_quality_io.MAX_TEMPO
        and direct_max_quality_io.MAX_TEMPO <= 1.50
        and callable(direct_max_quality_io.speech_slot_seconds)
        and callable(direct_max_quality_cli._backend_generate)
        and callable(direct_max_quality_render.fit_without_slowdown)
        and callable(direct_timeline_delivery_qa.verify_timeline_delivery)
        and callable(final_media_qa.verify_final_media)
        and bool(getattr(backend.capabilities(), "continuation_context", False))
        and direct_max_quality_cli.CONTINUATION_POLICY
        == "backend-capability-gated-previous-block-prompt-v2"
        and source_prosody_policy.POLICY
        == "source-language-prosody-diagnostic-only-v1"
    )
    return ok, (
        f"tempo={direct_max_quality_io.PREFERRED_MAX_TEMPO}/"
        f"{direct_max_quality_io.MAX_TEMPO}; "
        f"continuation={direct_max_quality_cli.CONTINUATION_POLICY}; "
        f"source_prosody={source_prosody_policy.POLICY}; "
        f"delivery={direct_timeline_delivery_qa.POLICY}"
    )


def _quality_contract(repo: Path) -> tuple[bool, str]:
    del repo  # Active imports are the source of truth for this checkout.
    checks: dict[str, tuple[bool, str]] = {}
    _safe_check(checks, "speech-backend", _backend_contract)
    _safe_check(checks, "recipe-routing", _recipe_contract)
    _safe_check(checks, "worker-release", _worker_contract)
    _safe_check(checks, "runtime-safety", _runtime_safety_contract)
    _safe_check(checks, "quality-runtime", _quality_runtime_contract)

    failed = [name for name, (ok, _detail) in checks.items() if not ok]
    details = "; ".join(
        f"{name}: {detail}" for name, (_ok, detail) in checks.items()
    )
    if failed:
        return False, (
            f"{QUALITY_CONTRACT_POLICY}; не прошли: {', '.join(failed)}; {details}"
        )
    return True, f"{QUALITY_CONTRACT_POLICY}; {details}"


_legacy._quality_contract = _quality_contract
collect_dub_health = _legacy.collect_dub_health
dubcheck_command = _legacy.dubcheck_command
register_dub_health_handler = _legacy.register_dub_health_handler

__all__ = [
    "QUALITY_CONTRACT_POLICY",
    "_WORKER_RUNTIME",
    "_backend_contract",
    "_quality_contract",
    "_quality_runtime_contract",
    "_recipe_contract",
    "_runtime_safety_contract",
    "_worker_contract",
    "collect_dub_health",
    "dubcheck_command",
    "register_dub_health_handler",
]
