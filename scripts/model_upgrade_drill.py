#!/usr/bin/env python3
"""Prove that a model upgrade is a one-line change.

The drill swaps ``ROLE_PRIMARY`` in ``core/model_catalog.py`` for another model
from the same catalog, re-renders every generated artifact, runs the model
contract test suite, and then restores the repository exactly as it was.

The target must be free-tier eligible: this deployment runs on free keys, so a
paid-only model is rejected before anything is touched (``--allow-paid``
overrides the check for the deliberate "what would billing change" experiment).

Usage::

    python scripts/model_upgrade_drill.py --model <free-tier-model-id>
    python scripts/model_upgrade_drill.py --model <free-tier-model-id> --full

The example is deliberately a placeholder: a concrete id in this docstring would
go stale on the next model release (it already had), and ``tests/
test_no_hardcoded_model_ids.py`` now scans ``scripts/`` too.

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

from core.model_catalog import (  # noqa: E402
    FREE_TIER_ONLY,
    MODEL_LIVE,
    MODEL_PRIMARY,
    MODEL_TRANSCRIBE,
    MODEL_TTS,
    MODEL_TTS_LITE,
    MODEL_UTILITY,
    ROLE_MODELS,
    is_free_tier,
    model_spec,
)

CATALOG = ROOT / "core" / "model_catalog.py"

#: Roles that cannot serve the *semantic* primary route: they are mechanical,
#: speech-only or live-stream models. Swapping primary to one of them does not
#: "prove an upgrade"; it silently turns the semantic route into a lite/tts
#: route, and the contract suite correctly rejects it — with a confusing
#: traceback instead of an explanation.
NON_SEMANTIC_ROLES = {
    "utility": MODEL_UTILITY,
    "tts": MODEL_TTS,
    "tts_lite": MODEL_TTS_LITE,
    "live": MODEL_LIVE,
    "transcribe": MODEL_TRANSCRIBE,
}
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
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="allow a model that the free tier does not serve (default: refuse)",
    )
    args = parser.parse_args(argv)

    if args.model == MODEL_PRIMARY:
        raise SystemExit(f"{args.model} is already the primary route; pick another model")
    spec = model_spec(args.model)
    if spec is None:
        raise SystemExit(f"{args.model} is not present in core/model_catalog.py")
    for role_name, role_model in NON_SEMANTIC_ROLES.items():
        if args.model and args.model == role_model:
            raise SystemExit(
                f"{args.model} is the {role_name} route's model, not a semantic "
                "model: it cannot serve ROLE_PRIMARY. The drill proves that a "
                "*semantic* model swap needs no code edits, so pick a "
                f"free-tier model that can write text (roles: {sorted(ROLE_MODELS)})."
            )
    if FREE_TIER_ONLY and not is_free_tier(args.model) and not args.allow_paid:
        raise SystemExit(
            f"{args.model} is not available on the free tier "
            f"(free_tier={spec.free_tier}). This deployment runs on free keys, so "
            "the upgrade would take every route down. Use a free-tier model, or "
            "pass --allow-paid for a deliberate billing experiment."
        )

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
