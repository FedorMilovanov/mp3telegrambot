#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from . import main


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import sys
        import traceback

        print(f"ОШИБКА CLEAN AUDIO REPAIR: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
