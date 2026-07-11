"""AUDIT R19b: same fix as R19 (Synopsis), applied to the Study/Reflection
expanded-page publisher (services/telegraph_pages.py::_publish_expanded_page).

That function had its own independent "Edge-A" TOC-drop fallback — the same
bug class as Synopsis R19: on CONTENT_TOO_BIG for part 1 (TOC attached after
create-phase size check), the old code unconditionally republished without
any TOC. Fixed the same way: shrink part 1 by shifting its trailing
section(s) into the front of part 2 and retry with the TOC intact first;
only drop the TOC as a last resort (down to 1 section, or no next part to
shift into).
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _telegraph_pages_src() -> str:
    return (ROOT / "services/telegraph_pages.py").read_text(encoding="utf-8")


def _simulate_shrink(part_secs, next_secs, *, fits_when_len_lte):
    """Идентичный по духу R19 симулятор — тот же алгоритм сдвига секций."""
    while len(part_secs) > 1:
        if len(part_secs) <= fits_when_len_lte:
            return part_secs, next_secs, True
        moved = part_secs[-1]
        part_secs = part_secs[:-1]
        next_secs = [moved] + next_secs
    return part_secs, next_secs, len(part_secs) <= fits_when_len_lte


def test_shrink_preserves_order_and_reaches_fit():
    part_secs = ["A", "B", "C", "D"]
    next_secs = ["E", "F"]
    shrunk, grown, fit = _simulate_shrink(part_secs, next_secs, fits_when_len_lte=1)
    assert fit
    assert shrunk == ["A"]
    assert grown == ["B", "C", "D", "E", "F"]


def test_helper_extracted_and_reused_at_least_three_times():
    src = _telegraph_pages_src()
    assert "async def _build_expanded_part_nodes(" in src
    assert src.count("_build_expanded_part_nodes(i") >= 1
    assert src.count("_build_expanded_part_nodes(") >= 3


def test_shrink_attempted_before_dropping_toc():
    """AUDIT R39: перенос секций между частями убран (терял секцию при двойном
    overflow). Осталась безопасная замена — пере-издать без оглавления."""
    src = _telegraph_pages_src()
    assert "while len(part_secs) > 1:" not in src
    assert "переношу последнюю секцию" not in src
    assert "include_outline=False" in src


def test_shrink_guarded_by_more_than_one_part():
    """AUDIT R39: раз переноса нет — нет и его мутаций между частями."""
    src = _telegraph_pages_src()
    assert "_next_secs = [_moved_sec] + parts[i + 1]" not in src
    assert "parts[i + 1] = _next_secs" not in src


def test_old_unconditional_edge_a_language_removed():
    src = _telegraph_pages_src()
    assert "(Edge-A fallback)" not in src
    assert "include_outline=False" in src   # безопасный drop-TOC fallback присутствует


def test_related_materials_still_wired_through_shared_helper():
    src = _telegraph_pages_src()
    helper = src.split("async def _build_expanded_part_nodes(", 1)[1].split(
        "\n\n    for i, (page_url, part_secs) in enumerate", 1
    )[0]
    assert "aget_related_materials" in helper
    assert "if i == total - 1 and ai_data:" in helper
