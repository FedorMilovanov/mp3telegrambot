from __future__ import annotations
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from handlers import dub_wizard
from tools.voxcpm2 import expressive_translation
from tools.voxcpm2 import generic_short_production as generic_short_runtime
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'tools' / 'voxcpm2' / 'generic_short_production.py'
EXPRESSIVE = ROOT / 'tools' / 'voxcpm2' / 'expressive_translation.py'

def _source(path: Path) -> str:
    source = path.read_text(encoding='utf-8')
    ast.parse(source)
    return source

def test_current_stable_models_have_separate_roles(monkeypatch) -> None:
    monkeypatch.delenv('DUB_TRANSLATION_MODEL', raising=False)
    monkeypatch.delenv('DUB_TITLE_MODEL', raising=False)
    payload = dub_wizard._request_payload('AbCdEf12345', 'https://youtube.com/watch?v=AbCdEf12345', 'gemini', dub_wizard.DEFAULT_MODEL_PROFILE_ID)
    assert payload['translation_model'] == 'gemini-3.6-flash'
    assert payload['title_model'] == 'gemini-3.5-flash-lite'
    assert payload['translation_model'] != payload['title_model']
    monkeypatch.setenv('DUB_TRANSLATION_MODEL', 'translation-fixture')
    monkeypatch.setenv('DUB_TITLE_MODEL', 'title-fixture')
    overridden = dub_wizard._request_payload('AbCdEf12345', 'https://youtube.com/watch?v=AbCdEf12345', 'gemini', dub_wizard.DEFAULT_MODEL_PROFILE_ID)
    assert overridden['translation_model'] == 'translation-fixture'
    assert overridden['title_model'] == 'title-fixture'

def test_bounded_gemini_request_fails_over_to_next_key(monkeypatch) -> None:
    monkeypatch.setattr(generic_short_runtime, '_load_dotenv_for_manual_run', lambda: None)
    monkeypatch.setenv('GEMINI_API_KEY', 'key-one')
    monkeypatch.setenv('GEMINI_API_KEY_2', 'key-two')
    monkeypatch.delenv('GEMINI_API_KEY_3', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY_4', raising=False)
    monkeypatch.delenv('DUB_GEMINI_REQUEST_TIMEOUT_SEC', raising=False)
    monkeypatch.delenv('DUB_GEMINI_PASS_TIMEOUT_SEC', raising=False)
    created: list[tuple[str, int]] = []
    closed: list[str] = []
    logs: list[str] = []

    class FakeModels:

        def __init__(self, key: str) -> None:
            self.key = key

        def generate_content(self, **_kwargs):
            if self.key == 'key-one':
                raise TimeoutError('route stalled')
            return SimpleNamespace(text=json.dumps({'segments': [{'id': 1, 'russian': 'Готово'}]}))

    class FakeClient:

        def __init__(self, key: str) -> None:
            self.key = key
            self.models = FakeModels(key)

        def close(self) -> None:
            closed.append(self.key)

    def fake_client(key: str, timeout_ms: int):
        created.append((key, timeout_ms))
        return FakeClient(key)
    monkeypatch.setattr(generic_short_runtime, '_translation_client', fake_client)
    monkeypatch.setattr(generic_short_runtime, '_generation_config', lambda _model: object())
    monkeypatch.setattr(generic_short_runtime, 'log', logs.append)
    result = generic_short_runtime.gemini_json('Ты — первоклассный переводчик. Верни JSON.', model_name='gemini-3.6-flash')
    assert result == {'segments': [{'id': 1, 'russian': 'Готово'}]}
    assert [item[0] for item in created] == ['key-one', 'key-two']
    assert all((30000 <= item[1] <= 180000 for item in created))
    assert closed == ['key-one', 'key-two']
    assert any(('ключ 1/2 не сработал' in line for line in logs))
    assert any(('завершён ключом 2/2' in line for line in logs))

def test_gemini_does_not_start_key_beyond_pass_budget(monkeypatch) -> None:
    created: list[str] = []
    closed: list[str] = []
    monotonic_values = iter([0.0, 0.0, 0.0, 40.0, 40.0])

    class FailingModels:

        def generate_content(self, **_kwargs):
            raise TimeoutError('first key consumed forty seconds')

    class FakeClient:
        models = FailingModels()

        def __init__(self, key: str) -> None:
            self.key = key

        def close(self) -> None:
            closed.append(self.key)

    def fake_client(key: str, _timeout_ms: int):
        created.append(key)
        return FakeClient(key)
    monkeypatch.setattr(generic_short_runtime, '_translation_keys', lambda: ['key-one', 'key-two'])
    monkeypatch.setattr(generic_short_runtime, '_translation_timeouts', lambda: (60.0, 60.0))
    monkeypatch.setattr(generic_short_runtime, '_translation_client', fake_client)
    monkeypatch.setattr(generic_short_runtime, '_generation_config', lambda _model: object())
    monkeypatch.setattr(generic_short_runtime.time, 'monotonic', lambda: next(monotonic_values))
    monkeypatch.setattr(generic_short_runtime, 'log', lambda _line: None)
    with pytest.raises(RuntimeError, match='остаток общего лимита'):
        generic_short_runtime.gemini_json('prompt', model_name='gemini-3.6-flash')
    assert created == ['key-one']
    assert closed == ['key-one']

def test_translation_timeout_environment_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv('DUB_GEMINI_REQUEST_TIMEOUT_SEC', '1')
    monkeypatch.setenv('DUB_GEMINI_PASS_TIMEOUT_SEC', '10')
    request_timeout, pass_timeout = generic_short_runtime._translation_timeouts()
    assert request_timeout == 30.0
    assert pass_timeout == 60.0
    monkeypatch.setenv('DUB_GEMINI_REQUEST_TIMEOUT_SEC', 'invalid')
    monkeypatch.setenv('DUB_GEMINI_PASS_TIMEOUT_SEC', 'invalid')
    request_timeout, pass_timeout = generic_short_runtime._translation_timeouts()
    assert request_timeout == 180.0
    assert pass_timeout == 300.0

def test_translation_keys_load_dotenv_without_overriding_env(monkeypatch) -> None:
    calls: list[bool] = []

    def fake_load() -> None:
        calls.append(True)
        monkeypatch.setenv('GEMINI_API_KEY_2', 'from-dotenv')
    monkeypatch.setattr(generic_short_runtime, '_load_dotenv_for_manual_run', fake_load)
    monkeypatch.setenv('GEMINI_API_KEY', 'already-present')
    monkeypatch.delenv('GEMINI_API_KEY_2', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY_3', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY_4', raising=False)
    assert generic_short_runtime._translation_keys() == ['already-present', 'from-dotenv']
    assert calls == [True]

def test_gemini_request_requires_a_real_key(monkeypatch) -> None:
    monkeypatch.setattr(generic_short_runtime, '_load_dotenv_for_manual_run', lambda: None)
    for name in generic_short_runtime._GEMINI_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match='GEMINI_API_KEY'):
        generic_short_runtime.gemini_json('prompt', model_name='gemini-3.6-flash')

def test_expressive_translation_really_runs_three_visible_editorial_passes(monkeypatch) -> None:
    source = _source(EXPRESSIVE)
    assert 'draft = _validate(_gemini(draft_prompt, model_name), groups)' in source
    assert 'faithful = _validate(_gemini(fidelity_prompt, model_name), groups)' in source
    assert 'final = _validate(_gemini(performance_prompt, model_name), groups)' in source
    assert 'намеренные повторы' in source
    assert 'риторические вопросы' in source
    assert 'богословский термин' in source
    assert 'не выше примерно' in source
    calls: list[str] = []
    logs: list[str] = []

    def fake_gemini(prompt: str, _model_name: str):
        calls.append(prompt)
        return {'segments': [{'id': 1, 'russian': 'Она смеётся над грядущим.'}]}
    monkeypatch.setattr(expressive_translation, '_gemini', fake_gemini)
    monkeypatch.setattr(expressive_translation.pipeline, 'log', logs.append)
    result = expressive_translation.translate_groups([{'id': 1, 'start': 0.0, 'end': 10.0, 'english': 'She laughs at the time to come.'}], metadata={'title': 'The Strength of a Godly Woman'}, caption_origin='creator', model_name='gemini-3.6-flash')
    assert result == [{'id': 1, 'russian': 'Она смеётся над грядущим.'}]
    assert len(calls) == 3
    progress_lines = [line for line in logs if line.startswith('DUB_PROGRESS ')]
    assert len(progress_lines) == 7
    assert any(('перевод 1/3' in line for line in progress_lines))
    assert any(('сверка 2/3' in line for line in progress_lines))
    assert any(('редактура 3/3' in line for line in progress_lines))
    assert any(('сжатие не требуется' in line for line in logs))


def test_translation_keeps_high_thinking_and_bounded_network_calls() -> None:
    runtime = _source(RUNTIME)
    assert 'thinking_level="high"' in runtime
    assert "types.ThinkingConfig" in runtime
    assert 'response_mime_type="application/json"' in runtime
    assert "max_output_tokens=16000" in runtime
    assert "types.HttpOptions(timeout=" in runtime
    assert "DUB_GEMINI_REQUEST_TIMEOUT_SEC" in runtime
    assert "DUB_GEMINI_PASS_TIMEOUT_SEC" in runtime
    assert "time.monotonic() + pass_timeout" in runtime
    assert "remaining < _MIN_REQUEST_TIMEOUT_SECONDS" in runtime
    assert "load_dotenv(override=False)" in runtime
