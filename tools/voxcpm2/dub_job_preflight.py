#!/usr/bin/env python3
"""Compatibility facade for backend-neutral Dub production preflight."""
from __future__ import annotations

from services.dub_preflight import POLICY, run

__all__ = ["POLICY", "run"]
