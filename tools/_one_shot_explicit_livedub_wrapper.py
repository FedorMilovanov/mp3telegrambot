#!/usr/bin/env python3
"""Run the branch codemod, then repair its one escaped generated newline."""
from pathlib import Path
import runpy

runpy.run_path("tools/_one_shot_explicit_livedub_refactor.py", run_name="__main__")

path = Path("pipelines/main_pipeline.py")
text = path.read_text(encoding="utf-8")
old = 'f"<b>{_livedub_title_html}</b>\n🎬 Живые голоса Яндекса"'
new = 'f"<b>{_livedub_title_html}</b>\\n🎬 Живые голоса Яндекса"'
count = text.count(old)
if count != 1:
    raise SystemExit(f"generated LiveDub newline anchor count={count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
