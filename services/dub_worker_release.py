#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for the active Dub worker release identity.

The Telegram supervisor and detached worker must agree on this marker.  A major
voice/render contract change advances the release so an idle stale process is
replaced before it can claim another production job.
"""
from __future__ import annotations

WORKER_RUNTIME = "dub-worker-quality-v6.0"
RELEASE_POLICY = "single-source-worker-release-identity-v1"
PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"
INDEPENDENT_QA_RECOVERY_POLICY = "bounded-independent-qa-segment-retry-v1"
MONOLITHIC_VOICE_POLICY = "single-speaker-monolithic-candidate-v1"
MONOLITHIC_TIMELINE_POLICY = "assembled-monolithic-voice-v1"
PRONUNCIATION_POLICY = "russian-pronunciation-overrides-v1"
EXPRESSION_POLICY = "source-guided-monolithic-expression-v3"


__all__ = [
    "EXPRESSION_POLICY",
    "INDEPENDENT_QA_RECOVERY_POLICY",
    "MONOLITHIC_TIMELINE_POLICY",
    "MONOLITHIC_VOICE_POLICY",
    "PREFLIGHT_TRANSPORT_POLICY",
    "PRONUNCIATION_POLICY",
    "RELEASE_POLICY",
    "WORKER_RUNTIME",
]
