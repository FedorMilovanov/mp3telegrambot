from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
    SOURCE_RELATIVE_CONTINUITY_POLICY,
    WORKER_RUNTIME,
)


REPO = Path(__file__).resolve().parents[1]


def test_monolithic_static_contract_passes_current_repository() -> None:
    ok, detail = dub_title_policy._monolithic_static_contract(REPO)

    assert ok is True, detail
    assert "semantic-breath ready-SRT grouping" in detail
    assert "one calm identity reference" in detail
    assert "stress evidence" in detail
    assert "whole-timeline" in detail
    assert "dialogue-suppressed stereo-side source bed" in detail


def test_release_contract_replaces_only_superseded_worker_and_renderer_checks() -> None:
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: worker-package-cancel-root, "
            "worker-runtime-sync, long-form-direct-resilience",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is True, detail
    assert "worker v6.2" in detail
    assert "one calm identity reference" in detail
    assert "dialogue-suppressed" in detail


def test_release_contract_keeps_unrelated_failure_red() -> None:
    health = SimpleNamespace(
        _v47_static_contract=lambda repo: (
            False,
            "v4.8-контракты не прошли: child-python-contract, worker-runtime-sync",
        )
    )

    ok, detail = dub_title_policy._release_static_contract(health, REPO)

    assert ok is False
    assert "child-python-contract" in detail


def test_shared_worker_runtime_and_policies_are_v62() -> None:
    assert WORKER_RUNTIME == "dub-worker-quality-v6.2"
    assert READY_SRT_GROUPING_POLICY == "ready-srt-semantic-breath-grouping-v1"
    assert MONOLITHIC_VOICE_POLICY == "single-speaker-monolithic-candidate-v1"
    assert SOURCE_RELATIVE_CONTINUITY_POLICY == "cross-language-source-prosody-advisory-v2"
    assert FAIL_CLOSED_IDENTITY_POLICY == "cross-language-prosody-cannot-override-identity-v1"
    assert MONOLITHIC_TIMELINE_POLICY == "assembled-monolithic-voice-v1"
    assert PRONUNCIATION_POLICY == "russian-pronunciation-overrides-v1"
    assert PRONUNCIATION_VARIANT_POLICY == "bounded-pronunciation-candidate-variants-v1"
    assert EXPRESSION_POLICY == "source-guided-monolithic-expression-v3"
    assert MASTER_MIX_POLICY == "dialogue-suppressed-spatial-bed-v1"
    assert RUNTIME_ROUTING_POLICY == "monolithic-ready-srt-runtime-routing-v2"
