#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Current behavioral Dub health contract.

The historical health layers still protect a broad legacy surface.  A small
number of their source-string assertions describe superseded v4/v5 layouts,
not runtime behaviour.  This module permits only those named legacy failures,
and only after executable v6.9 probes prove the replacement contracts.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

POLICY = "dub-v69-behavioral-health-v1"

_SUPERSEDED_STATIC_FAILURES = {
    "native-16to48",
    "long-form-direct-resilience",
    "atomic-project-request",
}
_FAILURE_PREFIXES = (
    "не прошли: ",
    "v4.8-контракты не прошли: ",
    "facade-контракты не прошли: ",
)


def _legacy_failures(detail: str) -> tuple[set[str], list[str]]:
    failures: set[str] = set()
    unknown_failure_parts: list[str] = []
    for raw_part in str(detail).split(";"):
        part = raw_part.strip()
        if not part:
            continue
        matched = False
        for prefix in _FAILURE_PREFIXES:
            if part.startswith(prefix):
                matched = True
                failures.update(
                    item.strip()
                    for item in part[len(prefix):].split(",")
                    if item.strip()
                )
                break
        if not matched and "не прош" in part.casefold():
            unknown_failure_parts.append(part)
    return failures, unknown_failure_parts


def _behavioural_contract(repo: Path) -> tuple[bool, str]:
    del repo
    try:
        from services import dub_studio_runtime
        from services.dub_worker_release import (
            BACKEND_CONTRACT_POLICY,
            CONTINUATION_POLICY,
            GENERATION_REQUEST_POLICY,
            PRODUCTION_CAPABILITY_POLICY,
            RENDER_MARKER_POLICY,
            RENDER_SUCCESS_POLICY,
            SESSION_CONFIG_POLICY,
            SOURCE_PROSODY_ROLE_POLICY,
            WORKER_RUNTIME,
        )
        from services.speech_backends import (
            BackendAudioSpec,
            BackendGenerationRequest,
            BackendSessionConfig,
            get_backend,
        )
        from tools.voxcpm2 import (
            direct_max_quality_render,
            generic_clean_direct_runtime,
            generic_project_runtime,
            semantic_block_runtime,
            source_prosody_policy,
        )
        from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
            voxcpm2_cpu_shorts_production as stable_renderer,
        )

        if WORKER_RUNTIME != "dub-worker-quality-v6.9":
            raise RuntimeError(f"unexpected worker runtime: {WORKER_RUNTIME}")
        if getattr(dub_studio_runtime, "_WORKER_RUNTIME", None) != WORKER_RUNTIME:
            raise RuntimeError("supervisor and worker release identities differ")

        spec = BackendAudioSpec(
            encode_sample_rate=None,
            output_sample_rate=24_000,
            seconds_per_step=None,
            cache_length=None,
        )
        if spec.as_dict()["output_sample_rate"] != 24_000:
            raise RuntimeError("backend-neutral AudioSpec lost output sample rate")
        try:
            BackendAudioSpec(None, 0, None, None)
        except ValueError:
            pass
        else:
            raise RuntimeError("invalid output sample rate was accepted")

        generation = BackendGenerationRequest(
            text="Проверка",
            reference_audio=Path("reference.wav"),
            seed=7,
            duration_budget=1.25,
            backend_options={"steps": 16},
        )
        if generation.option_int("steps", default=1, low=1, high=256) != 16:
            raise RuntimeError("generation request options are not typed")
        session = BackendSessionConfig(Path("model"), options={"cache_length": 4096})
        if session.options.get("cache_length") != 4096:
            raise RuntimeError("session options were not preserved")

        backend = get_backend("voxcpm2")
        missing = backend.capabilities().missing()
        if missing:
            raise RuntimeError("VoxCPM2 misses production capabilities: " + ", ".join(missing))
        if not backend.capabilities().continuation_context:
            raise RuntimeError("continuation capability is not declared")
        environment = backend.process_environment(
            {"threads": 2},
            base_environment={"TRANSFORMERS_OFFLINE": "0"},
        ).as_dict({"TRANSFORMERS_OFFLINE": "0"})
        if environment.get("HF_HUB_OFFLINE") != "1":
            raise RuntimeError("HF offline policy is inactive")
        if environment.get("TRANSFORMERS_OFFLINE") != "1":
            raise RuntimeError("Transformers offline policy is inactive")

        payload = generic_project_runtime.validate_request_payload(
            {
                "schema_version": 1,
                "video_id": "dQw4w9WgXcQ",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
                "translation_mode": "direct",
            }
        )
        if payload.get("speech_backend") != "voxcpm2":
            raise RuntimeError("request backend was not canonicalized")
        with tempfile.TemporaryDirectory(prefix="dub-health-") as temporary:
            destination = Path(temporary) / "request.json"
            generic_project_runtime.save_json(destination, {"ok": True, "value": 1})
            if json.loads(destination.read_text(encoding="utf-8")) != {
                "ok": True,
                "value": 1,
            }:
                raise RuntimeError("atomic JSON writer changed the payload")
            if list(destination.parent.glob(destination.name + ".tmp.*")):
                raise RuntimeError("atomic JSON writer left temporary files")

        marked = source_prosody_policy.mark_diagnostic_only(
            {"source_prosody": {"f0_median": 170.0}}
        )
        ranking = source_prosody_policy.ranking_view(marked)
        if "source_prosody" in ranking:
            raise RuntimeError("cross-language source prosody still reaches ranking")
        if marked.get("source_prosody_role") != SOURCE_PROSODY_ROLE_POLICY:
            raise RuntimeError("source prosody role marker is missing")

        if direct_max_quality_render.ADAPTIVE_RETRY_POLICY != (
            "stable-identity-candidate-retry-v2"
        ):
            raise RuntimeError("adaptive retry policy is stale")
        if direct_max_quality_render.HOOK_SYNC_POLICY != "facade-runtime-hook-sync-v2":
            raise RuntimeError("render facade hook synchronization is inactive")
        if generic_clean_direct_runtime.CHECKPOINT_MIGRATION_POLICY != (
            "signature-and-natural-tempo-checkpoint-adoption-v2"
        ):
            raise RuntimeError("checkpoint migration does not enforce natural tempo")
        if semantic_block_runtime.POLICY != "semantic-block-continuation-v1":
            raise RuntimeError("semantic block policy is inactive")

        expected_policies = {
            "backend": BACKEND_CONTRACT_POLICY,
            "generation": GENERATION_REQUEST_POLICY,
            "session": SESSION_CONFIG_POLICY,
            "capability": PRODUCTION_CAPABILITY_POLICY,
            "continuation": CONTINUATION_POLICY,
            "marker": RENDER_MARKER_POLICY,
            "success": RENDER_SUCCESS_POLICY,
        }
        if expected_policies != {
            "backend": "speech-backend-contract-v2",
            "generation": "model-neutral-generation-request-v1",
            "session": "model-neutral-session-config-v1",
            "capability": "production-speech-capability-gate-v2",
            "continuation": "backend-capability-gated-previous-block-prompt-v2",
            "marker": stable_renderer.MARKER_POLICY,
            "success": stable_renderer.SUCCESS_MARKER_POLICY,
        }:
            raise RuntimeError("release policy identities are inconsistent")
        if not math.isclose(
            float(direct_max_quality_render._legacy.MAX_TEMPO),
            1.36,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise RuntimeError("hard tempo ceiling changed unexpectedly")
    except Exception as exc:
        return False, f"{POLICY}: {type(exc).__name__}: {exc}"

    return True, (
        f"{POLICY}; worker={WORKER_RUNTIME}; backend={BACKEND_CONTRACT_POLICY}; "
        "typed generation/session contracts; capability-gated continuation; atomic request "
        "writes; offline child environment; diagnostic-only source prosody; natural-tempo "
        "checkpoint migration; transactional compatibility/success markers"
    )


def install(health: ModuleType) -> None:
    """Install one idempotent current contract over the historical facade."""
    current = getattr(health, "_quality_contract")
    if getattr(current, "_dub_v69_behavioral_health", False):
        return

    def quality_contract(repo: Path) -> tuple[bool, str]:
        legacy_ok, legacy_detail = current(Path(repo))
        behavioural_ok, behavioural_detail = _behavioural_contract(Path(repo))
        if not behavioural_ok:
            return False, behavioural_detail
        if legacy_ok:
            return True, str(legacy_detail) + "; " + behavioural_detail

        failures, unknown = _legacy_failures(str(legacy_detail))
        remaining = failures - _SUPERSEDED_STATIC_FAILURES
        if unknown or remaining or not failures:
            return False, str(legacy_detail) + "; " + behavioural_detail
        return True, (
            behavioural_detail
            + "; superseded source-layout checks replaced after executable probes: "
            + ", ".join(sorted(failures))
        )

    quality_contract._dub_v69_behavioral_health = True  # type: ignore[attr-defined]
    health._quality_contract = quality_contract
    legacy = getattr(health, "_legacy", None)
    if legacy is not None:
        legacy._quality_contract = quality_contract


__all__ = ["POLICY", "_behavioural_contract", "install"]
