#!/usr/bin/env python3
from pathlib import Path
import runpy

script = Path("tools/_stage_remaining_runtime_contracts.py")
text = script.read_text(encoding="utf-8")
old = '''    assert "core.globals" not in owner\\n'''
new = '''    assert "from core.globals" not in owner\\n    assert "import core.globals" not in owner\\n'''
if text.count(old) != 1:
    raise SystemExit(f"pre-main import assertion correction count={text.count(old)}")
script.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")
runpy.run_path(str(script), run_name="__main__")
Path(__file__).unlink(missing_ok=True)
