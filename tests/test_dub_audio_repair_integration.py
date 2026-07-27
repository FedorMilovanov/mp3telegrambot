from __future__ import annotations

from pathlib import Path


def test_audio_repair_handlers_are_registered_and_notified() -> None:
    runtime = Path("services/dub_studio_runtime.py").read_text(encoding="utf-8")
    commands = Path("handlers/dub_commands.py").read_text(encoding="utf-8")
    assert "register_dub_audio_repair_handlers(application)" in runtime
    assert 'action == "repair_audio"' in runtime
    assert "Gemini не запускался" in runtime
    assert 'callback_data=f"dub|audio|{project_id}"' in commands
    assert "/dubsegments" in commands
    assert "/dubfix" in commands


def test_audio_repair_recipe_is_utility_not_generic_repair_button() -> None:
    recipe = Path("tools/voxcpm2/recipes/generic_short_v1.json").read_text(encoding="utf-8")
    assert '"repair_audio"' in recipe
    assert '"kind": "utility"' in recipe
    assert "generic_audio_repair_runtime" in recipe
