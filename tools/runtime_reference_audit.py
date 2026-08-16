#!/usr/bin/env python3
"""Temporary reference map for suspicious runtime/patch modules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'tests', '.git', '.venv', 'venv', '__pycache__', '.pytest_cache'}
SELF = {
    'tools/runtime_reference_audit.py', 'tools/runtime_surgery_audit.py',
    'tools/zero_runtime_marathon.py', 'tools/repair_title_runner.py',
}

SERVICE_MODULES = [
    'cloud_media_fallback', 'cut_mode_source_policy', 'cut_replay_delivery_policy',
    'livedub_audio_cache_recovery', 'livedub_audio_companion', 'livedub_audio_dedupe',
    'livedub_audio_quality_guard', 'livedub_cached_delivery_atomicity', 'livedub_deep_audit',
    'livedub_delivery_hardening', 'livedub_dual_audio_policy', 'livedub_info_guard',
    'livedub_info_presentation', 'livedub_long_qa', 'livedub_new_delivery_atomicity',
    'livedub_output_policy', 'livedub_publication', 'livedub_publication_error_diagnostics',
    'livedub_qa_hardening', 'livedub_qa_trust', 'livedub_ru_provenance',
    'shorts_factory_portable_publication', 'shorts_factory_publication',
    'shorts_factory_video_quality', 'shorts_static_runtime',
]

SHADOWS = [
    'handlers/dub_audio_repair', 'handlers/dub_health', 'handlers/dub_wizard',
    'tools/voxcpm2/clean_production_core', 'tools/voxcpm2/clean_runtime_contract',
    'tools/voxcpm2/clean_source_download', 'tools/voxcpm2/continuous_reference_policy',
    'tools/voxcpm2/direct_max_quality_analysis', 'tools/voxcpm2/direct_max_quality_cli',
    'tools/voxcpm2/direct_max_quality_render', 'tools/voxcpm2/direct_monolith_contract',
    'tools/voxcpm2/direct_russian_cadence', 'tools/voxcpm2/direct_source_prosody',
    'tools/voxcpm2/direct_tail_artifact', 'tools/voxcpm2/direct_timeline_delivery_qa',
    'tools/voxcpm2/dub_job_preflight', 'tools/voxcpm2/dub_quality_v4',
    'tools/voxcpm2/dub_worker_hardened', 'tools/voxcpm2/expressive_continuity',
    'tools/voxcpm2/final_media_qa', 'tools/voxcpm2/final_media_spatial_bed',
    'tools/voxcpm2/generic_clean_audio_repair_runtime',
    'tools/voxcpm2/generic_clean_direct_runtime', 'tools/voxcpm2/generic_project_runtime',
    'tools/voxcpm2/professional_audio_qa_v45',
]


def production_files():
    for path in ROOT.rglob('*.py'):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SELF or any(part in SKIP for part in path.relative_to(ROOT).parts):
            continue
        yield rel, path.read_text(encoding='utf-8', errors='replace')


def refs_for(needles, own_paths):
    rows=[]
    for rel,text in production_files():
        if rel in own_paths:
            continue
        for i,line in enumerate(text.splitlines(),1):
            if any(n in line for n in needles):
                rows.append((rel,i,line.strip()))
    return rows


def main():
    for name in SERVICE_MODULES:
        own={f'services/{name}.py', f'services/{name}/__init__.py'}
        needles=(f'services.{name}', f'from services import {name}', name)
        rows=refs_for(needles, own)
        print(f'\n## SERVICE {name} refs={len(rows)}')
        for r in rows[:80]: print(f'{r[0]}:{r[1]}: {r[2]}')
    for stem in SHADOWS:
        py=f'{stem}.py'; init=f'{stem}/__init__.py'
        py_exists=(ROOT/py).is_file(); init_exists=(ROOT/init).is_file()
        module=stem.replace('/','.')
        leaf=stem.split('/')[-1]
        rows=refs_for((module, leaf), {py,init})
        print(f'\n## SHADOW {stem} py={py_exists} package={init_exists} refs={len(rows)}')
        for r in rows[:100]: print(f'{r[0]}:{r[1]}: {r[2]}')
    print('\nREFERENCE_AUDIT_DONE')

if __name__ == '__main__':
    main()
