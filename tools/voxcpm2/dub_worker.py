#!/usr/bin/env python3
"""Write-through compatibility entrypoint for the neutral Dub worker."""
from __future__ import annotations

import sys
import types

from services import dub_worker as _implementation

for _name in dir(_implementation):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_implementation, _name)


class _WriteThroughModule(types.ModuleType):
    def __setattr__(self, name, value):
        types.ModuleType.__setattr__(self, name, value)
        if not name.startswith("__") and hasattr(_implementation, name):
            setattr(_implementation, name, value)

    def __getattr__(self, name):
        return getattr(_implementation, name)


if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__].__class__ = _WriteThroughModule
