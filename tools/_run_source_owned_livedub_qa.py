#!/usr/bin/env python3
"""Run the one-shot QA codemod, repair exact generated literals, self-clean."""
from pathlib import Path
import runpy

codemod = Path("tools/_stage_source_owned_livedub_qa.py")
runpy.run_path(str(codemod), run_name="__main__")

repairs = {
    "services/livedub_long_qa.py": {
        'return safe_trim_caption("\n".join(lines), 3900)':
            'return safe_trim_caption(chr(10).join(lines), 3900)',
        'return "\n".join(lines)[:3900]':
            'return chr(10).join(lines)[:3900]',
    },
    "services/livedub_qa_hardening.py": {
        '_OLD_LOW_CONFIDENCE_NOTE + "\n"':
            '_OLD_LOW_CONFIDENCE_NOTE + chr(10)',
    },
}
for file_name, mapping in repairs.items():
    path = Path(file_name)
    text = path.read_text(encoding="utf-8")
    for old, new in mapping.items():
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"generated repair anchor count={count}: {file_name}: {old!r}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

codemod.unlink(missing_ok=True)
Path(__file__).unlink(missing_ok=True)
