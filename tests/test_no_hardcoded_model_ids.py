"""Production code must not hardcode Gemini model identifiers.

Every identifier lives in ``core/model_catalog.py``.  This guard fails as soon
as a service, handler, pipeline, converter or tool writes a literal again, which
is exactly how the project previously ended up editing a dozen files per model
bump.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("core", "services", "pipelines", "handlers", "converters", "tools")
ALLOWED_FILES = {
    # The single source of truth.
    "core/model_catalog.py",
    # The catalog renderer maps roles to env keys and documents them.
    "scripts/render_model_catalog.py",
}
MODEL_LITERAL = re.compile(r"gemini-[0-9][0-9A-Za-z._-]*")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for directory in SCAN_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative in ALLOWED_FILES:
                continue
            files.append(path)
    return files


def _matches(text: str) -> list[str]:
    """Model-looking matches that are not part of a longer word."""
    hits: list[str] = []
    for match in MODEL_LITERAL.finditer(text):
        tail = text[match.end():match.end() + 1]
        # Reject prose such as "gemini-3.8-flash-HIGH" identifiers? No: keep it
        # strict, an identifier is an identifier. Only skip when followed by a
        # digit sequence that is clearly a version of something else.
        if tail and tail[0].isdigit():
            continue
        hits.append(match.group(0))
    return hits


def test_production_code_has_no_model_literals() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        hits = _matches(path.read_text(encoding="utf-8", errors="ignore"))
        if hits:
            offenders[path.relative_to(ROOT).as_posix()] = sorted(set(hits))
    assert offenders == {}, (
        "Hardcoded Gemini model ids found; import a role from core.model_catalog "
        f"instead: {offenders}"
    )


def test_catalog_itself_holds_the_identifiers() -> None:
    text = (ROOT / "core" / "model_catalog.py").read_text(encoding="utf-8")
    assert _matches(text), "catalog must contain the model identifiers"
