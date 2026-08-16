#!/usr/bin/env python3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for path in sorted(ROOT.rglob('*.py')):
    rel = path.relative_to(ROOT)
    if any(p in {'.git','.venv','venv','__pycache__'} for p in rel.parts) or rel.as_posix() == 'tools/zero_runtime_marathon.py':
        continue
    text = path.read_text(encoding='utf-8', errors='replace')
    if 'standardize_russian_title' not in text:
        continue
    print(f'### {rel}')
    for i,line in enumerate(text.splitlines(),1):
        if 'standardize_russian_title' in line:
            print(f'{i:05d}: {line}')
