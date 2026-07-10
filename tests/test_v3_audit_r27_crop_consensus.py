#!/usr/bin/env python3
"""AUDIT R27 (живой лог: `render_short_clip ffmpeg error`, Short 4/5 потерян):
cropdetect дал три разных crop по одному голосу
(votes={'648:634:154:86':1,'458:620:296:100':1,'894:644:140:76':1}),
blind max() выбрал случайный битый вариант, ffmpeg упал на фильтре.

Фикс: `_crop_consensus` — crop берётся только при согласии сэмплов;
иначе "" (без обрезки, безопасный полный кадр, Short рендерится).
"""
from services.ffmpeg import _crop_consensus


def test_three_disagreeing_single_votes_rejected():
    votes = {"648:634:154:86": 1, "458:620:296:100": 1, "894:644:140:76": 1}
    assert _crop_consensus(votes, 3) == ""


def test_full_agreement_accepted():
    assert _crop_consensus({"1202:676:0:0": 3}, 3) == "1202:676:0:0"


def test_majority_two_of_three_accepted():
    assert _crop_consensus({"1202:676:0:0": 2, "470:328:16:24": 1}, 3) == "1202:676:0:0"


def test_two_disagreeing_rejected():
    assert _crop_consensus({"a": 1, "b": 1}, 2) == ""


def test_single_sample_accepted():
    # при единственном сэмпле выбора нет — принимаем его
    assert _crop_consensus({"396:270:72:44": 1}, 1) == "396:270:72:44"


def test_empty_votes_returns_empty():
    assert _crop_consensus({}, 0) == ""
