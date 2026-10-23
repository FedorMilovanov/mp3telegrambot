#!/usr/bin/env python3
"""Prove that a model upgrade is a one-line change.

The drill swaps ``ROLE_PRIMARY`` in ``core/model_catalog.py`` for another model
from the same catalog, re-renders every generated artifact, runs the model
contract test suite, and then restores the repository exactly as it was.

Usage::

    python scripts/model_upgrade_drill.py --model gemini-4-argon
    python scripts/model_upgrade_drill.py --model gemini-4-argon --full

What a green run proves:

* no production module must be edited to switch models;
* ``.env.example``, ``config/shorts_factory.env.example``, README,
  ``docs/gemini-operations.md``, the feature docs and the migration script all
  follow the catalog automatically;
* the guards/tests that pin model behaviour are catalog-relative.

The script never leaves the working tree mutated: the original catalog bytes are
restored in ``finally`` and the renderer is re-run.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.model_catalog import MODEL_PRIMARY, model_spec  # noqa: E402

CATALOG = ROOT / "core" / "model_catalog.py"
RENDER = ROOT / "scripts" / "render_model_catalog.py"

CONTRACT_TESTS = [
    "tests/test_model_catalog.py",
    "tests/test_model_catalog_currentness.py",
    "tests/test_no_hardcoded_model_ids.py",
    "tests/test_gemini_model_status.py",
    "tests/test_gemini_qa_policy.py",
    "tests/test_gemini_max_quality.py",
    "tests/test_shorts_factory_candidates.py",
    "tests/test_shorts_factory_no_downgrade.py",
    "tests/test_livedub_quality_runtime.py",
    "tests/test_livedub_deep_audit.py",
    "tests/test_livedub_live_voice_policy.py",
    "tests/test_livedub_qa.py",
    "tests/test_translation_editorial_factory.py",
    "tests/test_gemini38_factory_resilience.py",
    "tests/test_gemini_translation_quality.py",
]


def _run(args: list[str], *, quiet: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if not quiet and result.returncode != 0:
        print(result.stdout[-4000:])
        print(result.stderr[-4000:], file=sys.stderr)
    return result


_ROLE_PRIMARY_ROW = re.compile(r'^(    ROLE_PRIMARY: ")[^"]*(",$)', re.MULTILINE)


def _swap_catalog(source: str, model: str) -> str:
    """Replace the one catalog line that decides the primary route."""
    if not _ROLE_PRIMARY_ROW.search(source):
        raise SystemExit("ROLE_PRIMARY row not found in core/model_catalog.py")
    return _ROLE_PRIMARY_ROW.sub(
        lambda m: f"{m.group(1)}{model}{m.group(2)}", source, count=1
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="catalog model id to try as primary")
    parser.add_argument(
        "--full",
        action="store_true",
        help="run the whole test suite instead of the model contract subset",
    )
    args = parser.parse_args(argv)

    if args.model == MODEL_PRIMARY:
        raise SystemExit(f"{args.model} is already the primary route; pick another model")
    spec = model_spec(args.model)
    if spec is None:
        raise SystemExit(f"{args.model} is not present in core/model_catalog.py")

    original = CATALOG.read_bytes()
    status = 1
    print(f"drill: primary {MODEL_PRIMARY} -> {args.model} (status: {spec.status})")
    try:
        swapped = _swap_catalog(original.decode("utf-8"), args.model)
        CATALOG.write_bytes(swapped.encode("utf-8"))
        print("drill: catalog edited in exactly one place")

        stale = _run([sys.executable, str(RENDER), "--check"], quiet=True)
        if stale.returncode == 0:
            print("drill: FAIL - generated artifacts did not notice the swap")
            return 1
        print("drill: generator detected the stale artifacts (expected)")

        rendered = _run([sys.executable, str(RENDER)])
        if rendered.returncode != 0:
            print("drill: FAIL - renderer failed")
            return 1
        updated = [line for line in rendered.stdout.splitlines() if line.startswith("updated ")]
        print(f"drill: renderer refreshed {len(updated)} artifacts, no code edits")

        tests = [] if args.full else CONTRACT_TESTS
        result = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests])
        if result.returncode != 0:
            print("drill: FAIL - contract tests are not catalog-relative")
            return 1
        print("drill: contract tests are green on the new model")
        status = 0
    finally:
        CATALOG.write_bytes(original)
        _run([sys.executable, str(RENDER)], quiet=True)
        restored = _run([sys.executable, str(RENDER), "--check"], quiet=True)
        if restored.returncode != 0:
            print("drill: WARNING - artifacts are not in sync after restore", file=sys.stderr)
            status = 1
        else:
            print("drill: repository restored, generated artifacts in sync")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
