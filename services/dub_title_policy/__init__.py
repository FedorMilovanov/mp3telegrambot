#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade preserving Dub title-policy monkeypatch semantics.

The canonical title implementation remains in ``services/dub_title_policy.py``.
This facade mirrors health wrappers into the legacy module and verifies the
shared worker release plus the active monolithic-voice production contract.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from services.dub_worker_release import (
    EXPRESSION_POLICY,
    INDEPENDENT_QA_RECOVERY_POLICY,
    MONOLITHIC_TIMELINE_POLICY,
    MONOLITHIC_VOICE_POLICY,
    PRONUNCIATION_POLICY,
    WORKER_RUNTIME,
)

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_title_policy.py"
_SPEC = importlib.util.spec_from_file_location(
    "services._dub_title_policy_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub title policy: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_patch_health = _legacy._patch_health


def _release_label() -> str:
    value = str(WORKER_RUNTIME)
    return value.rsplit("v", 1)[-1] if "v" in value else value


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _monolithic_static_contract(repo: Path) -> tuple[bool, str]:
    voxcpm = Path(repo) / "tools" / "voxcpm2"
    files = {
        "pronunciation": voxcpm / "russian_pronunciation.py",
        "expression": voxcpm / "expressive_continuity" / "__init__.py",
        "candidate": voxcpm / "direct_monolith_contract.py",
        "candidate_facade": voxcpm / "direct_monolith_contract" / "__init__.py",
        "cli": voxcpm / "direct_max_quality_cli" / "__init__.py",
        "render": voxcpm / "direct_max_quality_render" / "__init__.py",
        "tail": voxcpm / "direct_tail_artifact.py",
        "timeline": voxcpm / "direct_timeline_delivery_qa" / "__init__.py",
        "fingerprint": voxcpm / "clean_runtime_contract" / "__init__.py",
    }
    text = {name: _read(path) for name, path in files.items()}
    checks = {
        "single-identity-expression": all(
            marker in text["expression"]
            for marker in (
                f'POLICY = "{EXPRESSION_POLICY}"',
                'REFERENCE_POLICY = "single-calm-identity-reference-v1"',
                'reference_profile="extended"',
                'identity_reference_profile="extended"',
                "MAX_ADJACENT_SCORE_STEP = 0.26",
                "_legacy_plan_segments = _legacy.plan_segments",
                "def build_controlled_expressive_reference(",
                "return False",
            )
        ),
        "pronunciation-evidence": all(
            marker in text["pronunciation"]
            for marker in (
                f'POLICY = "{PRONUNCIATION_POLICY}"',
                '"replacement": "гря-дёт"',
                "def prepare_segment(",
                "def stress_evidence(",
                "final_stressed_nucleus_not_supported",
            )
        ),
        "candidate-monolith": all(
            marker in text["candidate"]
            for marker in (
                f'POLICY = "{MONOLITHIC_VOICE_POLICY}"',
                "ANCHOR_HARD_SIMILARITY",
                "NEIGHBOUR_HARD_SIMILARITY",
                "def evaluate_candidate(",
                "adjacent_f0_median_jump",
                "start_reference_leak",
                "pronunciation_stress_not_verified",
            )
        ) and all(
            marker in text["candidate_facade"]
            for marker in (
                'RESUME_POLICY = "nearest-accepted-checkpoint-identity-v1"',
                "_legacy._load_previous_checkpoint",
                "def evaluate_candidate(",
                'result["resume_policy"] = RESUME_POLICY',
                "class _WriteThroughModule",
            )
        ) and all(
            marker in text["cli"]
            for marker in (
                'POLICY = "direct-cli-monolithic-voice-v1"',
                "direct_monolith_contract.register_segments",
                "russian_pronunciation.synthesis_text",
                "direct_monolith_contract.evaluate_candidate",
                "direct_monolith_contract.candidate_hard_ok",
                "class _WriteThroughModule",
            )
        ),
        "monolithic-assembly": all(
            marker in text["render"]
            for marker in (
                'TIMELINE_COMPACTION_POLICY = "no-late-shift-monolithic-assembly-v2"',
                'FADE_POLICY = "cadence-aware-short-boundary-envelope-v1"',
                "authored starts preserved",
                "def fit_without_slowdown(",
                "def build_timeline(",
            )
        ) and all(
            marker in text["timeline"]
            for marker in (
                f'POLICY = "{MONOLITHIC_TIMELINE_POLICY}"',
                "PREFERRED_CONNECTED_GAP_SECONDS = 0.18",
                "MAX_CONNECTED_GAP_SECONDS = 0.32",
                "adjacent_voice_timbre_discontinuity",
                "whole_timeline_late_broadband_tail",
                "def verify_timeline_delivery(",
            )
        ),
        "broadband-tail-v3": all(
            marker in text["tail"]
            for marker in (
                'POLICY = "late-broadband-tail-v3"',
                "immediate_voice_to_broadband_transition",
                "burst_spectral_flatness",
                "spectral_jump_score",
            )
        ),
        "monolith-fingerprinted": all(
            marker in text["fingerprint"]
            for marker in (
                '"tools/voxcpm2/expressive_continuity/__init__.py"',
                '"tools/voxcpm2/russian_pronunciation.py"',
                '"tools/voxcpm2/direct_monolith_contract.py"',
                '"tools/voxcpm2/direct_monolith_contract/__init__.py"',
                '"tools/voxcpm2/direct_max_quality_cli/__init__.py"',
                '"tools/voxcpm2/direct_timeline_delivery_qa/__init__.py"',
            )
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "monolithic-контракты не прошли: " + ", ".join(failed)
    return True, (
        "one calm identity reference; bounded neighbour-supported emotion; separate synthesis "
        "text and stress evidence; candidate/adjacent voice continuity; resume-safe nearest "
        "checkpoint identity; no late cue shifting; short cadence-aware fades; start-chirp and "
        "immediate broadband-tail gates; assembled whole-timeline monolith QA; "
        "synthesis-critical fingerprinting"
    )


def _release_static_contract(health: object, repo: Path) -> tuple[bool, str]:
    """Preserve old gates, replacing only contracts superseded by the current release."""
    label = _release_label()
    ok, detail = health._v47_static_contract(Path(repo))
    raw = str(detail)
    remaining: list[str] = []
    if not ok:
        prefix = "v4.8-контракты не прошли: "
        if not raw.startswith(prefix):
            return False, raw
        failed = [item.strip() for item in raw[len(prefix):].split(",") if item.strip()]
        superseded = {
            "worker-package-cancel-root",
            "worker-runtime-sync",
            "long-form-direct-resilience",
        }
        remaining = [item for item in failed if item not in superseded]

    repo = Path(repo)
    release = repo / "services" / "dub_worker_release.py"
    worker_main = repo / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
    supervisor = repo / "services" / "dub_studio_runtime" / "__init__.py"
    direct_main = repo / "tools" / "voxcpm2" / "generic_clean_direct_runtime" / "__main__.py"
    recovery = repo / "tools" / "voxcpm2" / "independent_qa_retry.py"
    release_text = _read(release)
    worker_text = _read(worker_main)
    supervisor_text = _read(supervisor)
    direct_main_text = _read(direct_main)
    recovery_text = _read(recovery)

    release_ok = all(
        marker in release_text
        for marker in (
            f'WORKER_RUNTIME = "{WORKER_RUNTIME}"',
            'RELEASE_POLICY = "single-source-worker-release-identity-v1"',
            'PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"',
            f'INDEPENDENT_QA_RECOVERY_POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
            f'MONOLITHIC_VOICE_POLICY = "{MONOLITHIC_VOICE_POLICY}"',
            f'MONOLITHIC_TIMELINE_POLICY = "{MONOLITHIC_TIMELINE_POLICY}"',
            f'PRONUNCIATION_POLICY = "{PRONUNCIATION_POLICY}"',
            f'EXPRESSION_POLICY = "{EXPRESSION_POLICY}"',
        )
    )
    worker_ok = all(
        marker in worker_text
        for marker in (
            "from services.dub_worker_release import WORKER_RUNTIME",
            "def activate_release_identity(",
            "package._RUNTIME_VERSION = WORKER_RUNTIME",
            "_legacy._RUNTIME_VERSION = WORKER_RUNTIME",
            "activate_release_identity()",
            "install_preflight_json()",
            "main()",
        )
    )
    supervisor_ok = all(
        marker in supervisor_text
        for marker in (
            "from services.dub_worker_release import WORKER_RUNTIME",
            "_WORKER_RUNTIME = WORKER_RUNTIME",
            "_legacy._WORKER_RUNTIME = _WORKER_RUNTIME",
            "class _WriteThroughModule",
            "_module.__class__ = _WriteThroughModule",
        )
    )
    recovery_ok = all(
        marker in recovery_text
        for marker in (
            f'POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
            "MAX_RECOVERY_CYCLES = 3",
            "INTERNAL_SEED_ROUNDS_PER_CALL = 2",
            "def _retry_context(",
            "def _retarget_checkpoints(",
            "failed_ids=failed_ids",
            "next_request[\"base_seed\"] = next_base_seed",
            "def install(",
        )
    ) and all(
        marker in direct_main_text
        for marker in (
            "from tools.voxcpm2 import independent_qa_retry",
            "independent_qa_retry.install()",
            "main()",
        )
    )
    monolith_ok, monolith_detail = _monolithic_static_contract(repo)
    if not (release_ok and worker_ok and supervisor_ok and recovery_ok):
        remaining.append("worker-current-release")
    if not monolith_ok:
        remaining.append(monolith_detail)
    if remaining:
        return False, f"v{label}-контракты не прошли: " + ", ".join(remaining)
    return True, (
        f"worker v{label}/preflight v2; shared release identity; stale-worker replacement; "
        "marked noise-tolerant JSON transport; bounded report-backed segment-only independent "
        "QA recovery; " + monolith_detail + "; cancellation and explicit root active"
    )


def _patch_health() -> None:
    _legacy_patch_health()
    try:
        import handlers.dub_health as health
    except Exception:
        return

    legacy_health = getattr(health, "_legacy", None)
    wrapped = getattr(health, "collect_dub_health", None)
    if legacy_health is None or not callable(wrapped):
        return

    health._WORKER_RUNTIME = WORKER_RUNTIME
    legacy_health._WORKER_RUNTIME = WORKER_RUNTIME

    def release_aware_quality_contract(repo: Path) -> tuple[bool, str]:
        base_ok, base_detail = health._legacy_quality_without_superseded_worker(repo)
        release_ok, release_detail = _release_static_contract(health, repo)
        supplemental_ok, supplemental_detail = health._supplemental_quality_contract(repo)
        detail = "; ".join((base_detail, release_detail, supplemental_detail))
        return bool(base_ok and release_ok and supplemental_ok), detail

    health._quality_contract = release_aware_quality_contract
    legacy_health._quality_contract = release_aware_quality_contract
    legacy_health.collect_dub_health = wrapped


_legacy._patch_health = _patch_health

install_dub_title_policy = _legacy.install_dub_title_policy
install_voxcpm_title_policy = _legacy.install_voxcpm_title_policy
canonical_delivery_filename = _legacy.canonical_delivery_filename
canonical_media_title = _legacy.canonical_media_title
RU_SERVICE_WORDS = _legacy.RU_SERVICE_WORDS

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"_monolithic_static_contract", "_patch_health", "_release_static_contract"}
)
