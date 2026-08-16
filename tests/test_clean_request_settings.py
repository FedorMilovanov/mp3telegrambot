from __future__ import annotations
import json
from pathlib import Path
import pytest
from tools.voxcpm2 import clean_request_settings as settings
from tools.voxcpm2 import clean_segment_normalizer as normalizer
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair
ROOT = Path(__file__).resolve().parents[1]

def test_explicit_zero_settings_are_preserved() -> None:
    assert settings.values({'original_level': 0, 'russian_delay_ms': 0}) == {'policy': settings.POLICY, 'original_level': 0.0, 'russian_delay_ms': 0}
    assert settings.original_level({}) == pytest.approx(0.18)
    assert settings.russian_delay_ms({}) == 420

@pytest.mark.parametrize('request_payload', [{'original_level': True}, {'original_level': float('nan')}, {'original_level': 1.01}, {'russian_delay_ms': True}, {'russian_delay_ms': 1.5}, {'russian_delay_ms': -1}, {'russian_delay_ms': settings.MAX_RUSSIAN_DELAY_MS + 1}])
def test_invalid_mix_settings_fail_closed(request_payload) -> None:
    with pytest.raises(RuntimeError):
        settings.values(request_payload)

def test_manifest_is_repaired_to_actual_zero_settings(tmp_path: Path) -> None:
    root = tmp_path / 'project'
    output = root / 'output'
    output.mkdir(parents=True)
    manifest = {'phase': 'completed', 'original_level': 0.18, 'russian_delay_ms': 420, 'telegram_outputs': [{'label': 'Готовый ролик: оригинал 18%, русский с задержкой 420 мс'}, {'label': 'Финальные русские субтитры с задержкой 420 мс'}]}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    result = settings.repair_manifest(root, {'original_level': 0, 'russian_delay_ms': 0})
    assert result['settings_policy'] == settings.POLICY
    assert result['settings_delay_source'] == 'request'
    assert result['original_level'] == 0.0
    assert result['russian_delay_ms'] == 0
    labels = [item['label'] for item in result['telegram_outputs']]
    assert labels == ['Готовый ролик: оригинал 0%, русский без задержки', 'Финальные русские субтитры без задержки']
    stored = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert stored == result

def test_audio_repair_manifest_uses_delay_proven_by_segments(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / 'project'
    output = root / 'output'
    output.mkdir(parents=True)
    manifest_path = output / 'manifest.json'
    manifest = {'phase': 'completed', 'original_level': 0.18, 'russian_delay_ms': 420, 'telegram_outputs': [{'label': 'Готовый ролик: оригинал 18%, русский с задержкой 420 мс'}, {'label': 'Финальные русские субтитры с задержкой 420 мс'}]}
    (root / 'segments_ru_final.json').write_text(json.dumps([{'id': 1, 'start_delay_ms': 0}, {'id': 2, 'start_delay_ms': 0}]), encoding='utf-8')

    def fake_legacy_update(path: Path, payload: dict, **_kwargs) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(repair, '_legacy_update_manifest', fake_legacy_update)
    monkeypatch.setattr(repair.production, 'load_request', lambda _root: {'original_level': 0, 'russian_delay_ms': 420})
    repair._update_manifest(manifest_path, manifest, selected_ids=[1, 2], repair_all=True, seed=100, report_path=output / 'audio_repair_report.json', marker={})
    stored = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert stored['settings_delay_source'] == 'segments'
    assert stored['original_level'] == 0.0
    assert stored['russian_delay_ms'] == 0
    assert [item['label'] for item in stored['telegram_outputs']] == ['Готовый ролик: оригинал 0%, русский без задержки', 'Финальные русские субтитры без задержки']

def test_full_repair_normalizer_preserves_zero_delay(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / 'project'
    (root / 'input').mkdir(parents=True)
    (root / 'output').mkdir(parents=True)
    (root / 'segments_ru_final.json').write_text(json.dumps([{'id': 1, 'start': 0.0, 'end': 2.0, 'source_end': 2.0, 'start_delay_ms': 420, 'text': 'Нулевая задержка.', 'source': 'Zero delay.'}], ensure_ascii=False), encoding='utf-8')
    (root / 'input' / 'audio_repair.json').write_text(json.dumps({'repair_all': True, 'segment_ids': [1]}), encoding='utf-8')
    monkeypatch.setattr(normalizer.production, 'log', lambda _message: None)
    normalizer.normalize(root, {'russian_delay_ms': 0}, duration=5.0)
    segments = json.loads((root / 'segments_ru_final.json').read_text(encoding='utf-8'))
    assert [item['start_delay_ms'] for item in segments] == [0]
    assert '00:00:00,000 --> 00:00:02,000' in (root / 'output' / 'russian_subtitles.srt').read_text(encoding='utf-8')
    source = Path(normalizer.__file__).read_text(encoding='utf-8')
    assert 'clean_request_settings.russian_delay_ms(request)' in source
    assert 'request.get("russian_delay_ms") or 420' not in source


def test_repair_owner_preserves_runtime_helpers() -> None:
    assert callable(repair._next_seed)
    assert callable(repair._fingerprinted_baseline_ready)
    assert callable(repair._validate_repair_request)
    assert Path(repair.__file__).name == "generic_clean_audio_repair_runtime.py"
