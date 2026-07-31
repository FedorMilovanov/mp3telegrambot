#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single source of truth for the active Dub worker release identity.

The Telegram supervisor and the detached worker must agree on this exact marker.
Changing worker/preflight orchestration without advancing the marker leaves an
already-running process alive on stale code. Keeping the marker here makes a
release mismatch explicit and forces the supervisor to replace an idle stale
worker before another production job is claimed.
"""
from __future__ import annotations

WORKER_RUNTIME = "dub-worker-quality-v5.0"
RELEASE_POLICY = "single-source-worker-release-identity-v1"
PREFLIGHT_TRANSPORT_POLICY = "marked-preflight-json-transport-v1"
INDEPENDENT_QA_RECOVERY_POLICY = "bounded-independent-qa-segment-retry-v1"


__all__ = [
    "INDEPENDENT_QA_RECOVERY_POLICY",
    "PREFLIGHT_TRANSPORT_POLICY",
    "RELEASE_POLICY",
    "WORKER_RUNTIME",
]
