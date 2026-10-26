"""Executed Dub Studio handler-composition contracts."""

from types import SimpleNamespace

from handlers import (
    dub_audio_repair,
    dub_commands,
    dub_delivery,
    dub_health,
    dub_multicommand,
    dub_quickstart,
    dub_studio_registration,
    dub_wizard,
)


def test_registration_is_disabled_without_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(dub_studio_registration, "enabled", lambda: False)
    monkeypatch.setattr(
        dub_studio_registration,
        "ensure_worker_running",
        lambda: (_ for _ in ()).throw(AssertionError("worker must not start")),
    )

    assert dub_studio_registration.register_dub_studio(SimpleNamespace()) is False


def test_registration_preserves_handler_order_and_worker_startup(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(dub_studio_registration, "enabled", lambda: True)
    monkeypatch.setattr(dub_studio_registration, "ensure_worker_running", lambda: calls.append("worker"))
    registrations = (
        (dub_wizard, "register_dub_wizard_handlers", "wizard"),
        (dub_health, "register_dub_health_handler", "health"),
        (dub_commands, "register_dub_handlers", "commands"),
        (dub_audio_repair, "register_dub_audio_repair_handlers", "audio-repair"),
        (dub_delivery, "register_dub_delivery_handlers", "delivery"),
        (dub_quickstart, "register_dub_quickstart_handler", "quickstart"),
        (dub_multicommand, "register_dub_multicommand_handler", "multicommand"),
    )
    application = SimpleNamespace()
    for module, name, label in registrations:
        monkeypatch.setattr(
            module, name, lambda actual, label=label: calls.append(label) if actual is application else None
        )

    assert dub_studio_registration.register_dub_studio(application) is True
    assert calls == [
        "wizard",
        "health",
        "commands",
        "audio-repair",
        "delivery",
        "quickstart",
        "multicommand",
        "worker",
    ]
