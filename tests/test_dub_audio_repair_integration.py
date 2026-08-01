from __future__ import annotations

from services.dub_studio import load_recipe


def test_audio_repair_handlers_are_registered_and_notified() -> None:
    from pathlib import Path

    runtime = Path("services/dub_studio_runtime.py").read_text(encoding="utf-8")
    commands = Path("handlers/dub_commands.py").read_text(encoding="utf-8")
    assert "register_dub_audio_repair_handlers(application)" in runtime
    assert 'action == "repair_audio"' in runtime
    assert "Gemini не запускался" in runtime
    assert 'callback_data=f"dub|audio|{project_id}"' in commands
    assert "/dubsegments" in commands
    assert "/dubfix" in commands


def test_audio_repair_recipe_is_clean_utility_action() -> None:
    action = load_recipe("generic_short_v1").action("repair_audio")

    assert action["kind"] == "utility"
    assert action["runner"] == "python_module"
    assert action["module"] == "tools.voxcpm2.generic_clean_audio_repair_runtime"
    assert action.get("parameters", []) == []
