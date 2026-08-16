#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def remove_functions(path: str, names: set[str]) -> None:
    target = Path(path)
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        )
    ]
    ast.fix_missing_locations(tree)
    target.write_text(ast.unparse(tree).rstrip() + "\n", encoding="utf-8")


# The semantic wrapper itself was retired. Keep title behavior coverage, not the
# deleted compatibility entrypoint contract.
p = Path("tests/test_dub_runtime_regressions.py")
text = p.read_text(encoding="utf-8")
text = text.replace("from pathlib import Path\n\n", "")
p.write_text(text, encoding="utf-8")
remove_functions(str(p), {"test_semantic_wrapper_stable_entrypoint_exists"})

# Dub Studio now requires an explicit durable TTS profile. Preserve the model
# role assertions while using the current request contract. v1 may AST-unparse
# the file first, so handle both quote styles deterministically.
p = Path("tests/test_gemini_translation_quality.py")
text = p.read_text(encoding="utf-8")
for quote in ('"', "'"):
    old = f"        {quote}gemini{quote},\n    )"
    new = (
        f"        {quote}gemini{quote},\n"
        "        dub_wizard.DEFAULT_MODEL_PROFILE_ID,\n"
        "    )"
    )
    text = text.replace(old, new)
text = text.replace(
    'runtime.index("def validate_translation")',
    'runtime.index("def download_source")',
)
text = text.replace(
    "runtime.index('def validate_translation')",
    "runtime.index('def download_source')",
)
p.write_text(text, encoding="utf-8")

# Audio repair is already source-owned. Patch the actual owner and stop
# asserting that the runtime must masquerade as a package facade.
p = Path("tests/test_clean_request_settings.py")
text = p.read_text(encoding="utf-8")
text = text.replace("repair._legacy.production", "repair.production")
text = text.replace(
    '    assert Path(repair.__file__).name == "__init__.py"\n',
    "",
)
text = text.replace(
    "    assert Path(repair.__file__).name == '__init__.py'\n",
    "",
)
p.write_text(text, encoding="utf-8")

# Tempo preference now belongs to direct_max_quality_cli rather than the old
# stable example entrypoint.
p = Path("tests/test_direct_tempo_boundary_resume.py")
text = p.read_text(encoding="utf-8")
old = '''from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (\n    voxcpm2_cpu_shorts_production as entrypoint,\n)\n'''
new = 'from tools.voxcpm2 import direct_max_quality_cli as entrypoint\n'
if old not in text:
    raise SystemExit("tempo entrypoint import anchor missing")
text = text.replace(old, new, 1)
text = text.replace("entrypoint.HARD_MAX_TEMPO", "entrypoint.MAX_TEMPO")
p.write_text(text, encoding="utf-8")
