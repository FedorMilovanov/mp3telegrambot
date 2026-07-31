#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute the hardened Dub worker with noise-tolerant preflight transport."""
from tools.voxcpm2.preflight_json_protocol import install as install_preflight_json

from . import main


if __name__ == "__main__":
    install_preflight_json()
    main()
