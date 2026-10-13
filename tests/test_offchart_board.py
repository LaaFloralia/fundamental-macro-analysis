"""Deterministic bias board and economic-event table (scripts/offchart_board.py)."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

from scripts import offchart_board as board
from scripts import report_bundle as bundle

NOW = datetime.fromisoformat('2026-10-13T18:01:41+09:00')
SKILL = Path('/Users/laa/.agents/skills/human-first-docs/scripts')


def fred(value, prev, ago, change_20, as_of='2026-10-08'):
    return {'source': 'FRED', 'value': value, 'prev_value': prev, 'change': round(value - prev, 6),
            'value_20obs_ago': ago, 'as_of_20obs_ago': '2026-09-10', 'change_20obs': change_20,
            'as_of_date': as_of, 'prev_as_of_date': '2026-10-07', 'stale': False, 'error': None}


def sample():
    return {
        'timestamp': NOW.isoformat(), 'report_quote': {'close': '4186.69', 'datetime': '2026-10-13'},
        'fred': {'DFII10': fred(2.87, 2.92, 2.55, 0.3200000000000003), 'DGS2': fred(4.75, 4.77, 4.56, 0.19),
                 'DTWEXBGS': fred(121.3848, 121.7882, 118.127, 3.2578, '2026-10-02')},
        'dxy': {'current_price': 102.31, 'prev_close': 102.59, 'change': -0.28, 'change_pct': -0.2729,
                'timestamp': '2026-10-13T09:03:42Z', 'error': None, 'stale': False},
        'fedwatch': {'next_fomc_date': 'Oct 28, 2026', 'error': None, 'stale': False,
                     'target_rates': [{'range': '3.75-4.00', 'current': 80.5, 'prev_day': 80.5, 'prev_week': 75.9},
                                      {'range': '4.00-4.25', 'current': 19.5, 'prev_day': 19.5, 'prev_week': 24.1}],
                     'deltas': {'prev_day': {'by_range': {'3.75-4.00': -1.1, '4.00-4.25': 1.1}, 'source': '履歴 4日前 (2026-10-09)'},
                                'prev_week': {'by_range': {'3.75-4.00': 3.5, '4.00-4.25': -3.5}, 'source': '履歴 8日前 (2026-10-05)'}}},
        'gold_etf': {'tonnes': 1055.41, 'as_of_date': '2026-10-12', 'change_5d_t': -0.86, 'change_20d_t': 7.99, 'error': None},
        'gold_cb': {'cumulative_3m_t': 154.2, 'regime': 'net_buying', 'as_of_date': '2026-M08', 'error': None},
        'retail_sentiment': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 56.0, 'short_pct': 44.0, 'avg_long_entry': 4412.6358,
                                        'avg_short_entry': 4025.0241, 'timestamp': '2026-10-13T09:03:42Z', 'as_of_date': None,
                                        'error': None, 'stale': False}},
        'cot_disaggregated': {'error': None, 'stale': False, 'data': {
            'date': '2026-10-06', 'open_interest': 396109,
            'managed_money': {'long': 127101, 'short': 16114, 'net': 110987, 'net_change': -9331, 'net_pct_oi': 28.0},
            'swap_dealers': {'long': 14604, 'short': 239630, 'net': -225026},
            'producer_merchant': {'long': 18860, 'short': 38361, 'net': -19501},
            'other_reportables': {'long': 118302, 'short': 18954, 'net': 99348}}},
        'offchart_features': {
            'positioning': {'retail': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 56.0, 'percentile': 54.4, 'history_n': 57}},
                            'cot_gold_mm': {'report_date': '2026-10-06', 'mm_net_pct_oi': 28.0, 'percentile': 43.9, 'history_n': 156}},
            'event_blackouts': [{'event': 'CPI (MoM) (Sep)', 'country': 'United States', 'time_jst': '2026-10-13T21:30:00+09:00',
                                 'window_start': '2026-10-13T21:00:00+09:00', 'window_end': '2026-10-13T22:30:00+09:00'}]},
        'macro_surprise_hours': 36,
        'macro_surprises': [
            {'date': 'Tuesday, October 13, 2026', 'time_jst': '15:00', 'country': 'Germany', 'indicator': 'German CPI (MoM) (Sep)',
             'actual': '0.6%', 'previous': '0.2%', 'forecast': '0.6%', 'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）',
             'released_at_jst': '2026-10-13T15:00:00+09:00', 'surprise': {'diff': 0.0, 'unit': 'pp', 'relative': 0.0, 'gold_direction': '判定対象外'}},
            {'date': 'Monday, October 12, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': 'Initial Jobless Claims',
             'actual': '231K', 'previous': '220K', 'forecast': '225K', 'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）',
             'released_at_jst': '2026-10-12T21:30:00+09:00', 'surprise': {'diff': 6.0, 'unit': 'K', 'relative': 0.0267, 'gold_direction': '金に上向き'}}],
        'economic_calendar': {'error': None, 'stale': False, 'events': [
            {'date': 'Monday, October 12, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': 'Initial Jobless Claims',
             'actual': '231K', 'previous': '220K', 'forecast': '225K'},
            {'date': 'Tuesday, October 13, 2026', 'time_jst': '15:00', 'country': 'Germany', 'indicator': 'German CPI (MoM) (Sep)',
             'actual': '0.6%', 'previous': '0.2%', 'forecast': '0.6%'},
            {'date': 'Tuesday, October 13, 2026', 'time_jst': '23:00', 'country': 'United States', 'indicator': 'Existing Home Sales  (Sep)',
             'actual': 'N/A', 'previous': '3.98M', 'forecast': '3.95M'},
            {'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': 'CPI (MoM) (Sep)',
             'actual': 'N/A', 'previous': '0.4%', 'forecast': '0.6%'},
            {'date': 'Wednesday, October 14, 2026', 'time_jst': '23:30', 'country': 'United States', 'indicator': 'Crude Oil Inventories',
             'actual': 'N/A', 'previous': '1.2M', 'forecast': '0.8M'},
            {'date': 'Thursday, October 15, 2026', 'time_jst': '15:00', 'country': 'United Kingdom', 'indicator': 'GDP (MoM) (Aug)',
             'actual': 'N/A', 'previous': '0.4%', 'forecast': '-0.1%'},
            {'date': 'Friday, October 16, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': 'Durable Goods Orders (MoM)',
             'actual': 'N/A', 'previous': 'N/A', 'forecast': '0.5%'}]}}


def by_label(items, prefix):
    return next(i for i in items if i['label'].startswith(prefix))


def test_board_rows_follow_the_documented_direction_rules():
    items, bindings, limitations = board.bias_rows(sample(), NOW, 'daily')
    rows = {i['label']: i for i in items}
    assert rows['実質金利（米10年TIPS）']['symbol'] == '▼' and rows['実質金利（米10年TIPS）']['change'] == '+0.32pp'
    assert rows['実質金利（米10年TIPS）']['baseline'] == '20営業日前 2.55%（2026-09-10）'
    assert rows['ドル（FRB広義ドル指数）']['symbol'] == '▼' and rows['ドル（FRB広義ドル指数）']['change'] == '+3.26（+2.76%）'
    assert rows['政策金利の織り込み（前週比）']['symbol'] == '△' and rows['政策金利の織り込み（前週比）']['change'] == '+3.5pp'
    assert rows['政策金利の織り込み（前日比）']['symbol'] == '▽' and '前日 81.6%' in rows['政策金利の織り込み（前日比）']['baseline']
    assert rows['GLD保有量（20営業日累計）']['symbol'] == '▲' and rows['GLD保有量（5営業日累計）']['symbol'] == '▽'
    assert rows['中銀の金購入（IMF報告国・確定3ヶ月累計）']['symbol'] == '▲'
    assert rows['米2年債利回り（前営業日比）']['symbol'] == '△' and rows['米2年債利回り（前営業日比）']['change'] == '-0.02pp'
    assert rows['ドル指数DXY（前日比）']['symbol'] == '△' and rows['ドル指数DXY（前日比）']['asof'] == '記録 2026-10-13 18:03 JST'
    assert rows['個人のロング比率']['direction'] == '極端値なし' and rows['個人のロング比率']['symbol'] == '—'
    assert rows['投機筋（COT Managed Money 純ロング）']['change'] == '前週比 -9,331枚'
    assert rows['投機筋（COT Managed Money 純ロング）']['baseline'] == '前週 120,318枚／156週の百分位 43.9'
    surprise = by_label(items, '指標サプライズ: Initial Jobless Claims')
    assert surprise['symbol'] == '▲' and surprise['change'] == '結果−予想 +6K'
    assert not any(i['label'].startswith('指標サプライズ: German') for i in items)  # non-US has no rule
    cpi = by_label(items, '予想 vs 前回: CPI (MoM) (Sep)')
    assert cpi['symbol'] == '▽' and cpi['direction'] == '予想どおりなら下向き' and cpi['change'] == '予想−前回 +0.2pp'
    assert not any('Durable Goods' in i['label'] for i in items)  # outside the 24h window
    assert [i['group'] for i in items][:5] == [board.LONG_GROUP] * 5
    assert limitations == []
    paths = {b['label']: b['path'] for b in bindings}
    assert paths['政策金利の織り込み（前日比）'] == 'fedwatch.target_rates.0.current'
    assert paths['個人のロング比率'] == 'offchart_features.positioning.retail.XAUUSD.percentile'
    for item in items:  # every visible field is covered by its quote
        for key, value in item.items():
            if key != 'source_quote' and isinstance(value, str):
                assert value in item['source_quote'] or key in ('tone', 'side', 'strength'), (key, value)


def test_tally_counts_only_shown_rows_and_tiles_quote_the_appendix():
    result = board.bias_figure(sample(), NOW, 'daily')
    verdicts = result['figure']['verdicts']
    assert verdicts[0]['direction'] == '中立（拮抗）' and verdicts[0]['detail'] == '▲2 △1 ▼2 ▽0'
    assert verdicts[1]['symbol'] in '▲△▼▽—' and verdicts[1]['source_quote'] in result['rows']
    assert verdicts[2]['direction'] == '記載なし'
    assert board.tally([{'group': 'g', 'symbol': '▲'}, {'group': 'g', 'symbol': '△'}], 'g')['word'] == '上向き（弱）'
    assert board.tally([{'group': 'g', 'symbol': '▲'}, {'group': 'g', 'symbol': '▲'}, {'group': 'g', 'symbol': '▽'}], 'g')['word'] == '上向き（弱）'
    assert board.tally([{'group': 'g', 'symbol': '▼'}, {'group': 'g', 'symbol': '▼'}], 'g')['symbol'] == '▼'
    assert board.tally([], 'g')['word'] == '中立（材料なし）'


def test_extreme_percentiles_vote_against_the_majority():
    data = sample()
    data['offchart_features']['positioning']['retail']['XAUUSD']['percentile'] = 92.0
    data['offchart_features']['positioning']['cot_gold_mm']['percentile'] = 8.0
    items, _, _ = board.bias_rows(data, NOW, 'daily')
    rows = {i['label']: i for i in items}
    assert rows['個人のロング比率']['symbol'] == '▼' and rows['投機筋（COT Managed Money 純ロング）']['symbol'] == '▲'
    data['offchart_features']['positioning']['retail']['XAUUSD']['percentile'] = None
    items, _, limitations = board.bias_rows(data, NOW, 'daily')
    assert {i['label']: i for i in items}['個人のロング比率']['symbol'] == '？' and limitations


@pytest.mark.parametrize('line,direction,detail', [
    ('時間軸別の向き: 長期 下向き（低）｜ 短期 上向き（中）｜ 総合 中立（低）', '中立（低）', '長期 下向き（低）／短期 上向き（中）'),
    ('方向付与: 可（上向き・中） ｜ イベント停止: なし', '上向き・中', '方向付与: 可（上向き・中）'),
    ('方向付与: 保留（理由: 材料混在） ｜ イベント停止: 10/13 22:30〜00:00 JST', '保留', '方向付与: 保留（理由: 材料混在）'),
    ('XAUUSD：中立・低。当日は保留。', '中立・低', '')])
def test_source_verdict_quotes_the_report_line(line, direction, detail):
    tile = board.source_verdict('# R\n\n## セクション0: エグゼクティブサマリー\n\n信頼度: Low\n\n' + line + '\n')
    assert tile['direction'] == direction and tile['detail'] == detail and tile['source_quote'] == line
    assert board.source_verdict('no verdict here') is None


def test_event_rows_show_previous_forecast_actual_difference_and_direction():
    figure = board.event_figure(sample(), NOW, 'daily')['figure']
    rows = {i['label']: i for i in figure['items']}
    claims = rows['Initial Jobless Claims']
    assert claims['status'] == '発表済み' and claims['actual'] == '231K' and claims['diff'] == '結果−予想 +6K（発表前記録の予想）'
    assert claims['symbol'] == '▲' and claims['direction'] == '上向き' and claims['window'] == 'NY KZ'
    assert rows['German CPI (MoM) (Sep)']['direction'] == '規則なし（米指標以外）'
    cpi = rows['CPI (MoM) (Sep)']
    assert cpi['status'] == '予定' and cpi['previous'] == '0.4%' and cpi['forecast'] == '0.6%' and cpi['actual'] == '—'
    assert cpi['diff'] == '予想−前回 +0.2pp' and cpi['symbol'] == '▽' and cpi['window'] == 'NY KZ・停止 21:00〜22:30'
    assert rows['Existing Home Sales (Sep)']['window'] == 'NY KZ・停止 22:30〜00:00'  # default window when no blackout entry
    assert rows['Crude Oil Inventories']['direction'] == '規則なし（この指標は対象外）'
    assert rows['GDP (MoM) (Aug)']['direction'] == '規則なし（米指標以外）' and rows['GDP (MoM) (Aug)']['window'].startswith('KZ外')
    assert rows['Durable Goods Orders (MoM)']['diff'] == '差: 前回なし'
    assert [i['importance'] for i in figure['items']] == ['高'] * len(figure['items'])
    assert [i['status'] for i in figure['items']][:2] == ['発表済み', '発表済み']
    for item in figure['items']:
        for key in ('date', 'time', 'country', 'label', 'previous', 'forecast', 'actual', 'diff', 'direction', 'window'):
            assert item[key] in item['source_quote'], key


def test_empty_calendar_still_yields_a_quoted_row():
    data = sample(); data['economic_calendar']['events'] = []
    figure = board.event_figure(data, NOW, 'daily')['figure']
    assert figure['items'][0]['label'] == '掲載なし' and '掲載なし' in figure['items'][0]['source_quote']


def test_weekly_mode_uses_week_windows():
    data = sample(); data['macro_surprise_hours'] = 168
    items, _, _ = board.bias_rows(data, NOW, 'weekly')
    labels = [i['label'] for i in items]
    assert '米2年債利回り（前営業日比）' not in labels and '政策金利の織り込み（前日比）' not in labels
    assert labels.count('政策金利の織り込み（前週比）') == 1
    assert {i['group'] for i in items if i['label'].startswith('政策金利')} == {board.LONG_GROUP}  # weekly has no shorter window
    assert any('Crude Oil Inventories' in label for label in labels)  # 7-day horizon (Durable Goods has no previous value)
    figure = board.event_figure(data, NOW, 'weekly')['figure']
    assert figure['title'].startswith('経済指標（直近7日')


def test_bundle_integrates_board_and_table_with_bindings_and_quotes():
    data = sample()
    source = '# ICT Daily Bias Report — 2026-10-13\n\n## セクション0: エグゼクティブサマリー\n\n信頼度: Low ｜ スコア 0\n\n時間軸別の向き: 長期 下向き（低）｜ 短期 中立（低）｜ 総合 中立（低）\n'
    result = bundle.observations(data, NOW, 'daily', source)
    assert [f['id'] for f in result['figures']][:3] == ['bias', 'price', 'calendar']
    assert result['figures'][0]['placement'] == 'band' and result['figures'][0]['verdicts'][2]['direction'] == '中立（低）'
    assert 'bias' not in ' '.join(result['missing']) and 'calendar' not in ' '.join(result['missing'])
    bundle.check_bindings(data, result, result['figures'])
    data['fred']['DFII10']['change_20obs'] = 0.33
    with pytest.raises(bundle.BundleError, match='does not match'):
        bundle.check_bindings(data, result, result['figures'])
    full = source + result['appendix']
    if SKILL.exists():
        sys.path.insert(0, str(SKILL))
        import visual_blocks
        checks = visual_blocks.validate(result['figures'], full)
        assert len(checks) >= sum(len(f['items']) for f in result['figures']) + 3


def test_missing_inputs_drop_rows_instead_of_inventing_them():
    data = {'timestamp': NOW.isoformat(), 'report_quote': {'close': '4186.69'}}
    assert board.bias_figure(data, NOW, 'daily') is None and board.event_figure(data, NOW, 'daily') is None
    result = bundle.observations(deepcopy(data), NOW)
    assert any(entry.startswith('bias') for entry in result['missing']) and any(entry.startswith('calendar') for entry in result['missing'])
