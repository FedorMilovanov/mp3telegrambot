from __future__ import annotations

from pathlib import Path

from tools.voxcpm2.generic_short_runtime import standardize_russian_title


def test_semantic_wrapper_stable_entrypoint_exists() -> None:
    wrapper = Path("tools/voxcpm2/voxcpm2_cpu_semantic_wrapper.py")
    implementation = Path(
        "tools/voxcpm2/examples/john_piper_z20py4yqhyq/voxcpm2_cpu_semantic_wrapper.py"
    )
    assert wrapper.is_file()
    assert implementation.is_file()


def test_title_standard_fixes_christian_woman_and_adds_speaker() -> None:
    result = standardize_russian_title(
        "В чем заключается сила христианской женщины",
        context="Original title: What Is the Strength of a Christian Woman? | John Piper",
    )
    assert result == "В Чем Заключается Сила Женщины Христианки - Джон Пайпер"


def test_title_standard_preserves_known_mixed_case_words() -> None:
    result = standardize_russian_title("как YouTube и AI меняют мир")
    assert result == "Как YouTube И AI Меняют Мир"
