#!/usr/bin/env python3
"""Dub Studio handler composition for one concrete Application instance."""

from __future__ import annotations

import logging
from typing import Any

from services.dub_studio_runtime import enabled, ensure_worker_running

logger = logging.getLogger(__name__)


def register_dub_studio(application: Any) -> bool:
    """Register Dub Studio handlers on this concrete Application instance."""
    if not enabled():
        return False
    from handlers.dub_audio_repair import register_dub_audio_repair_handlers
    from handlers.dub_commands import register_dub_handlers
    from handlers.dub_delivery import register_dub_delivery_handlers
    from handlers.dub_health import register_dub_health_handler
    from handlers.dub_quickstart import register_dub_quickstart_handler
    from handlers.dub_wizard import register_dub_wizard_handlers
    from handlers.dub_multicommand import register_dub_multicommand_handler

    register_dub_wizard_handlers(application)
    register_dub_health_handler(application)
    register_dub_handlers(application)
    register_dub_audio_repair_handlers(application)
    register_dub_delivery_handlers(application)
    register_dub_quickstart_handler(application)
    register_dub_multicommand_handler(application)
    ensure_worker_running()
    logger.info("🎙 Dub Studio handlers registered on Application")
    return True


__all__ = ["register_dub_studio"]
