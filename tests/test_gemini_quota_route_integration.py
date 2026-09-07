from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace


def _install_import_stubs() -> None:
    """Keep focused routing tests independent of optional production SDKs."""
    try:
        import dotenv  # noqa: F401
    except ImportError:
        dotenv_stub = ModuleType("dotenv")
        dotenv_stub.load_dotenv = lambda: None
        sys.modules["dotenv"] = dotenv_stub

    try:
        import flask  # noqa: F401
    except ImportError:
        flask_stub = ModuleType("flask")

        class _Flask:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def route(self, *args, **kwargs):
                def decorator(func):
                    return func

                return decorator

        flask_stub.Flask = _Flask
        sys.modules["flask"] = flask_stub

    try:
        import telegram  # noqa: F401
    except ImportError:
        telegram_stub = ModuleType("telegram")
        telegram_stub.InlineKeyboardButton = object
        telegram_stub.InlineKeyboardMarkup = object
        sys.modules["telegram"] = telegram_stub


_install_import_stubs()

from core import globals as core_globals
from services import gemini_capacity_control as capacity_control
from services import livedub_qa
from services import quiz_generator


class _ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _project_quota_error() -> _ServiceError:
    return _ServiceError(
        429,
        "RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    )


def _generic_quota_error() -> _ServiceError:
    return _ServiceError(429, "RESOURCE_EXHAUSTED: rate limit exceeded")


class _Models:
    def __init__(self, name: str, calls: list[tuple[str, list]], handler) -> None:
        self._name = name
        self._calls = calls
        self._handler = handler

    async def generate_content(self, **kwargs):
        self._calls.append((self._name, list(kwargs.get("contents") or [])))
        result = self._handler()
        if isinstance(result, BaseException):
            raise result
        return result


class _Files:
    def __init__(self, name: str, calls: list[tuple[str, str]], upload_handler=None) -> None:
        self._name = name
        self._calls = calls
        self._upload_handler = upload_handler
        self._counter = 0

    async def upload(self, **kwargs):
        self._calls.append((self._name, "upload"))
        if self._upload_handler is not None:
            result = self._upload_handler()
            if isinstance(result, BaseException):
                raise result
        self._counter += 1
        return SimpleNamespace(name=f"{self._name}-file-{self._counter}", state="ACTIVE")

    async def get(self, **kwargs):
        self._calls.append((self._name, "get"))
        return SimpleNamespace(name=kwargs.get("name", "file"), state="ACTIVE")

    async def delete(self, **kwargs):
        self._calls.append((self._name, "delete"))


def _client(name: str, model_calls: list, model_handler, file_calls=None, upload_handler=None):
    return SimpleNamespace(
        name=name,
        aio=SimpleNamespace(
            models=_Models(name, model_calls, model_handler),
            files=_Files(name, file_calls if file_calls is not None else [], upload_handler),
        ),
    )


def _quiz_response() -> SimpleNamespace:
    return SimpleNamespace(text="routing-ok")


def _quiz_questions() -> list[dict]:
    return [
        {
            "question": "Каков основной вывод?",
            "options": ["Первый полный ответ", "Второй полный ответ", "Третий полный ответ", "Четвёртый полный ответ"],
            "correct": 0,
            "explanation": "Проверочный ответ.",
        }
    ]


def _install_quiz_stubs(monkeypatch) -> None:
    monkeypatch.setattr(quiz_generator, "make_text_config_smart", lambda **kwargs: {})
    monkeypatch.setattr(
        quiz_generator,
        "_parse_quiz_json",
        lambda raw, expected_count=None: _quiz_questions(),
    )

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(quiz_generator, "alog_gemini_response", _noop)
    monkeypatch.setattr(quiz_generator, "alog_gemini_run", _noop)


def test_quiz_project_quota_skips_same_domain_and_drops_key_bound_audio_on_failover(monkeypatch) -> None:
    _install_quiz_stubs(monkeypatch)
    calls: list[tuple[str, list]] = []
    first = _client("first", calls, _project_quota_error)
    same_project = _client(
        "same-project",
        calls,
        lambda: AssertionError("same project must be skipped"),
    )
    other_project = _client("other-project", calls, _quiz_response)
    audio_part = object()

    monkeypatch.setattr(quiz_generator, "GEMINI_CLIENTS", [first, same_project, other_project])
    monkeypatch.setattr(
        quiz_generator,
        "GEMINI_CLIENT_QUOTA_DOMAINS",
        ["domain-a", "domain-a", "domain-b"],
    )

    result = asyncio.run(
        quiz_generator.generate_quiz(
            {"main_topic": "Оправдание верой"},
            existing_audio_part=audio_part,
            existing_client=first,
            count=1,
        )
    )

    assert result and len(result) == 1
    assert [name for name, _contents in calls] == ["first", "other-project"]
    assert audio_part in calls[0][1]
    assert audio_part not in calls[1][1]


def test_quiz_generic_429_still_rotates_to_same_labeled_domain(monkeypatch) -> None:
    _install_quiz_stubs(monkeypatch)
    calls: list[tuple[str, list]] = []
    first = _client("first", calls, _generic_quota_error)
    sibling = _client("sibling", calls, _quiz_response)

    monkeypatch.setattr(quiz_generator, "GEMINI_CLIENTS", [first, sibling])
    monkeypatch.setattr(
        quiz_generator,
        "GEMINI_CLIENT_QUOTA_DOMAINS",
        ["domain-a", "domain-a"],
    )

    result = asyncio.run(
        quiz_generator.generate_quiz(
            {"main_topic": "Оправдание верой"},
            count=1,
        )
    )

    assert result and len(result) == 1
    assert [name for name, _contents in calls] == ["first", "sibling"]


class _Budget:
    instances: list["_Budget"] = []

    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self.used = 0
        type(self).instances.append(self)

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def claim(self) -> None:
        if self.exhausted:
            raise RuntimeError("test retry budget exhausted")
        self.used += 1


def _install_livedub_stubs(monkeypatch, clients, domains) -> None:
    _Budget.instances = []
    monkeypatch.setattr(livedub_qa, "HAS_GEMINI", True)
    monkeypatch.setattr(livedub_qa, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(livedub_qa, "GEMINI_CLIENT_QUOTA_DOMAINS", domains)
    monkeypatch.setattr(
        livedub_qa,
        "types",
        SimpleNamespace(UploadFileConfig=lambda **kwargs: kwargs),
    )
    monkeypatch.setattr(capacity_control, "GeminiRetryBudget", _Budget)
    monkeypatch.setattr(capacity_control, "require_domain_available", lambda domain: None)
    monkeypatch.setattr(capacity_control, "note_overload", lambda *args, **kwargs: None)

    async def _run_heavy(call, **kwargs):
        return await call()

    monkeypatch.setattr(capacity_control, "run_heavy_gemini_call", _run_heavy)
    monkeypatch.setattr(core_globals, "make_audio_config", lambda **kwargs: {})


def _write_audio_inputs(tmp_path):
    original = tmp_path / "original.mp3"
    dub = tmp_path / "dub.mp3"
    video = tmp_path / "video.mp4"
    original.write_bytes(b"o" * 2048)
    dub.write_bytes(b"d" * 2048)
    video.write_bytes(b"v" * 2048)
    return video, original, dub


def _qa_response() -> SimpleNamespace:
    return SimpleNamespace(text='{"score":100,"verdict":"точно","issues":[]}')


def test_livedub_files_project_quota_skips_same_domain_before_budget_claim(monkeypatch, tmp_path) -> None:
    model_calls: list[tuple[str, list]] = []
    file_calls: list[tuple[str, str]] = []
    first = _client(
        "first",
        model_calls,
        _qa_response,
        file_calls,
        upload_handler=_project_quota_error,
    )
    same_project = _client(
        "same-project",
        model_calls,
        lambda: AssertionError("same project inference must be skipped"),
        file_calls,
        upload_handler=lambda: AssertionError("same project Files must be skipped"),
    )
    other_project = _client("other-project", model_calls, _qa_response, file_calls)
    _install_livedub_stubs(
        monkeypatch,
        [first, same_project, other_project],
        ["domain-a", "domain-a", "domain-b"],
    )
    video, original, dub = _write_audio_inputs(tmp_path)

    result = asyncio.run(
        livedub_qa._run_translation_qa_base(
            video,
            original,
            {},
            60,
            model_name="gemini-test",
            dub_audio_path=dub,
        )
    )

    assert result and result["score"] == 100
    assert not any(name == "same-project" for name, _op in file_calls)
    assert [budget.used for budget in _Budget.instances[:3]] == [2, 1, 1]


def test_livedub_inference_project_quota_skips_same_domain_before_reupload(monkeypatch, tmp_path) -> None:
    model_calls: list[tuple[str, list]] = []
    file_calls: list[tuple[str, str]] = []
    first = _client("first", model_calls, _project_quota_error, file_calls)
    same_project = _client(
        "same-project",
        model_calls,
        lambda: AssertionError("same project inference must be skipped"),
        file_calls,
        upload_handler=lambda: AssertionError("same project reupload must be skipped"),
    )
    other_project = _client("other-project", model_calls, _qa_response, file_calls)
    _install_livedub_stubs(
        monkeypatch,
        [first, same_project, other_project],
        ["domain-a", "domain-a", "domain-b"],
    )
    video, original, dub = _write_audio_inputs(tmp_path)

    result = asyncio.run(
        livedub_qa._run_translation_qa_base(
            video,
            original,
            {},
            60,
            model_name="gemini-test",
            dub_audio_path=dub,
        )
    )

    assert result and result["score"] == 100
    assert [name for name, _contents in model_calls] == ["first", "other-project"]
    assert not any(name == "same-project" for name, _op in file_calls)
    assert [budget.used for budget in _Budget.instances[:3]] == [2, 2, 2]


def test_livedub_generic_429_keeps_same_domain_failover(monkeypatch, tmp_path) -> None:
    model_calls: list[tuple[str, list]] = []
    file_calls: list[tuple[str, str]] = []
    first = _client("first", model_calls, _generic_quota_error, file_calls)
    sibling = _client("sibling", model_calls, _qa_response, file_calls)
    _install_livedub_stubs(
        monkeypatch,
        [first, sibling],
        ["domain-a", "domain-a"],
    )
    video, original, dub = _write_audio_inputs(tmp_path)

    result = asyncio.run(
        livedub_qa._run_translation_qa_base(
            video,
            original,
            {},
            60,
            model_name="gemini-test",
            dub_audio_path=dub,
        )
    )

    assert result and result["score"] == 100
    assert [name for name, _contents in model_calls] == ["first", "sibling"]
    assert [budget.used for budget in _Budget.instances[:3]] == [2, 2, 2]


def test_livedub_key_bound_original_never_rotates_to_non_owner(monkeypatch, tmp_path) -> None:
    model_calls: list[tuple[str, list]] = []
    file_calls: list[tuple[str, str]] = []
    owner = _client("owner", model_calls, _project_quota_error, file_calls)
    other_project = _client(
        "other-project",
        model_calls,
        lambda: AssertionError("key-bound original cannot move to another client"),
        file_calls,
    )
    _install_livedub_stubs(
        monkeypatch,
        [owner, other_project],
        ["domain-a", "domain-b"],
    )
    video, _original, dub = _write_audio_inputs(tmp_path)
    existing_audio_part = SimpleNamespace(name="existing", state="ACTIVE")

    result = asyncio.run(
        livedub_qa._run_translation_qa_base(
            video,
            None,
            {},
            60,
            model_name="gemini-test",
            dub_audio_path=dub,
            existing_audio_part=existing_audio_part,
            existing_client=owner,
        )
    )

    assert result is None
    assert [name for name, _contents in model_calls] == ["owner"]
    assert all(name != "other-project" for name, _op in file_calls)
    assert [budget.used for budget in _Budget.instances[:3]] == [0, 1, 1]
