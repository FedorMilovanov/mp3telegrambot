#!/usr/bin/env python3
"""Single source of truth for every Gemini model identifier in this project.

Why this module exists
----------------------
Before this catalog, the model identifier was duplicated in ~15 production
modules, in the PowerShell migration script, in both ``.env`` examples and in
several source-grep regression tests.  Bumping a model therefore meant editing
a dozen files and hoping no stale literal survived.

Now a model change is **one edit here** plus::

    python scripts/render_model_env.py          # refresh .env/docs/scripts
    python tools/verify_repo.py                 # compileall + pytest + ruff

Role-based access
-----------------
Callers ask for a *role*, never for a concrete model::

    from core.model_catalog import MODEL_PRIMARY, MODEL_UTILITY

    DEFAULT_INFO_MODEL = MODEL_PRIMARY          # user-visible semantic work
    LIGHT_MODEL = MODEL_UTILITY                 # mechanical work

Runtime overrides (optional, normalized)
----------------------------------------
Each role can be overridden with an environment variable.  Values that point at
a deprecated alias are transparently normalized to the current model, so a
stale ``.env`` keeps working after a catalog bump::

    MP3BOT_PRIMARY_MODEL=gemini-3.7-flash   -> resolves to gemini-3.8-flash
    GEMINI_MODEL=gemini-3.7-flash           -> resolved via the legacy name too

The catalog itself is dependency-free: it is imported by pre-main policy
modules before ``core.globals`` exists, so it must never import repository code.

Catalog snapshot: 2026-10-22 (Gemini API models / deprecations / changelog).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Final

CATALOG_VERSION: Final[str] = "2026-10-22"
POLICY: Final[str] = f"project-gemini-routing-{CATALOG_VERSION}"

# ── Roles ─────────────────────────────────────────────────────────────────────
# A role is the stable contract. The model behind it may change at any time.
ROLE_PRIMARY: Final[str] = "primary"
ROLE_UTILITY: Final[str] = "utility"
ROLE_FALLBACK: Final[str] = "fallback"
ROLE_TTS: Final[str] = "tts"
ROLE_TTS_LITE: Final[str] = "tts_lite"
ROLE_TRANSCRIBE: Final[str] = "transcribe"
ROLE_LIVE: Final[str] = "live"
ROLE_REASONING: Final[str] = "reasoning"

ROLE_MODELS: Final[dict[str, str]] = {
    # User-visible semantic output (synopsis, QA, Factory, publication, info).
    ROLE_PRIMARY: "gemini-3.8-flash",
    # Mechanical/utility work that never writes user-visible semantic text.
    ROLE_UTILITY: "gemini-3.5-flash-lite",
    # Cross-model degradation is a policy decision, not a default. An empty
    # value means "do not downgrade": callers fail closed or retry the primary.
    ROLE_FALLBACK: "",
    ROLE_TTS: "gemini-3.8-flash-tts",
    ROLE_TTS_LITE: "gemini-3.8-flash-lite-tts",
    ROLE_TRANSCRIBE: "gemini-3.5-transcribe",
    ROLE_LIVE: "gemini-3.8-live",
    ROLE_REASONING: "gemini-4-argon",
}

# Environment overrides per role. Order matters: the first non-empty wins, so a
# project-specific name beats the legacy generic one.
ROLE_ENV: Final[dict[str, tuple[str, ...]]] = {
    ROLE_PRIMARY: ("MP3BOT_PRIMARY_MODEL", "GEMINI_MODEL"),
    ROLE_UTILITY: ("MP3BOT_UTILITY_MODEL",),
    ROLE_FALLBACK: ("MP3BOT_FALLBACK_MODEL",),
    ROLE_TTS: ("MP3BOT_TTS_MODEL",),
    ROLE_TTS_LITE: ("MP3BOT_TTS_LITE_MODEL",),
    ROLE_TRANSCRIBE: ("MP3BOT_TRANSCRIBE_MODEL",),
    ROLE_LIVE: ("MP3BOT_LIVE_MODEL",),
    ROLE_REASONING: ("MP3BOT_REASONING_MODEL",),
}

# Convenience constants. Production modules should import these instead of
# writing model strings.
MODEL_PRIMARY: Final[str] = ROLE_MODELS[ROLE_PRIMARY]
MODEL_UTILITY: Final[str] = ROLE_MODELS[ROLE_UTILITY]
MODEL_TTS: Final[str] = ROLE_MODELS[ROLE_TTS]
MODEL_TTS_LITE: Final[str] = ROLE_MODELS[ROLE_TTS_LITE]
MODEL_TRANSCRIBE: Final[str] = ROLE_MODELS[ROLE_TRANSCRIBE]
MODEL_LIVE: Final[str] = ROLE_MODELS[ROLE_LIVE]
MODEL_REASONING: Final[str] = ROLE_MODELS[ROLE_REASONING]


# ── Catalog entries ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ModelSpec:
    """One row of the official Gemini model catalog."""

    model_id: str
    generation: int
    # current | deprecated_alias | scheduled_migration | access_limited |
    # preview | shutdown
    status: str
    # Filled in from ROLE_MODELS so a role assignment is declared exactly once.
    roles: tuple[str, ...] = ()
    replacement: str = ""
    deadline: str = ""
    deprecated_since: str = ""
    note: str = ""


def _spec(model_id: str, generation: int, status: str = "current", **kw) -> ModelSpec:
    return ModelSpec(model_id=model_id, generation=generation, status=status, **kw)


_BASE_MODELS: Final[dict[str, ModelSpec]] = {
    # ── Current stable generation served by this project ─────────────────────
    "gemini-4-argon": _spec(
        "gemini-4-argon",
        4,
        note="flagship reasoning (unified 1M context); review cost before switching",
    ),
    "gemini-3.8-flash": _spec(
        "gemini-3.8-flash",
        3,
        note="stable Flash quality route of this deployment",
    ),
    "gemini-3.6-flash": _spec(
        "gemini-3.6-flash",
        3,
        note="previous-generation Flash; kept only as an alias target",
    ),
    "gemini-3.5-flash-lite": _spec(
        "gemini-3.5-flash-lite",
        3,
        note="utility/mechanical route; never user-visible semantic text",
    ),
    "gemini-3.8-flash-tts": _spec(
        "gemini-3.8-flash-tts",
        3,
        note="dedicated speech synthesis (130 languages); not a text route",
    ),
    "gemini-3.8-flash-lite-tts": _spec(
        "gemini-3.8-flash-lite-tts",
        3,
        note="dedicated high-throughput speech synthesis (101 languages)",
    ),
    "gemini-3.5-transcribe": _spec(
        "gemini-3.5-transcribe",
        3,
        note="dedicated speech-to-text (word timestamps, diarization ≤8)",
    ),
    "gemini-3.8-live": _spec(
        "gemini-3.8-live",
        3,
        note="Live API audio-to-audio model",
    ),
    "gemini-3.8-live-extended-thinking": _spec(
        "gemini-3.8-live-extended-thinking",
        3,
        note="Live API high-reasoning model",
    ),
    # ── Deprecated endpoint aliases (provider still answers, auto-routed) ────
    "gemini-3.7-flash": _spec(
        "gemini-3.7-flash",
        3,
        status="deprecated_alias",
        deprecated_since="2026-10-08",
        replacement="gemini-3.8-flash",
    ),
    "gemini-3.5-flash": _spec(
        "gemini-3.5-flash",
        3,
        status="deprecated_alias",
        deprecated_since="2026-10-08",
        replacement="gemini-3.6-flash",
    ),
    # ── Announced shutdown / migration deadlines ─────────────────────────────
    "gemini-3.1-flash-lite": _spec(
        "gemini-3.1-flash-lite",
        3,
        status="scheduled_migration",
        deadline="2027-05-07",
        replacement="gemini-3.5-flash-lite",
    ),
    "gemini-3.1-flash-tts-preview": _spec(
        "gemini-3.1-flash-tts-preview",
        3,
        status="scheduled_migration",
        deadline="2026-11-17",
        replacement="gemini-3.8-flash-tts",
    ),
    "gemini-2.5-flash-preview-tts": _spec(
        "gemini-2.5-flash-preview-tts",
        2,
        status="scheduled_migration",
        deadline="2026-11-17",
        replacement="gemini-3.8-flash-tts",
    ),
    "gemini-2.5-pro-preview-tts": _spec(
        "gemini-2.5-pro-preview-tts",
        2,
        status="scheduled_migration",
        deadline="2026-11-17",
        replacement="gemini-3.8-flash-lite-tts",
    ),
    "gemini-3.1-flash-live-preview": _spec(
        "gemini-3.1-flash-live-preview",
        3,
        status="scheduled_migration",
        deadline="2026-11-17",
        replacement="gemini-3.8-live",
    ),
    "gemini-2.5-flash-native-audio-preview-12-2025": _spec(
        "gemini-2.5-flash-native-audio-preview-12-2025",
        2,
        status="scheduled_migration",
        deadline="2026-11-17",
        replacement="gemini-3.8-live",
    ),
    # ── Legacy stable generation: served, access limited to existing users ──
    "gemini-2.5-flash": _spec(
        "gemini-2.5-flash",
        2,
        status="access_limited",
        replacement="gemini-3.8-flash",
    ),
    "gemini-2.5-flash-lite": _spec(
        "gemini-2.5-flash-lite",
        2,
        status="access_limited",
        replacement="gemini-3.5-flash-lite",
    ),
    "gemini-2.5-pro": _spec(
        "gemini-2.5-pro",
        2,
        status="access_limited",
        replacement="gemini-4-argon",
    ),
    # ── Current previews (valid, not this deployment's stable route) ────────
    "gemini-3.1-pro-preview": _spec(
        "gemini-3.1-pro-preview",
        3,
        status="preview",
        replacement="gemini-3.8-flash",
    ),
    "gemini-3-flash-preview": _spec(
        "gemini-3-flash-preview",
        3,
        status="preview",
        replacement="gemini-3.6-flash",
    ),
    # ── Shut down / no longer served ─────────────────────────────────────────
    **{
        model_id: _spec(model_id, 3, status="shutdown")
        for model_id in (
            "gemini-3-pro-preview",
            "gemini-3.1-flash-lite-preview",
        )
    },
    **{
        model_id: _spec(model_id, 2, status="shutdown")
        for model_id in (
            "gemini-2.5-pro-preview",
            "gemini-2.5-pro-preview-03-25",
            "gemini-2.5-pro-preview-05-06",
            "gemini-2.5-pro-preview-06-05",
            "gemini-2.5-flash-preview-05-20",
            "gemini-2.5-flash-preview-09-25",
            "gemini-2.5-computer-use-preview-10-2025",
            "gemini-2.0-flash",
            "gemini-2.0-flash-001",
            "gemini-2.0-flash-lite",
            "gemini-2.0-flash-lite-001",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-1.5-flash-001",
            "gemini-1.0-pro",
            "gemini-pro",
            "gemini-pro-vision",
        )
    },
}

def _roles_by_model() -> dict[str, tuple[str, ...]]:
    """Reverse index: model id -> roles that point at it."""
    roles: dict[str, list[str]] = {}
    for role, model_id in ROLE_MODELS.items():
        if model_id:
            roles.setdefault(model_id, []).append(role)
    return {model_id: tuple(assigned) for model_id, assigned in roles.items()}


def _bind_roles(
    models: dict[str, ModelSpec],
) -> dict[str, ModelSpec]:
    assigned = _roles_by_model()
    return {
        model_id: replace(spec, roles=assigned.get(spec.model_id, ()))
        for model_id, spec in models.items()
    }


_MODELS: Final[dict[str, ModelSpec]] = _bind_roles(_BASE_MODELS)

# Floating alias that silently follows whatever Google ships next.
FLOATING_ALIASES: Final[frozenset[str]] = frozenset({"gemini-flash-latest"})

# ── Thinking configuration ────────────────────────────────────────────────────
# Gemini 3.x/4.x take ``thinking_level`` instead of the legacy ``temperature``.
# The pattern (not a generation comparison) keeps historical oddities like
# ``gemini-40-argon`` from being treated as Gemini 4.
_THINKING_MODEL_RE: Final[re.Pattern[str]] = re.compile(
    r"gemini-[34](?:[.\-]\d+)?(?:[\-_.].*)?"
)

# prefix -> (supported levels, fallback level when the requested one is invalid)
_THINKING_RULES: Final[tuple[tuple[str, frozenset[str], str], ...]] = (
    ("gemini-4", frozenset({"low", "medium", "high"}), "high"),
    ("gemini-3.8", frozenset({"low", "medium", "high"}), "high"),
    ("gemini-3.7", frozenset({"low", "medium", "high"}), "high"),
    ("gemini-3.6", frozenset({"low", "medium", "high"}), "high"),
    ("gemini-3.5-flash-lite", frozenset({"minimal"}), "minimal"),
    ("gemini-3.5-flash", frozenset({"minimal"}), "minimal"),
)


# ── Lookup helpers ────────────────────────────────────────────────────────────
def normalize_model_id(model_name: str) -> str:
    """Return the current model id, following one deprecated-alias hop.

    Unknown names are returned unchanged (stripped), so project-specific custom
    endpoints keep working.
    """
    value = str(model_name or "").strip()
    spec = _MODELS.get(value.casefold())
    if spec is not None and spec.status == "deprecated_alias" and spec.replacement:
        return spec.replacement
    return value


def model_spec(model_name: str) -> ModelSpec | None:
    return _MODELS.get(str(model_name or "").strip().casefold())


def role_model(role: str) -> str:
    """Catalog default for a role (no environment lookup)."""
    return ROLE_MODELS[role]


def role_of(model_name: str) -> str:
    spec = model_spec(model_name)
    if spec is None or not spec.roles:
        return ""
    return spec.roles[0]


def configured_model(role: str) -> str:
    """Role model with an optional environment override, alias-normalized."""
    for name in ROLE_ENV.get(role, ()):
        raw = os.getenv(name, "").strip()
        if raw:
            return normalize_model_id(raw)
    return ROLE_MODELS[role]


def fallback_model() -> str:
    """Configured cross-model fallback, or ``""`` when downgrade is disabled."""
    return configured_model(ROLE_FALLBACK)


def alias_target(model_name: str) -> str:
    spec = model_spec(model_name)
    if spec is not None and spec.status == "deprecated_alias":
        return spec.replacement
    return ""


def deprecated_aliases() -> dict[str, tuple[str, str]]:
    """``alias -> (deprecated_since, replacement)`` for every known alias."""
    return {
        spec.model_id: (spec.deprecated_since, spec.replacement)
        for spec in _MODELS.values()
        if spec.status == "deprecated_alias"
    }


def scheduled_migrations() -> dict[str, tuple[str, str]]:
    """``model -> (deadline, replacement)`` for announced migrations."""
    return {
        spec.model_id: (spec.deadline, spec.replacement)
        for spec in _MODELS.values()
        if spec.status == "scheduled_migration"
    }


def access_limited_models() -> dict[str, str]:
    return {
        spec.model_id: spec.replacement
        for spec in _MODELS.values()
        if spec.status == "access_limited"
    }


def shutdown_models() -> frozenset[str]:
    return frozenset(
        spec.model_id for spec in _MODELS.values() if spec.status == "shutdown"
    )


def preview_models() -> frozenset[str]:
    return frozenset(
        spec.model_id for spec in _MODELS.values() if spec.status == "preview"
    )


def current_models_outside_routing() -> dict[str, str]:
    """Current models that are not part of this deployment's text routing."""
    routed = {ROLE_PRIMARY, ROLE_UTILITY}
    return {
        spec.model_id: spec.note
        for spec in _MODELS.values()
        if spec.status == "current" and not routed.intersection(spec.roles)
    }


def all_model_ids() -> frozenset[str]:
    return frozenset(_MODELS)


# ── Thinking helpers ──────────────────────────────────────────────────────────
def uses_thinking_config(model_name: str) -> bool:
    """Gemini 3.x/4.x use ``thinking_config``; older models use ``temperature``."""
    model = str(model_name or "").strip().casefold()
    if not model:
        return False
    return bool(_THINKING_MODEL_RE.fullmatch(model))


def thinking_level_for(model_name: str, requested: str) -> str:
    """Clamp a requested thinking level to what the model actually accepts."""
    model = str(model_name or "").strip().casefold()
    level = str(requested or "high").strip().lower() or "high"
    for prefix, supported, fallback in _THINKING_RULES:
        if model.startswith(prefix):
            return level if level in supported else fallback
    return level


def supported_thinking_levels(model_name: str) -> frozenset[str]:
    model = str(model_name or "").strip().casefold()
    for prefix, supported, _fallback in _THINKING_RULES:
        if model.startswith(prefix):
            return supported
    return frozenset()


__all__ = [
    "CATALOG_VERSION",
    "POLICY",
    "FLOATING_ALIASES",
    "MODEL_LIVE",
    "MODEL_PRIMARY",
    "MODEL_REASONING",
    "MODEL_TRANSCRIBE",
    "MODEL_TTS",
    "MODEL_TTS_LITE",
    "MODEL_UTILITY",
    "ModelSpec",
    "ROLE_ENV",
    "ROLE_FALLBACK",
    "ROLE_LIVE",
    "ROLE_MODELS",
    "ROLE_PRIMARY",
    "ROLE_REASONING",
    "ROLE_TRANSCRIBE",
    "ROLE_TTS",
    "ROLE_TTS_LITE",
    "ROLE_UTILITY",
    "access_limited_models",
    "alias_target",
    "all_model_ids",
    "configured_model",
    "current_models_outside_routing",
    "deprecated_aliases",
    "fallback_model",
    "model_spec",
    "normalize_model_id",
    "preview_models",
    "role_model",
    "role_of",
    "scheduled_migrations",
    "shutdown_models",
    "supported_thinking_levels",
    "thinking_level_for",
    "uses_thinking_config",
]
