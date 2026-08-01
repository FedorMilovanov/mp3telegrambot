#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for the active Dub worker release identity.

The Telegram supervisor and detached worker must agree on this marker. A major
voice/render contract change advances the release so an idle stale process is
replaced before it can claim another production job. Shared backend constants
are imported from the active contract rather than copied here.
"""
from __future__ import annotations

from services.speech_backends.base import (
    BACKEND_COMMAND_POLICY,
    BACKEND_CONTRACT_POLICY,
    BACKEND_ENVIRONMENT_POLICY,
    GENERATION_REQUEST_POLICY,
    PRODUCTION_CAPABILITY_POLICY,
    SESSION_CONFIG_POLICY,
)

WORKER_RUNTIME = "dub-worker-quality-v6.11"
RELEASE_POLICY = "single-source-worker-release-identity-v1"
PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"
INDEPENDENT_QA_RECOVERY_POLICY = "bounded-independent-qa-segment-retry-v1"
READY_SRT_GROUPING_POLICY = "ready-srt-semantic-breath-grouping-v1"
REFERENCE_POLICY = "continuous-clean-reference-v3"
REFERENCE_SELECTION_POLICY = "robust-typical-f0-continuous-window-v1"
LEGACY_IMPORT_POLICY = "transactional-sys-modules-registration-v1"
TAIL_BRACKETING_POLICY = "analysis-window-overlap-aware-voice-brackets-v1"
SEMANTIC_BLOCK_POLICY = "semantic-block-continuation-v1"
SOURCE_PROSODY_ROLE_POLICY = "diagnostic-only-no-cross-language-ranking-v2"
CONTINUATION_POLICY = "backend-capability-gated-previous-block-prompt-v2"
RENDER_MARKER_POLICY = "direct-cli-runtime-marker-v2"
RENDER_SUCCESS_POLICY = "direct-cli-success-marker-v1"
MONOLITHIC_VOICE_POLICY = "single-speaker-monolithic-candidate-v1"
SOURCE_RELATIVE_CONTINUITY_POLICY = "cross-language-source-prosody-diagnostic-v3"
FAIL_CLOSED_IDENTITY_POLICY = "cross-language-prosody-cannot-override-identity-v1"
MONOLITHIC_TIMELINE_POLICY = "assembled-monolithic-voice-v1"
PRONUNCIATION_POLICY = "russian-pronunciation-overrides-v1"
PRONUNCIATION_VARIANT_POLICY = "bounded-pronunciation-candidate-variants-v1"
EXPRESSION_POLICY = "source-guided-monolithic-expression-v3"
MASTER_MIX_POLICY = "russian-only-direct-master-v2"
SOURCE_BED_POLICY = "speech-bearing-original-disabled-v1"
RUNTIME_ROUTING_POLICY = "monolithic-ready-srt-runtime-routing-v2"


__all__ = [
    "BACKEND_COMMAND_POLICY",
    "BACKEND_CONTRACT_POLICY",
    "BACKEND_ENVIRONMENT_POLICY",
    "CONTINUATION_POLICY",
    "EXPRESSION_POLICY",
    "FAIL_CLOSED_IDENTITY_POLICY",
    "GENERATION_REQUEST_POLICY",
    "INDEPENDENT_QA_RECOVERY_POLICY",
    "LEGACY_IMPORT_POLICY",
    "MASTER_MIX_POLICY",
    "MONOLITHIC_TIMELINE_POLICY",
    "MONOLITHIC_VOICE_POLICY",
    "PREFLIGHT_TRANSPORT_POLICY",
    "PRODUCTION_CAPABILITY_POLICY",
    "PRONUNCIATION_POLICY",
    "PRONUNCIATION_VARIANT_POLICY",
    "READY_SRT_GROUPING_POLICY",
    "REFERENCE_POLICY",
    "REFERENCE_SELECTION_POLICY",
    "RELEASE_POLICY",
    "RENDER_MARKER_POLICY",
    "RENDER_SUCCESS_POLICY",
    "RUNTIME_ROUTING_POLICY",
    "SESSION_CONFIG_POLICY",
    "SOURCE_BED_POLICY",
    "SOURCE_PROSODY_ROLE_POLICY",
    "SOURCE_RELATIVE_CONTINUITY_POLICY",
    "SEMANTIC_BLOCK_POLICY",
    "TAIL_BRACKETING_POLICY",
    "WORKER_RUNTIME",
]
