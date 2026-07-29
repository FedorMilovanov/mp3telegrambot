#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable PowerShell/bot CLI for the direct VoxCPM2 max-quality renderer.

This file preserves the proven command path used by both the bot and manual
PowerShell runs. The implementation is ordinary imported production code: no
runpy, no subprocess proxy, no model monkeypatching and no rescue wrapper.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.voxcpm2.direct_max_quality_cli import main


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
