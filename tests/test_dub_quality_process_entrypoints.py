from __future__ import annotations

from services.dub_worker import build_command


def test_recipe_routes_all_actions_to_current_source_owners() -> None:
    expected = {
        "render": "tools.voxcpm2.generic_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_direct_runtime",
        "render_custom": "tools.voxcpm2.generic_custom_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
    }
    for action, module in expected.items():
        command, spec = build_command("generic_short_v1", action)
        assert spec["module"] == module
        assert command[1:3] == ["-m", module]
