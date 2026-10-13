"""Released US Tier A/B events without a number (statements, speeches) are archived, so their release
candle stays a level after the calendar page drops them (Monday's Daily looking back at Friday).

All offline: the archive is a temporary file and bars are synthetic.
"""
import json
from datetime import datetime

from scrapers import macro_surprise as ms
from scrapers import xau_levels as xl
from tests.test_offchart_collectors import flat_bars, set_bar, utc

FRIDAY = 'Friday, October 9, 2026'


def ev(indicator, time_jst, actual='N/A', forecast='N/A', country='United States'):
    return {'date': FRIDAY, 'time_jst': time_jst, 'country': country, 'indicator': indicator,
            'previous': 'N/A', 'forecast': forecast, 'actual': actual}


def jst(text):
    return datetime.fromisoformat(text).replace(tzinfo=ms.JST)


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def friday_pages():
    """The calendar on Friday: before the releases, at 21:31 (payrolls not yet filled in) and at 23:30."""
    speech = ev('Fed Chair Powell Speaks', '23:00')
    other = [ev('Existing Home Sales (Sep)', '23:00'), ev('ECB President Lagarde Speaks', '23:00', country='Euro Zone')]
    payrolls = ev('Nonfarm Payrolls (Sep)', '21:30', forecast='150K')
    return ([payrolls, speech] + other, [payrolls, speech] + other,
            [{**payrolls, 'actual': '180K'}, speech] + other)


def archive_friday(path):
    before, at_release, later = friday_pages()
    assert ms.archive_forecasts(before, jst('2026-10-09T18:00'), path) == 1
    assert ms.archive_actuals(before, jst('2026-10-09T18:00'), set(), path) == 0    # nothing released yet
    assert ms.archive_actuals(at_release, jst('2026-10-09T21:31'), set(), path) == 0
    assert ms.archive_actuals(later, jst('2026-10-09T23:30'), set(ms.load_actual_history(path)), path) == 1
    assert ms.archive_actuals(later, jst('2026-10-09T23:45'), set(ms.load_actual_history(path)), path) == 0


def test_released_tier_events_without_a_number_are_archived_once(tmp_path):
    path = tmp_path / 'f.jsonl'
    archive_friday(path)
    kinds = [(row.get('type'), row['key'].split('|')[1]) for row in rows(path)]
    assert kinds == [(None, 'Nonfarm Payrolls (Sep)'),
                     ('release_seen', 'Nonfarm Payrolls (Sep)'),                    # released, figure not yet shown
                     ('actual_first_seen', 'Nonfarm Payrolls (Sep)'),
                     ('release_seen', 'Fed Chair Powell Speaks')]                   # not Tier A/B or not US: none
    payrolls = 'United States|Nonfarm Payrolls (Sep)|2026-10-09T21:30:00+09:00'
    assert ms.load_forecast_history(path)[payrolls]['forecast'] == '150K'          # release rows are not forecasts
    assert set(ms.load_release_history(path)) == {payrolls, 'United States|Fed Chair Powell Speaks|2026-10-09T23:00:00+09:00'}


def test_mondays_daily_rebuilds_the_speech_and_keeps_its_release_candle(tmp_path):
    path = tmp_path / 'f.jsonl'
    archive_friday(path)
    events = [ev('CPI (MoM) (Sep)', '21:30') | {'date': 'Wednesday, October 14, 2026'}]  # Monday's page: this week only
    since, until = jst('2026-10-09T00:00'), jst('2026-10-12T18:05')
    assert ms.merge_history_events(events, since, until, path) == 2
    got = {e['indicator']: (e['actual'], e['forecast'], e.get('from_history')) for e in events[1:]}
    assert got == {'Nonfarm Payrolls (Sep)': ('180K', '150K', True), 'Fed Chair Powell Speaks': ('N/A', 'N/A', True)}
    assert ms.merge_history_events(events, since, until, path) == 0                # already listed: not twice
    surprises = ms.released_surprises(events, until, lookback_hours=90, forecast_history=ms.load_forecast_history(path),
                                      actual_history=ms.load_actual_history(path))
    assert [s['indicator'] for s in surprises] == ['Nonfarm Payrolls (Sep)']        # a speech has no surprise
    bars = flat_bars('2026-08-31T21:00', '2026-10-12T07:00')
    set_bar(bars, '2026-10-09T14:00', high=4060.0, low=3940.0)                      # the hour holding 23:00 JST
    result = xl.build_levels(bars, [], utc('2026-10-12T09:05'), price=4000.0, events=events)
    names = [part for lv in result['levels'] for part in lv['name'].split('・') if ' 発表' in part]
    assert 'FRB議長 発言 発表時刻を含む1時間足高値（10/09 23:00）' in names


def test_history_ignores_release_rows_outside_the_tiers(tmp_path):
    path = tmp_path / 'f.jsonl'
    path.write_text(json.dumps({'key': 'United States|Existing Home Sales (Sep)|2026-10-09T23:00:00+09:00',
                                'type': 'release_seen', 'captured_at': '2026-10-09T23:30:00+09:00'}) + '\n', encoding='utf-8')
    assert ms.merge_history_events([], jst('2026-10-09T00:00'), jst('2026-10-12T18:00'), path) == 0
