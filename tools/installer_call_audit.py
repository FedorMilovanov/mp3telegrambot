#!/usr/bin/env python3
"""Temporary AST audit of install_* definitions and production call sites."""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKIP={'.git','.venv','venv','__pycache__','.pytest_cache','tests'}
SELF={'tools/installer_call_audit.py','tools/runtime_surgery_audit.py','tools/runtime_reference_audit.py','tools/dead_runtime_cleanup.py','tools/zero_runtime_marathon.py','tools/repair_title_runner.py'}

def dotted(n):
    if isinstance(n,ast.Name): return n.id
    if isinstance(n,ast.Attribute):
        b=dotted(n.value); return f'{b}.{n.attr}' if b else n.attr
    return ''

def main():
    defs=[]; calls=[]
    for path in ROOT.rglob('*.py'):
        rel=path.relative_to(ROOT).as_posix()
        if rel in SELF or any(p in SKIP for p in path.relative_to(ROOT).parts): continue
        text=path.read_text(encoding='utf-8',errors='replace')
        try: tree=ast.parse(text)
        except SyntaxError: continue
        parents={}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent): parents[child]=parent
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and (node.name.startswith('install_') or node.name.startswith('_install_')):
                defs.append((node.name,rel,node.lineno))
            if isinstance(node,ast.Call):
                name=dotted(node.func)
                leaf=name.rsplit('.',1)[-1]
                if leaf.startswith('install_') or leaf.startswith('_install_'):
                    scope='<module>'
                    p=parents.get(node)
                    while p is not None:
                        if isinstance(p,(ast.FunctionDef,ast.AsyncFunctionDef)):
                            scope=p.name; break
                        p=parents.get(p)
                    calls.append((leaf,name,rel,node.lineno,scope))
    by=defaultdict(list)
    for row in calls: by[row[0]].append(row)
    print('## INSTALLER DEFINITIONS / CALLS')
    for name,rel,line in sorted(defs):
        rows=by.get(name,[])
        external=[r for r in rows if r[2]!=rel]
        top=[r for r in rows if r[4]=='<module>']
        print(f'\n{name} DEF {rel}:{line} calls={len(rows)} external={len(external)} top_level={len(top)}')
        for r in rows:
            print(f'  CALL {r[2]}:{r[3]} scope={r[4]} expr={r[1]}')
    print('\nINSTALLER_CALL_AUDIT_DONE')
if __name__=='__main__': main()
