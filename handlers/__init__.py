"""Telegram bot handlers — commands and callbacks.

The Dub health facade is initialized here so direct imports and normal bot
startup use the same executable v6.9 contract rather than stale source-layout
assertions.
"""
from __future__ import annotations

from . import dub_health as dub_health
from .dub_health_current_contract import install as _install_dub_health

_install_dub_health(dub_health)

__all__ = ["dub_health"]
