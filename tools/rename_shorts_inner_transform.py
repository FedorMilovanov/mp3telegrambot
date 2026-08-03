#!/usr/bin/env python3
"""One-time branch patch: keep the public API name without duplicating metrics."""
from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("services/shorts_video.py")
OLD = "_unowned_postprocess_short"
NEW = "_unowned_short_transform"


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    count = source.count(OLD)
    if count != 2:
        raise SystemExit(f"expected exactly two {OLD!r} occurrences, found {count}")
    updated = source.replace(OLD, NEW)
    ast.parse(updated)
    if updated.count(NEW) != 2 or OLD in updated:
        raise SystemExit("rename postcondition failed")
    PATH.write_text(updated, encoding="utf-8")
    print(f"renamed {OLD} -> {NEW}")


if __name__ == "__main__":
    main()
