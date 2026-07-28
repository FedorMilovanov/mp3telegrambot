#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality v4.5 master wrapper: keep exact source gain and soften Russian loudness."""
from __future__ import annotations

import sys

from tools.voxcpm2 import master_quality_v4 as base


def _replace(flag: str, value: str) -> None:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        sys.argv.extend([flag, value])
    else:
        if index + 1 < len(sys.argv):
            sys.argv[index + 1] = value


def main() -> None:
    _replace("--target-i", "-16.0")
    _replace("--target-tp", "-1.5")
    base.main()


if __name__ == "__main__":
    main()
