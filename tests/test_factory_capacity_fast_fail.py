from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as capacity_runtime

class _ServiceError(RuntimeError):

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code

class _FakeFiles:

    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.upload_calls = 0
        self.delete_calls = 0

    async def upload(self, *, file, config):
        self.upload_calls += 1
        return SimpleNamespace(name=f'files/{self.owner}')

    async def delete(self, *, name):
        self.delete_calls += 1

def _install_fake_factory_modules(monkeypatch, run_pass):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    async def verified_duration(_path):
        return 120.0

    monkeypatch.setattr(candidates, 'types', SimpleNamespace(Part=SimpleNamespace(from_bytes=lambda *, data, mime_type: SimpleNamespace(data=data, mime_type=mime_type)), UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setattr(candidates, 'shorts_factory_model', lambda: 'gemini-3.7-flash')
    monkeypatch.setattr(candidates, '_run_pass', run_pass)
    monkeypatch.setattr(candidates, '_scout_prompt', lambda *args: 'scout')
    monkeypatch.setattr(candidates, '_judge_prompt', lambda *args: 'judge')
    monkeypatch.setattr(candidates, '_boundary_prompt', lambda *args: 'boundary')
    monkeypatch.setattr(candidates, 'validate_factory_plan', lambda *args, **kwargs: {'shorts_candidates': [{'start': 1.0, 'end': 10.0}], 'long_candidates': []})
    monkeypatch.setattr(quality_gate, 'apply_factory_quality_gate', lambda plan: plan)
    monkeypatch.setattr(quality_gate, 'validated_factory_plan_language', lambda plan: 'ru')
    monkeypatch.setattr(source, 'factory_audio_mime_type', lambda path: 'audio/flac')
    monkeypatch.setattr(source, 'measure_factory_audio_duration', verified_duration)
    monkeypatch.setattr(source, 'factory_duration_matches', lambda actual, expected: abs(float(actual) - float(expected)) <= 2.0)

def _disable_capacity_retry_delay(monkeypatch) -> None:
    monkeypatch.setattr(capacity_runtime, '_capacity_retry_delay', lambda attempt: 0.0)

def test_capacity_backoff_is_longer_and_exponential(monkeypatch):
    monkeypatch.setattr(capacity_runtime.random, 'uniform', lambda *_args: 0.0)
    assert capacity_runtime._capacity_retry_delay(1) == 15.0
    assert capacity_runtime._capacity_retry_delay(2) == 30.0
    assert capacity_runtime._capacity_retry_delay(3) == 60.0
    assert capacity_runtime._capacity_retry_delay(4) == 120.0

def test_503_high_demand_retries_bounded_then_rotates_all_clients(monkeypatch, tmp_path):
    audio = tmp_path / 'factory.flac'
    audio.write_bytes(b'x' * 2048)
    first = SimpleNamespace(name='first')
    second = SimpleNamespace(name='second')
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        raise _ServiceError(503, 'UNAVAILABLE: high demand')
    _install_fake_factory_modules(monkeypatch, run_pass)
    _disable_capacity_retry_delay(monkeypatch)
    monkeypatch.setattr(capacity, 'factory_gemini_clients', lambda: [first, second])
    with pytest.raises(RuntimeError, match='503/high demand') as raised:
        asyncio.run(capacity_runtime.create_factory_plan_resumable(audio, title='Title', performer='Author', duration=120))
    assert calls == ['first', 'first', 'first', 'first', 'second', 'second', 'second', 'second']
    assert '3.6/3.5/Lite' in str(raised.value)
    assert 'все настроенные API-ключи/клиенты' in str(raised.value)
    assert 'retry-кэше' in str(raised.value)

def test_duration_mismatch_fails_before_any_gemini_client(monkeypatch, tmp_path):
    import services.shorts_factory_source as source

    audio = tmp_path / 'factory.flac'
    audio.write_bytes(b'x' * 2048)

    async def wrong_duration(_path):
        return 54.0

    monkeypatch.setattr(source, 'measure_factory_audio_duration', wrong_duration)
    monkeypatch.setattr(capacity, 'factory_gemini_clients', lambda: pytest.fail('Gemini client must not be created'))

    with pytest.raises(RuntimeError, match='duration does not match'):
        asyncio.run(capacity_runtime.create_factory_plan_resumable(audio, title='Title', performer='Author', duration=120))

def test_503_recovers_on_same_client_and_same_uploaded_audio(monkeypatch, tmp_path):
    import services.shorts_factory_candidates as candidates
    audio = tmp_path / 'factory.flac'
    with audio.open('wb') as stream:
        stream.truncate(19 * 1024 * 1024)
    first_files = _FakeFiles('first')
    second_files = _FakeFiles('second')
    first = SimpleNamespace(name='first', aio=SimpleNamespace(files=first_files))
    second = SimpleNamespace(name='second', aio=SimpleNamespace(files=second_files))
    calls: list[str] = []
    audio_parts: list[object] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        audio_parts.append(kwargs['audio_part'])
        if len(calls) == 1:
            raise _ServiceError(503, 'UNAVAILABLE: high demand')
        return {'ok': True, 'pass': len(calls)}

    async def wait_uploaded_file(client, uploaded):
        return uploaded
    _install_fake_factory_modules(monkeypatch, run_pass)
    _disable_capacity_retry_delay(monkeypatch)
    monkeypatch.setattr(candidates, '_wait_uploaded_file', wait_uploaded_file)
    monkeypatch.setattr(capacity, 'factory_gemini_clients', lambda: [first, second])
    plan = asyncio.run(capacity_runtime.create_factory_plan_resumable(audio, title='Title', performer='Author', duration=120))
    assert calls == ['first', 'first', 'first', 'first']
    assert first_files.upload_calls == 1
    assert first_files.delete_calls == 1
    assert second_files.upload_calls == 0
    assert second_files.delete_calls == 0
    assert audio_parts and all((part is audio_parts[0] for part in audio_parts))
    assert plan['model'] == 'gemini-3.7-flash'
    assert plan['thinking_level'] == 'high'
    assert plan['review_passes'] == 3
    assert plan['strict_quality'] is True

def test_429_still_rotates_and_keeps_three_pass_high_quality(monkeypatch, tmp_path):
    audio = tmp_path / 'factory.flac'
    audio.write_bytes(b'x' * 2048)
    first = SimpleNamespace(name='first')
    second = SimpleNamespace(name='second')
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(client.name)
        if client is first:
            raise _ServiceError(429, 'RESOURCE_EXHAUSTED')
        return {'ok': True, 'pass': len(calls)}
    _install_fake_factory_modules(monkeypatch, run_pass)
    monkeypatch.setattr(capacity, 'factory_gemini_clients', lambda: [first, second])
    plan = asyncio.run(capacity_runtime.create_factory_plan_resumable(audio, title='Title', performer='Author', duration=120))
    assert calls == ['first', 'second', 'second', 'second']
    assert plan['model'] == 'gemini-3.7-flash'
    assert plan['thinking_level'] == 'high'
    assert plan['review_passes'] == 3
    assert plan['strict_quality'] is True
