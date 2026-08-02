"""AUDIT R16 (2026-07-09, скриншоты трёхчастного прогона «Трус и лжец»).

1. Часть 1 нумерует TOC «1. 2. 3. ...», а mini-outline частей 2+ ставил «•»
   и НЕ продолжал сквозную нумерацию материала — разный визуальный формат
   оглавления на разных страницах одного конспекта. Пронумеровано сквозно.
2. _publish_recursive делит sections пополам по КОЛИЧЕСТВУ (не по размеру) —
   дало неровный расклад 4+2+3 секции по 3 частям. Хвостовые части объединяются
   только после реального editPage. Structured edit outcome distinguishes
   CONTENT_TOO_BIG from transient transport failures.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _telegraph_src() -> str:
    return (ROOT / "services/telegraph.py").read_text(encoding="utf-8")


# ── 1. Сквозная нумерация вместо «•» ─────────────────────────────

def test_global_numbering_formula_matches_expected_sequence():
    """Точная симуляция скриншота: части [4]+[2]+[3] секций → части 2 и 3
    должны продолжить нумерацию с 5 и с 7, а не начинать заново с 1."""
    parts = [
        [{"title": f"T{i}"} for i in range(4)],
        [{"title": "A"}, {"title": "B"}],
        [{"title": "C"}, {"title": "D"}, {"title": "E"}],
    ]
    expected = {1: [5, 6], 2: [7, 8, 9]}
    for i in (1, 2):
        _sec_offset_for_part = sum(len(p) for p in parts[:i])
        nums = [_sec_offset_for_part + idx + 1 for idx in range(len(parts[i]))]
        assert nums == expected[i], f"part {i}: got {nums}, expected {expected[i]}"


def test_mini_outline_uses_global_numbering_not_bullets():
    src = _telegraph_src()
    block = src.split("elif total > 1:", 1)[1].split("for sec_idx, sec in enumerate(part_secs):", 1)[0]
    assert "_sec_offset_for_part = sum(len(p) for p in parts[:i])" in block
    assert '"{_global_num}.' in block or "_global_num}." in block
    assert 'f"• {_ps_title}"' not in block, "старый формат с кружочком не убран"
    assert "_md_parse_inline(_ps_title)" in block


def test_mini_outline_still_builds_real_link_after_r16_edit():
    src = _telegraph_src()
    block = src.split("elif total > 1:", 1)[1].split("for sec_idx, sec in enumerate(part_secs):", 1)[0]
    assert '"tag": "a"' in block
    assert "get_youtube_timestamp_url(url, _secs)" in block


# ── 2. Слияние тонких хвостовых частей через реальный editPage ──

def test_merge_loop_present_and_uses_classified_real_editpage_as_ground_truth():
    src = _telegraph_src()
    block = src.split("success = await _publish_recursive(sections)", 1)[1].split(
        "total      = len(published_parts)", 1
    )[0]
    assert "while len(published_parts) >= 2:" in block
    assert "_edit_telegraph_page_classified(" in block
    assert "if not _merged_result.ok:" in block and "break" in block
    assert "_merged_result.error != \"CONTENT_TOO_BIG\"" in block
    assert "published_parts.pop()" in block
    assert "published_parts[-2] = (_combined_secs, _prev_url)" in block


def test_merge_loop_combines_sections_in_correct_order():
    src = _telegraph_src()
    block = src.split("while len(published_parts) >= 2:", 1)[1][:1000]
    assert "_combined_secs = _prev_secs + _last_secs" in block


def test_merge_loop_has_ratelimit_pause_before_first_attempt():
    src = _telegraph_src()
    block = src.split("success = await _publish_recursive(sections)", 1)[1].split(
        "while len(published_parts) >= 2:", 1
    )[0]
    assert "await asyncio.sleep(2)" in block
