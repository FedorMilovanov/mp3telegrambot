#!/usr/bin/env python3
"""Flatten the direct CLI base snapshot into its canonical source module."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
BASE = ROOT / "tools" / "voxcpm2" / "_direct_max_quality_cli_base.py"
POLISH = ROOT / "tools" / "voxcpm2" / "direct_surgical_polish_v2.py"
UNIVERSAL = ROOT / "tools" / "voxcpm2" / "direct_universal_runtime.py"


def strip_main_guard(text: str) -> str:
    tree = ast.parse(text, filename=str(BASE))
    lines = text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if not isinstance(node, ast.If):
            continue
        source = ast.get_source_segment(text, node) or ""
        if '__name__ == "__main__"' not in source and "__name__ == '__main__'" not in source:
            continue
        start = node.lineno - 1
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start : (node.end_lineno or node.lineno)]
        return "".join(lines).rstrip() + "\n"
    raise RuntimeError("base CLI main guard not found")


def main() -> int:
    if not CLI.is_file() or not BASE.is_file():
        raise RuntimeError("direct CLI snapshot pair is incomplete")
    base = strip_main_guard(BASE.read_text(encoding="utf-8-sig"))
    current = CLI.read_text(encoding="utf-8")

    marker = "_BASE_ALL = tuple(globals().get('__all__', ()))\n"
    index = current.find(marker)
    if index < 0:
        raise RuntimeError("flattened override marker not found")
    tail = current[index + len(marker) :].lstrip("\n")

    installer_block = '''from tools.voxcpm2.direct_universal_runtime import install_direct_runtime\nfrom tools.voxcpm2.direct_surgical_runtime import install_surgical_runtime\nfrom tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish\nfrom tools.voxcpm2.direct_final_audit_v3 import install_final_audit\nfrom tools.voxcpm2.direct_failure_recovery import install_main_failure_recovery\n\ninstall_direct_runtime(globals())\ninstall_surgical_runtime(globals())\ninstall_global_polish()\ninstall_final_audit(globals())\ninstall_main_failure_recovery(globals())\n'''
    for needle in (
        "from tools.voxcpm2.direct_universal_runtime import install_direct_runtime",
        "from tools.voxcpm2.direct_surgical_runtime import install_surgical_runtime",
        "from tools.voxcpm2.direct_surgical_polish_v2 import install_global_polish",
        "from tools.voxcpm2.direct_final_audit_v3 import install_final_audit",
        "from tools.voxcpm2.direct_failure_recovery import install_main_failure_recovery",
    ):
        if needle not in current:
            raise RuntimeError(f"direct CLI installer import missing before flatten: {needle}")

    merged = (
        base.rstrip()
        + "\n\n"
        + installer_block
        + "\n_BASE_ALL = tuple(globals().get('__all__', ()))\n\n"
        + tail.rstrip()
        + '''\n\nif __name__ == "__main__":\n    try:\n        main()\n    except Exception as exc:\n        import traceback\n\n        print(f"ОШИБКА: {exc}", file=sys.stderr)\n        traceback.print_exc()\n        raise SystemExit(1)\n'''
    )
    forbidden = (
        "_direct_max_quality_cli_base.py",
        "exec(compile(",
        "globals()[\"__name__\"]",
        "_ORIGINAL_NAME",
        "Missing direct renderer base snapshot",
    )
    bad = [token for token in forbidden if token in merged]
    if bad:
        raise RuntimeError(f"direct CLI snapshot loader survived: {bad}")
    ast.parse(merged, filename=str(CLI))
    CLI.write_text(merged, encoding="utf-8")
    BASE.unlink()

    polish = POLISH.read_text(encoding="utf-8")
    polish = polish.replace('    "tools/voxcpm2/_direct_max_quality_cli_base.py",\n', "")
    if "_direct_max_quality_cli_base.py" in polish:
        raise RuntimeError("direct surgical polish still fingerprints deleted CLI base")
    ast.parse(polish, filename=str(POLISH))
    POLISH.write_text(polish, encoding="utf-8")

    universal = UNIVERSAL.read_text(encoding="utf-8")
    for stale in (
        '        "tools/voxcpm2/_direct_max_quality_cli_base.py",\n',
        '        "tools/voxcpm2/direct_max_quality_cli/__init__.py",\n',
    ):
        universal = universal.replace(stale, "")
    if "_direct_max_quality_cli_base.py" in universal:
        raise RuntimeError("direct universal runtime still fingerprints deleted CLI base")
    ast.parse(universal, filename=str(UNIVERSAL))
    UNIVERSAL.write_text(universal, encoding="utf-8")

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in ("source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_")):
            continue
        if "_direct_max_quality_cli_base.py" in path.read_text(encoding="utf-8", errors="replace"):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("deleted direct CLI snapshot still referenced: " + ", ".join(sorted(set(blockers))))

    print("flattened _direct_max_quality_cli_base.py into direct_max_quality_cli.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
