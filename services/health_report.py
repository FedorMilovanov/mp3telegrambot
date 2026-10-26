"""Compatibility exports for the core-owned health report implementation."""

from core.health_report import (
    MIN_FREE_DISK_GB,
    STALE_MAINTENANCE_SECONDS,
    STALE_UPDATE_SECONDS,
    _is_windows_drive,
    _metric,
    build_health,
    collect_problems,
    database_ok,
    disk_free_gb,
    gemini_keys_configured,
    readonly_uri,
    render_prometheus,
)

__all__ = [
    "MIN_FREE_DISK_GB",
    "STALE_MAINTENANCE_SECONDS",
    "STALE_UPDATE_SECONDS",
    "_is_windows_drive",
    "_metric",
    "build_health",
    "collect_problems",
    "database_ok",
    "disk_free_gb",
    "gemini_keys_configured",
    "readonly_uri",
    "render_prometheus",
]
