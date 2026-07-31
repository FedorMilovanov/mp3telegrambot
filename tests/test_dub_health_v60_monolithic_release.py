from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from services import dub_release_health_v64
from services import dub_title_policy
from services.dub_worker_release import (
    EXPRESSION_POLICY,
    FAIL_CLOSED_IDENTITY_POLICY,
    MASTER_MIX_POLICY,
    MONOLITHIC_TIMELINE_POLICY,
    MONOLITHIC_VOICE_POLICY,
    PRONUNCIATION_POLICY,
    PRONUNCIATION_VARIANT_POLICY,
    READY_SRT_GROUPING_POLICY,
    RUNTIME_ROUTING_POLICY,
    SOURCE_BED_POLICY,
    SOURCE_RELATIVE_CONTINUITY_POLICY,
    WORKER_RUNTIME,
)


REPO = Path(__file__).resolve().parents[1]


def _install_v64_static_upgrade() -> None:
    dub_release_health_v64._upgrade_monolithic_contract(dub_title_policy)


def test_monolithic_static_contract_passes_current_repository() -> None:
    _install_v64_static_upgrade()
    ok, detail = dub_title_policy._monolithic_static_contract(REPO)

    assert ok is True, detail
    assert "semantic-breath" in detail
    assert "Russian-only direct master" in detail
    assert "applied center/side are zero" in detail


def test_release_contract_replaces_only_superseded_worker_and_renderer_checks() -> None:
    _install_v64_static_upgrade()
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: worker-package-cancel-root, "
            "worker-runtime-sync, long-form-direct-resilience",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is True, detail
    assert "worker v6.4" in detail
    assert "Russian-only direct master" in detail


def test_release_contract_keeps_unrelated_failure_red() -> None:
    _install_v64_static_upgrade()
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: child-python-contract, worker-runtime-sync",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is False
    assert "child-python-contract" in detail


def test_v64_master_contract_fails_closed_on_current_repository() -> None:
    ok, detail = dub_release_health_v64._russian_only_master_contract(REPO)

    assert ok is True, detail
    assert "speech-bearing original is absent" in detail
    assert "post-AAC center and side leakage regressions" in detail


def test_shared_worker_runtime_and_policies_are_v64() -> None:
    assert WORKER_RUNTIME == "dub-worker-quality-v6.4"
    assert READY_SRT_GROUPING_POLICY == "ready-srt-semantic-breath-grouping-v1"
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
