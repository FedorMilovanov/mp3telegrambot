"""Guard the Gemini model catalog strings that operators copy into runtime.

The repository must not ship a documentation/config value that the runtime
gate would reject (Factory) or that the provider has already deprecated.
Catalog snapshot: 2026-10-22.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.model_catalog import MODEL_PRIMARY, MODEL_UTILITY, deprecated_aliases

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_factory_example_env_matches_runtime_gate() -> None:
    from services.shorts_factory_candidates import DEFAULT_SHORTS_FACTORY_MODEL

    example = _read("config/shorts_factory.env.example")
    match = re.search(r"^SHORTS_FACTORY_MODEL=(\S+)$", example, re.MULTILINE)
    assert match is not None
    from core.model_catalog import MODEL_PRIMARY

    assert match.group(1) == DEFAULT_SHORTS_FACTORY_MODEL == MODEL_PRIMARY


def test_factory_docs_do_not_describe_the_retired_pro_contract() -> None:
    doc = _read("docs/shorts_factory_max.md")
    assert "gemini-3.1-pro-preview" not in doc
    assert "GEMINI_PRO_MODEL" not in doc
    # The document names a role and lets the generated block carry the ID.
    assert "`primary`" in doc
    assert "<!-- MODEL-ROUTE:START -->" in doc
    assert MODEL_PRIMARY in doc


def test_editorial_review_docs_and_code_agree_on_the_primary_role() -> None:
    from services.translation_editorial_factory import FACTORY_EDITORIAL_GEMINI_MODEL

    assert FACTORY_EDITORIAL_GEMINI_MODEL == MODEL_PRIMARY
    doc = _read("docs/translation_editorial_review.md")
    assert "`primary`" in doc
    assert "<!-- MODEL-ROUTE:START -->" in doc
    assert f"`{MODEL_PRIMARY}`" in doc
    # Status of a model never leaks into the doc as a hand-written identifier.
    for model in deprecated_aliases():
        assert f"`{model}`" not in doc.replace(
            f"`{model}` (deprecated", ""
        ), f"{model} is documented as a standalone route"


def test_operator_docs_do_not_route_through_the_deprecated_37_alias() -> None:
    readme = _read("README.md")
    operations = _read("docs/gemini-operations.md")
    for text in (readme, operations):
        assert "`gemini-3.7-flash` / HIGH контракт" not in text
        assert "dedicated `gemini-3.7-flash`" not in text
        assert "3.7/3.6/3.5/Lite" not in text
        # Prose describes roles; identifiers live only in the generated block.
        assert "core/model_catalog.py" in text
        assert "MODEL-CATALOG:START" in text


def test_generated_catalog_block_matches_the_code() -> None:

    for doc in ("README.md", "docs/gemini-operations.md", "docs/model-catalog.md"):
        text = _read(doc)
        assert MODEL_PRIMARY in text, doc
        assert MODEL_UTILITY in text, doc


def test_generated_artifacts_are_in_sync() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/render_model_catalog.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_migration_wrapper_targets_existing_script() -> None:
    wrapper = _read("scripts/migrate-gemini-36.ps1")
    assert "migrate-gemini-38.ps1" in wrapper
    assert "migrate-gemini-37.ps1" not in wrapper
    assert (ROOT / "scripts" / "migrate-gemini-38.ps1").is_file()


def test_env_example_primary_route_is_current() -> None:
    env = _read(".env.example")
    assert f"GEMINI_MODEL={MODEL_PRIMARY}" in env
    assert "GEMINI_MODEL=gemini-3.7-flash" not in env
    assert "LIVEDUB_LONG_QA_MODEL=gemini-3.7-flash" not in env
