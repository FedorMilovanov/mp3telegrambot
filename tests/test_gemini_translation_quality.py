from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.voxcpm2 import expressive_translation
from tools.voxcpm2 import generic_short_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tools" / "voxcpm2" / "generic_short_runtime.py"
EXPRESSIVE = ROOT / "tools" / "voxcpm2" / "expressive_translation.py"
GEMINI_ENTRY = ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"
WIZARD = ROOT / "handlers" / "dub_wizard.py"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_current_stable_models_have_separate_roles() -> None:
    wizard = _source(WIZARD)
    assert '"gemini-3.6-flash"' in wizard
    assert '"gemini-3.5-flash-lite"' in wizard
    request_section = wizard[
        wizard.index("def _request_payload"):
        wizard.index("async def _admin")
    ]
    assert '"translation_model": os.getenv("DUB_TRANSLATION_MODEL", "gemini-3.6-flash")' in request_section
    assert '"title_model": os.getenv("DUB_TITLE_MODEL", "gemini-3.5-flash-lite")' in request_section


def test_translation_keeps_high_thinking_and_bounded_network_calls() -> None:
    runtime = _source(RUNTIME)
    gemini_section = runtime[
        runtime.index("def _generation_config"):
        runtime.index("def install_runtime_adapters")
    ]
    assert 'thinking_level="high"' in gemini_section
    assert "types.ThinkingConfig" in gemini_section
    assert 'response_mime_type="application/json"' in gemini_section
    assert "max_output_tokens=16000" in gemini_section
    assert "types.HttpOptions(timeout=" in runtime
    assert 'DUB_GEMINI_REQUEST_TIMEOUT_SEC' in runtime
    assert 'DUB_GEMINI_PASS_TIMEOUT_SEC' in runtime
    assert "time.monotonic() + pass_timeout" in runtime
    assert "temperature=" not in gemini_section
    assert "top_p=" not in gemini_section
    assert "top_k=" not in gemini_section


def test_bounded_gemini_request_fails_over_to_next_key(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "key-one")
    monkeypatch.setenv("GEMINI_API_KEY_2", "key-two")
    monkeypatch.delenv("GEMINI_API_KEY_3", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_4", raising=False)
    monkeypatch.delenv("DUB_GEMINI_REQUEST_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("DUB_GEMINI_PASS_TIMEOUT_SEC", raising=False)

    created: list[tuple[str, int]] = []
    closed: list[str] = []
    logs: list[str] = []

    class FakeModels:
        def __init__(self, key: str) -> None:
            self.key = key

        def generate_content(self, **_kwargs):
            if self.key == "key-one":
                raise TimeoutError("route stalled")
            return SimpleNamespace(text=json.dumps({"segments": [{"id": 1, "russian": "Готово"}]}))

    class FakeClient:
        def __init__(self, key: str) -> None:
            self.key = key
            self.models = FakeModels(key)

        def close(self) -> None:
            closed.append(self.key)

    def fake_client(key: str, timeout_ms: int):
        created.append((key, timeout_ms))
        return FakeClient(key)

    monkeypatch.setattr(generic_short_runtime, "_translation_client", fake_client)
    monkeypatch.setattr(generic_short_runtime, "_generation_config", lambda _model: object())
    monkeypatch.setattr(generic_short_runtime.pipeline, "log", logs.append)

    result = generic_short_runtime.gemini_json(
        "Ты — первоклассный переводчик. Верни JSON.",
        model_name="gemini-3.6-flash",
    )

    assert result == {"segments": [{"id": 1, "russian": "Готово"}]}
    assert [item[0] for item in created] == ["key-one", "key-two"]
    assert all(30_000 <= item[1] <= 180_000 for item in created)
    assert closed == ["key-one", "key-two"]
    assert any("ключ 1/2 не сработал" in line for line in logs)
    assert any("завершён ключом 2/2" in line for line in logs)


def test_translation_timeout_environment_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("DUB_GEMINI_REQUEST_TIMEOUT_SEC", "1")
    monkeypatch.setenv("DUB_GEMINI_PASS_TIMEOUT_SEC", "10")
    request_timeout, pass_timeout = generic_short_runtime._translation_timeouts()
    assert request_timeout == 30.0
    assert pass_timeout == 60.0

    monkeypatch.setenv("DUB_GEMINI_REQUEST_TIMEOUT_SEC", "invalid")
    monkeypatch.setenv("DUB_GEMINI_PASS_TIMEOUT_SEC", "invalid")
    request_timeout, pass_timeout = generic_short_runtime._translation_timeouts()
    assert request_timeout == 180.0
    assert pass_timeout == 300.0


def test_gemini_request_requires_a_real_key(monkeypatch) -> None:
    for name in generic_short_runtime._GEMINI_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        generic_short_runtime.gemini_json("prompt", model_name="gemini-3.6-flash")


def test_expressive_translation_really_runs_three_visible_editorial_passes(
    monkeypatch,
) -> None:
    source = _source(EXPRESSIVE)
    assert "draft = _validate(_gemini(draft_prompt, model_name), groups)" in source
    assert "faithful = _validate(_gemini(fidelity_prompt, model_name), groups)" in source
    assert "final = _validate(_gemini(performance_prompt, model_name), groups)" in source
    assert "намеренные повторы" in source
    assert "риторические вопросы" in source
    assert "богословский термин" in source
    assert "не выше примерно" in source

    calls: list[str] = []
    logs: list[str] = []

    def fake_gemini(prompt: str, _model_name: str):
        calls.append(prompt)
        return {"segments": [{"id": 1, "russian": "Она смеётся над грядущим."}]}

    monkeypatch.setattr(expressive_translation, "_gemini", fake_gemini)
    monkeypatch.setattr(expressive_translation.pipeline, "log", logs.append)
    result = expressive_translation.translate_groups(
        [{"id": 1, "start": 0.0, "end": 10.0, "english": "She laughs at the time to come."}],
        metadata={"title": "The Strength of a Godly Woman"},
        caption_origin="creator",
        model_name="gemini-3.6-flash",
    )

    assert result == [{"id": 1, "russian": "Она смеётся над грядущим."}]
    assert len(calls) == 3
    progress_lines = [line for line in logs if line.startswith("DUB_PROGRESS ")]
    assert len(progress_lines) == 7
    assert any("перевод 1/3" in line for line in progress_lines)
    assert any("сверка 2/3" in line for line in progress_lines)
    assert any("редактура 3/3" in line for line in progress_lines)
    assert any("сжатие не требуется" in line for line in logs)


def test_clean_gemini_route_uses_expressive_translator_and_key_pool() -> None:
    entry = _source(GEMINI_ENTRY)
    runtime = _source(RUNTIME)
    assert "production.translate_groups_max = expressive_translation.translate_groups" in entry
    assert "hardened.pipeline.gemini_json = hardened.gemini_json" in entry
    assert "_translation_keys" in runtime
    assert "GEMINI_API_KEY_4" in runtime
    assert "_translation_client" in runtime
