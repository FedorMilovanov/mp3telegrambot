from __future__ import annotations
import json
from pathlib import Path
import pytest
from tools.voxcpm2.examples.john_piper_z20py4yqhyq import voxcpm2_cpu_shorts_production as direct_wrapper

def test_direct_wrapper_persists_failure_without_losing_checkpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compatibility = {'schema_version': 2, 'policy': direct_wrapper.MARKER_POLICY, 'speech_backend': 'voxcpm2', 'render_contract_sha256': 'a' * 64, 'cache_length': 4096, 'python_executable': 'python'}
    marker_path = tmp_path / 'direct_cli_runtime.marker.json'
    marker_path.write_text(json.dumps(compatibility, ensure_ascii=False), encoding='utf-8')
    checkpoint = tmp_path / 'checkpoints' / 'segment_01.json'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{"complete": true}', encoding='utf-8')
    monkeypatch.setattr(direct_wrapper, '_runtime_contract', lambda: (tmp_path, dict(compatibility)))

    def fail() -> None:
        raise RuntimeError('synthetic render failure')
    with pytest.raises(RuntimeError, match='synthetic render failure'):
        direct_wrapper.run(fail)
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    failure = json.loads((tmp_path / 'direct_renderer_failure.json').read_text(encoding='utf-8'))
    assert marker == compatibility
    assert checkpoint.is_file()
    assert not (tmp_path / 'direct_cli_runtime.completed.json').exists()
    assert failure['error_type'] == 'RuntimeError'
    assert failure['message'] == 'synthetic render failure'
