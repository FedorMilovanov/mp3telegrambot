#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy symbols retained for the active Dub preflight package facade.

Normal imports resolve to ``tools.voxcpm2.dub_job_preflight`` package. This
module is loaded only by that compatibility package while existing callers are
migrated to the backend-neutral ``services.dub_preflight`` implementation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from services.dub_preflight import run
from services.dub_studio import repo_root, studio_root

POLICY = "backend-neutral-dub-production-preflight-v2"
_ACTIONS = {"render", "render_direct", "render_gemini", "render_custom", "repair_audio"}
_MODULES = (
    "tools.voxcpm2.final_media_qa",
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.master_constant_mix",
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.voxcpm2_cpu_shorts_production",
    "voxcpm",
    "torch",
    "soundfile",
)


def _read_json(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = [
    "POLICY",
    "_ACTIONS",
    "_MODULES",
    "_read_json",
    "os",
    "repo_root",
    "run",
    "shutil",
    "studio_root",
    "subprocess",
]
