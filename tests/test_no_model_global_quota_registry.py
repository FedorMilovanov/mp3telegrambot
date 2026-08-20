from __future__ import annotations

from pathlib import Path


LEGACY_NAMES = (
    "mark_model_" + "exhausted",
    "is_model_" + "exhausted",
    "_EXHAUSTED_" + "MODELS",
)


def test_application_has_no_model_global_gemini_quota_registry():
    """429/quota state may not be cached globally by model name anywhere."""
    offenders: list[str] = []
    for path in Path(".").rglob("*.py"):
        parts = set(path.parts)
        if "tests" in parts or ".venv" in parts or "venv" in parts:
            continue
        if any(part.startswith(".") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for legacy_name in LEGACY_NAMES:
            if legacy_name in text:
                offenders.append(f"{path}:{legacy_name}")
    assert offenders == [], "model-global Gemini quota state returned: " + ", ".join(offenders)
