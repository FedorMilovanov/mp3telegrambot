#!/usr/bin/env python3
"""Correct generated escape literals, run the one-shot QA codemod, self-clean."""
from pathlib import Path
import runpy

codemod = Path("tools/_stage_source_owned_livedub_qa.py")
text = codemod.read_text(encoding="utf-8")
replacements = {
    'return safe_trim_caption("\\n".join(lines), 3900)':
        'return safe_trim_caption("\\\\n".join(lines), 3900)',
    'return "\\n".join(lines)[:3900]':
        'return "\\\\n".join(lines)[:3900]',
    '_OLD_LOW_CONFIDENCE_NOTE + "\\n"':
        '_OLD_LOW_CONFIDENCE_NOTE + "\\\\n"',
}
for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"escape correction anchor count={count}: {old!r}")
    text = text.replace(old, new, 1)
codemod.write_text(text, encoding="utf-8")
runpy.run_path(str(codemod), run_name="__main__")
codemod.unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
