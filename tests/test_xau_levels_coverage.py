"""xau_levels coverage: missing bars in formation and scan windows, contiguous runs, reproducible inputs.

All offline; bars are synthetic (helpers shared with test_offchart_collectors).
"""
import copy
import hashlib
import json

import pytest

from scrapers import xau_bars as xb
from scrapers import xau_levels as xl
from tests.test_offchart_collectors import flat_bars, scenario, set_bar, utc

NOW = '2026-10-13T13:15'


def base(end='2026-10-13T12:00'):
    """Flat bars (high 4001, low 3999) from the first trading date of September."""
    return flat_bars('2026-08-31T21:00', end)


def drop(bars, *whens):
    return [b for b in bars if b['ts'] not in {utc(w) for w in whens}]


def by_key(result, key):
    return next(lv for lv in result['levels'] if key in lv['key'].split('+'))


def test_complete_bars_have_no_gaps():
    bars, m15, events = scenario()
    for weekly in (False, True):
        result = xl.build_levels(bars, m15, utc(NOW), price=4000.0, events=events, weekly=weekly)
        assert result['cautions'] == []
        assert all(lv['gaps'] == {'formation': 0.0, 'scan': 0.0} for lv in result['levels'])
        assert result['coverage']['previous_week'] == {'period': '2026-10-05', 'bars': 115, 'expected': 115}
        assert result['coverage']['previous_month'] == {'period': '2026-09', 'bars': 506, 'expected': 506}


def test_a_missing_bar_in_the_scan_window_is_named_and_not_a_resting_candidate():
    bars = base()
    set_bar(bars, '2026-10-12T14:00', high=4010.0)                   # previous day's high
    complete = xl.build_levels(bars, [], utc(NOW), price=4005.0)
    assert xl.nearest_resting(complete, 4005.0)['high']['key'] == 'PDH'
    result = xl.build_levels(drop(bars, '2026-10-13T03:00'), [], utc(NOW), price=4005.0)   # Asia, 12:00 JST
    pdh = by_key(result, 'PDH')
    assert (pdh['state'], pdh['state_word'], pdh['gaps']) == ('resting', '未到達・欠測1時間', {'formation': 0.0, 'scan': 1.0})
    assert by_key(result, 'ASIA_H')['note'] == '形成期間に欠測1時間'
    near = xl.nearest_resting(result, 4005.0)
    assert 'high' not in near                                         # the only high above has a gap
    assert near['low']['key'] == 'LONDON_L' and near['low']['gaps'] == {'formation': 0.0, 'scan': 0.0}
    lines = xl.format_level_lines(result)
    assert '- 前日高値（10/12）: 4,010.00（現在値比 +5.00／+0.12%）｜BSL｜未到達・欠測1時間' in lines
    assert lines[3] == '- 欠測: 水準の形成期間や形成後に足が欠けた水準は、状態の後に欠測時間を付け、直近の未到達候補から外す'
    assert lines[-1] == '- 直近の未到達: 下 London安値（当日） 3,999.00'


def test_nearest_resting_takes_the_next_complete_level():
    row = lambda key, price, gaps: {'key': key, 'side': 'high', 'price': price, 'state': 'resting', 'gaps': gaps}
    levels = {'levels': [row('NEAR', 4005.0, {'formation': 0.0, 'scan': 0.25}),
                         row('FORMED', 4008.0, {'formation': 1.0, 'scan': 0.0}),
                         row('FAR', 4030.0, {'formation': 0.0, 'scan': 0.0})]}
    assert xl.nearest_resting(levels, 4000.0)['high']['key'] == 'FAR'


def test_a_missing_bar_in_the_previous_week_or_month_is_a_caution_and_a_note():
    week = xl.build_levels(drop(base(), '2026-10-07T10:00'), [], utc(NOW), price=4000.0)
    assert week['cautions'] == ['前週（10/05週）の1時間足が114/115本。欠けた時間の高安は入っていない']
    pwh = by_key(week, 'PWH')
    assert (pwh['name'], pwh['state_word'], pwh['note']) == ('前週高値（10/05週）', '未到達', '形成期間に欠測1時間')
    assert '- 前週高値（10/05週）: 4,001.00（現在値比 +1.00／+0.03%）｜BSL｜未到達｜形成期間に欠測1時間' in xl.format_level_lines(week)
    assert by_key(week, 'PMH')['gaps'] == {'formation': 0.0, 'scan': 1.0}   # scanned across it, a row of its own
    month = xl.build_levels(drop(base(), '2026-09-15T10:00'), [], utc(NOW), price=4000.0)
    assert month['cautions'] == ['前月（9月）の1時間足が505/506本。欠けた時間の高安は入っていない']
    assert by_key(month, 'PMH')['note'] == '形成期間に欠測1時間'


def hour(when, close, hours=None):
    bar = {'ts': utc(when), 'high': close + 1, 'low': close - 1, 'close': close}
    return {**bar, 'hours': hours} if hours else bar


def test_closes_outside_count_only_across_a_contiguous_run():
    outside = lambda *whens: [hour(w, 4015) for w in whens]
    run = outside(*(f'2026-10-13T{h:02d}:00' for h in (1, 2, 3, 4, 6, 7, 8, 9)))   # 05:00 missing, eight hours outside
    assert xl.sweep_state(4010, 'high', run) == ('beyond', utc('2026-10-13T01:00'))
    assert xl.sweep_state(4010, 'high', run[:4] + [hour('2026-10-13T05:00', 4015)] + run[4:])[0] == 'broken'
    weekend = outside('2026-10-09T18:00', '2026-10-09T19:00', '2026-10-09T20:00',   # Friday 16:00 New York last
                      '2026-10-11T22:00', '2026-10-11T23:00', '2026-10-12T00:00')   # Sunday 18:00 New York first
    assert xl.sweep_state(4010, 'high', weekend)[0] == 'broken'
    daily_break = outside('2026-10-12T18:00', '2026-10-12T19:00', '2026-10-12T20:00',
                          '2026-10-12T22:00', '2026-10-12T23:00', '2026-10-13T00:00')  # 17:00 New York is closed
    assert xl.sweep_state(4010, 'high', daily_break)[0] == 'broken'
    quarters = [hour(f'2026-10-13T05:{m:02d}', 4015, 0.25) for m in (0, 30, 45)]       # 05:15 missing
    hours = outside(*(f'2026-10-13T{h:02d}:00' for h in range(6, 11)))
    assert xl.sweep_state(4010, 'high', quarters + hours)[0] == 'beyond'               # 5.5 hours after the gap
    quarters.insert(1, hour('2026-10-13T05:15', 4015, 0.25))
    assert xl.sweep_state(4010, 'high', quarters + hours)[0] == 'broken'               # 6 hours


def test_one_missing_bar_today_is_a_caution_and_stale_needs_two_bars():
    today = xl.build_levels(drop(base(), '2026-10-13T05:00'), [], utc(NOW), price=4000.0)
    assert today['cautions'] == ['当日の1時間足に1本の欠落。その時間の値動きは状態に入っていない']
    one = xl.build_levels(base('2026-10-13T11:00'), [], utc(NOW), price=4000.0)
    assert one['coverage']['stale_hours'] == 1 and one['cautions'] == []
    # A provider one bar late is not a gap of every level: the states cover the bars received.
    assert by_key(one, 'PDH')['state_word'] == '未到達' and xl.nearest_resting(one, 4000.0)
    two = xl.build_levels(base('2026-10-13T10:00'), [], utc(NOW), price=4000.0)
    assert two['coverage']['stale_hours'] == 2
    assert two['cautions'] == ['最終確定足が10/13 19:00 JSTで、その後の2時間分の足がない。以降の値動きは状態に入っていない']
    assert by_key(two, 'PDH')['state_word'] == '未到達' and xl.nearest_resting(two, 4000.0) == {}  # stale: no lead chip


def test_a_started_hour_counts_each_missing_quarter():
    h1 = {utc('2026-10-13T12:00'), utc('2026-10-13T13:00')}
    start, now = utc('2026-10-13T12:30'), utc('2026-10-13T14:05')
    assert xl.scan_missing(h1, {utc('2026-10-13T12:30')}, start, now) == 0.25
    assert xl.scan_missing(h1, set(), start, now) == 0.5
    assert xl.scan_missing({utc('2026-10-13T12:00')}, set(), start, now) == 1.5
    assert xl.scan_missing(h1, set(), utc('2026-10-13T13:00'), now) == 0.0
    assert xl.scan_missing(set(), set(), utc('2026-10-13T13:30'), utc('2026-10-13T13:50')) == 0.0  # hour not closed


def test_a_release_candle_without_the_rest_of_its_hour_gives_way_to_a_complete_level():
    bars = base()
    set_bar(bars, '2026-10-13T08:00', high=4030.0)                                  # London high
    set_bar(bars, '2026-10-13T12:00', high=4010.0, low=3995.0)                      # holds the release candle
    m15 = [{'ts': utc('2026-10-13T12:30'), 'open': 4000.0, 'high': 4010.0, 'low': 3995.0, 'close': 4002.0}]
    events = [{'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'Retail Sales (MoM) (Sep)'}]
    result = xl.build_levels(bars, m15, utc(NOW), price=4002.0, events=events)
    news = next(lv for lv in result['levels'] if lv['name'].startswith('小売売上高 発表15分足高値'))
    assert (news['state_word'], news['gaps']) == ('未到達・欠測0.25時間', {'formation': 0.0, 'scan': 0.25})
    assert xl.nearest_resting(result, 4002.0)['high']['key'] == 'LONDON_H'


def raw_inputs():
    bars, m15, events = scenario()
    old = flat_bars('2026-08-03T00:00', '2026-08-31T20:00')
    unclosed = {'ts': utc('2026-10-13T13:00'), 'open': 4000.0, 'high': 4001.0, 'low': 3999.0, 'close': 4000.0}
    m15_bar = lambda when: {'ts': utc(when), 'open': 4000.0, 'high': 4001.0, 'low': 3999.0, 'close': 4000.0}
    raw_m15 = [m15_bar('2026-10-09T12:30')] + m15 + [m15_bar('2026-10-13T13:00'), m15_bar('2026-10-13T13:15')]
    return bars, m15, events, old + bars + [unclosed], raw_m15


def test_level_inputs_trim_to_the_previous_month_and_closed_bars():
    bars, m15, events, raw_h1, raw_m15 = raw_inputs()
    h1_used, m15_used = xl.level_inputs(raw_h1, raw_m15, utc(NOW))
    assert h1_used == bars and h1_used[0]['ts'] == utc('2026-08-31T22:00')   # 09/01's trading date, 18:00 New York
    assert [b['ts'] for b in m15_used] == [utc('2026-10-12T12:30'), utc('2026-10-12T12:45'), utc('2026-10-13T13:00')]
    weekly_m15 = xl.level_inputs(raw_h1, raw_m15, utc(NOW), weekly=True)[1]
    assert weekly_m15[0]['ts'] == utc('2026-10-09T12:30')                         # the Weekly keeps a week of releases
    result = xl.build_levels(raw_h1, raw_m15, utc(NOW), price=4000.0, events=events)
    assert result['inputs'] == {'computed_at': '2026-10-13T13:15:00+00:00',
                                'h1': {'count': len(bars), 'first_utc': '2026-08-31 22:00', 'last_utc': '2026-10-13 12:00'},
                                'm15': {'count': 3, 'first_utc': '2026-10-12 12:30', 'last_utc': '2026-10-13 13:00'}}
    assert xl.build_levels([], [], utc(NOW))['inputs']['h1'] == {'count': 0, 'first_utc': None, 'last_utc': None}


@pytest.mark.parametrize('weekly', [False, True])
def test_recompute_from_the_snapshot_reproduces_the_result(weekly):
    _, _, events, raw_h1, raw_m15 = raw_inputs()
    now = utc(NOW).replace(microsecond=500)
    original = xl.build_levels(raw_h1, raw_m15, now, price=4000.0, events=events, weekly=weekly)
    used = xl.level_inputs(raw_h1, raw_m15, now, weekly)
    assert xl.build_levels(*used, now, price=4000.0, events=events, weekly=weekly) == original
    stored = json.loads(json.dumps(xb.snapshot(*used)))                        # as saved with the edition
    again = xl.recompute(stored, original['inputs']['computed_at'], 4000.0, events, weekly)
    assert again == original and again['levels']


def resign(snap):
    payload = {'h1': snap['h1'], 'm15': snap['m15']}
    text = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return {**snap, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def test_restore_rejects_tampered_duplicate_and_malformed_bars():
    _, _, _, raw_h1, raw_m15 = raw_inputs()
    snap = xb.snapshot(*xl.level_inputs(raw_h1, raw_m15, utc(NOW)))
    assert snap['format'] == 1 and snap['h1'][0] == [int(utc('2026-08-31T22:00').timestamp()), 4000.0, 4001.0, 3999.0, 4000.0]
    assert resign(snap)['sha256'] == snap['sha256']                             # canonical JSON, sorted keys
    h1, m15 = xb.restore(snap)
    assert h1[0]['ts'] == utc('2026-08-31T22:00') and len(m15) == 3
    tampered = copy.deepcopy(snap)
    tampered['h1'][5][2] += 10
    with pytest.raises(ValueError, match='SHA-256'):
        xb.restore(tampered)
    duplicate = copy.deepcopy(snap)
    duplicate['h1'].insert(1, duplicate['h1'][0])
    with pytest.raises(ValueError, match='重複'):
        xb.restore(resign(duplicate))
    malformed = copy.deepcopy(snap)
    malformed['m15'][0][2] = malformed['m15'][0][4] - 1                         # high below the close
    with pytest.raises(ValueError, match='矛盾'):
        xb.restore(resign(malformed))
    with pytest.raises(ValueError, match='形式'):
        xb.restore({**snap, 'format': 2})


def test_weekly_this_month_includes_today_and_needs_no_previous_month():
    bars = base()
    set_bar(bars, '2026-10-07T10:00', high=4050.0)
    set_bar(bars, '2026-10-13T05:00', high=4090.0)                                 # the month's high, today
    mtdh = by_key(xl.build_levels(bars, [], utc(NOW), price=4000.0, weekly=True), 'MTDH')
    assert (mtdh['name'], mtdh['price'], mtdh['formed'], mtdh['state_word']) == ('今月高値（10月）', 4090.0, '10/13 14:00', '未到達')
    set_bar(bars, '2026-10-13T12:00', high=4095.0)                                 # in the latest bar
    latest = by_key(xl.build_levels(bars, [], utc(NOW), price=4000.0, weekly=True), 'MTDH')
    assert (latest['price'], latest['state_word'], latest['touched_at']) == (4095.0, '未到達', None)
    october = [b for b in bars if b['ts'] >= utc('2026-09-30T21:00')]               # trading date 10/01 onwards
    result = xl.build_levels(october, [], utc(NOW), price=4000.0, weekly=True)
    keys = {lv['key'] for lv in result['levels']}
    assert by_key(result, 'MTDH') and by_key(result, 'MTDL') and 'PM' not in ''.join(keys)
    assert by_key(result, 'MTDH')['gaps'] == {'formation': 0.0, 'scan': 0.0}
