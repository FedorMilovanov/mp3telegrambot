#!/usr/bin/env python3
"""Temporary branch-only refactor runner for the zero-runtime-surgery marathon."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    path = ROOT / "services" / "dub_studio_runtime.py"
    text = path.read_text(encoding="utf-8")
    import_anchor = "    from handlers.dub_quickstart import register_dub_quickstart_handler\n    from handlers.dub_wizard import register_dub_wizard_handlers\n"
    import_new = (
        "    from handlers.dub_quickstart import register_dub_quickstart_handler\n"
        "    from handlers.dub_wizard import register_dub_wizard_handlers\n"
        "    from handlers.dub_multicommand import register_dub_multicommand_handler\n"
    )
    if import_anchor not in text:
        raise RuntimeError("Dub Studio handler import anchor missing")
    text = text.replace(import_anchor, import_new, 1)
    call_anchor = "    register_dub_quickstart_handler(application)\n    ensure_worker_running()\n"
    call_new = (
        "    register_dub_quickstart_handler(application)\n"
        "    register_dub_multicommand_handler(application)\n"
        "    ensure_worker_running()\n"
    )
    if call_anchor not in text:
        raise RuntimeError("Dub Studio handler registration anchor missing")
    text = text.replace(call_anchor, call_new, 1)
    path.write_text(text, encoding="utf-8")

    shadow = ROOT / "services" / "dub_studio_runtime" / "__init__.py"
    if not shadow.is_file():
        raise RuntimeError("Dub Studio shadow package is missing")
    shadow.unlink()
    print("deleted services/dub_studio_runtime/__init__.py")
    print("registered multicommand directly in source-owned Dub Studio composition")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
