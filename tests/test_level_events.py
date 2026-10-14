"""Release-candle levels when the economic calendar failed: the released US events restored from the archive
during collection are used (xau_levels.level_events), the source is recorded as inputs.events_source, and
validation (report_bundle.check_regenerated) recomputes them with the same rule.

All offline: the archive is a temporary file and bars are synthetic.
"""
import json
from copy import deepcopy
from datetime import datetime

import pytest

import main
from scrapers import macro_surprise as ms
from scrapers import xau_levels as xl
from scripts import report_bundle as bundle
from tests.test_offchart_board import DRAW_LINE, SOURCE, prepared, sample
from tests.test_offchart_collectors import scenario, utc

NOW = utc('2026-10-13T13:10')                       # Tuesday 22:10 JST
MONDAY = 'Monday, October 12, 2026'
FAILED = {'error': 'ページ取得失敗: timeout', 'events': []}
CPI_ROWS = ['CPI 発表15分足高値（10/12 21:30）', 'CPI 発表15分足安値（10/12 21:30）']


def us(indicator, time_jst, actual='N/A', forecast='N/A'):
    return {'date': MONDAY, 'time_jst': time_jst, 'country': 'United States', 'indicator': indicator,
            'previous': 'N/A', 'forecast': forecast, 'actual': actual}


def jst(text):
    return datetime.fromisoformat(text).replace(tzinfo=ms.JST)


def archive(path):
    """Monday's CPI (21:30 JST, with a number) and the Fed chair's speech (23:00 JST, no number)."""
    cpi = us('CPI (MoM) (Sep)', '21:30', forecast='0.3%')
    assert ms.archive_forecasts([cpi], jst('2026-10-12T18:00'), path) == 1
    assert ms.archive_actuals([{**cpi, 'actual': '0.4%'}, us('Fed Chair Powell Speaks', '23:00')],
                              jst('2026-10-12T23:30'), set(), path) == 1
    # A release row outside Tier A/B (written by hand: archive_actuals does not record it) is not restored.
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'key': 'United States|Existing Home Sales (Sep)|2026-10-12T23:00:00+09:00',
                            'type': 'release_seen', 'captured_at': '2026-10-12T23:30:00+09:00'}) + '\n')


def release_rows(levels):
    return [part for lv in levels['levels'] for part in lv['name'].split('・') if ' 発表' in part]


def collect(calendar, path, data=None):
    """attach_levels as in collect_all_data, then the data JSON as written."""
    bars, m15, _ = scenario()
    results = data if data is not None else {}
    if calendar is not None:
        results['economic_calendar'] = deepcopy(calendar)
    else:
        results.pop('economic_calendar', None)
    main.attach_levels(results, bars, m15, NOW, 4177.47, False, history_path=path)
    return json.loads(json.dumps(results, default=str, ensure_ascii=False))


def test_a_failed_calendar_keeps_the_cpi_release_candle_from_the_archive_and_validates(tmp_path):
    path = tmp_path / 'f.jsonl'
    archive(path)
    data = collect(FAILED, path, sample())
    assert data['economic_calendar'] == FAILED                                  # the page's own result is kept as fetched
    levels = data['xau_levels']
    assert levels['inputs']['events_source'] == 'archive'
    assert set(CPI_ROWS) <= set(release_rows(levels))
    cpi = [lv for lv in levels['levels'] if 'CPI 発表' in lv['name']]
    assert sorted(lv['price'] for lv in cpi) == [3992.0, 4012.0]               # the 15-minute candle at 21:30 JST
    assert xl.level_events(data) == (data['economic_calendar_restored']['events'], 'archive')
    # The recomputation used at validation gives the same result, and the whole edition validates.
    again = xl.recompute(data['xau_bars_used'], levels['inputs']['computed_at'], levels['price'],
                         data['economic_calendar_restored']['events'], False, 'archive')
    assert json.loads(json.dumps(again, default=str)) == levels
    manifest, figures, source, summary = prepared(tmp_path, data, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(data, manifest, figures, source, summary)
    # Without the restored events the stored release candles cannot be reproduced.
    changed = deepcopy(data)
    del changed['economic_calendar_restored']
    with pytest.raises(bundle.BundleError, match='Liquidity levels differ'):
        bundle.check_regenerated(changed, manifest, figures, source, summary)


def test_a_working_calendar_still_uses_its_own_events(tmp_path):
    path = tmp_path / 'f.jsonl'
    archive(path)
    _, _, events = scenario()
    data = collect({'error': None, 'events': events}, path, sample())
    levels = data['xau_levels']
    assert levels['inputs']['events_source'] == 'calendar' and 'economic_calendar_restored' not in data
    # The previous result: build_levels with the calendar's events (the archived CPI and speech merged in).
    assert data['economic_calendar']['history_added'] == 2
    bars, m15, _ = scenario()
    before = json.loads(json.dumps(xl.build_levels(bars, m15, NOW, price=4177.47,
                                                   events=data['economic_calendar']['events']), default=str))
    assert before == levels
    manifest, figures, source, summary = prepared(tmp_path, data, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(data, manifest, figures, source, summary)
    # An edition stored before events_source existed is still recomputed exactly.
    legacy = deepcopy(data)
    del legacy['xau_levels']['inputs']['events_source']
    manifest, figures, source, summary = prepared(tmp_path, legacy, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(legacy, manifest, figures, source, summary)


@pytest.mark.parametrize('calendar', [None, FAILED])
def test_no_calendar_and_no_archive_gives_no_release_levels(tmp_path, calendar):
    data = collect(calendar, tmp_path / 'missing.jsonl')
    levels = data['xau_levels']
    assert levels['inputs']['events_source'] == 'none' and levels['levels']
    assert release_rows(levels) == [] and not any('NEWS' in lv['key'] for lv in levels['levels'])
    assert data['economic_calendar_restored']['events'] == [] and xl.level_events(data) == (None, 'none')
    again = xl.recompute(data['xau_bars_used'], levels['inputs']['computed_at'], levels['price'], None, False, 'none')
    assert json.loads(json.dumps(again, default=str)) == levels


def test_restored_events_include_a_tier_release_recorded_without_a_number(tmp_path):
    path = tmp_path / 'f.jsonl'
    archive(path)
    data = collect(FAILED, path)
    restored = data['economic_calendar_restored']
    assert [(e['indicator'], e['actual'], e['forecast']) for e in restored['events']] == [
        ('CPI (MoM) (Sep)', '0.4%', '0.3%'), ('Fed Chair Powell Speaks', 'N/A', 'N/A')]   # Existing Home Sales: not A/B
    assert (restored['since'], restored['until'], restored['source']) == \
        ('2026-10-12T00:00:00+09:00', '2026-10-13T22:10:00+09:00', 'calendar_forecasts.jsonl')
    assert 'FRB議長 発言 発表時刻を含む1時間足高値（10/12 23:00）' in release_rows(data['xau_levels'])


def test_enrich_keeps_the_events_the_levels_used_and_the_same_surprises(tmp_path, monkeypatch):
    path = tmp_path / 'f.jsonl'
    archive(path)
    for name in ('load_forecast_history', 'load_actual_history', 'load_release_history'):
        monkeypatch.setattr(main, name, lambda _=None, f=getattr(ms, name): f(path))
    monkeypatch.setattr(main, 'append_snapshot', lambda snap: False)
    monkeypatch.setattr(main, 'load_history', lambda: [])
    kwargs = dict(now=jst('2026-10-13T22:12'), gvz_fetch=lambda sid: {'value': 24.0, 'as_of_date': '2026-10-09'},
                  news_builder=lambda now, hours: {'candidate_count': 0, 'kept': [], 'sources': [],
                                                   'selection': {'mode': 'skipped'}}, save=False)
    results = {'timestamp': '2026-10-13T22:00:00'}
    collect(FAILED, path, results)
    used = deepcopy(results['economic_calendar_restored'])
    assert [e['indicator'] for e in used['events']] == ['CPI (MoM) (Sep)', 'Fed Chair Powell Speaks']
    main.enrich_offchart_inputs(results, **kwargs)
    assert results['economic_calendar_restored'] == used                       # the levels' set is not replaced
    assert [(s['indicator'], s['surprise']['diff']) for s in results['macro_surprises']] == [('CPI (MoM) (Sep)', 0.1)]
    json.dumps(results, default=str)
    # Collected without the levels (another symbol): enrich stores the restored events itself.
    alone = {'timestamp': '2026-10-13T22:00:00', 'economic_calendar': deepcopy(FAILED)}
    main.enrich_offchart_inputs(alone, **kwargs)
    assert [e['indicator'] for e in alone['economic_calendar_restored']['events']] == \
        ['CPI (MoM) (Sep)', 'Fed Chair Powell Speaks']
    assert alone['economic_calendar_restored']['until'] == '2026-10-13T22:12:00+09:00'
    json.dumps(alone['economic_calendar_restored'])                            # JSON-ready without a default
    # A restore that failed when the levels were made gives no surprises; the archive is not read a second time.
    failed = {'timestamp': '2026-10-13T22:00:00', 'economic_calendar': deepcopy(FAILED),
              'economic_calendar_restored': {'events': None, 'error': 'OSError'}}
    main.enrich_offchart_inputs(failed, **kwargs)
    assert failed['economic_calendar_restored'] == {'events': None, 'error': 'OSError'}
    assert failed['macro_surprises'] == []
