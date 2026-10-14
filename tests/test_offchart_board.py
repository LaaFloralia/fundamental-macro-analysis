"""Bias board, liquidity map and US event table (scripts/offchart_board.py)."""
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from scrapers import us_events as ue
from scripts import offchart_board as board
from scripts import report_bundle as bundle

NOW = datetime.fromisoformat('2026-10-13T22:14:00+09:00')
VERDICT_LINE = '時間軸別の向き: 長期 下向き・弱｜短期 上向き・弱｜総合 中立'
NEWS_LINE = 'ニュース・地政学: 下向き・弱｜利上げ観測とドル高の見出しが続き、地政学の新材料はなし。'
DRAW_LINE = '方向: 上 / 水準: 前日高値（10/12） / 価格: 4,216.21'
SOURCE = ('# ICT Daily Bias Report — 2026-10-13\n\n## セクション0: エグゼクティブサマリー\n\n信頼度: Low\n'
          f'{VERDICT_LINE}\nXAUUSD：長期は下向き・弱、短期は上向き・弱で総合は中立。\n\n'
          f'## セクション5: ニュース・地政学\n\n{NEWS_LINE}\n\n'
          f'## その日の目的地（Draw on Liquidity）\n\n{DRAW_LINE}\n')


def business_days(n, end):
    out, d = [], date.fromisoformat(end)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def series(first, last, end, n=21):
    step = (last - first) / (n - 1)
    return [[d, round(first + step * i, 4)] for i, d in enumerate(business_days(n, end))]


def ev(day, time, indicator, previous='N/A', forecast='N/A', actual='N/A', country='United States'):
    return {'date': day, 'time_jst': time, 'country': country, 'indicator': indicator,
            'previous': previous, 'forecast': forecast, 'actual': actual}


def level(key, name, side, price, state='resting', touched=None, session=None, note=''):
    words = {'resting': '未到達', 'swept': '掃引済み', 'beyond': '抜けて推移', 'broken': '抜けて定着', 'forming': '形成中'}
    return {'key': key, 'name': name, 'side': side, 'price': price, 'state': state, 'state_word': words[state],
            'touched_at': touched, 'touched_session': session, 'note': note}


def sample():
    return {
        'timestamp': NOW.isoformat(), 'mode': 'daily',
        'report_quote': {'close': '4177.47', 'datetime': '2026-10-13', 'fetched_at': '2026-10-13T13:14:30+00:00'},
        'fred': {'DFII10': {'value': 2.87, 'value_20obs_ago': 2.55, 'as_of_20obs_ago': '2026-09-10',
                            'change_20obs': 0.3200000000000003, 'as_of_date': '2026-10-08', 'error': None, 'stale': False,
                            'recent': series(2.55, 2.87, '2026-10-08')},
                 'DGS2': {'value': 4.75, 'as_of_date': '2026-10-08', 'error': None, 'stale': False,
                          'recent': series(4.56, 4.75, '2026-10-08')[:-2] + [['2026-10-07', 4.77], ['2026-10-08', 4.75]]},
                 'DTWEXBGS': {'value': 121.3848, 'value_20obs_ago': 118.127, 'as_of_20obs_ago': '2026-09-04',
                              'change_20obs': 3.2578, 'as_of_date': '2026-10-02', 'error': None, 'stale': False}},
        'correlation': {'pairs': [{'series_id': 'DFII10', 'r_20d': -0.53}]},
        'dxy': {'current_price': 102.28, 'prev_close': 102.59, 'change_pct': -0.3022, 'timestamp': '2026-10-13T13:16:13Z',
                'error': None, 'recent_closes': series(100.0, 102.0, '2026-10-13')},
        'us_yields': {'source': 'CNBC', 'error': None,
                      'US2Y': {'value': 4.802, 'prev_close': 4.791, 'change': 0.011, 'as_of_jst': '2026-10-13 22:11',
                               'stale': False}},
        'fedwatch': {'next_fomc_date': 'Oct 28, 2026', 'error': None, 'timestamp': '2026-10-13T22:10:00+09:00',
                     'target_rates': [{'range': '4.00-4.25', 'current': 19.5, 'prev_day': 19.5, 'prev_week': 24.1},
                                      {'range': '3.75-4.00', 'current': 80.5, 'prev_day': 80.5, 'prev_week': 75.9}],
                     'deltas': {'prev_day': {'by_range': {'3.75-4.00': -1.1}, 'meeting': 'Oct 28, 2026',
                                             'source': '履歴 4日前 (2026-10-09)'},
                                'prev_week': {'by_range': {'3.75-4.00': 3.5}, 'meeting': 'Oct 28, 2026',
                                              'source': '履歴 8日前 (2026-10-05)'}}},
        'gold_etf': {'tonnes': 1055.41, 'as_of_date': '2026-10-12', 'change_5d_t': -0.86, 'change_20d_t': 7.99, 'error': None},
        'gold_cb': {'cumulative_3m_t': 154.2, 'as_of_date': '2026-M08', 'error': None,
                    'cumulative_periods': ['2026-M08', '2026-M07', '2026-M06'],
                    'months': [{'period': '2026-M08', 'net_tonnes': 41.1}, {'period': '2026-M07', 'net_tonnes': 68.5},
                               {'period': '2026-M06', 'net_tonnes': 44.6}]},
        'retail_sentiment': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 56.0, 'short_pct': 44.0,
                                        'avg_long_entry': 4412.6358, 'avg_short_entry': 4025.0241,
                                        'timestamp': '2026-10-13T09:03:42Z', 'error': None, 'stale': False}},
        'cot_disaggregated': {'error': None, 'data': {'date': '2026-10-06', 'managed_money': {
            'net': 110987, 'net_change': -9331, 'long': 150000, 'short': 39013}}},
        'offchart_features': {'positioning': {'retail': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 56.0, 'percentile': 54.4,
                                                                    'history_n': 57, 'window': ['2026-09-26', '2026-10-13']}},
                                              'cot_gold_mm': {'percentile': 43.9, 'history_n': 156,
                                                              'report_date': '2026-10-06'}}},
        'inflation_nowcast': {'error': None, 'timestamp': '2026-10-13T13:12:00+00:00', 'months': {'2026-9': {'cpi': {'value': 0.531, 'as_of': '10/09'},
                                                                  'core_cpi': {'value': 0.202, 'as_of': '10/09'}}}},
        'macro_surprises': [
            {'date': 'Monday, October 12, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': 'Initial Jobless Claims',
             'actual': '231K', 'previous': '220K', 'forecast': '225K', 'released_at_jst': '2026-10-12T21:30:00+09:00',
             'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）'},
            {'date': 'Tuesday, October 13, 2026', 'time_jst': '15:00', 'country': 'Germany', 'indicator': 'German CPI (MoM) (Sep)',
             'actual': '0.6%', 'previous': '0.2%', 'forecast': '0.6%', 'released_at_jst': '2026-10-13T15:00:00+09:00',
             'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）'}],
        'economic_calendar': {'error': None, 'events': [
            ev('Friday, October 9, 2026', '23:00', 'Michigan 1-Year Inflation Expectations (Oct)', '4.7%', '4.6%', '4.8%'),
            ev('Monday, October 12, 2026', '21:30', 'Initial Jobless Claims', '220K', '230K', '231K'),
            ev('Tuesday, October 13, 2026', '15:00', 'German CPI (MoM) (Sep)', '0.2%', '0.6%', '0.6%', country='Germany'),
            ev('Tuesday, October 13, 2026', '23:00', 'Existing Home Sales (Sep)', '3.98M', '3.95M'),
            ev('Wednesday, October 14, 2026', '21:30', 'CPI (MoM) (Sep)', '0.4%', '0.6%'),
            ev('Wednesday, October 14, 2026', '21:30', 'Core CPI (MoM) (Sep)', '0.3%', '0.3%'),
            ev('Wednesday, October 14, 2026', '23:30', 'Crude Oil Inventories', '1.2M', '0.8M'),
            ev('Thursday, October 15, 2026', '21:30', 'Retail Sales (MoM) (Sep)', '0.6%', '0.4%'),
            ev('Friday, October 16, 2026', '21:30', 'Durable Goods Orders (MoM) (Sep)', '-0.2%', '0.5%'),
            ev('Tuesday, October 20, 2026', '21:30', 'Nonfarm Payrolls (Sep)', '143K', '150K'),
            ev('Thursday, October 29, 2026', '03:00', 'Fed Interest Rate Decision', '4.00%', '3.75%')],
            'tab_counts': {'This Week': 9, 'Next Week': 2}},  # the collector's per-tab counts: the range is known
        'xau_levels': {'price': 4177.47, 'last_bar_utc': '2026-10-13 12:00', 'midnight_open': 4186.99, 'error': None,
                       'levels': [level('PMH', '前月高値（9月）', 'high', 4514.11),
                                  level('PDH', '前日高値（10/12）', 'high', 4216.21),
                                  level('PWH', '前週高値（10/05週）', 'high', 4206.98, 'swept', '10/12 09:00', 'アジア'),
                                  level('NEWS_H', '新規失業保険申請 発表15分足高値（10/12 21:30）', 'high', 4195.5, note='Tier B'),
                                  level('LONDON_L', 'London安値（当日）', 'low', 4180.0, 'beyond', '10/13 21:00', 'NY'),
                                  level('NEWS_L', '新規失業保険申請 発表15分足安値（10/12 21:30）', 'low', 4168.2, 'swept',
                                        '10/13 03:00', 'NY', 'Tier B'),
                                  level('PDL', '前日安値（10/12）', 'low', 4156.45, 'swept', '10/13 09:00', 'アジア'),
                                  level('PWL', '前週安値（10/05週）', 'low', 4061.73)]},
        'liquidity_levels': {'price': 4177.47, 'expected_move_1sd': 60.0, 'gvz': 22.79, 'gvz_as_of': '2026-10-09'},
    }


# MyFXBook Order Book clusters fetched two minutes before NOW (the collector's schema: myfxbook_open_orders).
ORDER_BOOK = {'source': 'MyFXBook (Order Book Positions)', 'symbol': 'XAUUSD', 'current_price': 4177.0, 'error': None,
              'timestamp': '2026-10-13T13:12:00Z', 'stale': False,
              'bsl_candidates': [{'low': 4190.0, 'high': 4201.5, 'volume_sum': 35, 'entries': 5, 'share_pct': 70.0},
                                 {'low': 4429.0, 'high': 4434.56, 'volume_sum': 15, 'entries': 2, 'share_pct': 30.0}],
              'ssl_candidates': [{'low': 4104.0, 'high': 4170.0, 'volume_sum': 279, 'entries': 20, 'share_pct': 100.0}]}


def with_order_book(data, **change):
    data['myfxbook_open_orders'] = {'XAUUSD': {**deepcopy(ORDER_BOOK), **change}}
    return data


def rows_of(data, mode='daily', source=SOURCE):
    return {i['label']: i for i in board.bias_rows(data, NOW, mode, source)[0]}


def shown_fields_are_quoted(item, keys):
    for key in keys:
        value = item.get(key)
        if value not in (None, ''):
            assert str(value) in item['source_quote'], (key, value)


# ------------------------------------------------------------------ bias board

def test_daily_rows_compare_each_input_with_its_baseline_and_map_it_to_gold():
    rows = rows_of(sample())
    real = rows['実質金利（米10年TIPS）']
    assert (real['symbol'], real['display'], real['baseline'], real['change'], real['asof']) == \
        ('▼', '2.87%', '20営業日前 2.55%（09/10）', '+0.32pp', '10/08')
    assert real['note'] == '金との20日相関 -0.53（通常どおり逆方向）' and real['viz']['type'] == 'spark'
    assert len(real['viz']['values']) == 21 and real['viz']['path'] == 'fred.DFII10.recent'  # the 20-day window only
    dxy = rows['ドル指数DXY（20営業日）']
    assert (dxy['symbol'], dxy['change'], dxy['baseline']) == ('▼', '+2.00%', '20営業日前 100.000（09/15）')
    news = rows['ニュース・地政学（原稿の判断）']
    assert news['kind'] == 'text' and news['symbol'] == '▽' and news['direction'] == '下向き・弱'
    assert news['display'] == '利上げ観測とドル高の見出しが続き、地政学の新材料はなし。'
    cb = rows['中銀の金購入（IMF報告国・3ヶ月）']
    assert (cb['symbol'], cb['display'], cb['asof']) == ('▲', '+154.2 t（6〜8月）', '2026年08月分')
    assert cb['note'] == '月別 6月 +44.6 t・7月 +68.5 t・8月 +41.1 t' and 'viz' not in cb  # the numbers say it all
    us2y = rows['米2年債利回り（当日）']
    assert (us2y['symbol'], us2y['display'], us2y['change'], us2y['asof']) == ('▽', '4.802%', '+0.011pp', '10/13 22:11 JST')
    assert 'viz' not in us2y  # two points: the text carries both ends
    day = rows['ドル指数DXY（当日）']
    assert (day['symbol'], day['change'], day['asof']) == ('▲', '-0.30%', '記録 10/13 22:16 JST')  # -0.3022% reaches 0.3%
    fed = rows['政策金利の織り込み（前日比）']
    assert (fed['symbol'], fed['direction'], fed['display'], fed['baseline'], fed['change'], fed['asof']) == \
        ('—', '中立', '期待 3.924%', '3.924%（提供表の前日列）', '+0.0bp', '次回FOMC 10/28')
    assert fed['note'] == '確率 3.75-4.00 80.5%・4.00-4.25 19.5%。前日列の比較日時は提供元の表示になく未確認'
    assert 'viz' not in fed  # the distribution is in the note
    claims = rows['指標の結果: 新規失業保険申請']
    assert (claims['symbol'], claims['display'], claims['baseline'], claims['change']) == \
        ('△', '結果 231K', '予想 225K（発表前記録）', '+6K')  # one series alone is weak
    cpi = rows['予測との差: CPI（前月比・9月）']
    assert (cpi['symbol'], cpi['display'], cpi['baseline'], cpi['change'], cpi['asof']) == \
        ('△', 'nowcast 0.53%', '市場予想 0.6%', '-0.07pp', '10/14 21:30 JST 発表・nowcast 10/09時点')
    assert rows['予測との差: コアCPI（前月比・9月）']['change'] == '-0.10pp'
    etf = rows['GLD保有量（5営業日）']
    assert (etf['symbol'], etf['baseline'], etf['note']) == ('▽', '5営業日前 1,056.27 t', '20営業日 +7.99 t')
    assert 'viz' not in etf
    cot = rows['投機筋の買い越し（COT Managed Money）']
    assert (cot['symbol'], cot['direction'], cot['display'], cot['change']) == ('—', '中立（偏りなし）', '110,987枚', '前週比 -9,331枚')
    assert cot['baseline'] == '156週百分位 43.9'
    data = sample()
    data['offchart_features']['positioning']['cot_gold_mm'].update(mm_net_pct_oi=21.04, window=['2023-10-10', '2026-10-06'])
    assert rows_of(data)['投機筋の買い越し（COT Managed Money）']['baseline'] == '建玉比 21.0%の156週百分位 43.9（2023/10/10〜2026/10/06）'
    retail = rows['個人のロング比率（MyFXBook）']
    assert (retail['symbol'], retail['display'], retail['asof']) == ('—', 'ロング 56.0%・ショート 44.0%', '記録 10/13 18:03 JST')
    assert 'viz' not in retail
    assert {i['material']: i['factor'] for i in rows.values()}['個人の比率'] == '建玉の偏り'
    assert not any('German' in label or 'Durable' in label for label in rows)  # non-US, and outside the 2-day window
    for item in rows.values():
        assert item['symbol'] != '？' and '保留' not in item['direction']
        shown_fields_are_quoted(item, ('label', 'display', 'baseline', 'change', 'direction', 'symbol', 'asof', 'note'))
        assert (item.get('viz') or {}).get('type') in (None, 'spark')  # trend lines of several observations only
    assert board.bias_rows(sample(), NOW, 'daily', SOURCE)[2] == []  # nothing missing


def test_the_two_ends_of_a_change_are_printed_at_the_changes_precision():
    """4.8496 − 4.800 is +0.0496pp (weak): the ends must not read 4.850 − 4.800 (the strong line)."""
    data = sample()
    data['us_yields']['US2Y'].update(value=4.8496, prev_close=4.8, change=0.0496)
    row = rows_of(data)['米2年債利回り（当日）']
    assert (row['display'], row['baseline'], row['change'], row['symbol']) == \
        ('4.8496%', '前日終値 4.8000%', '+0.0496pp', '▽')
    data = sample()
    data['us_yields']['US2Y'].update(value=4.837, prev_close=4.791, change=0.046)
    assert rows_of(data)['米2年債利回り（当日）']['display'] == '4.837%'  # three decimals stay three


def test_verdicts_use_unrounded_values_and_the_shown_value_agrees():
    assert board.shown(0.046, 2, 0.05) == (0.046, 3)      # 0.05 when rounded, but under the line
    assert board.shown(0.05 - 1e-12, 2, 0.05) == (0.05, 2)  # binary noise only: at the line
    assert board.shown(0.0499999, 2, 0.05) == (0.0499999, 7)  # a real shortfall keeps its digits
    assert board.shown(4.837 - 4.791, 2, 0.05) == (0.046, 3)
    assert board.shown(0.004, 2, 0.05) == (0.004, 3)      # not shown as zero
    assert board.shown(89.96, 1, 90, below=(10,)) == (89.96, 2) and board.shown(10.04, 1, 90, below=(10,)) == (10.04, 2)
    assert board.judge(0.046, 0.05, -1) == ('down', 'weak') and board.judge(0.05, 0.05, -1) == ('down', 'strong')
    assert board.judge(0.15 + 0.15 - 0.3, 0.05, -1) == ('flat', 'none')  # 5.6e-17: noise
    assert board.judge(0.0000001, 0.05, -1) == ('down', 'weak')         # a real, tiny move
    data = sample()
    data['us_yields']['US2Y'].update(change=0.046, value=4.837)
    us2y = rows_of(data)['米2年債利回り（当日）']
    assert (us2y['symbol'], us2y['change']) == ('▽', '+0.046pp')
    data['offchart_features']['positioning']['retail']['XAUUSD']['percentile'] = 89.96
    retail = rows_of(data)['個人のロング比率（MyFXBook）']
    assert (retail['symbol'], retail['baseline']) == ('—', '自前履歴57件（09/26〜10/13）の百分位 89.96')
    del data['offchart_features']['positioning']['retail']['XAUUSD']['window']
    assert rows_of(data)['個人のロング比率（MyFXBook）']['baseline'] == '自前履歴57件（期間未確認）の百分位 89.96'


def test_a_change_and_its_two_ends_share_one_precision():
    # The ends printed at the end digits differ by exactly the printed change.
    assert board.shown_ends(0.12, 1.25, 1.13, 2, 0.10) == (0.12, 2, 2)
    assert board.shown_ends(1.234 - 1.126, 1.234, 1.126, 2, 0.10) == (0.108, 3, 3)  # 1.23−1.13 would give 0.10
    # % ends and a bp change (×100): the ends keep two more digits than the change.
    assert board.shown_ends((3.6246 - 3.6012) * 100, 3.6246, 3.6012, 3, 2.5, scale=100) == (2.34, 2, 4)
    # A source whose change is not the difference of its ends keeps the shortest change that agrees.
    assert board.shown_ends(0.5, 1.0, 1.0, 2, 0.10) == (0.5, 2, 2)
    data = sample()
    data['fred']['DFII10'].update(value=1.234, value_20obs_ago=1.126, change_20obs=1.234 - 1.126)
    real = rows_of(data)['実質金利（米10年TIPS）']
    assert (real['display'], real['change']) == ('1.234%', '+0.108pp')
    assert real['baseline'].startswith('20営業日前 1.126%')


def test_missing_materials_are_listed_and_an_empty_group_is_not_neutral():
    data = sample()
    del data['fred']['DFII10'], data['gold_cb']
    notes = board.bias_rows(data, NOW, 'daily', SOURCE)[2]
    assert notes == ['欠測: 実質金利（FRED DFII10・取得失敗）。長期（数週間〜）の集計に入れていない',
                     '欠測: 中銀の金購入（IMF IRFCL・取得失敗）。長期（数週間〜）の集計に入れていない']
    tiles = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', SOURCE)['figure']['verdicts']}
    assert tiles['長期']['detail'] == '欠測: 実質金利・中銀の金購入'  # the verdict follows the tally
    assert tiles['長期']['show_detail'] is True and tiles['短期']['show_detail'] is False
    empty = {'timestamp': NOW.isoformat(), 'fedwatch': sample()['fedwatch']}
    tiles = {t['label']: t for t in board.bias_figure(empty, NOW, 'daily', '# R\n')['figure']['verdicts']}
    assert (tiles['長期']['direction'], tiles['長期']['symbol'], tiles['長期']['show_detail']) == ('材料なし', '—', True)


@pytest.mark.parametrize('summary', ['未評価（見出しのみで本文を開けない）',
                                     '提供された新着見出し・X抜粋は本文未確認で、方向票に採用できる追加ニュースはない。',
                                     '追加の政策・地政学ニュースは見出しと抜粋の範囲にとどまり、確認済みの方向材料を加えない。'])
def test_an_unread_news_line_is_not_a_material(summary):
    """A neutral news line the report could not read is 判定外: no vote, and the horizon is not covered by it."""
    source = SOURCE.replace(NEWS_LINE, f'ニュース・地政学: 中立｜{summary}')
    data = sample()
    items, _, notes = board.bias_rows(data, NOW, 'daily', source)
    assert 'ニュース・地政学' not in {i['material'] for i in items}
    assert notes == ['判定外: ニュース・地政学（原稿の「ニュース・地政学:」行・原稿が本文未確認で中立とした）。'
                     '長期（数週間〜）の集計に入れていない']
    del data['fred']['DFII10']
    tiles = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', source)['figure']['verdicts']}
    assert tiles['長期']['detail'].endswith('欠測: 実質金利。判定外: ニュース・地政学')
    # Without any other long material the horizon has none (the unread line does not count).
    for key in ('dxy', 'gold_cb'):
        data.pop(key)
    data['fred'].pop('DTWEXBGS', None)
    tiles = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', source)['figure']['verdicts']}
    assert tiles['長期']['direction'] == '材料なし' or '材料なし' in tiles['長期']['detail']
    # An evaluated quiet day stays a neutral material.
    quiet = SOURCE.replace(NEWS_LINE, 'ニュース・地政学: 中立｜特記すべきニュースなし')
    assert 'ニュース・地政学' in {i['material'] for i in board.bias_rows(sample(), NOW, 'daily', quiet)[0]}


@pytest.mark.parametrize('change,why', [
    (lambda d: d['fred']['DFII10'].update(as_of_date='2026-10-02'), '実質金利（FRED DFII10・最新 10/02・7日より古い）'),
    (lambda d: d['fred']['DFII10'].update(as_of_date=None), '実質金利（FRED DFII10・観測日の記録なし）'),
    (lambda d: d['gold_cb'].update(as_of_date='2026-M05'), '中銀の金購入（IMF IRFCL・最新 5月分・4ヶ月より古い）'),
    (lambda d: d['gold_etf'].update(as_of_date='2026-10-02'), 'GLD保有量（SPDR・最新 10/02・7日より古い）'),
    (lambda d: d['fedwatch'].pop('timestamp'), '政策金利の織り込み（Fed Rate Monitor・取得時刻の記録なし）'),
    (lambda d: d['fedwatch'].update(timestamp='2026-10-12T18:00:00+09:00'),
     '政策金利の織り込み（Fed Rate Monitor・取得 10/12 18:00 JST・24時間より前）'),
    (lambda d: d['fedwatch'].update(next_fomc_date=''), '政策金利の織り込み（Fed Rate Monitor・次回FOMCの日付なし）'),
    (lambda d: d['dxy'].update(timestamp='2026-10-12T09:00:00Z'), 'ドル（短期）（DXY・取得 10/12 18:00 JST・24時間より前）'),
    (lambda d: d['dxy'].update(current_price=44.782, prev_close=44.779, change_pct=0.007, estimated=True),
     'ドル（短期）（DXY・EUR/USDからの推定値のみ（DXYの当日値なし））'),
    (lambda d: d['retail_sentiment']['XAUUSD'].update(short_pct=40.0), '個人の比率（MyFXBook・ロングとショートの合計が100%でない）'),
    (lambda d: d['retail_sentiment']['XAUUSD'].update(timestamp='2026-10-11T09:00:00Z'),
     '個人の比率（MyFXBook・取得 10/11 18:00 JST・24時間より前）'),
    # The fallback source is named; a ratio without enough own history is not "no value".
    # The ratio or position was obtained but has no percentile yet: 判定外, with the value, not 欠測.
    (lambda d: d['retail_sentiment']['XAUUSD'].update(source='FXSSI') or
     d['offchart_features']['positioning']['retail']['XAUUSD'].update(source='FXSSI', percentile=None, history_n=12),
     '判定外: 個人の比率（FXSSI・ロング56.0%は取得・同じ取得元の自前履歴12件で百分位なし（20件・5種以上で判定））'),
    (lambda d: d['offchart_features']['positioning']['cot_gold_mm'].update(percentile=None, history_n=8),
     '判定外: 投機筋の建玉（CFTC COT・買い越し110,987枚は取得・自前履歴8週で百分位なし（20週以上で判定））'),
    # A percentile computed for another source or another reading is not this ratio's.
    (lambda d: d['offchart_features']['positioning']['retail']['XAUUSD'].update(source='FXSSI'),
     '欠測: 個人の比率（MyFXBook・百分位が現在の比率と別の観測）'),
    (lambda d: d['offchart_features']['positioning']['retail']['XAUUSD'].update(long_pct=57.0),
     '欠測: 個人の比率（MyFXBook・百分位が現在の比率と別の観測）'),
    # A stale ratio is missing even when its percentile is also missing.
    (lambda d: d['retail_sentiment']['XAUUSD'].update(timestamp='2026-10-11T09:00:00Z') or
     d['offchart_features']['positioning']['retail']['XAUUSD'].update(percentile=None),
     '欠測: 個人の比率（MyFXBook・取得 10/11 18:00 JST・24時間より前）'),
    (lambda d: d['cot_disaggregated']['data']['managed_money'].update(short=40000), '投機筋の建玉（CFTC COT・買い−売り＝差引が合わない）'),
    (lambda d: d['offchart_features']['positioning']['cot_gold_mm'].update(report_date='2026-09-29'),
     '投機筋の建玉（CFTC COT・百分位と建玉の報告日が違う）'),
    (lambda d: d['cot_disaggregated']['data'].update(date='2026-09-22') or
     d['offchart_features']['positioning']['cot_gold_mm'].update(report_date='2026-09-22'),
     '投機筋の建玉（CFTC COT・最新 09/22・14日より古い）')])
def test_old_or_inconsistent_materials_are_left_out_with_the_reason(change, why):
    data = sample()
    change(data)
    notes = board.bias_rows(data, NOW, 'daily', SOURCE)[2]
    assert [n for n in notes if why in n], notes
    assert len(notes) == 1


def test_tally_and_verdict_tiles_quote_the_report_line():
    result = board.bias_figure(sample(), NOW, 'daily', SOURCE)
    tiles = {t['label']: t for t in result['figure']['verdicts']}
    assert (tiles['長期']['symbol'], tiles['長期']['direction'], tiles['長期']['origin']) == ('▽', '下向き・弱', '原稿の判断')
    assert tiles['長期']['detail'] == '規則の集計: 下向き（弱）（実質金利 ▼・ドル ▼・ニュース・地政学 ▽・中銀 ▲）'
    assert (tiles['長期']['short'], tiles['長期']['group']) == ('規則の集計 下向き（弱）・要因4', '長期（数週間〜）')
    # 米2年債 ▽, 織り込み —, 失業保険 ▲ are one factor that disagrees: no vote. Two nowcasts are one half vote.
    assert (tiles['短期']['symbol'], tiles['短期']['detail']) == \
        ('△', '規則の集計: 上向き（弱）（金利・織り込み —・ドル ▲・発表前の予測 △・ETF ▽・建玉の偏り —）')
    assert (tiles['総合']['symbol'], tiles['総合']['direction'], tiles['総合']['detail']) == ('—', '中立', '長期×短期の規則: 中立')
    assert not any(t['mismatch'] or t['show_detail'] for t in tiles.values()) and 'reason' not in result['figure']
    for tile in tiles.values():
        assert tile['source_quote'] in result['rows']
        shown_fields_are_quoted(tile, ('label', 'symbol', 'direction', 'origin', 'detail', 'short'))


def test_a_verdict_that_differs_from_the_tally_is_marked_with_the_reports_reason():
    source = SOURCE.replace(VERDICT_LINE, '時間軸別の向き: 長期 上向き・弱｜短期 上向き・弱｜総合 上向き・弱\n'
                                          '集計と違う理由: 中銀の買いを実質金利より重く見た。')
    assert board.verdict_mismatches(sample(), NOW, 'daily', source) == ['長期', '総合']
    result = board.bias_figure(sample(), NOW, 'daily', source)
    tiles = {t['label']: t for t in result['figure']['verdicts']}
    assert (tiles['長期']['origin'], tiles['長期']['mismatch'], tiles['長期']['show_detail']) == ('原稿の判断・集計と異なる', True, True)
    assert tiles['短期']['mismatch'] is False and tiles['短期']['show_detail'] is False
    assert result['figure']['reason'] == {'text': '集計と違う理由: 中銀の買いを実質金利より重く見た。',
                                          'source_quote': '集計と違う理由: 中銀の買いを実質金利より重く見た。'}
    assert board.verdict_mismatches(sample(), NOW, 'daily', SOURCE) == []
    assert result['rows'][-1].startswith('バイアス盤の読み方: ') and result['figure']['legend'] in result['rows'][-1]
    assert {r['label'] for r in result['figure']['rules']} == {i['label'] for i in result['figure']['items']}


def test_tiles_fall_back_to_the_rules_when_the_report_has_no_verdict_line():
    tiles = {t['label']: t for t in board.bias_figure(sample(), NOW, 'daily', '# R\n\n本文のみ。\n')['figure']['verdicts']}
    assert (tiles['長期']['direction'], tiles['長期']['origin']) == ('下向き（弱）', '規則集計')
    assert tiles['長期']['detail'] == '欠測: ニュース・地政学' and tiles['長期']['show_detail'] is True
    assert not any(t['mismatch'] for t in tiles.values())
    assert (tiles['短期']['direction'], tiles['総合']['direction']) == ('上向き（弱）', '中立')
    assert tiles['総合']['origin'] == '長期×短期の規則'


def test_tally_counts_one_vote_per_factor():
    def t(*rows):
        return board.tally([{'group': 'g', 'symbol': s, 'factor': f, 'label': f} for f, s in rows], 'g')
    assert (t(('実質金利', '▲'), ('中銀', '△'))['word'], t(('実質金利', '▲'), ('中銀', '△'))['symbol']) == ('上向き（弱）', '△')
    assert t(('a', '▲'), ('b', '▲'), ('c', '▽'))['word'] == '上向き（弱）'
    assert (t(('a', '▼'), ('b', '▼'))['symbol'], t(('a', '▼'), ('b', '▼'))['word']) == ('▼', '下向き')
    assert t(('a', '▲'), ('b', '▽'))['word'] == '中立'  # a difference under one vote is neutral
    # Rows of one factor: the larger vote when they agree, none when they disagree.
    agree = t(('金利・織り込み', '▲'), ('金利・織り込み', '△'), ('中銀', '▲'))
    assert (agree['word'], agree['detail']) == ('上向き', '金利・織り込み ▲・中銀 ▲')
    split = t(('金利・織り込み', '▲'), ('金利・織り込み', '▽'), ('中銀', '▲'))
    assert (split['word'], split['detail']) == ('上向き（弱）', '金利・織り込み —・中銀 ▲')
    assert t(('金利・織り込み', '▲'), ('金利・織り込み', '—'))['detail'] == '金利・織り込み ▲'  # neutral rows do not cancel
    # Positioning and pre-release forecasts are capped at half a vote.
    capped = t(('建玉の偏り', '▼'), ('発表前の予測', '▼'))
    assert (capped['word'], capped['detail']) == ('下向き（弱）', '建玉の偏り ▽・発表前の予測 ▽')
    assert t(('建玉の偏り', '▲'))['word'] == '中立'
    assert (t()['word'], t()['detail'], t()['symbol']) == ('材料なし', '材料なし', '—')


@pytest.mark.parametrize('long_t,short_t,expected', [
    (('up', 'strong'), ('up', 'strong'), ('up', 'strong')),
    (('up', 'strong'), ('up', 'weak'), ('up', 'weak')),
    (('flat', 'none'), ('down', 'strong'), ('down', 'weak')),
    (('up', 'weak'), ('flat', 'none'), ('up', 'weak')),
    (('up', 'strong'), ('down', 'strong'), ('flat', 'none'))])
def test_blend_combines_long_and_short(long_t, short_t, expected):
    side = lambda pair: {'side': pair[0], 'strength': pair[1]}
    assert board.blend(side(long_t), side(short_t)) == expected


@pytest.mark.parametrize('line,expected', [
    (VERDICT_LINE, {'長期': ('down', 'weak', '下向き・弱'), '短期': ('up', 'weak', '上向き・弱'), '総合': ('flat', 'none', '中立')}),
    ('時間軸別の向き：長期 上向き ｜ 短期: 下向き・弱 ｜ 総合 中立',  # a bare 上向き is not one of the five words
     {'短期': ('down', 'weak', '下向き・弱'), '総合': ('flat', 'none', '中立')}),
    ('時間軸別の向き: 長期 保留｜短期 中立ではない｜総合 中立', {'総合': ('flat', 'none', '中立')}),
    ('時間軸別の向き: 長期 上向き・強（実質金利）｜短期 中立｜総合 中立', {'短期': ('flat', 'none', '中立'), '総合': ('flat', 'none', '中立')}),
    ('信頼度: Low', {})])
def test_source_verdicts_read_only_the_five_verdict_words(line, expected):
    assert board.source_verdicts('# R\n\n' + line + '\n') == expected


def test_repeated_verdict_news_or_reason_lines_are_errors():
    with pytest.raises(ValueError, match='repeats'):
        board.source_verdicts(f'# R\n\n{VERDICT_LINE}\n\n{VERDICT_LINE}\n')
    with pytest.raises(ValueError, match='repeats 長期'):
        board.source_verdicts('時間軸別の向き: 長期 中立｜長期 上向き・強｜総合 中立\n')
    with pytest.raises(ValueError, match='repeats'):
        board.news_verdict(f'{NEWS_LINE}\n{NEWS_LINE}\n')
    with pytest.raises(ValueError, match='repeats'):
        board.reason_line('集計と違う理由: a\n集計と違う理由: b\n')


def test_news_verdict_needs_a_verdict_word_and_a_summary():
    assert board.news_verdict(NEWS_LINE)['side'] == 'down' and board.news_verdict(NEWS_LINE)['word'] == '下向き・弱'
    assert board.news_verdict('ニュース・地政学: 保留｜材料混在') is None
    assert board.news_verdict('ニュース・地政学: 上向き・強') is None
    assert board.news_verdict('ニュース・地政学: 上向き｜材料') is None  # exactly one of the five words
    assert '（原稿の判断）' not in ' '.join(rows_of(sample(), source='# R\n'))  # no line, no news row


def test_stale_cnbc_quote_falls_back_to_fred_with_its_own_date():
    data = sample()
    data['us_yields']['US2Y']['stale'] = True
    rows = rows_of(data)
    assert '米2年債利回り（当日）' not in rows
    fred = rows['米2年債利回り（前営業日比）']
    assert (fred['symbol'], fred['baseline'], fred['change'], fred['asof']) == ('△', '10/07 4.77%', '-0.02pp', '10/08')
    data['us_yields'] = {'error': '取得不可（Timeout）'}
    assert '米2年債利回り（前営業日比）' in rows_of(data)


def test_cnbc_change_that_contradicts_its_ends_is_not_drawn():
    data = sample()
    data['us_yields']['US2Y'].update(value=4.800, prev_close=4.790, change=-0.010)
    items, _, notes = board.bias_rows(data, NOW, 'daily', SOURCE)
    labels = [i['label'] for i in items]
    assert '米2年債利回り（当日）' not in labels and '米2年債利回り（前営業日比）' in labels  # FRED instead
    assert not any('米2年債' in n for n in notes)  # FRED supplied the row: nothing missing
    del data['fred']['DGS2']
    items, _, notes = board.bias_rows(data, NOW, 'daily', SOURCE)
    assert not any(i['material'] == '米2年債' for i in items)
    assert any('前日比が当日値と前日終値の差と合わない' in n for n in notes if '米2年債' in n)
    data = sample()
    data['us_yields']['US2Y'].update(value=4.800, prev_close=4.790, change=0.010)  # consistent
    assert rows_of(data)['米2年債利回り（当日）']['change'] == '+0.010pp'


@pytest.mark.parametrize('value,prev_close,change', [
    (4.800, 4.799, -0.0004),   # within 0.0015 but the opposite sign
    (4.800, 4.800, 0.001),     # flat ends, a nonzero change
    (4.800, 4.799, 0.0),       # a zero change, ends apart
    (4.8505, 4.800, 0.0495)])  # within 0.0015 but across the 0.05 line (strong vs weak)
def test_cnbc_change_in_another_verdict_class_than_its_ends_is_not_drawn(value, prev_close, change):
    data = sample()
    data['us_yields']['US2Y'].update(value=value, prev_close=prev_close, change=change)
    rows = rows_of(data)
    assert '米2年債利回り（当日）' not in rows and '米2年債利回り（前営業日比）' in rows
    del data['fred']['DGS2']
    notes = board.bias_rows(data, NOW, 'daily', SOURCE)[2]
    assert any('CNBC 前日比が当日値と前日終値の差と合わない' in n for n in notes if '米2年債' in n)


def test_dollar_falls_back_to_the_broad_index_without_dxy_history():
    data = sample()
    del data['dxy']['recent_closes']
    broad = rows_of(data)['ドル（FRB広義ドル指数・20営業日）']
    assert (broad['symbol'], broad['change'], broad['baseline']) == ('▼', '+2.76%', '20営業日前 118.13（09/04）')


def test_fedwatch_judges_the_probability_weighted_rate():
    # 10/80/10 -> 20/0/80 moves the expected rate up although the lowest range gains: a headwind.
    data = sample()
    data['fedwatch']['target_rates'] = [
        {'range': '3.50-3.75', 'current': 20.0, 'prev_day': 10.0}, {'range': '3.75-4.00', 'current': 0.0, 'prev_day': 80.0},
        {'range': '4.00-4.25', 'current': 80.0, 'prev_day': 10.0}]
    fed = rows_of(data)['政策金利の織り込み（前日比）']
    assert (fed['symbol'], fed['change'], fed['display'], fed['baseline']) == \
        ('▼', '+15.0bp', '期待 4.025%', '3.875%（提供表の前日列）')


def fed_note(data):
    return [n for n in board.bias_rows(data, NOW, 'daily', SOURCE)[2] if n.startswith('欠測: 政策金利の織り込み')]


def test_fedwatch_uses_its_own_history_or_drops_the_row():
    data = sample()
    data['fedwatch']['target_rates'][1]['prev_day'] = None
    data['fedwatch']['deltas']['prev_day']['by_range'] = {'3.75-4.00': -1.1, '4.00-4.25': 1.1}
    fed = rows_of(data)['政策金利の織り込み（10/09比）']  # the history's own date, not 「前日」
    assert (fed['symbol'], fed['change'], fed['baseline']) == ('▽', '+0.3bp', '3.921%（自前履歴 10/09）')
    data['fedwatch']['deltas']['prev_day']['meeting'] = 'Dec 9, 2026'
    assert '別の会合の履歴' in fed_note(data)[0]
    data['fedwatch']['deltas']['prev_day'].update(meeting='Oct 28, 2026', source='履歴 6日前 (2026-10-07)')
    assert '比較元が古い' in fed_note(data)[0]  # more than four days old for the Daily
    data['fedwatch']['deltas']['prev_day'].update(source='履歴 4日前 (2026-10-09)', by_range={'3.75-4.00': -1.1})
    assert '同じレンジ構成の履歴がない' in fed_note(data)[0]  # history for one range only: no baseline, no row
    data = sample()
    data['fedwatch']['target_rates'][0]['current'] = 9.5   # the table sums to 90%: incomplete, no vote
    assert '確率の合計が100%前後でない' in fed_note(data)[0]
    data = sample()
    for rate in data['fedwatch']['target_rates']:
        rate['current'] = 120.0
    assert fed_note(data) and not any(label.startswith('政策金利') for label in rows_of(data))


def test_extreme_positioning_votes_against_the_crowd_and_bad_percentiles_drop_the_row():
    data = sample()
    data['offchart_features']['positioning']['retail']['XAUUSD']['percentile'] = 92.0
    data['offchart_features']['positioning']['cot_gold_mm']['percentile'] = 8.0
    rows = rows_of(data)
    # Crowding is a weak vote (half), not a confirmed reversal.
    assert rows['個人のロング比率（MyFXBook）']['symbol'] == '▽' and rows['投機筋の買い越し（COT Managed Money）']['symbol'] == '△'
    data['offchart_features']['positioning']['retail']['XAUUSD']['percentile'] = 101.0
    assert '個人のロング比率（MyFXBook）' not in rows_of(data)


def test_surprises_need_a_prerelease_record_inside_the_window():
    data = sample()
    data['macro_surprises'][0]['forecast_provenance'] = '発表後の取得'
    assert '指標の結果: 新規失業保険申請' not in rows_of(data)
    data = sample()
    data['macro_surprises'][0]['released_at_jst'] = '2026-10-09T21:30:00+09:00'  # before the previous weekday
    assert '指標の結果: 新規失業保険申請' not in rows_of(data)
    data['macro_surprises'][0]['released_at_jst'] = '2026-10-12T00:00:00+09:00'  # the previous weekday from 00:00 JST
    assert '指標の結果: 新規失業保険申請' in rows_of(data)
    monday = datetime.fromisoformat('2026-10-12T18:00:00+09:00')  # Monday looks back to Friday 00:00
    data['macro_surprises'][0]['released_at_jst'] = '2026-10-09T21:30:00+09:00'
    assert any(i['label'] == '指標の結果: 新規失業保険申請' for i in board.bias_rows(data, monday, 'daily', SOURCE)[0])
    data['macro_surprises'][0]['actual'] = data['macro_surprises'][0]['forecast']
    data['macro_surprises'][0]['released_at_jst'] = '2026-10-12T21:30:00+09:00'
    same = rows_of(data)['指標の結果: 新規失業保険申請']
    assert (same['symbol'], same['direction']) == ('—', '予想どおり（中立）')


def test_simultaneous_releases_are_one_vote():
    data = sample()
    cpi = lambda name, actual, forecast: {
        'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': name,
        'actual': actual, 'previous': '0.3%', 'forecast': forecast, 'released_at_jst': '2026-10-13T21:30:00+09:00',
        'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）'}
    data['macro_surprises'] = [cpi('CPI (MoM) (Sep)', '0.2%', '0.3%'), cpi('Core CPI (MoM) (Sep)', '0.2%', '0.3%'),
                               cpi('CPI (YoY) (Sep)', '3.1%', '3.0%')]
    rows = [r for r in board.bias_rows(data, NOW, 'daily', SOURCE)[0] if r['label'].startswith('指標の結果')]
    assert len(rows) == 1
    row = rows[0]
    assert (row['label'], row['display'], row['symbol'], row['direction']) == \
        ('指標の結果: CPI・コアCPI', '3系列: 追い風 2・逆風 1・予想どおり 0', '△', '上向き（弱）')  # majority, not unanimous
    assert row['change'] == 'CPI（前月比・9月） -0.1pp／コアCPI（前月比・9月） -0.1pp／CPI（前年比・9月） +0.1pp'
    assert 'viz' not in row  # the counts are in the text
    data['macro_surprises'] = data['macro_surprises'][:2]
    row = [r for r in board.bias_rows(data, NOW, 'daily', SOURCE)[0] if r['label'].startswith('指標の結果')][0]
    assert (row['symbol'], row['direction']) == ('▲', '上向き')  # unanimous: one strong vote
    data['macro_surprises'] = data['macro_surprises'][:1]
    row = [r for r in board.bias_rows(data, NOW, 'daily', SOURCE)[0] if r['label'].startswith('指標の結果')][0]
    assert (row['symbol'], row['direction']) == ('△', '上向き（弱）')  # one series alone is weak


def test_weekly_rows_use_week_windows_and_thresholds():
    data = sample()
    rows = rows_of(data, 'weekly')
    assert '米2年債利回り（当日）' not in rows and 'ドル指数DXY（当日）' not in rows
    us2y = rows['米2年債利回り（5営業日）']
    assert us2y['group'] == board.GROUPS['short']['weekly'] and us2y['rule'].startswith('上昇は金の逆風') and '±0.10pp' in us2y['rule']
    dxy5 = rows['ドル指数DXY（5営業日）']
    assert dxy5['symbol'] == '▽' and dxy5['change'] == '+0.49%'  # under the 0.7% line
    fed = rows['政策金利の織り込み（前週比）']
    # Under 2.5bp; the ends carry the digits that make their difference the shown change (3.924−3.935 is −1.1bp).
    assert (fed['symbol'], fed['change'], fed['display'], fed['baseline']) == \
        ('△', '-1.15bp', '期待 3.9238%', '3.9353%（提供表の前週列）')
    assert us2y['viz']['type'] == 'spark' and len(us2y['viz']['values']) == 6  # the judged five days only
    assert us2y['group'] == '短期（今後1週間）'


def test_format_board_lines_gives_the_parent_the_same_rows_without_rules():
    lines = board.format_board_lines(sample(), 'daily')
    assert lines[0] == '### バイアス盤の材料（コード集計・HTMLに表示）'
    assert any(line.startswith('- 長期（数週間〜）｜実質金利（米10年TIPS）: 2.87%') for line in lines)
    assert not any('規則:' in line or 'ニュース・地政学（原稿の判断）' in line for line in lines)
    assert '- 集計 長期: 要因ごと 実質金利 ▼・ドル ▼・中銀 ▲ → ▽ 下向き（弱）' in lines
    assert '- 集計 短期: 要因ごと 金利・織り込み —・ドル ▲・発表前の予測 △・ETF ▽・建玉の偏り — → △ 上向き（弱）' in lines
    assert lines[-1].startswith('- 「時間軸別の向き:」は原稿の判断') and '集計と違う理由:' in lines[-1]
    assert board.format_board_lines({'timestamp': 'not a time'}, 'daily')[1].startswith('- 集計できず')
    # The board's "now" is the end of collection: a release just before it counts as released.
    data = sample()
    data['timestamp'] = '2026-10-13T21:20:00+09:00'
    data['macro_surprises'][0]['released_at_jst'] = '2026-10-13T21:30:00+09:00'
    assert not any('指標の結果: 新規失業保険申請' in line for line in board.format_board_lines(data, 'daily'))
    data['collection_finished_at'] = '2026-10-13T21:40:00+09:00'
    assert any('指標の結果: 新規失業保険申請' in line for line in board.format_board_lines(data, 'daily'))


def test_missing_inputs_drop_figures_instead_of_inventing_them():
    data = {'timestamp': NOW.isoformat(), 'report_quote': {'close': '4177.47'}}
    assert board.bias_figure(data, NOW, 'daily') is None
    assert board.liquidity_figure(data, NOW, 'daily') is None and board.event_figure(data, NOW, 'daily') is None
    result = bundle.observations(deepcopy(data), NOW)
    assert [entry.split(':')[0] for entry in result['missing']] == ['bias', 'liquidity', 'calendar']


# ------------------------------------------------------------------ liquidity map

def test_liquidity_map_orders_named_levels_with_state_distance_and_pool():
    result = board.liquidity_figure(sample(), NOW, 'daily', SOURCE)
    items = result['figure']['items']
    # Retail average entries are rows of the map, in price order; both sit outside the ±2.5% range here.
    assert [i['label'] for i in items if i['kind'] == 'retail'] == ['個人ロングの平均建値', '個人ショートの平均建値']
    assert [i['label'] for i in items].index('個人ロングの平均建値') == 1
    items = [i for i in items if i['kind'] != 'retail']
    assert [i['label'] for i in items] == [
        '前月高値（9月）', '前日高値（10/12）', '前週高値（10/05週）',
        '新規失業保険申請 発表15分足高値（10/12 21:30）', 'NY 0時の始値', 'London安値（当日）', '現在値',
        '新規失業保険申請 発表15分足安値（10/12 21:30）', '前日安値（10/12）', '前週安値（10/05週）']
    by = {i['label']: i for i in items}
    pdh = by['前日高値（10/12）']
    assert (pdh['pool'], pdh['icon'], pdh['state_word'], pdh['distance_text']) == ('BSL', '●', '未到達', '+38.74（+0.93%）')
    assert 'proximity' not in pdh  # the distance is the number alone
    assert pdh['target'] == '◎ 目的地の仮説' and pdh['source_quote'].endswith('｜◎ 目的地の仮説')
    assert not any(i['kind'] == 'draw' for i in items)  # the draw marks its level instead of adding a row
    pwh = by['前週高値（10/05週）']
    assert (pwh['icon'], pwh['touched']) == ('✓', '最初に抜けた足 10/12 09:00 JST（アジア）')
    assert by['London安値（当日）']['icon'] == '↘' and by['London安値（当日）']['pool'] == 'SSL'  # left through the low
    assert by['新規失業保険申請 発表15分足安値（10/12 21:30）']['state_word'] == '掃引済み・Tier B'
    assert by['現在値']['state_word'] == '市場時刻不明・参考価格'  # the sample kept no market time
    assert by['NY 0時の始値']['state_word'] == '当日の始値（参考）'  # not premium / discount
    retail = next(i for i in result['figure']['items'] if i['label'] == '個人ロングの平均建値')
    assert (retail['state_word'], retail['touched'], retail['offscale']) == \
        ('56%・含み損 -5.3%', 'MyFXBook・取得 10/13 18:03 JST', True)
    assert [i['label'] for i in items if i.get('offscale')] == ['前月高値（9月）', '前週安値（10/05週）']
    assert '未到達（確定足で一度も抜けていない）' in result['figure']['legend'] and '注文そのものは観測していません' in result['figure']['legend']
    assert result['figure']['cautions'] == []
    band = result['figure']['band']
    assert band == {'lower': 4117.47, 'upper': 4237.47, 'label': '想定変動 ±1σ（1日・±60.0ドル）'}
    assert '流動性｜想定変動 ±1σ（1日・±60.0ドル）: 4,117.47〜4,237.47（GVZ 22.79・2026-10-09）' in result['rows']
    for item in result['figure']['items']:
        shown_fields_are_quoted(item, ('label', 'price_text', 'pool', 'state_word', 'touched', 'distance_text', 'target'))
    assert 'キリ' not in str(result) and 'プレミアム' not in str(result) and '損切りが残る' not in str(result)
    data = sample()
    del data['report_quote']['fetched_at']
    data['xau_levels']['cautions'] = ['最終確定足が10/13 16:00 JSTで、その後の5時間分の足がない。以降の値動きは状態に入っていない']
    result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    assert next(i for i in result['figure']['items'] if i['kind'] == 'current')['state_word'] == '市場時刻不明・参考価格'
    # The heading says up to which closed hour the states were judged.
    span = board._bar_span(sample()["xau_levels"]["last_bar_utc"])
    assert span.endswith(' JST') and '〜' in span
    assert board.liquidity_figure(sample(), NOW, 'daily', SOURCE)['figure']['subtitle'].startswith(
        f'上ほど高い価格。状態は{span}の確定1時間足まで。')
    # With the market time (Twelve Data last_quote_at) the market time decides, and a closed market shows its
    # last price; a reference price is said in the heading and the nearest-level chip as well.
    epoch = lambda iso: datetime.fromisoformat(iso).timestamp()
    for extra, word, reference in (
            ({'last_quote_at': epoch('2026-10-13T22:10:00+09:00'), 'is_market_open': True}, '市場 10/13 22:10 JST', False),
            ({'last_quote_at': epoch('2026-10-13T20:00:00+09:00'), 'is_market_open': True},
             '市場 10/13 20:00 JST・1時間より前・参考価格', True),
            ({'last_quote_at': epoch('2026-10-10T05:59:00+09:00'), 'is_market_open': False}, '休場中の最終値（市場 10/10 05:59 JST）', False),
            ({'last_quote_at': epoch('2026-10-13T22:30:00+09:00'), 'is_market_open': True},
             '市場 10/13 22:30 JST が収集時刻より後・参考価格', True),
            ({'last_quote_at': None}, '市場時刻不明・参考価格', True),
            ({}, '市場時刻不明・参考価格', True)):  # old collections without the key: the fetch time does not decide
        data = sample()
        data['report_quote'].update(extra)
        assert ('last_quote_at' in data['report_quote']) is bool(extra)
        figure = board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']
        assert next(i for i in figure['items'] if i['kind'] == 'current')['state_word'] == word
        assert figure['subtitle'].endswith('。距離は参考価格（現在値の行）から') is reference
        assert board.nearest_chip(data, NOW).endswith('・距離は参考価格から') is reference
    for fetched in ('2026-10-13T13:10:00+00:00', '2026-10-13T12:00:00+00:00'):  # a fresh fetch is still a reference
        data = sample()
        data['report_quote']['fetched_at'] = fetched
        current = next(i for i in board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']['items'] if i['kind'] == 'current')
        assert current['state_word'] == '市場時刻不明・参考価格'
    data = sample()
    del data['report_quote']['fetched_at']
    data['xau_levels']['cautions'] = ['最終確定足が10/13 16:00 JSTで、その後の5時間分の足がない。以降の値動きは状態に入っていない']
    result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    assert result['figure']['cautions'][0]['source_quote'] == '流動性｜注意: ' + data['xau_levels']['cautions'][0]
    assert result['rows'][0] == result['figure']['cautions'][0]['source_quote']


def test_current_price_binding_is_observed_at_the_market_time_when_kept():
    def times(data):
        b = next(b for b in board.liquidity_figure(data, NOW, 'daily', SOURCE)['bindings'] if b['label'] == '現在値')
        return b['observedAt'], b['fetchedAt']
    data = sample()
    assert times(data) == ('', '2026-10-13T13:14:30+00:00')  # no market time: the fetch time is not an observation
    data['report_quote']['last_quote_at'] = datetime.fromisoformat('2026-10-13T22:10:00+09:00').timestamp()
    assert times(data) == ('2026-10-13T22:10:00+09:00', '2026-10-13T13:14:30+00:00')
    data['report_quote']['last_quote_at'] = None
    assert times(data) == ('', '2026-10-13T13:14:30+00:00')
    del data['report_quote']['fetched_at']
    assert times(data) == ('', '')  # without a fetch record the quote's date is not a fetch time


def test_weekly_map_widens_the_range_and_scales_the_band():
    result = board.liquidity_figure(sample(), NOW, 'weekly', SOURCE)
    assert result['figure']['band']['label'] == '想定変動 ±1σ（1週・±134.2ドル）'
    assert [i['label'] for i in result['figure']['items'] if i.get('offscale')] == ['前月高値（9月）', '個人ロングの平均建値']


def test_a_draw_must_be_a_listed_level_with_the_same_price_and_side():
    source = SOURCE.replace(DRAW_LINE, '方向: down / 水準: 前々日安値（10/09） / 価格: 4,140.00')
    draw = board._draw(source)
    assert (draw['direction'], draw['level'], draw['price'], draw['window']) == ('down', '前々日安値（10/09）', 4140.0, None)
    with pytest.raises(ValueError, match='not one of the listed levels'):
        board.liquidity_figure(sample(), NOW, 'daily', source)
    moved = SOURCE.replace(DRAW_LINE, '方向: 上 / 水準: 前日高値（10/12） / 価格: 4,220.00')  # right name, other price
    with pytest.raises(ValueError, match='not one of the listed levels'):
        board.liquidity_figure(sample(), NOW, 'daily', moved)
    wrong_side = SOURCE.replace(DRAW_LINE, '方向: 下 / 水準: 前日高値（10/12） / 価格: 4,216.21')
    with pytest.raises(ValueError, match='wrong side'):
        board.liquidity_figure(sample(), NOW, 'daily', wrong_side)
    twice = SOURCE + '\n## その日の目的地（draw）\n\n' + DRAW_LINE + '\n'
    with pytest.raises(ValueError, match='more than one'):
        board._draw(twice)
    assert board._draw('## その日の目的地\n\n方向: 上 / 水準: 前日高値\n') is None
    assert board._draw('## その日の目的地（draw）\n\n- 方向: none / 水準: none / 価格: None\n') is None
    # The pipeline's section (numeric_validation.draw_markdown) carries the hypothesis window.
    timed = SOURCE.replace(DRAW_LINE, '- ' + DRAW_LINE + '\n- 対象時間（JST）: 2026-10-13T22:40:00+09:00 ～ 2026-10-14T06:00:00+09:00')
    pdh = next(i for i in board.liquidity_figure(sample(), NOW, 'daily', timed)['figure']['items'] if i.get('target'))
    assert (pdh['label'], pdh['target']) == ('前日高値（10/12）', '◎ 目的地の仮説（10/13 22:40〜翌06:00）')
    assert pdh['source_quote'].endswith('｜◎ 目的地の仮説（10/13 22:40〜翌06:00）')
    # numeric_validation writes 「使ったSMT事実: なし」 when no silver or dollar fact supports the draw.
    window = '～ 2026-10-14T06:00:00+09:00'
    bare = timed.replace(window, window + '\n- 使ったSMT事実: なし')
    pdh = next(i for i in board.liquidity_figure(sample(), NOW, 'daily', bare)['figure']['items'] if i.get('target'))
    assert pdh['target'] == '◎ 目的地の仮説（10/13 22:40〜翌06:00）・他市場の裏付けなし'
    assert board._draw(timed.replace(window, window + '\n- 使ったSMT事実: H1:previous_day:high'))['unsupported'] is False


def test_no_levels_no_map():
    data = sample()
    data['xau_levels'] = {'error': '1時間足なし', 'levels': []}
    assert board.liquidity_figure(data, NOW, 'daily') is None


# ------------------------------------------------------------------ US event table

def test_event_table_keeps_us_tier_a_and_b_inside_their_windows():
    figure = board.event_figure(sample(), NOW, 'daily')['figure']
    assert [i['label'] for i in figure['items']] == [
        '新規失業保険申請', 'CPI（前月比・9月）', 'コアCPI（前月比・9月）', '小売売上高（前月比・9月）',
        '雇用統計 非農業部門雇用者数（9月）']
    by = {i['label']: i for i in figure['items']}
    claims = by['新規失業保険申請']
    assert (claims['tier'], claims['status'], claims['previous'], claims['forecast'], claims['actual']) == \
        ('B', '発表済み', '220K', '225K', '231K')  # the pre-release forecast, not the revised 230K
    assert (claims['diff'], claims['symbol'], claims['direction']) == ('結果−予想 +6K', '△', '上向き（弱）')  # one series
    assert claims['memo'] == '発表15分足 高 4,195.50（未到達）／安 4,168.20（掃引済み）'
    cpi = by['CPI（前月比・9月）']
    assert (cpi['tier'], cpi['status'], cpi['date'], cpi['time']) == ('A', '予定', '10/14（水）', '21:30')
    assert (cpi['diff'], cpi['symbol'], cpi['direction']) == ('nowcast 0.53%（予想との差 -0.07pp）', '△', '上向き（弱・予測）')
    assert cpi['memo'] == '停止 21:00〜22:30・NY KZ'
    assert by['コアCPI（前月比・9月）']['memo'] == ''  # the same release time: the stop window is shown once
    retail = by['小売売上高（前月比・9月）']
    assert (retail['diff'], retail['symbol'], retail['direction']) == ('発表前', '—', '発表待ち')
    for item in figure['items']:
        shown_fields_are_quoted(item, ('date', 'time', 'label', 'status', 'previous', 'forecast', 'actual', 'diff',
                                       'direction', 'symbol', 'memo'))
    assert figure['title'] == '米国の重要指標（直近の結果とこの先の予定）'


def test_weekly_table_covers_last_week_and_the_next_eight_days():
    labels = [i['label'] for i in board.event_figure(sample(), NOW, 'weekly')['figure']['items']]
    assert labels[0] == 'ミシガン大 1年先インフレ期待（10月）' and '耐久財受注（前月比・9月）' in labels
    assert 'FOMC 政策金利' not in labels  # 10/29 is beyond eight days
    figure = board.event_figure(sample(), NOW, 'weekly')['figure']
    assert figure['title'] == '米国の重要指標（10/06〜10/13の結果と10/21までの予定）'  # dates, not 「来週」


@pytest.mark.parametrize('when,expected', [
    ('2026-10-14T16:30:00+09:00', 'London KZ'), ('2026-10-14T21:30:00+09:00', 'NY KZ'),
    ('2026-10-28T16:30:00+09:00', 'KZ外'),        # UK on winter time: London KZ is 17-20 JST
    ('2026-10-28T21:30:00+09:00', 'NY KZ'),       # the US still on summer time
    ('2026-11-11T21:30:00+09:00', 'KZ外'), ('2026-11-11T22:30:00+09:00', 'NY KZ')])
def test_killzones_follow_each_market_clock(when, expected):
    assert board._killzone(datetime.fromisoformat(when)) == expected


def test_old_nowcasts_are_not_used():
    data = sample()
    for point in data['inflation_nowcast']['months']['2026-9'].values():
        point['as_of'] = '10/01'
    assert not any(label.startswith('予測との差') for label in rows_of(data))
    cpi = next(i for i in board.event_figure(data, NOW, 'daily')['figure']['items'] if i['label'] == 'CPI（前月比・9月）')
    assert cpi['diff'] == '発表前'


def test_released_without_a_prerelease_record_is_labelled_and_missing_actual_waits():
    data = sample()
    data['macro_surprises'] = []
    claims = board.event_figure(data, NOW, 'daily')['figure']['items'][0]
    assert claims['forecast'] == '230K' and claims['diff'] == '結果−予想 +1K・予想は発表後の取得'
    data['economic_calendar']['events'][1]['actual'] = 'N/A'
    claims = board.event_figure(data, NOW, 'daily')['figure']['items'][0]
    assert (claims['diff'], claims['direction']) == ('結果を未取得（発表時刻は経過）', '結果未取得')
    # A numbered release recorded without any number still waits for its result; only a statement, a speech
    # or minutes (judged by its name) has no number to compare.
    data['economic_calendar']['events'][1].update(previous='', forecast='')
    claims = board.event_figure(data, NOW, 'daily')['figure']['items'][0]
    assert (claims['diff'], claims['direction']) == ('結果を未取得（発表時刻は経過）', '結果未取得')
    data['economic_calendar']['events'][1]['indicator'] = 'Fed Chair Powell Speaks'
    speech = board.event_figure(data, NOW, 'daily')['figure']['items'][0]
    assert (speech['diff'], speech['direction']) == ('予定時刻を経過・実施と内容は未確認（声明・発言など）', '内容未確認')


def test_empty_window_yields_one_quoted_row():
    data = sample()
    data['economic_calendar']['events'] = [ev('Tuesday, October 13, 2026', '23:00', 'Existing Home Sales (Sep)')]
    data['macro_surprises'] = []
    items = board.event_figure(data, NOW, 'daily')['figure']['items']
    assert len(items) == 1 and items[0]['label'] == '該当なし（Tier A/Bの米指標なし）' and items[0]['empty'] is True
    assert items[0]['date'] == '10/12からの結果と、Tier Aは10/20・Tier Bは10/15までの予定'
    shown_fields_are_quoted(items[0], ('date', 'time', 'label', 'status', 'previous', 'forecast', 'actual', 'diff',
                                       'direction', 'symbol', 'memo'))


def test_released_records_the_calendar_dropped_stay_in_the_table():
    data = sample()
    del data['economic_calendar']['events'][1]  # the calendar no longer lists the claims release
    claims = board.event_figure(data, NOW, 'daily')['figure']['items'][0]
    assert (claims['label'], claims['forecast'], claims['actual'], claims['diff']) == \
        ('新規失業保険申請', '225K', '231K', '結果−予想 +6K')
    result = board.event_figure(data, NOW, 'daily')
    assert result['bindings'][0]['path'] == 'macro_surprises.0'


def test_stop_windows_are_merged_per_release_time_and_shown_while_active():
    data = sample()
    data['offchart_features'] = {**data['offchart_features'], 'event_blackouts': [
        {'event': 'Core CPI (MoM) (Sep)', 'time_jst': '2026-10-14T21:30:00+09:00',
         'window_start': '2026-10-14T20:45:00+09:00', 'window_end': '2026-10-14T22:30:00+09:00'}]}
    items = board.event_figure(data, NOW, 'daily')['figure']['items']
    cpi, core = items[1], items[2]
    assert cpi['memo'] == '停止 20:45〜22:30・NY KZ' and core['memo'] == ''  # the union of both windows, once
    assert (cpi['group_start'], core['group_start']) == (True, False)
    during = datetime.fromisoformat('2026-10-14T21:45:00+09:00')
    items = board.event_figure(sample(), during, 'daily')['figure']['items']
    cpi = next(i for i in items if i['label'] == 'CPI（前月比・9月）')
    assert cpi['status'] == '発表済み' and cpi['memo'].startswith('停止中（21:00〜22:30）')


def test_lead_chips_show_an_active_stop_window_and_the_next_us_release():
    assert board.lead_chips(sample(), NOW, 'daily') == ['次の米指標: CPI・コアCPI（Tier A） 10/14（水）21:30 JST']
    during = datetime.fromisoformat('2026-10-14T21:45:00+09:00')
    assert board.lead_chips(sample(), during, 'daily') == [
        '停止中: CPI・コアCPI（10/14（水）21:30 JST 発表）〜22:30',
        '次の米指標: 小売売上高（Tier B） 10/15（木）21:30 JST']
    data = sample()
    data['economic_calendar']['events'] = []
    assert board.lead_chips(data, NOW, 'daily') == []


# ------------------------------------------------------------------ bundle

def test_bundle_builds_three_figures_with_bindings_quotes_and_a_lead_chip():
    data = sample()
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    assert [f['id'] for f in result['figures']] == ['bias', 'liquidity', 'calendar'] and result['missing'] == []
    assert [x['text'] for x in result['lead']] == [
        '次の米指標: CPI・コアCPI（Tier A） 10/14（水）21:30 JST',
        '直近の未到達: 上 新規失業保険申請 発表15分足高値（10/12 21:30） 4,195.50（+18.03）／下 前週安値（10/05週） 4,061.73（-115.74）・距離は参考価格から']
    full = SOURCE + result['appendix']
    for figure in result['figures']:
        for item in figure['items']:
            assert item['source_quote'] in full
    bundle.check_bindings(data, result, result['figures'])
    changed = deepcopy(data)
    changed['xau_levels']['levels'][1]['price'] = 4216.5
    with pytest.raises(bundle.BundleError, match='does not match'):
        bundle.check_bindings(changed, result, result['figures'])
    changed = deepcopy(data)
    changed['xau_levels']['levels'][1]['state'] = 'swept'
    with pytest.raises(bundle.BundleError, match='state does not match'):
        bundle.check_bindings(changed, result, result['figures'])
    changed = deepcopy(data)
    changed['fred']['DFII10']['recent'][-3][1] = 9.99
    with pytest.raises(bundle.BundleError, match='sparkline'):
        bundle.check_bindings(changed, result, result['figures'])


def prepared(tmp_path, data, source=SOURCE, mode='daily'):
    import json
    from pathlib import Path
    data_path = tmp_path / 'r.data.json'
    data_path.write_text(json.dumps(data, ensure_ascii=False))
    md = tmp_path / 'r.md'
    md.write_text(source, encoding='utf-8')
    out = bundle.prepare(md, data_path, mode, now=NOW + timedelta(minutes=10))
    read = lambda key: json.loads(Path(out[key]).read_text())
    return read('bundle_path'), read('visuals_path'), md.read_text(encoding='utf-8'), read('summary_path')


@pytest.mark.parametrize('end,problem', [('not a time', '収集終了時刻を読めない'),
                                         ('2026-10-13T22:00:00+09:00', '収集終了時刻が開始より前')])
def test_an_unusable_collection_end_stops_the_edition(end, problem):
    data = sample()
    data['collection_finished_at'] = end
    assert bundle.cutoff_problem(data) == problem
    for call in (lambda: bundle.analysis_cutoff(data), lambda: bundle.build_observations(data, 'daily', SOURCE)):
        with pytest.raises(bundle.BundleError, match=problem):
            call()
    data['collection_finished_at'] = '2026-10-13T22:20:00+09:00'
    assert bundle.cutoff_problem(data) is None
    assert not any('収集終了時刻' in note for note in bundle.build_observations(data, 'daily', SOURCE)['limitations'])


def test_validation_rebuilds_every_figure_row_and_chip_from_the_collection(tmp_path):
    data = sample()
    data['collection_finished_at'] = '2026-10-13T22:20:00+09:00'
    # Levels without their stored bars cannot be rebuilt (test below); this one checks the other figures.
    del data['xau_levels']
    manifest, figures, source, summary = prepared(tmp_path, data, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    assert manifest['schemaVersion'] == 6
    bundle.check_regenerated(data, manifest, figures, source, summary)
    assert summary['as_of_label'] == '10/13（火）22:14〜22:20 JST 収集'
    assert 'データ収集: 2026-10-13T22:14:00+09:00〜2026-10-13T22:20:00+09:00（判定の基準は収集終了時刻）' in source
    tampered = [
        ('Figures differ', lambda m, f, s, c: f[0]['items'][0].update(symbol='▲')),
        ('Figures differ', lambda m, f, s, c: f[1]['items'].pop()),
        ('Bindings differ', lambda m, f, s, c: m['bindings'].pop()),
        ('Bindings differ', lambda m, f, s, c: m.update(missing=['calendar: 図の作成に必要なデータが不足'])),
        ('Lead chips differ', lambda m, f, s, c: c['chips'].pop(0)),
        # A code-made chip moved into the folded limits is a change too.
        ('Lead chips differ', lambda m, f, s, c: c['limitations'].append(c['chips'].pop(0))),
        ('Limitations differ', lambda m, f, s, c: c['limitations'].pop()),
        ('Conclusion differs', lambda m, f, s, c: c['conclusion'].update(text=c['conclusion']['text'] + '（買い優勢）')),
        ('Conclusion differs', lambda m, f, s, c: c['conclusion'].update(source_quote='XAUUSD：別の行')),
        ('Confidence differs', lambda m, f, s, c: c['confidence'].update(text='信頼度 高')),
        ('Confidence differs', lambda m, f, s, c: c.pop('confidence'))]
    for message, change in tampered:
        m, f, c = deepcopy(manifest), deepcopy(figures), deepcopy(summary)
        change(m, f, source, c)
        with pytest.raises(bundle.BundleError, match=message):
            bundle.check_regenerated(data, m, f, source, c)
    with pytest.raises(bundle.BundleError, match='Appendix rows differ'):
        bundle.check_regenerated(data, manifest, figures, source.replace('｜向き: ▼ 下向き｜', '｜向き: ▲ 上向き｜', 1), summary)
    changed = deepcopy(data)
    changed['fred']['DFII10']['change_20obs'] = -0.32   # the collection itself changed: the rebuild differs
    with pytest.raises(bundle.BundleError, match='differ'):
        bundle.check_regenerated(changed, manifest, figures, source, summary)
    # The parent's own limitations and a notice chip may be added; the code-made ones must stay.
    extra = deepcopy(summary)
    extra['chips'].insert(0, {'text': '過去資料の修正版です。', 'source_quote': '過去資料の修正版です。'})
    extra['limitations'].append({'title': '制約', 'text': '原稿の制約', 'source_quote': '原稿の制約'})
    bundle.check_regenerated(data, manifest, figures, source, extra)
    old = {**manifest, 'schemaVersion': 3}       # editions drawn by earlier code are not rebuilt
    bundle.check_regenerated(data, old, [], source, summary)


def test_validation_recomputes_the_levels_from_the_stored_bars(tmp_path):
    import json
    from scrapers import xau_bars as xb
    from scrapers import xau_levels as xl
    from tests.test_offchart_collectors import scenario, utc
    bars, m15, events = scenario()
    now = utc('2026-10-13T13:10')
    data = sample()
    data['xau_levels'] = json.loads(json.dumps(xl.build_levels(bars, m15, now, price=4177.47, events=data['economic_calendar']['events'])))
    data['xau_bars_used'] = xb.snapshot(*xl.level_inputs(bars, m15, now))
    manifest, figures, source, summary = prepared(tmp_path, data, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(data, manifest, figures, source, summary)
    changed = deepcopy(data)
    changed['xau_bars_used']['h1'][-30][2] += 50.0          # one stored bar edited: its digest no longer matches
    with pytest.raises(bundle.BundleError, match='Stored bars cannot be used'):
        bundle.check_regenerated(changed, manifest, figures, source, summary)
    changed = deepcopy(data)
    changed['xau_bars_used'] = xb.snapshot(*xl.level_inputs(bars[:-5], m15, now))  # other bars, a valid digest
    with pytest.raises(bundle.BundleError, match='Liquidity levels differ'):
        bundle.check_regenerated(changed, manifest, figures, source, summary)
    changed = deepcopy(data)
    del changed['xau_levels']['inputs']
    with pytest.raises(bundle.BundleError, match='do not record when'):
        bundle.check_regenerated(changed, manifest, figures, source, summary)


def test_release_rows_are_rechecked_against_their_records():
    data = sample()
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    texts = [b for b in result['bindings'] if 'value' not in b]
    assert {b['figure'] for b in texts} == {'bias', 'calendar'}
    assert next(b for b in texts if b['figure'] == 'bias')['members'] == ['macro_surprises.0']
    bundle.check_bindings(data, result, result['figures'])
    calendar_path = next(b['path'] for b in texts if b['figure'] == 'calendar' and b['path'].startswith('economic_calendar'))
    for path, field, value in ((calendar_path, 'previous', '9.9%'), ('macro_surprises.0', 'actual', '240K')):
        changed = deepcopy(data)
        bundle.resolve(changed, path)[field] = value
        with pytest.raises(bundle.BundleError, match='does not match its source record'):
            bundle.check_bindings(changed, result, result['figures'])
    dropped = {**result, 'bindings': [b for b in result['bindings'] if b.get('figure') != 'calendar']}
    with pytest.raises(bundle.BundleError, match='Text bindings are missing'):
        bundle.check_bindings(data, dropped, result['figures'])
    bundle.check_bindings(data, {**dropped, 'schemaVersion': 1}, result['figures'])  # editions before text bindings
    changed = deepcopy(data)
    changed['inflation_nowcast']['months']['2026-9']['cpi']['value'] = 0.61
    with pytest.raises(bundle.BundleError, match='Figure number does not match'):
        bundle.check_bindings(changed, result, result['figures'])


# ------------------------------------------------------------------ review round 3 (lane B)

def test_nowcast_rows_judge_exactly_and_show_values_whose_difference_is_the_shown_difference():
    # 0.2496 vs 0.2 is a gap of 0.0496pp: 予想並み. Rounding the nowcast to 0.25 first would make it +0.05pp.
    cases = [(0.2496, 'nowcast 0.2496%', '+0.0496pp', 'flat'), (0.2504, 'nowcast 0.25%', '+0.05pp', 'down'),
             (0.202, 'nowcast 0.202%', '+0.002pp', 'flat'), (0.1504, 'nowcast 0.1504%', '-0.0496pp', 'flat'),
             (0.1496, 'nowcast 0.15%', '-0.05pp', 'up'), (0.53, 'nowcast 0.53%', '+0.33pp', 'down')]
    for value, shown_value, change, side in cases:
        data = sample()
        data['inflation_nowcast']['months']['2026-9']['core_cpi']['value'] = value
        data['economic_calendar']['events'][5]['forecast'] = '0.2%'
        core = rows_of(data)['予測との差: コアCPI（前月比・9月）']
        assert (core['display'], core['baseline'], core['change'], core['side']) == \
            (shown_value, '市場予想 0.2%', change, side), value
        assert core['strength'] == 'weak' or side == 'flat'
        # The shown difference is the difference of the shown values.
        assert round(float(core['display'][8:-1]) - 0.2, 6) == float(core['change'][:-2])
    # A binding digits matches the shown nowcast, so the recorded value is what the page prints.
    bound = bundle.observations(data, NOW, 'daily', SOURCE)
    bundle.check_bindings(data, bound, bound['figures'])


def test_us_event_lines_use_the_same_gap_text_as_the_board():
    data = sample()
    data['inflation_nowcast']['months']['2026-9']['cpi']['value'] = 0.2496
    data['economic_calendar']['events'][4]['forecast'] = '0.2%'
    lines = ue.format_us_event_lines(data['economic_calendar']['events'], data['inflation_nowcast'], NOW)
    assert any('nowcast 0.2496%（10/09時点）− 市場予想 0.2% = +0.0496pp' in line for line in lines)
    assert ue.gap_display({'nowcast': 0.202, 'forecast': 0.2, 'gap': 0.002}) == ('0.202', '0.2', '+0.002')
    assert ue.gap_display({'nowcast': 0.531, 'forecast': 0.6, 'gap': -0.069}) == ('0.53', '0.6', '-0.07')
    # The event table prints the same digits as the board (not nowcast 0.20% with a +0.002pp gap).
    data = sample()
    for ev in data['economic_calendar']['events']:
        if ev['indicator'].startswith('Core CPI (MoM)'):
            ev['forecast'] = '0.2%'
    by = {i['label']: i for i in board.event_figure(data, NOW, 'daily')['figure']['items']}
    assert by['コアCPI（前月比・9月）']['diff'] == 'nowcast 0.202%（予想との差 +0.002pp）'
    assert ': nowcast 0.202%（' in rows_of(data)['予測との差: コアCPI（前月比・9月）']['source_quote']


@pytest.mark.parametrize('change,symbol', [(0.04999999999999982, '▼'), (0.0499, '▽'), (-0.05000000000000071, '▲')])
def test_unrounded_us2y_change_is_judged_on_the_threshold(change, symbol):
    data = sample()
    # Ends that agree with the change (a 0.0499 change with ends 0.0500 apart is a mismatch now).
    data['us_yields']['US2Y'].update(value={0.0499: 4.8409}.get(change, 4.841 if change > 0 else 4.741), change=change)
    assert rows_of(data)['米2年債利回り（当日）']['symbol'] == symbol


@pytest.mark.parametrize('pct,symbol', [(-0.30000000000000004, '▲'), (-0.2999, '△'), (0.3000000000000003, '▼')])
def test_unrounded_dxy_change_is_judged_on_the_threshold(pct, symbol):
    data = sample()
    data['dxy']['change_pct'] = pct
    assert rows_of(data)['ドル指数DXY（当日）']['symbol'] == symbol


def nowcast_data():
    data = sample()
    data['inflation_nowcast']['months']['2026-9'].update(pce={'value': 0.3, 'as_of': '10/09'},
                                                         core_pce={'value': 0.21, 'as_of': '10/09'})
    return data


def test_adopted_nowcasts_sort_before_deduplicating_and_cap_at_three():
    events = [ev('Thursday, October 15, 2026', '21:30', 'CPI (MoM) (Sep)', '0.4%', '0.9%'),  # a later duplicate, listed first
              ev('Thursday, October 15, 2026', '21:30', 'Core PCE Price Index (MoM) (Sep)', '0.2%', '0.2%'),
              ev('Wednesday, October 14, 2026', '21:30', 'CPI (MoM) (Sep)', '0.4%', '0.6%'),
              ev('Wednesday, October 14, 2026', '21:30', 'Core CPI (MoM) (Sep)', '0.3%', '0.3%'),
              ev('Thursday, October 15, 2026', '22:00', 'PCE Price Index (MoM) (Sep)', '0.3%', '0.3%')]
    data = nowcast_data()
    adopted = ue.adopted_nowcasts(events, data['inflation_nowcast'], NOW, 'daily')
    assert [(w.strftime('%m/%d %H:%M'), g['series']) for w, _, g in adopted] == \
        [('10/14 21:30', 'cpi'), ('10/14 21:30', 'core_cpi'), ('10/15 21:30', 'core_pce')]  # PCE is the fourth: cut
    assert adopted[0][1]['forecast'] == '0.6%' and adopted[0][2]['forecast'] == 0.6  # not the later 0.9% record
    # The board, the parent input and the table use that one list.
    data['economic_calendar']['events'] = events
    labels = [l for l in rows_of(data) if l.startswith('予測との差')]
    assert labels == ['予測との差: CPI（前月比・9月）', '予測との差: コアCPI（前月比・9月）', '予測との差: コアPCE価格指数（前月比・9月）']
    lines = ue.format_us_event_lines(events, data['inflation_nowcast'], NOW)
    assert sum('Cleveland連銀nowcast' in line for line in lines) == 3
    table = board.event_figure(data, NOW, 'daily')['figure']['items']
    assert sum('nowcast' in str(i.get('diff', '')) for i in table) == 3


@pytest.mark.parametrize('timestamp,usable', [
    ('2026-10-13T13:12:00+00:00', True), ('2026-10-13T13:19:00+00:00', True),   # 22:19 JST: 5 minutes after the cutoff
    ('2026-10-13T13:20:00+00:00', False),                                        # 22:20 JST: after the collection ended
    ('2026-10-07T13:15:00+00:00', True), ('2026-10-07T13:00:00+00:00', False),   # just inside / outside six days
    (None, False), ('', False), ('not a time', False)])
def test_a_nowcast_needs_a_fetch_time_within_the_cutoff_and_six_days(timestamp, usable):
    data = sample()
    if timestamp is None:
        del data['inflation_nowcast']['timestamp']
    else:
        data['inflation_nowcast']['timestamp'] = timestamp
    assert bool(ue.adopted_nowcasts(data['economic_calendar'], data['inflation_nowcast'], NOW, 'daily')) is usable
    assert any(l.startswith('予測との差') for l in rows_of(data)) is usable
    lines = ue.format_us_event_lines(data['economic_calendar']['events'], data['inflation_nowcast'], NOW)
    assert any('Cleveland連銀nowcast' in line for line in lines) is usable


def test_a_calendar_record_with_an_error_adopts_no_nowcast():
    data = sample()
    assert len(ue.adopted_nowcasts(data['economic_calendar'], data['inflation_nowcast'], NOW, 'daily')) == 2
    data['economic_calendar']['error'] = 'ページ取得失敗'
    assert ue.adopted_nowcasts(data['economic_calendar'], data['inflation_nowcast'], NOW, 'daily') == []
    assert not any(l.startswith('予測との差') for l in rows_of(data))
    # The event list alone (the old call) still works.
    events = sample()['economic_calendar']['events']
    assert len(ue.adopted_nowcasts(events, sample()['inflation_nowcast'], NOW, 'daily')) == 2


@pytest.mark.parametrize('broken', ['calendar', 'nowcast'])
def test_an_error_record_adopts_no_nowcast_on_the_board_the_table_or_the_parent_input(broken):
    """A calendar or nowcast record with an error (even with values left in it) is used nowhere."""
    data = sample()
    data['economic_calendar' if broken == 'calendar' else 'inflation_nowcast']['error'] = 'ページ取得失敗'
    assert ue.adopted_nowcasts(data['economic_calendar'], data['inflation_nowcast'], NOW, 'daily') == []
    assert not any(l.startswith('予測との差') for l in rows_of(data))
    figure = board.event_figure(data, NOW, 'daily')
    assert not any('nowcast' in str(i.get('diff', '')) for i in (figure or {}).get('figure', {}).get('items', []))
    lines = ue.format_us_event_lines(data['economic_calendar'], data['inflation_nowcast'], NOW)
    assert not any('Cleveland連銀nowcast' in line for line in lines)
    if broken == 'nowcast':
        assert '取得失敗' in board.nowcast_missing(data, NOW, 'daily')


def test_a_non_finite_nowcast_value_gives_no_gap():
    data = sample()
    for value in (float('nan'), float('inf'), True, '0.5'):
        data['inflation_nowcast']['months']['2026-9']['cpi']['value'] = value
        assert ue.nowcast_gap(data['economic_calendar']['events'][4], data['inflation_nowcast']) is None


def board_notes(data):
    return board.bias_rows(data, NOW, 'daily', SOURCE)[2]


def test_a_missing_or_failed_nowcast_is_listed_when_a_mapped_release_is_scheduled():
    assert not any('予測との差' in n for n in board_notes(sample()))
    for change, reason in ((lambda d: d.update(inflation_nowcast=None), 'Cleveland Fed・CPI・コアCPIは取得失敗）'),
                           (lambda d: d['inflation_nowcast'].update(error='取得不可（ValueError）'),
                            'Cleveland Fed・CPI・コアCPIは取得失敗（取得不可（ValueError））'),
                           (lambda d: d['inflation_nowcast'].pop('timestamp'), 'CPI・コアCPIは取得時刻の記録なし'),
                           (lambda d: d['inflation_nowcast']['months'].clear(), 'CPI・コアCPIは対象月の予測か市場予想がない'),
                           (lambda d: d['inflation_nowcast']['months']['2026-9']['cpi'].update(as_of='10/01'),
                            'CPIは予測の更新が6日より前')):
        data = sample()
        change(data)
        notes = [n for n in board_notes(data) if '予測との差' in n]
        assert len(notes) == 1 and reason in notes[0] and notes[0].startswith('欠測: 予測との差（'), notes
        assert notes[0].endswith('短期（当日〜1、2日）の集計に入れていない')
    # The short tile lists it with the other missing materials.
    data = sample()
    data['inflation_nowcast'] = None
    tile = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', SOURCE)['figure']['verdicts']}['短期']
    assert '予測との差' in tile['detail'] and tile['show_detail'] is True
    # Nothing is listed when no mapped release is scheduled in the window.
    data['economic_calendar']['events'] = [e for e in data['economic_calendar']['events'] if 'CPI' not in e['indicator']]
    assert not any('予測との差' in n for n in board_notes(data))


def test_a_horizon_without_material_is_not_called_neutral_in_the_blend():
    data = sample()
    for key in ('DFII10', 'DTWEXBGS'):
        del data['fred'][key]
    del data['dxy']['recent_closes'], data['gold_cb']
    source = '# R\n'                                   # no verdict line and no news row: 長期 has no material
    figure = board.bias_figure(data, NOW, 'daily', source)['figure']
    tiles = {t['label']: t for t in figure['verdicts']}
    assert (tiles['長期']['direction'], tiles['長期']['symbol']) == ('材料なし', '—')
    assert tiles['短期']['symbol'] == '△'
    assert (tiles['総合']['symbol'], tiles['総合']['direction']) == ('△', '上向き（弱）')  # arithmetic unchanged
    assert tiles['総合']['detail'] == '長期×短期の規則: 上向き（弱）。長期は材料なし（短期だけで判定）'
    assert tiles['総合']['show_detail'] is True
    assert all('長期は中立' not in t['detail'] and '中立' not in t['detail'] for t in tiles.values() if t['label'] != '短期')
    # The report's own 中立 for that horizon is named as the report's, with the tally saying 材料なし.
    own = '時間軸別の向き: 長期 中立｜短期 上向き・弱｜総合 上向き・弱\n'
    tiles = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', own)['figure']['verdicts']}
    assert (tiles['長期']['direction'], tiles['長期']['origin']) == ('中立', '原稿の判断（集計は材料なし）')
    assert '規則の集計: 材料なし' in tiles['長期']['detail']
    # When both horizons have material nothing is added to the blend.
    tiles = {t['label']: t for t in board.bias_figure(sample(), NOW, 'daily', SOURCE)['figure']['verdicts']}
    assert tiles['総合']['detail'] == '長期×短期の規則: 中立'
    # A short horizon without material is explained the same way.
    data = sample()
    data['us_yields']['US2Y']['stale'] = True
    for key in ('fedwatch', 'gold_etf', 'retail_sentiment', 'cot_disaggregated', 'inflation_nowcast', 'macro_surprises'):
        data.pop(key)
    del data['fred']['DGS2'], data['dxy']['current_price']
    tiles = {t['label']: t for t in board.bias_figure(data, NOW, 'daily', NEWS_LINE + '\n')['figure']['verdicts']}
    assert tiles['短期']['direction'] == '材料なし'
    assert tiles['総合']['detail'] == '長期×短期の規則: 下向き（弱）。短期は材料なし（長期だけで判定）'


def test_fedwatch_needs_a_dated_upcoming_meeting_and_valid_ranges():
    for meeting, why in (('Sept 31, 2026', '次回FOMCの日付を読めない'), ('soon', '次回FOMCの日付を読めない'),
                         ('Oct 12, 2026', '収集日より前')):
        data = sample()
        data['fedwatch']['next_fomc_date'] = meeting
        assert why in fed_note(data)[0], meeting
    data = sample()
    data['fedwatch']['next_fomc_date'] = 'Oct 13, 2026'      # the cutoff date itself is still ahead
    assert '政策金利の織り込み（前日比）' in rows_of(data)
    for rates, why in ((['4.25-4.00', '3.75-4.00'], 'レンジ'), (['4.00-4.00', '3.75-4.00'], 'レンジ'),
                       (['nan-4.25', '3.75-4.00'], 'レンジ'), (['3.75-4.00', '3.75-4.00'], 'レンジ'),
                       (['3.75-inf', '3.75-4.00'], 'レンジ'), (['4.00-4.25-4.5', '3.75-4.00'], 'レンジ'),
                       (['3.90-4.25', '3.75-4.00'], 'レンジ')):            # overlapping ranges
        data = sample()
        for rate, label in zip(data['fedwatch']['target_rates'], rates):
            rate['range'] = label
        assert why in fed_note(data)[0], rates
        assert '政策金利の織り込み（前日比）' not in rows_of(data)
    assert board._rate_ranges([{'range': '4.00-4.25'}, {'range': '3.75-4.00'}])  # adjacent ranges are fine


def test_fedwatch_history_is_comparable_only_for_the_same_meeting_and_range_set():
    def own_history():
        data = sample()
        data['fedwatch']['target_rates'][1]['prev_day'] = None
        data['fedwatch']['deltas']['prev_day']['by_range'] = {'3.75-4.00': -1.1, '4.00-4.25': 1.1}
        return data
    assert '政策金利の織り込み（10/09比）' in rows_of(own_history())
    data = own_history()
    del data['fedwatch']['deltas']['prev_day']['meeting']       # a history with no meeting proves nothing
    assert '履歴の会合が不明' in fed_note(data)[0] and '政策金利の織り込み（10/09比）' not in rows_of(data)
    for meeting in ('', None, 'someday'):
        data = own_history()
        data['fedwatch']['deltas']['prev_day']['meeting'] = meeting
        assert '履歴の会合が不明' in fed_note(data)[0]
    data = own_history()
    data['fedwatch']['deltas']['prev_day']['meeting'] = '2026-10-28'   # the same day in another notation
    assert '政策金利の織り込み（10/09比）' in rows_of(data)
    data = own_history()
    data['fedwatch']['deltas']['prev_day']['meeting'] = 'Dec 9, 2026'
    assert '別の会合の履歴' in fed_note(data)[0]
    data = own_history()
    data['fedwatch']['deltas']['prev_day']['by_range'] = {'3.75-4.00': -1.1, '4.00-4.25': float('nan')}
    assert '同じレンジ構成の履歴がない' in fed_note(data)[0]


def test_provider_comparison_columns_need_a_valid_table_time():
    data = sample()
    assert '政策金利の織り込み（前日比）' in rows_of(data)
    for stamp in (None, 'garbage', '2026-10-13T22:30:00+09:00', '2026-10-12T21:00:00+09:00'):
        data = sample()
        data['fedwatch']['timestamp'] = stamp
        assert fed_note(data) and not any(l.startswith('政策金利') for l in rows_of(data)), stamp


@pytest.mark.parametrize('minutes,usable', [(-1, True), (4, True), (5, True), (6, False), (59, False)])
def test_records_after_the_cutoff_are_rejected_after_five_minutes(minutes, usable):
    stamp = (NOW + timedelta(minutes=minutes)).isoformat()
    assert (board.stale_time(stamp, NOW) is None) is usable
    data = sample()
    data['dxy']['timestamp'] = stamp
    data['retail_sentiment']['XAUUSD']['timestamp'] = stamp
    data['fedwatch']['timestamp'] = stamp
    assert any(l.startswith('ドル指数DXY（当日）') for l in rows_of(data)) is usable
    assert any(l.startswith('個人のロング比率') for l in rows_of(data)) is usable
    assert any(l.startswith('政策金利') for l in rows_of(data)) is usable
    assert bool(board_notes(data)) is not usable


def test_a_us2y_quote_after_the_cutoff_falls_back_to_fred():
    data = sample()
    data['us_yields']['US2Y']['as_of_jst'] = '2026-10-13 22:30'
    rows = rows_of(data)
    assert '米2年債利回り（当日）' not in rows and '米2年債利回り（前営業日比）' in rows
    data['us_yields']['US2Y']['as_of_jst'] = None
    assert '米2年債利回り（当日）' not in rows_of(data)


def test_legend_explains_the_trend_line_scale_only():
    legend = board.bias_figure(sample(), NOW, 'daily', SOURCE)['figure']['legend']
    assert 'その行だけの自動尺度' in legend and '傾きを行どうしで比べることはできません' in legend
    assert '2点の比較線' not in legend and '棒が' not in legend
    assert '傾きは閾値に対する大きさ' not in legend
