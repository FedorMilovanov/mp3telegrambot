#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


def rewrite(path_s: str, transform) -> None:
    path = Path(path_s)
    text = path.read_text(encoding="utf-8")
    updated = transform(text)
    if updated == text:
        raise RuntimeError(f"v10 made no change to {path}")
    path.write_text(updated, encoding="utf-8")


# The merged Direct source owns progress through direct_surgical_runtime.
rewrite(
    "tools/voxcpm2/direct_max_quality_cli.py",
    lambda s: s.replace('"policy": _PROGRESS_POLICY,', '"policy": surgical_runtime._PROGRESS_POLICY,'),
)

# The raw/facade split is gone: the canonical CLI source is the only owner.
rewrite(
    "tests/test_speech_backend_generation_length_plan.py",
    lambda s: s.replace(
        'RAW_CLI = ROOT / "tools" / "voxcpm2" / "_direct_max_quality_cli_base.py"\nFACADE = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli" / "__init__.py"',
        'RAW_CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"\nFACADE = RAW_CLI',
    ),
)

# Explicitly represent the fail-closed no-proof case in the multiline boundary test.
rewrite(
    "tests/test_shorts_factory_ru_boundaries.py",
    lambda s: s.replace(
        "            source_duration=200.0,\n            candidate_kind=\"short\",",
        "            source_duration=200.0,\n            evidence={},\n            candidate_kind=\"short\",",
    ),
)

# Source download now directly owns pipeline access; no hardened facade exists.
rewrite(
    "tests/test_clean_source_download.py",
    lambda s: s.replace("source_cache.hardened", "source_cache.pipeline"),
)

print("source-owner regression finalizer v10 applied")
