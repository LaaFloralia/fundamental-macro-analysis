"""Calendar coverage and history restoration (F10), liquidity-map freshness (F9), retail split drawing (F15)
and the folded retail average (F17). Display side only: no network."""
from copy import deepcopy
from datetime import datetime, timedelta
import re

import pytest

import main
from scrapers import fred
from scrapers import macro_surprise as ms
from scripts import offchart_board as board
from scripts import offchart_dashboard as dash
from scripts import report_bundle as bundle
from tests.test_offchart_board import DRAW_LINE, NOW, SOURCE, ev, prepared, sample

FAILED = 'カレンダー取得失敗のため予定を確認できない（Investing.com: {}）'


def calendar_items(data, mode='daily'):
    return board.event_figure(data, NOW, mode)['figure']


def page_of(data, source=SOURCE):
    result = bundle.observations(data, NOW, 'daily', source)
    full = source + result['appendix']
    summary = bundle.make_summary(full, 'daily', result['asOf'], result['lead'])
    summary['limitations'] = [{'title': '今回の制約と直近の変化', 'text': n, 'source_quote': n} for n in result['limitations']]
    page, _ = dash.build(full, summary, result['figures'], title='t', source_name='s.md')
    return page, result, full, summary


# ------------------------------------------------------------------ F10: coverage

@pytest.mark.parametrize('calendar,expected', [
    ({'error': None, 'events': [ev('Monday, October 12, 2026', '21:30', 'Initial Jobless Claims')]}, None),
    ({'error': 'ページ取得失敗: timeout', 'events': []}, ('failed', FAILED.format('ページ取得失敗: timeout'))),
    ({'error': None, 'events': []}, ('failed', FAILED.format('予定が0件'))),
    ({'error': None, 'events': [], 'missing_tabs': ['Next Week']}, ('failed', FAILED.format('予定が0件・週タブ Next Week が見つからない'))),
    ({}, ('failed', FAILED.format('取得の記録なし'))),
    (None, ('failed', FAILED.format('取得の記録なし'))),
    ({'error': None, 'events': [ev('Monday, October 12, 2026', '21:30', 'Initial Jobless Claims')], 'missing_tabs': ['Next Week']},
     ('incomplete', 'カレンダー取得が不完全で、一部の予定を確認できない（Investing.com: 週タブ Next Week を読めない）')),
    # A week tab that was opened but gave no row was not read either.
    ({'error': None, 'events': [ev('Monday, October 12, 2026', '21:30', 'Initial Jobless Claims')],
      'tab_counts': {'This Week': 1, 'Next Week': 0}},
     ('incomplete', 'カレンダー取得が不完全で、一部の予定を確認できない（Investing.com: 週タブ Next Week を読めない）')),
    ({'error': None, 'events': [ev('Monday, October 12, 2026', '21:30', 'Initial Jobless Claims')],
      'tab_counts': {'This Week': 1, 'Next Week': 7}}, None),
    # Releases restored from the archive say nothing about what the page listed.
    ({'error': None, 'events': [{**ev('Friday, October 9, 2026', '21:30', 'Nonfarm Payrolls (Sep)'), 'from_history': True}]},
     ('failed', FAILED.format('予定が0件')))])
def test_calendar_coverage_separates_complete_incomplete_and_failed(calendar, expected):
    assert board.calendar_shortfall(calendar) == expected


def test_a_complete_calendar_with_no_us_event_says_nothing_scheduled():
    data = sample()
    data['economic_calendar']['events'] = [ev('Tuesday, October 13, 2026', '23:00', 'Existing Home Sales (Sep)')]
    data['macro_surprises'] = []
    result = board.event_figure(data, NOW, 'daily')
    assert [i['label'] for i in result['figure']['items']] == ['該当なし（Tier A/Bの米指標なし）']
    assert result['figure']['cautions'] == [] and result['coverage'] == 'complete'


def test_a_failed_calendar_is_not_reported_as_nothing_scheduled():
    data = sample()
    data['economic_calendar'] = {'error': 'ページ取得失敗: timeout', 'events': []}
    data['macro_surprises'] = []
    result = board.event_figure(data, NOW, 'daily')
    figure = result['figure']
    assert [i['label'] for i in figure['items']] == ['予定を確認できない（取得失敗）'] and figure['items'][0]['empty'] is True
    assert figure['cautions'] == [{'text': FAILED.format('ページ取得失敗: timeout'),
                                   'source_quote': '米指標｜注意: ' + FAILED.format('ページ取得失敗: timeout')}]
    assert not any('該当なし' in row for row in result['rows'] if not row.startswith('米指標表の読み方'))
    assert result['coverage'] == 'failed'
    assert FAILED.format('ページ取得失敗: timeout') in result['notes']


def test_a_partial_fetch_keeps_the_rows_and_says_what_could_not_be_checked():
    data = sample()
    data['economic_calendar']['missing_tabs'] = ['Next Week']
    result = board.event_figure(data, NOW, 'daily')
    assert result['coverage'] == 'incomplete' and len(result['figure']['items']) > 3
    assert result['figure']['cautions'][0]['text'].startswith('カレンダー取得が不完全で、一部の予定を確認できない')
    # Nothing in the window and a tab missing: the page cannot say 「該当なし」.
    data['economic_calendar']['events'] = [ev('Tuesday, October 13, 2026', '23:00', 'Existing Home Sales (Sep)')]
    data['macro_surprises'] = []
    items = board.event_figure(data, NOW, 'daily')['figure']['items']
    assert [i['label'] for i in items] == ['予定を確認できない（取得が不完全）']


def test_released_results_from_history_stay_in_the_table_when_the_calendar_failed():
    data = sample()
    data['economic_calendar'] = {'error': 'ページ取得失敗: timeout', 'events': []}   # macro_surprises were restored from the archive
    items = calendar_items(data)['items']
    assert [i['label'] for i in items] == ['新規失業保険申請'] and items[0]['actual'] == '231K' and not items[0].get('empty')
    assert calendar_items(data)['cautions'][0]['text'] == FAILED.format('ページ取得失敗: timeout')
    # No record of the calendar at all (the key is missing) with archived results: still shown, with the failure line.
    del data['economic_calendar']
    figure = calendar_items(data)
    assert [i['label'] for i in figure['items']] == ['新規失業保険申請'] and figure['cautions'][0]['text'] == FAILED.format('取得の記録なし')


def test_the_failure_line_is_in_the_section_the_appendix_and_the_limits_and_validates():
    data = sample()
    data['economic_calendar'] = {'error': 'ページ取得失敗: timeout', 'events': []}
    page, result, full, summary = page_of(data)
    line = FAILED.format('ページ取得失敗: timeout')
    section = page[page.index('id="calendar"'):page.index('<details class="howto"', page.index('id="calendar"'))]
    assert f'<p class="caution" role="note">{line}</p>' in section and section.index(line) < section.index('<table')
    assert f'米指標｜注意: {line}' in full and f'制約: {line}' in full
    assert result['missing'] == [] and [f['id'] for f in result['figures']] == ['bias', 'liquidity', 'calendar']
    assert '該当なし' not in section and 'class="ev-name"' in section      # the archived release is still listed


def test_the_failure_figure_is_rebuilt_exactly_at_validation(tmp_path):
    data = sample()
    data['economic_calendar'] = {'error': 'ページ取得失敗: timeout', 'events': []}
    del data['xau_levels']  # levels need their stored bars to be rebuilt (tests/test_bundle_contract.py)
    manifest, figures, source, summary = prepared(tmp_path, data, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(data, manifest, figures, source, summary)
    changed = deepcopy(figures)
    next(f for f in changed if f['id'] == 'calendar')['cautions'] = []
    with pytest.raises(bundle.BundleError, match='Figures differ'):
        bundle.check_regenerated(data, manifest, changed, source, summary)


def test_enrich_restores_archived_releases_when_the_calendar_failed(tmp_path, monkeypatch):
    path = tmp_path / 'f.jsonl'
    friday = {'date': 'Friday, October 9, 2026', 'time_jst': '21:30', 'country': 'United States',
              'indicator': 'Nonfarm Payrolls (Sep)', 'actual': 'N/A', 'forecast': '150K', 'previous': '196K'}
    assert ms.archive_forecasts([friday], datetime(2026, 10, 9, 18, 0, tzinfo=ms.JST), path) == 1
    assert ms.archive_actuals([{**friday, 'actual': '180K'}], datetime(2026, 10, 9, 22, 0, tzinfo=ms.JST), set(), path) == 1
    monkeypatch.setattr(main, 'load_forecast_history', lambda: ms.load_forecast_history(path))
    monkeypatch.setattr(main, 'load_actual_history', lambda: ms.load_actual_history(path))
    monkeypatch.setattr(main, 'load_release_history', lambda: ms.load_release_history(path))
    monkeypatch.setattr(main, 'append_snapshot', lambda snap: False)
    monkeypatch.setattr(main, 'load_history', lambda: [])
    for calendar in ({'error': 'ページ取得失敗: timeout', 'events': []}, {'error': None, 'events': []}):
        results = {'timestamp': '2026-10-12T09:00:00', 'economic_calendar': deepcopy(calendar)}
        main.enrich_offchart_inputs(results, now=datetime(2026, 10, 12, 9, 5, tzinfo=ms.JST),
                                    gvz_fetch=lambda sid: {'value': 24.0, 'as_of_date': '2026-10-09'},
                                    news_builder=lambda now, hours: {'candidate_count': 0, 'kept': [], 'sources': [],
                                                                     'selection': {'mode': 'skipped'}}, save=False)
        restored = results['macro_surprises']
        assert [(r['indicator'], r['actual'], r['forecast'], r['surprise']['diff']) for r in restored] == \
            [('Nonfarm Payrolls (Sep)', '180K', '150K', 30.0)]
        assert restored[0]['forecast_provenance'].startswith('発表前記録')
        assert results['economic_calendar']['events'] == []          # the page's own result is not rewritten
        kept = results['economic_calendar_restored']                   # kept apart, for the release-candle levels
        assert [e['indicator'] for e in kept['events']] == ['Nonfarm Payrolls (Sep)']
        assert (kept['since'], kept['source']) == ('2026-10-09T00:00:00+09:00', 'calendar_forecasts.jsonl')
        # The board shows the restored release (Monday morning looking back at Friday) with the failure line.
        data = {**sample(), 'macro_surprises': restored, 'economic_calendar': results['economic_calendar']}
        stamp = datetime(2026, 10, 12, 9, 5, tzinfo=ms.JST)
        figure = board.event_figure(data, stamp, 'daily')['figure']
        assert [(i['label'], i['actual']) for i in figure['items']] == [('雇用統計 非農業部門雇用者数（9月）', '180K')]
        assert figure['cautions']


# ------------------------------------------------------------------ F9: freshness of the liquidity map extras

def liquidity(data, mode='daily'):
    return board.liquidity_figure(data, NOW, mode, SOURCE)


def test_fresh_retail_average_and_gvz_band_are_shown_without_notes():
    result = liquidity(sample())
    assert [i['label'] for i in result['figure']['aux']] == ['個人ロングの平均建値', '個人ショートの平均建値']
    assert result['figure']['band'] and result['notes'] == []


@pytest.mark.parametrize('timestamp,reason', [
    ('2026-10-12T12:00:00Z', '取得 10/12 21:00 JST・24時間より前'),
    ('2026-10-13T15:00:00Z', '取得 10/14 00:00 JST が収集時刻より後'),
    (None, '取得時刻の記録なし')])
def test_stale_retail_average_is_left_out_with_a_reason(timestamp, reason):
    data = sample()
    data['retail_sentiment']['XAUUSD']['timestamp'] = timestamp
    result = liquidity(data)
    assert result['figure']['aux'] == []
    assert f'流動性マップ: 個人の平均建値を出していない（{reason}）' in result['notes']
    assert not any(b['label'].endswith('の平均建値') for b in result['bindings'])
    assert not any('平均建値' in row for row in result['rows'] if not row.startswith('流動性マップの読み方'))
    # The same stamp keeps the bias row out too (one rule), and the whole bundle still validates.
    assert '個人の比率' in str(board.bias_rows(data, NOW, 'daily', SOURCE)[2])
    out = bundle.observations(deepcopy(data), NOW, 'daily', SOURCE)
    bundle.check_bindings(data, out, out['figures'])
    assert f'流動性マップ: 個人の平均建値を出していない（{reason}）' in out['limitations']


@pytest.mark.parametrize('change,reason', [
    ({'gvz_as_of': '2026-10-07'}, 'GVZ 最新 10/07・5日より古い'),
    ({'gvz_as_of': None}, 'GVZ 観測日の記録なし'),
    ({'gvz_as_of': '2026-10-14'}, 'GVZ 観測日 10/14 が収集日より後'),
    ({'gvz_stale': True}, 'GVZ は提供元が古い値と表示'),
    ({'expected_move_1sd': None, 'gvz_error': 'ConnectionError'}, 'GVZ の取得に失敗（ConnectionError）'),
    ({'expected_move_1sd': None}, 'GVZ の値なし')])
def test_stale_or_missing_gvz_band_is_left_out_with_a_reason(change, reason):
    data = sample()
    data['liquidity_levels'] = {**data['liquidity_levels'], **change}
    result = liquidity(data)
    figure = result['figure']
    assert figure['band'] is None and not any('想定変動 ±1σ' in row for row in result['rows'])
    assert all(i['proximity'] is None for i in figure['items'] if i['kind'] == 'level')   # no bar against a stale scale
    assert f'流動性マップ: 想定変動の帯を出していない（{reason}）' in result['notes']
    page, built, full, _ = page_of(data)
    assert 'class="band-note"' not in page and f'制約: 流動性マップ: 想定変動の帯を出していない（{reason}）' in full


def test_gvz_age_rule_is_the_fred_series_rule_and_the_weekly_band_follows_it():
    assert board.GVZ_MAX_AGE_DAYS == fred.SERIES_CONFIG['GVZCLS']['stale_days']
    data = sample()
    data['liquidity_levels']['gvz_as_of'] = '2026-10-08'                        # exactly 5 days: still within the rule
    assert liquidity(data)['figure']['band'] and liquidity(data)['notes'] == []
    assert liquidity(data, 'weekly')['figure']['band']['label'].startswith('想定変動 ±1σ（1週')
    data['liquidity_levels']['gvz_as_of'] = '2026-10-07'
    assert liquidity(data, 'weekly')['figure']['band'] is None


def test_last_bar_is_labelled_as_the_bar_start_without_iso_text():
    legend = liquidity(sample())['figure']['legend']
    assert '計算に使った最終確定足は10/13 21:00 JST開始（Twelve Data 1時間足）' in legend
    assert not re.search(r'\d{4}-\d{2}-\d{2}T', legend)


# ------------------------------------------------------------------ F15: the retail split is drawn as given

def split_widths(left, right):
    svg = dash.viz_svg({'type': 'split', 'left': left, 'right': right, 'track': 50.0}, 'neutral')
    return [float(w) for w in re.findall(r'width="([\d.]+)" height="7" class="(?:long|short)"', svg)]


@pytest.mark.parametrize('left,right', [(50.3, 50.0), (49.8, 49.9), (56.0, 44.0), (100.0, 0.0), (0.4, 99.9)])
def test_retail_split_widths_follow_the_given_values(left, right):
    long_w, short_w = split_widths(left, right)
    full = 112 - 8
    assert long_w == pytest.approx(full * left / (left + right), abs=0.06)
    assert short_w == pytest.approx(full * right / (left + right), abs=0.06)
    assert long_w + short_w == pytest.approx(full, abs=0.1)        # no side is cut, nothing is left over
    assert (long_w > short_w) == (left > right)                       # the drawn order agrees with the printed one


def test_retail_split_for_a_ratio_within_tolerance_agrees_with_the_text():
    data = sample()
    data['retail_sentiment']['XAUUSD'].update(long_pct=50.3, short_pct=50.0)
    data['offchart_features']['positioning']['retail']['XAUUSD']['long_pct'] = 50.3
    row = next(i for i in board.bias_rows(data, NOW, 'daily', SOURCE)[0] if i['label'].startswith('個人のロング比率'))
    assert row['display'] == 'ロング 50.3%・ショート 50.0%' and (row['viz']['left'], row['viz']['right']) == (50.3, 50.0)
    long_w, short_w = split_widths(row['viz']['left'], row['viz']['right'])
    # 50.3 / 100.3 and 50.0 / 100.3 of the track (the old clamp drew 50.3 and 49.7, i.e. 52.3 and 51.7)
    assert (long_w, short_w) == (pytest.approx(104 * 50.3 / 100.3, abs=0.051), pytest.approx(104 * 50.0 / 100.3, abs=0.051))
    # A lone left value is still its share of 100%.
    assert 'width="0.0" height="7" class="short"' in dash.viz_svg({'type': 'split', 'left': 40.0, 'track': 50}, 'neutral')


# ------------------------------------------------------------------ F17: the retail average is folded away

def test_the_retail_average_is_inside_the_folded_details_only():
    page, result, full, summary = page_of(sample())
    liquidity_html = page[page.index('id="liquidity"'):page.index('id="calendar"')]
    details = liquidity_html[liquidity_html.index('<details'):]
    main_view = liquidity_html[:liquidity_html.index('<details')]
    assert '個人ロングの平均建値' not in main_view and '平均建値' not in main_view.replace('個人ロング', '')
    assert 'class="aux"' not in main_view and 'class="aux">参考（流動性の位置ではない）' in details
    assert '個人ロングの平均建値 4,412.64' in details and '個人ショートの平均建値 4,025.02' in details
    assert '水準の決め方と状態の意味・個人の平均建値（開いて確認）' in details
    assert '流動性｜個人ロングの平均建値: 4,412.64' in full                       # the Markdown keeps it
    assert '<details' in details and re.findall(r'<details[^>]*\bopen\b', page) == []
    # Without the average the details keep their plain title.
    data = sample()
    data['retail_sentiment']['XAUUSD'].update(avg_long_entry=None, avg_short_entry=None)
    page, *_ = page_of(data)
    assert '水準の決め方と状態の意味（開いて確認）' in page and '・個人の平均建値（開いて確認）' not in page
    assert 'class="aux"' not in page
