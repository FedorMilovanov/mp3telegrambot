#!/usr/bin/env python3
"""Temporary branch-only AST/text audit. Deleted before merge."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache'}
SKIP_PREFIXES = ('tests/',)
SKIP_FILES = {
    'tools/runtime_surgery_audit.py',
    'tools/zero_runtime_marathon.py',
    'tools/repair_title_runner.py',
}
TEXT_NEEDLES = (
    'sys.modules', 'sys.meta_path', 'spec_from_file_location',
    'module_from_spec', '__class__ =', 'setattr(', 'ContextVar(',
    'ApplicationBuilder.build =', 'Application.start =',
)


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f'{base}.{node.attr}' if base else node.attr
    if isinstance(node, ast.Subscript):
        base = dotted(node.value)
        return f'{base}[...]' if base else '[...]'
    return ''


def main() -> int:
    attr_assigns: list[tuple[str,int,str]] = []
    setattr_calls: list[tuple[str,int,str]] = []
    import_hooks: list[tuple[str,int,str]] = []
    installers: list[tuple[str,int,str]] = []
    contexts: list[tuple[str,int,str]] = []
    parse_errors: list[tuple[str,str]] = []

    for path in sorted(ROOT.rglob('*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
            continue
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as exc:
            parse_errors.append((rel, str(exc)))
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    targets = [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        name = dotted(target)
                        attr_assigns.append((rel, node.lineno, name))
                    elif isinstance(target, ast.Subscript) and dotted(target.value) in {'sys.modules', 'sys.meta_path'}:
                        import_hooks.append((rel, node.lineno, dotted(target)))
            elif isinstance(node, ast.Call):
                name = dotted(node.func)
                if name == 'setattr':
                    expr = ast.get_source_segment(text, node) or 'setattr(...)'
                    setattr_calls.append((rel, node.lineno, expr.replace('\n',' ')[:260]))
                if name in {'importlib.util.spec_from_file_location', 'importlib.util.module_from_spec'}:
                    import_hooks.append((rel, node.lineno, name))
                if name == 'ContextVar' or name.endswith('.ContextVar'):
                    contexts.append((rel, node.lineno, ast.get_source_segment(text,node) or name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith('install_') or node.name.startswith('_install_'):
                    installers.append((rel, node.lineno, node.name))
        for i, line in enumerate(lines,1):
            if any(n in line for n in TEXT_NEEDLES):
                if 'ContextVar(' in line or 'setattr(' in line or 'sys.modules' in line or 'sys.meta_path' in line or 'spec_from_file_location' in line or '__class__ =' in line:
                    import_hooks.append((rel, i, line.strip()[:260]))

    def emit(title, rows):
        print(f'\n## {title} ({len(rows)})')
        seen = set()
        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            print(f'{row[0]}:{row[1]}: {row[2]}')

    emit('ATTRIBUTE ASSIGNMENTS', attr_assigns)
    emit('SETATTR CALLS', setattr_calls)
    emit('IMPORT/MODULE HOOK SIGNALS', import_hooks)
    emit('CONTEXTVARS', contexts)
    emit('INSTALLER FUNCTIONS', installers)
    if parse_errors:
        emit('PARSE ERRORS', [(a,0,b) for a,b in parse_errors])
    print('\nAUDIT_DONE')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
