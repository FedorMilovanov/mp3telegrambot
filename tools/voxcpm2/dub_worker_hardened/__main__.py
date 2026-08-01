#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute the active Dub worker release with strict preflight transport."""
from __future__ import annotations

import sys

from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2.preflight_json_protocol import install as install_preflight_json

from . import _legacy
from . import main


def activate_release_identity() -> None:
    """Apply the shared release marker before worker registration and heartbeat."""
    package = sys.modules[__package__]
    package._RUNTIME_VERSION = WORKER_RUNTIME
    _legacy._RUNTIME_VERSION = WORKER_RUNTIME


if __name__ == "__main__":
    activate_release_identity()
    install_preflight_json()
    main()
