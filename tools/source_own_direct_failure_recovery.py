#!/usr/bin/env python3
"""Make direct failure recovery explicit instead of rebinding CLI main at runtime."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
RECOVERY = ROOT / "tools" / "voxcpm2" / "direct_failure_recovery.py"


def rename_first_main(text: str) -> str:
    tree = ast.parse(text, filename=str(CLI))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            line = lines[node.lineno - 1]
            if "def main(" not in line:
                raise RuntimeError("unexpected direct CLI main definition")
            lines[node.lineno - 1] = line.replace("def main(", "def _render_main(", 1)
            return "".join(lines)
    raise RuntimeError("direct CLI main definition not found")


def main() -> int:
    recovery = RECOVERY.read_text(encoding="utf-8")
    recovery = recovery.replace("from collections.abc import Mapping, MutableMapping\n", "from collections.abc import Mapping\n")
    recovery = recovery.replace("from typing import Any\n", "from typing import Any, Callable\n")
    old = '''def install_main_failure_recovery(namespace: MutableMapping[str, Any]) -> None:\n    """Advance once for a new synthesis failure; never advance a blocked repeat."""\n    original = namespace.get("main")\n    invalidate = namespace.get("invalidate_segment_for_retry")\n    if not callable(original) or not callable(invalidate):\n        raise RuntimeError("direct main recovery contract is incomplete.")\n\n    def main() -> Any:\n        try:\n            return original()\n        except RuntimeError as exc:\n            message = str(exc)\n            failure_type = getattr(\n                direct_timing_guard,\n                "RetryableSynthesisFailure",\n                None,\n            )\n            structured = bool(\n                isinstance(failure_type, type)\n                and isinstance(exc, failure_type)\n            )\n            if structured:\n                if not bool(exc.advance_retry):\n                    raise\n                segment = dict(exc.segment)\n                segment_id = int(exc.segment_id)\n                evidence = {\n                    **dict(exc.evidence),\n                    "policy": POLICY,\n                    "early_stop_kind": exc.failure_kind,\n                    "early_stop_message": message[:1000],\n                }\n            else:\n                if not any(marker in message for marker in _LEGACY_RECOVERABLE):\n                    raise\n                match = _SEGMENT_RE.search(message)\n                segments_value = _flag("--segments-json")\n                if match is None or not segments_value:\n                    raise\n                segment_id = int(match.group(1))\n                segment = _segment_from_json(\n                    Path(segments_value).resolve(),\n                    segment_id,\n                )\n                if not isinstance(segment, dict):\n                    raise\n                evidence = {\n                    "policy": POLICY,\n                    "early_stop_kind": "legacy_message_fallback",\n                    "early_stop_message": message[:1000],\n                }\n\n            work_value = _flag("--work-dir")\n            if not work_value:\n                raise\n            work_dir = Path(work_value).resolve()\n            try:\n                result = invalidate(\n                    work_dir,\n                    segment,\n                    reason="raw_candidate_hard_failure",\n                    evidence=evidence,\n                )\n            except Exception as recovery_error:\n                raise RuntimeError(\n                    f"{message} Retry-state recovery failed: "\n                    f"{type(recovery_error).__name__}: {recovery_error}"\n                ) from exc\n            if not isinstance(result, Mapping):\n                raise RuntimeError(\n                    f"{message} Retry-state recovery returned invalid payload."\n                ) from exc\n            raise RuntimeError(\n                f"{message} Retry scope advanced to {_reported_epoch(result)}."\n            ) from exc\n\n    namespace["main"] = main\n    namespace["EARLY_STOP_RECOVERY_POLICY"] = POLICY\n\n\n__all__ = ["POLICY", "install_main_failure_recovery"]\n'''
    new = '''def run_with_failure_recovery(\n    original: Callable[[], Any],\n    invalidate: Callable[..., Any],\n) -> Any:\n    """Advance once for a new synthesis failure; never advance a blocked repeat."""\n    if not callable(original) or not callable(invalidate):\n        raise TypeError("direct main recovery contract is incomplete.")\n    try:\n        return original()\n    except RuntimeError as exc:\n        message = str(exc)\n        failure_type = getattr(direct_timing_guard, "RetryableSynthesisFailure", None)\n        structured = bool(\n            isinstance(failure_type, type) and isinstance(exc, failure_type)\n        )\n        if structured:\n            if not bool(exc.advance_retry):\n                raise\n            segment = dict(exc.segment)\n            evidence = {\n                **dict(exc.evidence),\n                "policy": POLICY,\n                "early_stop_kind": exc.failure_kind,\n                "early_stop_message": message[:1000],\n            }\n        else:\n            if not any(marker in message for marker in _LEGACY_RECOVERABLE):\n                raise\n            match = _SEGMENT_RE.search(message)\n            segments_value = _flag("--segments-json")\n            if match is None or not segments_value:\n                raise\n            segment_id = int(match.group(1))\n            segment = _segment_from_json(Path(segments_value).resolve(), segment_id)\n            if not isinstance(segment, dict):\n                raise\n            evidence = {\n                "policy": POLICY,\n                "early_stop_kind": "legacy_message_fallback",\n                "early_stop_message": message[:1000],\n            }\n\n        work_value = _flag("--work-dir")\n        if not work_value:\n            raise\n        work_dir = Path(work_value).resolve()\n        try:\n            result = invalidate(\n                work_dir,\n                segment,\n                reason="raw_candidate_hard_failure",\n                evidence=evidence,\n            )\n        except Exception as recovery_error:\n            raise RuntimeError(\n                f"{message} Retry-state recovery failed: "\n                f"{type(recovery_error).__name__}: {recovery_error}"\n            ) from exc\n        if not isinstance(result, Mapping):\n            raise RuntimeError(\n                f"{message} Retry-state recovery returned invalid payload."\n            ) from exc\n        raise RuntimeError(\n            f"{message} Retry scope advanced to {_reported_epoch(result)}."\n        ) from exc\n\n\n__all__ = ["POLICY", "run_with_failure_recovery"]\n'''
    if old not in recovery:
        raise RuntimeError("direct failure recovery installer block diverged")
    recovery = recovery.replace(old, new, 1)
    forbidden_recovery = ("MutableMapping", "namespace[", "install_main_failure_recovery")
    bad = [token for token in forbidden_recovery if token in recovery]
    if bad:
        raise RuntimeError(f"failure recovery mutation survived: {bad}")
    ast.parse(recovery, filename=str(RECOVERY))
    RECOVERY.write_text(recovery, encoding="utf-8")

    cli = rename_first_main(CLI.read_text(encoding="utf-8"))
    cli = cli.replace(
        "from tools.voxcpm2.direct_failure_recovery import install_main_failure_recovery\n",
        "from tools.voxcpm2.direct_failure_recovery import (\n    POLICY as EARLY_STOP_RECOVERY_POLICY,\n    run_with_failure_recovery,\n)\n",
        1,
    )
    cli = cli.replace("install_main_failure_recovery(globals())\n", "", 1)
    wrapper = '''def main() -> Any:\n    return run_with_failure_recovery(_render_main, invalidate_segment_for_retry)\n'''
    if "\nmain = main\n" not in cli:
        raise RuntimeError("direct CLI final main binding not found")
    cli = cli.replace("\nmain = main\n", "\n" + wrapper + "\n", 1)
    forbidden_cli = (
        "install_main_failure_recovery",
        "namespace[\"main\"]",
    )
    bad = [token for token in forbidden_cli if token in cli]
    if bad:
        raise RuntimeError(f"direct CLI recovery installer survived: {bad}")
    parsed = ast.parse(cli, filename=str(CLI))
    mains = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
    render_mains = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "_render_main"]
    if len(mains) != 1 or len(render_mains) != 1:
        raise RuntimeError(f"unexpected direct CLI main ownership: main={len(mains)} render={len(render_mains)}")
    CLI.write_text(cli, encoding="utf-8")
    print("direct failure recovery is explicit source composition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
