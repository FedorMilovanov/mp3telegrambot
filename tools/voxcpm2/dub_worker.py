#!/usr/bin/env python3
"""Compatibility entrypoint for the backend-neutral Dub Studio worker."""
from __future__ import annotations

import sys

from services import dub_worker as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
