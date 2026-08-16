#!/usr/bin/env python3
"""Move rate-limit async-state reset into core.database source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "core" / "database.py"
MAIN = ROOT / "main.py"


def main() -> int:
    db = DB.read_text(encoding="utf-8")
    anchor = "_rate_limit_async_locks: dict[int, asyncio.Lock] = {}\n_rate_limit_locks_guard = asyncio.Lock()\n"
    owner = '''_rate_limit_async_locks: dict[int, asyncio.Lock] = {}\n_rate_limit_locks_guard = asyncio.Lock()\n\n\ndef reset_rate_limit_async_state() -> int:\n    """Reset event-loop-bound rate-limit locks after bot loop recreation.\n\n    The database module owns both the per-user lock map and its guard, so callers\n    never mutate private module state across an import boundary. Returns the number\n    of discarded per-user locks for diagnostics.\n    """\n    global _rate_limit_locks_guard\n    stale = len(_rate_limit_async_locks)\n    _rate_limit_async_locks.clear()\n    _rate_limit_locks_guard = asyncio.Lock()\n    return stale\n'''
    if anchor not in db:
        raise RuntimeError("rate-limit lock owner anchor missing")
    db = db.replace(anchor, owner, 1)
    ast.parse(db, filename=str(DB))
    DB.write_text(db, encoding="utf-8")

    main_text = MAIN.read_text(encoding="utf-8")
    old_import = '''from core.database import (\n    db_init, asettings_get,\n    GEMINI_MODEL, WHITELIST_IDS, ADMIN_IDS,\n    set_effective_max_file_size_mb,\n)\n'''
    new_import = '''from core.database import (\n    db_init, asettings_get,\n    GEMINI_MODEL, WHITELIST_IDS, ADMIN_IDS,\n    reset_rate_limit_async_state,\n    set_effective_max_file_size_mb,\n)\n'''
    if old_import not in main_text:
        raise RuntimeError("main core.database import anchor missing")
    main_text = main_text.replace(old_import, new_import, 1)
    old_block = '''    import core.database as _core_db\n    _core_db._rate_limit_async_locks.clear()\n    _core_db._rate_limit_locks_guard = asyncio.Lock()\n'''
    new_block = '''    _stale_rate_limit_locks = reset_rate_limit_async_state()\n    if _stale_rate_limit_locks:\n        logger.info(\n            "🧹 Очищено rate-limit locks от предыдущего event loop: %d",\n            _stale_rate_limit_locks,\n        )\n'''
    if old_block not in main_text:
        raise RuntimeError("main private rate-limit mutation block missing")
    main_text = main_text.replace(old_block, new_block, 1)
    forbidden = (
        "_core_db._rate_limit_async_locks",
        "_core_db._rate_limit_locks_guard",
        "import core.database as _core_db",
    )
    bad = [token for token in forbidden if token in main_text]
    if bad:
        raise RuntimeError(f"main private database mutation survived: {bad}")
    ast.parse(main_text, filename=str(MAIN))
    MAIN.write_text(main_text, encoding="utf-8")
    print("rate-limit async reset is source-owned by core.database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
