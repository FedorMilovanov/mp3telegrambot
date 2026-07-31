from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services import dub_release_health_v64
from services import dub_title_policy
from services.dub_worker_release import (
    BACKEND_COMMAND_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    EXPRESSION_POLICY,
    FAIL_CLOSED_IDENTITY_POLICY,
    LEGACY_IMPORT_POLICY,
    MASTER_MIX_POLICY,
    MONOLITHIC_TIMELINE_POLICY,
    MONOLITHIC_VOICE_POLICY,
    PRONUNCIATION_POLICY,
    PRONUNCIATION_VARIANT_POLICY,
    READY_SRT_GROUPING_POLICY,
    REFERENCE_POLICY,
    REFERENCE_SELECTION_POLICY,
    RUNTIME_ROUTING_POLICY,
    SEMANTIC_BLOCK_POLICY,
    SOURCE_BED_POLICY,
    SOURCE_PROSODY_ROLE_POLICY,
    SOURCE_RELATIVE_CONTINUITY_POLICY,
    TAIL_BRACKETING_POLICY,
    WORKER_RUNTIME,
)


REPO = Path(__file__).resolve().parents[1]


def _install_v67_static_upgrade() -> None:
    dub_release_health_v64._upgrade_monolithic_contract(dub_title_policy)


def test_monolithic_static_contract_passes_current_repository() -> None:
    _install_v67_static_upgrade()
    ok, detail = dub_title_policy._monolithic_static_contract(REPO)

    assert ok is True, detail
    assert "semantic-breath" in detail
    assert "Russian-only direct master" in detail
    assert "robust median F0" in detail
    assert LEGACY_IMPORT_POLICY in detail
    assert TAIL_BRACKETING_POLICY in detail
    assert SEMANTIC_BLOCK_POLICY in detail
    assert SOURCE_PROSODY_ROLE_POLICY in detail
    assert BACKEND_COMMAND_POLICY in detail
    assert BACKEND_ENVIRONMENT_POLICY in detail


def test_release_contract_replaces_only_superseded_worker_and_renderer_checks() -> None:
    _install_v67_static_upgrade()
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: worker-package-cancel-root, "
            "worker-runtime-sync, long-form-direct-resilience",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is True, detail
    assert "worker v6.8" in detail
    assert "Russian-only direct master" in detail
    assert "robust median F0" in detail
    assert LEGACY_IMPORT_POLICY in detail
    assert TAIL_BRACKETING_POLICY in detail


def test_release_contract_keeps_unrelated_failure_red() -> None:
    _install_v67_static_upgrade()
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: child-python-contract, worker-runtime-sync",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is False
    assert "child-python-contract" in detail


def test_v67_quality_contract_fails_closed_on_current_repository() -> None:
    ok, detail = dub_release_health_v64._v67_quality_contract(REPO)

    assert ok is True, detail
    assert "speech-bearing original is absent" in detail
    assert "post-AAC center and side leakage regressions" in detail
    assert "robust median F0" in detail
    assert LEGACY_IMPORT_POLICY in detail
    assert TAIL_BRACKETING_POLICY in detail


def test_shared_worker_runtime_and_policies_are_v68() -> None:
    assert WORKER_RUNTIME == "dub-worker-quality-v6.8"
    assert READY_SRT_GROUPING_POLICY == "ready-srt-semantic-breath-grouping-v1"
    assert REFERENCE_POLICY == "continuous-clean-reference-v3"
    assert REFERENCE_SELECTION_POLICY == "robust-typical-f0-continuous-window-v1"
    assert LEGACY_IMPORT_POLICY == "transactional-sys-modules-registration-v1"
    assert TAIL_BRACKETING_POLICY == "analysis-window-overlap-aware-voice-brackets-v1"
    assert SEMANTIC_BLOCK_POLICY == "semantic-block-continuation-v1"
    assert SOURCE_PROSODY_ROLE_POLICY == "diagnostic-only-no-cross-language-ranking-v1"
    assert BACKEND_COMMAND_POLICY == "speech-backend-command-builder-v1"
    assert MONOLITHIC_VOICE_POLICY == "single-speaker-monolithic-candidate-v1"
    assert SOURCE_RELATIVE_CONTINUITY_POLICY == "cross-language-source-prosody-diagnostic-v3"
    assert FAIL_CLOSED_IDENTITY_POLICY == "cross-language-prosody-cannot-override-identity-v1"
    assert MONOLITHIC_TIMELINE_POLICY == "assembled-monolithic-voice-v1"
    assert PRONUNCIATION_POLICY == "russian-pronunciation-overrides-v1"
    assert PRONUNCIATION_VARIANT_POLICY == "bounded-pronunciation-candidate-variants-v1"
    assert EXPRESSION_POLICY == "source-guided-monolithic-expression-v3"
    assert MASTER_MIX_POLICY == "russian-only-direct-master-v2"
    assert SOURCE_BED_POLICY == "speech-bearing-original-disabled-v1"
    assert RUNTIME_ROUTING_POLICY == "monolithic-ready-srt-runtime-routing-v2"
