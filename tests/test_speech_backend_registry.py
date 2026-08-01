from __future__ import annotations

import pytest

from services.speech_backends import BackendCapabilities
from services.speech_backends import registry


class _StubBackend:
    adapter_policy = "registry-test-v1"

    def __init__(self, backend_id: str, aliases: tuple[str, ...]) -> None:
        self.backend_id = backend_id
        self.aliases = aliases

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            voice_cloning=True,
            reference_audio=True,
            deterministic_seed=True,
            style_instruction=False,
            cpu_inference=True,
            pcm_output=True,
            checkpointable_segments=True,
        )


def test_alias_collision_does_not_partially_register_backend() -> None:
    first = _StubBackend("registry-atomic-first", ("registry-shared-alias",))
    second = _StubBackend("registry-atomic-second", ("registry-shared-alias",))
    registry.register_backend(first)
    try:
        before = registry.backend_ids()
        with pytest.raises(RuntimeError, match="Alias speech backend уже занят"):
            registry.register_backend(second)

        assert registry.backend_ids() == before
        assert registry.get_backend("registry-shared-alias") is first
        with pytest.raises(RuntimeError, match="Неизвестный speech backend"):
            registry.get_backend(second.backend_id)
    finally:
        registry.unregister_backend(first.backend_id)


def test_registry_public_api_exports_unregister() -> None:
    assert "unregister_backend" in registry.__all__
