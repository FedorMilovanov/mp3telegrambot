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
# ``scripts`` and the root entrypoints were the blind spot: the audit found
# stale literals there (``--model gemini-3.6-flash`` in the drill and in the
# generated docs) precisely because the guard never looked at them.
SCAN_DIRS = ("core", "services", "pipelines", "handlers", "converters", "tools", "scripts")
ROOT_FILES = ("main.py", "bot_new.py", "bot.py")
ALLOWED_FILES = {
    # The single source of truth.
    "core/model_catalog.py",
    # Not a model id: the renderer writes and reads the migration script whose
    # FILE NAME contains `gemini-38` (scripts/migrate-gemini-38.ps1). Renaming
    # that file is out of scope; the renderer derives every real id from the
    # catalog, which is asserted below.
    "scripts/render_model_catalog.py",
}
MODEL_LITERAL = re.compile(r"gemini-[0-9][0-9A-Za-z._-]*")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
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


def test_the_renderer_derives_ids_from_the_catalog_only() -> None:
    """The renderer's allowance must stay about a file name, not model ids.

    It may mention ``scripts/migrate-gemini-38.ps1`` (a path), but every model
    identifier it prints has to come from ``core/model_catalog.py``: importing
    the roles/helpers instead of spelling ids out.
    """
    source = (ROOT / "scripts" / "render_model_catalog.py").read_text(encoding="utf-8")
    for literal in ("gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-4-argon"):
        assert literal not in source, (
            f"{literal} появился в генераторе литералом — берите роль из каталога"
        )
    assert "from core.model_catalog import" in source
    assert "paid_only_models" in source


def test_catalog_itself_holds_the_identifiers() -> None:
    text = (ROOT / "core" / "model_catalog.py").read_text(encoding="utf-8")
    assert _matches(text), "catalog must contain the model identifiers"
