from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.speech_backends import BackendSessionConfig, default_backend
from services.speech_backends.voxcpm2 import SESSION_CALL_POLICY


ROOT = Path(__file__).resolve().parents[1]


class _Cache:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def setup_cache(self, *args: object) -> None:
        self.calls.append(args)


class _Parameter:
    dtype = "float32"


class _TTSModel:
    _encode_sample_rate = 16_000
    sample_rate = 48_000
    patch_size = 2
    chunk_size = 640
    device = "cpu"

    def __init__(self) -> None:
        self.base_lm = _Cache()
        self.residual_lm = _Cache()

    def parameters(self):
        return iter((_Parameter(),))


class _Model:
    def __init__(self) -> None:
        self.tts_model = _TTSModel()


class _VoxCPM:
    calls: list[tuple[str, dict[str, object]]] = []
    model = _Model()

    @classmethod
    def from_pretrained(cls, path: str, **kwargs: object) -> _Model:
        cls.calls.append((path, dict(kwargs)))
        return cls.model


def test_voxcpm2_backend_accepts_only_typed_session_config(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "voxcpm", SimpleNamespace(VoxCPM=_VoxCPM))
    backend = default_backend()
    config = BackendSessionConfig(
        model_path=Path("models/voxcpm2"),
        options={"cache_length": 8192},
    )

    assert SESSION_CALL_POLICY == "typed-backend-session-config-v1"
    assert tuple(inspect.signature(backend.open_session).parameters) == ("config",)

    session = backend.open_session(config)

    assert session.audio_spec.encode_sample_rate == 16_000
    assert session.audio_spec.output_sample_rate == 48_000
    assert session.audio_spec.cache_length == 8192
    assert _VoxCPM.calls[-1][1] == {
        "device": "cpu",
        "optimize": False,
        "load_denoiser": False,
    }
    assert _VoxCPM.model.tts_model.base_lm.calls[-1][1] == 8192
    assert _VoxCPM.model.tts_model.residual_lm.calls[-1][1] == 8192

    with pytest.raises(TypeError, match="BackendSessionConfig"):
        backend.open_session(Path("models/voxcpm2"))  # type: ignore[arg-type]


def test_production_cli_builds_typed_session_config() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py").read_text(
        encoding="utf-8"
    )
    adapter = (ROOT / "services" / "speech_backends" / "voxcpm2.py").read_text(
        encoding="utf-8"
    )

    assert "from services.speech_backends import BackendSessionConfig, get_backend" in source
    assert "session = backend.open_session(\n        BackendSessionConfig(" in source
    assert 'options={"cache_length": cache_length}' in source
    assert "torch_module=torch" not in source
    assert "config: BackendSessionConfig | Path" not in adapter
    assert "cache_length: int | None" not in adapter
