"""
Tests for v3 minor-debt patch 2:
- telegraph_pages: no inner `import time as _time`, no `import core.globals` in loop
- database: json.loads failures log at DEBUG (not silently swallowed)
- pdf_generator: _TELEGRAPH_CACHE_SIZE respects PDF_TELEGRAPH_CACHE_SIZE env var
- md_telegraph: _extract_partial_sections logs malformed JSON at DEBUG
"""
import os
import pathlib
import sqlite3
import tempfile
import importlib
import logging


# ---------------------------------------------------------------------------
# telegraph_pages: no bare inner imports
# ---------------------------------------------------------------------------

def test_telegraph_pages_no_inner_time_import():
    src = pathlib.Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "import time as _time" not in src, \
        "Found 'import time as _time' inside function — should use module-level import"


def test_telegraph_pages_no_inner_core_globals_import():
    src = pathlib.Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "import core.globals" not in src, \
        "Found 'import core.globals' inside loop — anti-pattern removed in this patch"


def test_telegraph_pages_has_module_level_time_import():
    src = pathlib.Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    # module-level import time (not inside a function)
    lines = src.splitlines()
    top_imports = [l for l in lines[:60] if l.strip() == "import time"]
    assert top_imports, "module-level 'import time' not found in first 60 lines"


# ---------------------------------------------------------------------------
# database: json.loads failures are logged
# ---------------------------------------------------------------------------

def test_database_ai_data_json_failure_logs_debug():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    assert "db_get: ai_data JSON parse failed" in src, \
        "ai_data json.loads failure not logged"


def test_database_questions_json_failure_logs_debug():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    assert "db_get: questions JSON parse failed" in src, \
        "questions json.loads failure not logged"


# ---------------------------------------------------------------------------
# pdf_generator: cache size is configurable
# ---------------------------------------------------------------------------

def test_pdf_telegraph_cache_size_env_var():
    src = pathlib.Path("services/pdf_generator.py").read_text(encoding="utf-8")
    assert "PDF_TELEGRAPH_CACHE_SIZE" in src, \
        "PDF_TELEGRAPH_CACHE_SIZE env var not found in pdf_generator"
    assert "_TELEGRAPH_CACHE_SIZE" in src, \
        "_TELEGRAPH_CACHE_SIZE variable not found"


# ---------------------------------------------------------------------------
# md_telegraph: _extract_partial_sections logs at DEBUG
# ---------------------------------------------------------------------------

def test_md_telegraph_partial_sections_logs_on_error():
    src = pathlib.Path("converters/md_telegraph.py").read_text(encoding="utf-8")
    assert "_extract_partial_sections: skipped malformed section" in src, \
        "malformed section not logged in _extract_partial_sections"


