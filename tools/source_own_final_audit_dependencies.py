#!/usr/bin/env python3
"""Move final-audit cross-module mutations into their true source owners."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETRY = ROOT / "tools" / "voxcpm2" / "direct_retry_epoch.py"
GUARD = ROOT / "tools" / "voxcpm2" / "direct_timing_guard.py"
SIO = ROOT / "tools" / "voxcpm2" / "direct_surgical_io.py"

STRICT_ID = '''def _strict_segment_id(value: Any) -> int:\n    if isinstance(value, bool) or not isinstance(value, Integral):\n        raise RuntimeError(f"Некорректный segment_id: {value!r}")\n    result = int(value)\n    if not 1 <= result <= MAX_SEGMENT_ID:\n        raise RuntimeError(\n            f"segment_id должен быть в диапазоне 1..{MAX_SEGMENT_ID}: {result}."\n        )\n    return result\n'''

PRUNE_HELPER = '''def _prune_marker_archives(directory: Path, stem: str, *, limit: int = 8) -> None:\n    files: list[Path] = []\n    for pattern in (f"{stem}.stale-*", f"{stem}.corrupt-*", f"{stem}.oversized-*"):\n        files.extend(path for path in Path(directory).glob(pattern) if path.is_file())\n    files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)\n    for stale in files[max(0, int(limit)):]:\n        stale.unlink(missing_ok=True)\n'''


def replace_function(text: str, path: Path, name: str, source: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            lines[start : (node.end_lineno or node.lineno)] = ["\n\n" + source.strip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def main() -> int:
    retry = RETRY.read_text(encoding="utf-8")
    if "from numbers import Integral\n" not in retry:
        retry = retry.replace("from pathlib import Path\n", "from pathlib import Path\nfrom numbers import Integral\n", 1)
    retry = replace_function(retry, RETRY, "_strict_segment_id", STRICT_ID)
    if "Integral" not in retry or "result = int(value)" not in retry:
        raise RuntimeError("strict retry segment id was not installed")
    ast.parse(retry, filename=str(RETRY))
    RETRY.write_text(retry, encoding="utf-8")

    guard = GUARD.read_text(encoding="utf-8")
    if "def _prune_marker_archives(" not in guard:
        anchor = "\ndef load_matching_timing_block(\n"
        if anchor not in guard:
            raise RuntimeError("timing-block load anchor missing")
        guard = guard.replace(anchor, "\n\n" + PRUNE_HELPER.strip() + "\n" + anchor, 1)
    old_load = '''def load_matching_timing_block(\n    work_dir: Path,\n    *,\n    segment: Mapping[str, Any],\n    signature_context: Mapping[str, Any] | None,\n) -> dict[str, Any] | None:\n    path = timing_block_path(work_dir, int(segment.get("id") or 0))\n    if not path.is_file():\n        return None\n    try:\n        if path.stat().st_size > MAX_MARKER_BYTES:\n            _archive_timing_marker(path, "oversized")\n            return None\n        payload = json.loads(path.read_text(encoding="utf-8-sig"))\n    except (OSError, json.JSONDecodeError):\n        _archive_timing_marker(path, "corrupt-json")\n        return None\n    if not isinstance(payload, dict) or (\n        payload.get("schema_version") != MARKER_SCHEMA_VERSION\n        or payload.get("policy") != SURGICAL_GUARD_POLICY\n        or int(payload.get("segment_id") or 0) != int(segment.get("id") or 0)\n        or not isinstance(payload.get("evidence"), dict)\n        or not isinstance(payload.get("recommendation"), dict)\n    ):\n        _archive_timing_marker(path, "contract-mismatch")\n        return None\n    expected = failure_scope_fingerprint(\n        segment, signature_context=signature_context\n    )\n    if payload.get("signature") == expected:\n        return payload\n    _archive_timing_marker(path, "input-changed")\n    return None\n'''
    new_load = '''def load_matching_timing_block(\n    work_dir: Path,\n    *,\n    segment: Mapping[str, Any],\n    signature_context: Mapping[str, Any] | None,\n) -> dict[str, Any] | None:\n    path = timing_block_path(work_dir, int(segment.get("id") or 0))\n    if not path.is_file():\n        return None\n    try:\n        if path.stat().st_size > MAX_MARKER_BYTES:\n            _archive_timing_marker(path, "oversized")\n            return None\n        try:\n            payload = json.loads(path.read_text(encoding="utf-8-sig"))\n        except (OSError, json.JSONDecodeError):\n            _archive_timing_marker(path, "corrupt-json")\n            return None\n        if not isinstance(payload, dict) or (\n            payload.get("schema_version") != MARKER_SCHEMA_VERSION\n            or payload.get("policy") != SURGICAL_GUARD_POLICY\n            or int(payload.get("segment_id") or 0) != int(segment.get("id") or 0)\n            or not isinstance(payload.get("evidence"), dict)\n            or not isinstance(payload.get("recommendation"), dict)\n        ):\n            _archive_timing_marker(path, "contract-mismatch")\n            return None\n        expected = failure_scope_fingerprint(\n            segment, signature_context=signature_context\n        )\n        if payload.get("signature") == expected:\n            return payload\n        _archive_timing_marker(path, "input-changed")\n        return None\n    finally:\n        _prune_marker_archives(path.parent, path.name, limit=8)\n'''
    if old_load not in guard:
        raise RuntimeError("timing-block loader diverged before audit ownership")
    guard = guard.replace(old_load, new_load, 1)
    ast.parse(guard, filename=str(GUARD))
    GUARD.write_text(guard, encoding="utf-8")

    sio = SIO.read_text(encoding="utf-8")
    old_init = '''    def __init__(\n        self,\n        backend: Any,\n        *,\n        encode: int,\n        output: int,\n        log: Callable[[str], Any],\n    ) -> None:\n        self._backend = backend\n        self._encode = int(encode)\n        self._output = int(output)\n        self._log = log\n        self._session: LazySession | None = None\n'''
    new_init = '''    def __init__(\n        self,\n        backend: Any,\n        *,\n        encode: int,\n        output: int,\n        log: Callable[[str], Any],\n        model_discovery_callback: Callable[[Path], Any] | None = None,\n    ) -> None:\n        self._backend = backend\n        self._encode = int(encode)\n        self._output = int(output)\n        self._log = log\n        self._model_discovery_callback = model_discovery_callback\n        self._session: LazySession | None = None\n'''
    if old_init not in sio:
        raise RuntimeError("LazyBackend init contract diverged")
    sio = sio.replace(old_init, new_init, 1)
    anchor = '''    def __getattr__(self, name: str) -> Any:\n        return getattr(self._backend, name)\n\n'''
    callback_methods = '''    def __getattr__(self, name: str) -> Any:\n        return getattr(self._backend, name)\n\n    def set_model_discovery_callback(\n        self,\n        callback: Callable[[Path], Any] | None,\n    ) -> None:\n        self._model_discovery_callback = callback\n\n    def discover_model(self, archive_root: Path) -> Path:\n        model = Path(self._backend.discover_model(Path(archive_root))).resolve()\n        callback = self._model_discovery_callback\n        if callback is not None:\n            callback(model)\n        return model\n\n'''
    if anchor not in sio:
        raise RuntimeError("LazyBackend getattr anchor missing")
    sio = sio.replace(anchor, callback_methods, 1)
    ast.parse(sio, filename=str(SIO))
    SIO.write_text(sio, encoding="utf-8")

    print("final-audit dependencies are source-owned by retry, timing and LazyBackend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
