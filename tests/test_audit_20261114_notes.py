"""Rows say which base and which ranges they used (2026-11-14 audit, findings M1 and E2).

Neither note changes a vote: the rules stay as they were, and the reader sees what the number rests on.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import offchart_board as board  # noqa: E402

DATA = json.loads((Path(__file__).parent / 'fixtures/edition_v9/2026-11-13T1802.data.json').read_text())
STAMP = datetime(2026, 11, 13, 9, 4, tzinfo=timezone.utc)  # 18:04 JST, 04:04 New York


def test_dxy_row_says_when_the_quoted_previous_close_differs_from_the_daily_table():
    note = board._dxy_base_note(DATA['dxy'], STAMP)
    assert note == '前日終値は現在値と同じ表示の値。日足の表の11/12終値は103.170で0.010違い、その基準なら+0.00%'


def test_dxy_row_has_no_note_when_both_sources_agree_or_the_table_is_missing():
    same = {**DATA['dxy'], 'prev_close': 103.17}
    assert board._dxy_base_note(same, STAMP) == ''
    assert board._dxy_base_note({**DATA['dxy'], 'recent_closes': None}, STAMP) == ''
    # The forming bar of the collection day is not a previous close.
    only_today = {**DATA['dxy'], 'recent_closes': [['2026-11-13', 103.14]]}
    assert board._dxy_base_note(only_today, STAMP) == ''


def test_fedwatch_row_names_the_range_shown_without_a_probability_and_does_not_count_it_as_zero():
    (item, _), reason = board._fedwatch(DATA, 'daily', STAMP)
    assert reason is None
    assert '3.75-4.00 は提供表で空欄。0%と見なさず計算から除外、表示のある確率の合計 100.0%' in item['note']
    assert item['note'] in item['source_quote'] and item['display'] == '期待 4.144%'


def test_fedwatch_row_has_no_such_note_when_every_range_has_a_probability():
    data = {**DATA, 'fedwatch': {**DATA['fedwatch'], 'unavailable_target_rates': []}}
    (item, _), _ = board._fedwatch(data, 'daily', STAMP)
    assert '空欄' not in item['note']
