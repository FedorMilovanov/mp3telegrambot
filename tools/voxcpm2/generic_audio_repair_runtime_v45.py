#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Professional audio-only repair entrypoint v4.6."""
from __future__ import annotations

import os

from tools.voxcpm2 import generic_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_audio_repair_runtime_bootstrap as bootstrap
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import legacy_segment_migration_v45
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import semantic_tts_guard_v46


def _ensure_focused_rounds() -> None:
    try:
        configured = int(os.getenv("DUB_TTS_QA_MAX_ROUNDS", "5") or "5")
    except ValueError:
        configured = 5
    os.environ["DUB_TTS_QA_MAX_ROUNDS"] = str(max(5, min(7, configured)))


def main() -> None:
    project_id = production.current_project_id()
    root = production.project_root(project_id)
    request = production.load_request(root)
    bootstrap.ensure_repair_manifest(root, request, project_id)
    _ensure_focused_rounds()
    professional_audio_v45.install()
    professional_audio_qa_v45.install()
    semantic_tts_guard_v46.install()
    legacy_segment_migration_v45.migrate(root, request)
    log_path = bootstrap.install_repair_diagnostics(root)
    production.log(f"AUDIO REPAIR child log: {log_path}")
    repair_runtime.main()


if __name__ == "__main__":
    main()
