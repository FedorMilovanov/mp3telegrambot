#!/usr/bin/env python3
"""Explicit pre-main policy composition.

The entrypoint calls this through ``services.runtime_manifest`` *before* importing
``main``/``core.globals``.  Importing the ``services`` package itself is now
side-effect free; environment/model/network policy has one auditable lifecycle
owner instead of depending on package import order.
"""
from __future__ import annotations

import os


def configure_pre_main_policy() -> str:
    from services.gemini_max_quality import configure_max_quality_env
    from services.gemini_qa_policy import configure_gemini_qa_policy
    from services.livedub_quality_runtime import (
        configure_gemini_network,
        configure_gemini_policy,
    )

    os.environ.setdefault("LIVEDUB_QUICK_QA_MAX_DURATION", "10800")
    qa = configure_gemini_qa_policy()
    maximum = configure_max_quality_env()
    livedub = configure_gemini_policy()
    route = configure_gemini_network()
    return f"{maximum}; {livedub}; {qa}; route={route}"
