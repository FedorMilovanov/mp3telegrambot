"""External-service package.

Importing :mod:`services` is intentionally side-effect free.  Production runtime
composition is owned explicitly by :mod:`services.runtime_manifest`, which the
validated ``bot_new.py`` entrypoint executes before and after importing ``main``.

Keeping package import pure removes the former ``sys.meta_path`` hook and makes
startup order observable, testable and fail-closed instead of depending on which
service happened to be imported first.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY = "explicit-runtime-manifest-v2"


def service_bootstrap_events() -> tuple[dict[str, Any], ...]:
    """Return an immutable diagnostic snapshot from the explicit manifest."""
    try:
        from services.runtime_manifest import runtime_manifest_payload

        payload = runtime_manifest_payload()
    except Exception as exc:
        return (
            {
                "sequence": 1,
                "component": "runtime manifest",
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "policy": SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY,
            },
        )

    events: list[dict[str, Any]] = []
    for sequence, (feature_id, result) in enumerate(
        payload.get("features", {}).items(), start=1
    ):
        state = str(result.get("state") or "pending")
        detail = str(result.get("detail") or state)
        events.append(
            {
                "sequence": sequence,
                "component": feature_id,
                "ok": state in {"installed", "skipped"},
                "detail": detail,
                "policy": str(payload.get("policy") or SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY),
            }
        )
    return tuple(events)


def emit_service_bootstrap_diagnostics(
    sink: Callable[[str], Any] | None = None,
) -> int:
    """Emit explicit-bootstrap state plus one privacy-safe build identity line."""
    target = sink or print
    events = service_bootstrap_events()
    for event in events:
        marker = "✅" if event["ok"] else "⚠️"
        target(
            f"{marker} {event['component']}: {event['detail']} "
            f"[{event['policy']}]"
        )
    try:
        from services.runtime_build_identity import runtime_build_identity_log_line

        target(runtime_build_identity_log_line())
    except Exception as exc:
        target(f"🧾 Runtime build: unavailable ({type(exc).__name__})")
    return len(events)


__all__ = [
    "SERVICE_BOOTSTRAP_DIAGNOSTICS_POLICY",
    "emit_service_bootstrap_diagnostics",
    "service_bootstrap_events",
]
