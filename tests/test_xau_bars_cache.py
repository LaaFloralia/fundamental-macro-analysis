"""15-minute bars are cached like the hourly bars, so a release candle outlives a later failed or shorter fetch.

All offline: the Twelve Data getter is injected and bars are synthetic.
"""
import json

import main
from scrapers import xau_bars as xb
from scrapers import xau_levels as xl
from tests.test_offchart_collectors import flat_bars, set_bar, utc

RELEASE = [{'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States',
            'indicator': 'Retail Sales (MoM) (Sep)'}]                              # 12:30 UTC, Tier B
CANDLE = {'ts': utc('2026-10-13T12:30'), 'open': 4000.0, 'high': 4010.0, 'low': 3995.0, 'close': 4002.0}


def quarters(start, end):
    """Flat 15-minute bars for the market hours from `start` to `end` (hour starts)."""
    return [{**b, 'ts': b['ts'].replace(minute=minute)} for b in flat_bars(start, end) for minute in (0, 15, 30, 45)]


def values(bars):
    return [{'datetime': b['ts'].strftime('%Y-%m-%d %H:%M:%S'), 'open': str(b['open']), 'high': str(b['high']),
             'low': str(b['low']), 'close': str(b['close'])} for b in reversed(bars)]


def getter(h1, m15):
    """Twelve Data /time_series stand-in; None for an interval answers with a provider error."""
    def get(path, params):
        bars = {'1h': h1, '15min': m15}[params['interval']]
        return {'status': 'error', 'message': 'limit'} if bars is None else {'values': values(bars)}
    return get


def hourly(end):
    bars = flat_bars('2026-08-31T21:00', end)
    set_bar(bars, '2026-10-13T12:00', high=4012.0, low=3993.0)                     # holds the release candle
    return bars


def release_rows(result):
    """Release-candle names, without the other names sharing their row (the hour's high is also the previous day's)."""
    return [part for lv in result['levels'] for part in lv['name'].split('・') if ' 発表' in part]


def first_edition(cache):
    m15 = [b for b in quarters('2026-10-13T11:00', '2026-10-13T12:00') if b['ts'] != CANDLE['ts']] + [CANDLE]
    meta, h1, got = xb.collect(utc('2026-10-13T13:15'), getter(hourly('2026-10-13T12:00'), sorted(m15, key=lambda b: b['ts'])), cache)
    assert meta['error'] is None and CANDLE in got
    return meta


def test_a_failed_15_minute_fetch_keeps_the_cached_release_candle(tmp_path):
    cache = tmp_path / 'xau_h1.csv'
    meta = first_edition(cache)
    assert meta['m15_path'] == str(tmp_path / 'xau_m15.csv') and meta['m15_count'] == 8
    now = utc('2026-10-14T09:00')                                                   # next day, 18:00 JST
    meta, h1, m15 = xb.collect(now, getter(hourly('2026-10-14T07:00'), None), cache)
    assert meta['error'] == 'Twelve Data XAU/USD 15min 取得不可（limit）'
    assert (meta['m15_count'], meta['m15_last_bar_utc'], CANDLE in m15) == (8, '2026-10-13 12:45', True)
    result = xl.build_levels(h1, m15, now, price=4000.0, events=RELEASE)
    assert release_rows(result) == ['小売売上高 発表15分足高値（10/13 21:30）', '小売売上高 発表15分足安値（10/13 21:30）']
    # Without the cache the same edition fell back to the hour holding the release.
    fallback = xl.build_levels(h1, [], now, price=4000.0, events=RELEASE)
    assert release_rows(fallback)[0] == '小売売上高 発表時刻を含む1時間足高値（10/13 21:30）'


def test_a_shorter_15_minute_fetch_is_merged_with_the_cache_and_stored_with_the_edition(tmp_path):
    cache = tmp_path / 'xau_h1.csv'
    first_edition(cache)
    now = utc('2026-10-14T09:00')
    later = quarters('2026-10-14T06:00', '2026-10-14T07:00')                        # no longer reaches 10/13
    meta, h1, m15 = xb.collect(now, getter(hourly('2026-10-14T07:00'), later), cache)
    assert meta['error'] is None and meta['m15_count'] == 16 and CANDLE in m15
    assert xb.read_cache(tmp_path / 'xau_m15.csv') == m15
    results = {'economic_calendar': {'events': list(RELEASE)}}
    main.attach_levels(results, h1, m15, now, 4000.0, False, history_path=tmp_path / 'f.jsonl')
    assert release_rows(results['xau_levels'])[0] == '小売売上高 発表15分足高値（10/13 21:30）'
    stored = json.loads(json.dumps(results, default=str))                          # as written to the data JSON
    assert [int(CANDLE['ts'].timestamp()), 4000.0, 4010.0, 3995.0, 4002.0] in stored['xau_bars_used']['m15']
    again = xl.recompute(stored['xau_bars_used'], stored['xau_levels']['inputs']['computed_at'], 4000.0,
                         stored['economic_calendar']['events'], False)
    assert json.loads(json.dumps(again, default=str)) == stored['xau_levels']


def test_a_fresh_15_minute_bar_wins_and_the_cache_is_bounded(tmp_path):
    path = tmp_path / 'xau_m15.csv'
    xb.update_cache([CANDLE], path, xb.M15_KEEP)
    revised = {**CANDLE, 'high': 4011.0}
    assert xb.update_cache([revised], path, xb.M15_KEEP) == [revised]                # the provider's latest value
    merged = xb.update_cache(quarters('2026-10-13T13:00', '2026-10-13T13:00'), path, keep=3)
    assert [b['ts'] for b in merged] == [utc(f'2026-10-13T13:{m}') for m in ('15', '30', '45')]  # oldest dropped
    assert xb.M15_KEEP * 0.25 > 7 * 24                                              # longer than the Weekly window
