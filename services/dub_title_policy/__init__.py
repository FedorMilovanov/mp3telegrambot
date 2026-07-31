#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade preserving Dub title-policy and truthful health semantics.

The canonical title implementation remains in ``services/dub_title_policy.py``.
This facade mirrors health wrappers into the legacy module and verifies the
shared worker release plus the active fail-closed monolithic production
contract. Cross-language source prosody is diagnostic only: it may not relax
speaker-identity gates or influence candidate ranking.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from services.dub_worker_release import (
    EXPRESSION_POLICY,
    FAIL_CLOSED_IDENTITY_POLICY,
    INDEPENDENT_QA_RECOVERY_POLICY,
    MASTER_MIX_POLICY,
    MONOLITHIC_TIMELINE_POLICY,
    MONOLITHIC_VOICE_POLICY,
    PRONUNCIATION_POLICY,
    PRONUNCIATION_VARIANT_POLICY,
    READY_SRT_GROUPING_POLICY,
    RUNTIME_ROUTING_POLICY,
    SEMANTIC_BLOCK_POLICY,
    SOURCE_PROSODY_ROLE_POLICY,
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    SOURCE_RELATIVE_CONTINUITY_POLICY,
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
sys.modules[_SPEC.name] = _legacy
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


def _all(text: str, *markers: str) -> bool:
    return bool(text) and all(marker in text for marker in markers)


def _monolithic_static_contract(repo: Path) -> tuple[bool, str]:
    repo = Path(repo)
    voxcpm = repo / "tools" / "voxcpm2"
    paths = {
        "grouping": voxcpm / "dub_quality_v4" / "__init__.py",
        "pronunciation": voxcpm / "russian_pronunciation.py",
        "expression": voxcpm / "expressive_continuity" / "__init__.py",
        "source_diagnostic": voxcpm / "direct_source_relative_continuity.py",
        "candidate": voxcpm / "direct_monolith_contract.py",
        "candidate_facade": voxcpm / "direct_monolith_contract" / "__init__.py",
        "cli": voxcpm / "direct_max_quality_cli" / "__init__.py",
        "render": voxcpm / "direct_max_quality_render" / "__init__.py",
        "tail": voxcpm / "direct_tail_artifact.py",
        "tail_facade": voxcpm / "direct_tail_artifact" / "__init__.py",
        "timeline": voxcpm / "direct_timeline_delivery_qa" / "__init__.py",
        "master": voxcpm / "master_monolithic_mix.py",
        "routing": voxcpm / "monolithic_runtime_install.py",
        "direct_main": voxcpm / "generic_clean_direct_runtime" / "__main__.py",
        "backend": repo / "services" / "speech_backends" / "voxcpm2.py",
        "fingerprint": voxcpm / "clean_runtime_contract" / "__init__.py",
    }
    text = {name: _read(path) for name, path in paths.items()}
    checks = {
        "semantic-breath-grouping": _all(
            text["grouping"],
            f'POLICY = "{READY_SRT_GROUPING_POLICY}"',
            "TARGET_SECONDS = 4.15",
            "MAX_INTERNAL_GAP_SECONDS = 0.38",
            "MAX_WORDS_PER_SECOND = 5.45",
            "def _protected_final_pronunciation(",
            "def _candidate(",
            "def group_ready_srt_v4(",
            'best_cost = [float("inf")]',
            "Semantic-breath grouping изменил текст готового SRT",
        ),
        "single-identity-expression": _all(
            text["expression"],
            f'POLICY = "{EXPRESSION_POLICY}"',
            'REFERENCE_POLICY = "single-calm-identity-reference-v1"',
            'reference_profile="extended"',
            'identity_reference_profile="extended"',
            "MAX_ADJACENT_SCORE_STEP = 0.26",
            "_legacy_plan_segments = _legacy.plan_segments",
            "def build_controlled_expressive_reference(",
            "return False",
        ),
        "bounded-pronunciation-evidence-v2": _all(
            text["pronunciation"],
            f'POLICY = "{PRONUNCIATION_POLICY}"',
            f'VARIANT_POLICY = "{PRONUNCIATION_VARIANT_POLICY}"',
            'STRESS_EVIDENCE_POLICY = "final-stressed-syllable-duration-energy-pitch-v2"',
            '"variants": ("грядёт", "гря-дёт")',
            '"manual_pronunciation_review_required": evidence_required',
            '"manual_review_required": True',
            '"provisional_acoustic_not_lexical_alignment"',
            "periodicity >= 0.20",
            "strong_cues >= 2",
            "def variant_for_attempt(",
            "def stress_evidence(",
            "final_stressed_nucleus_not_supported",
        ),
        "cross-language-diagnostic-only": _all(
            text["source_diagnostic"],
            f'POLICY = "{SOURCE_RELATIVE_CONTINUITY_POLICY}"',
            "ABSOLUTE_GATE_OVERRIDE_ALLOWED = False",
            "RANKING_PENALTY_ENABLED = False",
            '"role": "diagnostics_only_until_semantic_alignment"',
            '"absolute_gate_override_allowed": ABSOLUTE_GATE_OVERRIDE_ALLOWED',
            '"ranking_penalty_enabled": RANKING_PENALTY_ENABLED',
            '"penalty": 0.0',
            "raw_diagnostic_score",
            "def evaluate_transition(",
        ),
        "candidate-monolith": _all(
            text["candidate"],
            f'POLICY = "{MONOLITHIC_VOICE_POLICY}"',
            "ANCHOR_HARD_SIMILARITY",
            "NEIGHBOUR_HARD_SIMILARITY",
            "def evaluate_candidate(",
            "start_reference_leak",
            "pronunciation_stress_not_verified",
        ) and _all(
            text["candidate_facade"],
            'RESUME_POLICY = "nearest-accepted-checkpoint-identity-v1"',
            "SOURCE_RELATIVE_POLICY = direct_source_relative_continuity.POLICY",
            'START_VOICE_IMPLEMENTATION_POLICY = "short-island-before-sustained-voice-v3"',
            "direct_source_relative_continuity.evaluate_transition(",
            "def _apply_source_relative_transition(",
            "_legacy._load_previous_checkpoint",
            "def _start_artifact(",
            "def evaluate_candidate(",
            'result["resume_policy"] = RESUME_POLICY',
            "class _WriteThroughModule",
        ) and _all(
            text["cli"],
            'POLICY = "direct-cli-monolithic-voice-v1"',
            "PRONUNCIATION_VARIANT_POLICY = russian_pronunciation.VARIANT_POLICY",
            "direct_monolith_contract.register_segments",
            "russian_pronunciation.synthesis_text(segment, _CURRENT_ATTEMPT)",
            "russian_pronunciation.variant_for_attempt(",
            "direct_monolith_contract.evaluate_candidate",
            "direct_monolith_contract.candidate_hard_ok",
            "def _candidate_failure_summary(",
            "def _raw_failure_evidence(",
            "class _WriteThroughModule",
        ),
        "fail-closed-monolithic-assembly": _all(
            text["render"],
            'TIMELINE_COMPACTION_POLICY = "no-late-shift-monolithic-assembly-v2"',
            'FADE_POLICY = "cadence-aware-short-boundary-envelope-v1"',
            "authored starts preserved",
            "def fit_without_slowdown(",
            "def build_timeline(",
        ) and _all(
            text["timeline"],
            f'POLICY = "{MONOLITHIC_TIMELINE_POLICY}"',
            "SOURCE_RELATIVE_POLICY = direct_source_relative_continuity.POLICY",
            "PREFERRED_CONNECTED_GAP_SECONDS = 0.18",
            "MAX_CONNECTED_GAP_SECONDS = 0.32",
            "direct_source_relative_continuity.evaluate_transition(",
            "adjacent_voice_timbre_discontinuity",
            "whole_timeline_late_broadband_tail",
            "def verify_timeline_delivery(",
        ) and _all(
            text["routing"],
            f'POLICY = "{RUNTIME_ROUTING_POLICY}"',
            f'FAIL_CLOSED_IDENTITY_POLICY = "{FAIL_CLOSED_IDENTITY_POLICY}"',
            "ABSOLUTE_GLOBAL_F0_LIMIT_ST = 8.4",
            "ABSOLUTE_ADJACENT_F0_RATIO = (0.62, 1.62)",
            "ABSOLUTE_ADJACENT_P90_RATIO = (0.58, 1.72)",
            "def enforce_fail_closed_identity(",
            "def _install_fail_closed_timeline(",
            'transition["role"] = "ranking_and_diagnostics_only"',
            "_install_fail_closed_timeline()",
        ),
        "broadband-tail-v5": _all(
            text["tail"],
            'POLICY = "late-broadband-tail-v4"',
            "high_mask = audible_mask &",
            "embedded_terminal_broadband_island",
            "immediate_voice_to_broadband_transition",
            "burst_spectral_flatness",
            "spectral_jump_score",
        ) and _all(
            text["tail_facade"],
            'POLICY = "late-broadband-tail-v5"',
            'VOICE_CLASSIFICATION_POLICY = "conjunctive-voiced-vs-broadband-tail-v2"',
            'EMBEDDED_POLICY = "quiet-dip-broadband-island-voice-residue-v1"',
            "def _embedded_terminal_island(",
            "pre_quiet_level = float(np.percentile(before, 25))",
            '"repairable": False',
            '"artifact_type": "embedded_terminal_broadband_island"',
            "def detect_late_broadband_tail(",
        ),
        "dialogue-suppressed-master": _all(
            text["master"],
            f'POLICY = "{MASTER_MIX_POLICY}"',
            "CENTER_FLOOR_RATIO = 0.065",
            "MAX_CENTER_FLOOR = 0.010",
            "def source_bed_levels(",
            "def build_dialogue_suppressed_mix(",
            "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0",
            "original_dialogue_preserved_at_requested_level=False",
            "_legacy.build_constant_mix = build_dialogue_suppressed_mix",
        ) and _all(
            text["routing"],
            'MASTER_NAME = "master_monolithic_mix.py"',
            "def _renderer_paths(",
            "def _is_master_command(",
            "legacy._renderer_paths = _renderer_paths",
            "clean_production_core._is_master_command = _is_master_command",
        ) and _all(
            text["direct_main"],
            "from tools.voxcpm2 import monolithic_runtime_install",
            "monolithic_runtime_install.install()",
            "independent_qa_retry.install()",
            "main()",
        ) and _all(
            text["backend"],
            'ADAPTER_POLICY = "voxcpm2-speech-backend-adapter-v4"',
            'MASTER_SELECTION_POLICY = "translation-mode-specific-master-entrypoint-v1"',
            "def _master_contract(",
            'if mode == "direct"',
            '_DIRECT_MASTER_MODULE = "tools.voxcpm2.master_monolithic_mix"',
            "master_entrypoint, master_module = _master_contract(repo, request)",
            "def process_environment(",
        ),
        "monolith-fingerprinted": _all(
            text["fingerprint"],
            '"tools/voxcpm2/dub_quality_v4/__init__.py"',
            '"tools/voxcpm2/expressive_continuity/__init__.py"',
            '"tools/voxcpm2/russian_pronunciation.py"',
            '"tools/voxcpm2/direct_source_relative_continuity.py"',
            '"tools/voxcpm2/direct_monolith_contract.py"',
            '"tools/voxcpm2/direct_monolith_contract/__init__.py"',
            '"tools/voxcpm2/direct_max_quality_cli/__init__.py"',
            '"tools/voxcpm2/direct_tail_artifact/__init__.py"',
            '"tools/voxcpm2/direct_timeline_delivery_qa/__init__.py"',
            '"tools/voxcpm2/monolithic_runtime_install.py"',
            '"tools/voxcpm2/master_monolithic_mix.py"',
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        return False, "monolithic-контракты не прошли: " + ", ".join(failed)
    return True, (
        "semantic-breath ready-SRT grouping; one calm identity reference; "
        "cross-language source prosody is diagnostic-only with zero ranking penalty; "
        "bounded pronunciation variants with duration/energy/F0 screening and mandatory "
        "manual review; candidate anchor/neighbour identity gates; fail-closed absolute "
        "whole-timeline F0 gates that source evidence cannot override; resume-safe nearest "
        "checkpoint identity; no late cue shifting; short cadence-aware fades; detached-start "
        "and embedded/immediate broadband-tail gates; dialogue-suppressed stereo-side source "
        "bed with bounded center floor; model-independent backend command and process "
        "environment boundary; "
        f"backend={BACKEND_COMMAND_POLICY}; environment={BACKEND_ENVIRONMENT_POLICY}; "
        "synthesis/release fingerprinting"
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
    release_text = _read(repo / "services" / "dub_worker_release.py")
    worker_text = _read(
        repo / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
    )
    supervisor_text = _read(
        repo / "services" / "dub_studio_runtime" / "__init__.py"
    )
    direct_main_text = _read(
        repo / "tools" / "voxcpm2" / "generic_clean_direct_runtime" / "__main__.py"
    )
    recovery_text = _read(repo / "tools" / "voxcpm2" / "independent_qa_retry.py")

    release_ok = all(
        marker in release_text
        for marker in (
            f'WORKER_RUNTIME = "{WORKER_RUNTIME}"',
            'RELEASE_POLICY = "single-source-worker-release-identity-v1"',
            'PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"',
            f'INDEPENDENT_QA_RECOVERY_POLICY = "{INDEPENDENT_QA_RECOVERY_POLICY}"',
            f'READY_SRT_GROUPING_POLICY = "{READY_SRT_GROUPING_POLICY}"',
            f'MONOLITHIC_VOICE_POLICY = "{MONOLITHIC_VOICE_POLICY}"',
            f'SOURCE_RELATIVE_CONTINUITY_POLICY = "{SOURCE_RELATIVE_CONTINUITY_POLICY}"',
            f'FAIL_CLOSED_IDENTITY_POLICY = "{FAIL_CLOSED_IDENTITY_POLICY}"',
            f'MONOLITHIC_TIMELINE_POLICY = "{MONOLITHIC_TIMELINE_POLICY}"',
            f'PRONUNCIATION_POLICY = "{PRONUNCIATION_POLICY}"',
            f'PRONUNCIATION_VARIANT_POLICY = "{PRONUNCIATION_VARIANT_POLICY}"',
            f'EXPRESSION_POLICY = "{EXPRESSION_POLICY}"',
            f'MASTER_MIX_POLICY = "{MASTER_MIX_POLICY}"',
            f'RUNTIME_ROUTING_POLICY = "{RUNTIME_ROUTING_POLICY}"',
            f'SEMANTIC_BLOCK_POLICY = "{SEMANTIC_BLOCK_POLICY}"',
            f'SOURCE_PROSODY_ROLE_POLICY = "{SOURCE_PROSODY_ROLE_POLICY}"',
            f'BACKEND_COMMAND_POLICY = "{BACKEND_COMMAND_POLICY}"',
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
            'next_request["base_seed"] = next_base_seed',
            "def install(",
        )
    ) and all(
        marker in direct_main_text
        for marker in (
            "from tools.voxcpm2 import independent_qa_retry",
            "from tools.voxcpm2 import monolithic_runtime_install",
            "monolithic_runtime_install.install()",
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
