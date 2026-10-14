"""Where retail positions sit on the liquidity map (r10): the average entries and the MyFXBook Order Book clusters.

Contract: every row is rebuilt from the collection JSON alone (check_regenerated), every shown string is in its
quoted appendix row (offchart_dashboard.validate), and clusters from an earlier collection are used only when at
most three calendar days old, with their date and time. Real inputs: the 2026-10-14 18:00 Daily collection
(Order Book fetch failed) and the 2026-10-13 09:00 collection (clusters fetched).
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re

import pytest

from scripts import offchart_board as board
from scripts import offchart_dashboard as dash
from scripts import report_bundle as bundle
from tests.test_offchart_board import NOW, SOURCE, ORDER_BOOK, sample, with_order_book

REAL = Path(__file__).parent / 'fixtures' / 'real_daily'
SHOWN = ('label', 'price_text', 'pool', 'state_word', 'touched', 'distance_text', 'target')


def real(name):
    return json.loads((REAL / name).read_text())


def real_source(name):
    return (REAL / name).read_text(encoding='utf-8').rstrip()


def zones(figure):
    return [i for i in figure['items'] if i['kind'] == 'zone']


def edition(data, source, mode='daily'):
    """The figures, appendix and summary as prepare() makes them, then the validation checks of an edition."""
    result = bundle.build_observations(data, mode, source)
    full = source + result['appendix']
    summary = bundle.make_summary(full, mode, result['asOf'], result['lead'], result['cutoff'])
    summary['limitations'] = [{'title': '制約', 'text': n, 'source_quote': n} for n in result['limitations']]
    manifest = {'schemaVersion': bundle.MANIFEST_VERSION, 'kind': mode, 'bindings': result['bindings'],
                'missing': result['missing']}
    figures = json.loads(json.dumps(result['figures'], ensure_ascii=False))
    bundle.check_bindings(data, manifest, figures)
    bundle.check_regenerated(data, manifest, figures, full, summary)
    dash.validate(summary, figures, full)
    return result, full, summary, figures, manifest


# ------------------------------------------------------------------ this collection's clusters

def test_fetched_clusters_are_zone_rows_with_side_share_source_and_time():
    figure = board.liquidity_figure(with_order_book(sample()), NOW, 'daily', SOURCE)['figure']
    rows = {i['price_text']: i for i in zones(figure)}
    assert list(rows) == ['4,429.00〜4,434.56', '4,190.00〜4,201.50', '4,104.00〜4,170.00']  # price order
    above = rows['4,190.00〜4,201.50']
    assert (above['label'], above['pool'], above['distance_text'], above['state_word'], above['touched'], above['icon']) == \
        ('個人の注文・建玉が集まる価格帯', 'BSL', '差 +12.53', '35件', '', '▭')
    below = rows['4,104.00〜4,170.00']
    assert (below['pool'], below['distance_text'], below['state_word']) == ('SSL', '差 -7.47', '279件')
    assert rows['4,429.00〜4,434.56']['offscale'] is True and not above.get('offscale')
    assert above['source_quote'] == '流動性｜個人の注文・建玉が集まる価格帯: 4,190.00〜4,201.50｜BSL｜差 +12.53｜35件'
    for item in zones(figure):
        assert all(str(item[k]) in item['source_quote'] for k in SHOWN if item.get(k))
        assert '損切り' not in item['source_quote'] and '指値' not in item['source_quote']
    legend = figure['legend']
    assert '注文・建玉 MyFXBook・10/13 22:12 JST 取得（60分遅れ）' in legend  # the fetch line (not on every row)
    assert '注文・建玉の数でロットではありません' in legend and '15価格' in legend
    assert '未約定の注文か保有中の建玉か、損切りか指値かを示していない' in legend
    assert figure['missing'] == []


def test_a_cluster_around_the_price_has_no_side_tag_and_sits_by_the_current_row():
    data = with_order_book(sample())
    data['myfxbook_open_orders']['XAUUSD']['ssl_candidates'] = [{'low': 4170.0, 'high': 4180.0, 'volume_sum': 9,
                                                                  'share_pct': 40.0}]
    items = board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']['items']
    labels = [i['label'] for i in items]
    zone = items[labels.index('現在値') - 1]
    assert (zone['kind'], zone['pool'], zone['distance_text']) == ('zone', '', '現在値を含む')


# ------------------------------------------------------------------ an earlier collection's clusters

def failed(data, error='Order Book ペアを抽出できず (DOM 変更の可能性)'):
    data['myfxbook_open_orders'] = {'XAUUSD': {'source': ORDER_BOOK['source'], 'bsl_candidates': [], 'ssl_candidates': [],
                                               'error': error, 'timestamp': '2026-10-13T13:12:00Z'}}
    return data


def test_failed_fetch_uses_clusters_kept_within_three_days_with_their_fetch_time():
    data = failed(sample())
    data['retail_analytics'] = {'previous_pools': {'date': '2026-10-10', 'fetched_at': '2026-10-10T00:03:00Z',
                                                   'bsl': ORDER_BOOK['bsl_candidates'], 'ssl': ORDER_BOOK['ssl_candidates']}}
    figure = board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']
    assert {i['touched'] for i in zones(figure)} == {''}
    assert [n['text'] for n in figure['notices']] == ['個人の注文・建玉は前回 10/10 09:03 JST の取得分（最新の取得に失敗）']
    # The full reason is a limitation (not a 欠測 line under the map: the kept clusters are shown).
    result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    assert result['figure']['missing'] == []
    assert result['notes'] == ['流動性マップ: 個人の注文・建玉が集まる価格帯は10/10の前回取得分を表示'
                               '（今回の取得失敗（Order Book ペアを抽出できず (DOM 変更の可能性)））']
    # No stored bars in this sample: the state says so rather than guessing.
    assert {i['state_word'].split('・')[-1] for i in zones(figure)} == {'取得後の足なし'}
    data['retail_analytics']['previous_pools']['date'] = '2026-10-09'   # four calendar days: not used
    result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    assert zones(result['figure']) == []
    line = ('個人の注文・建玉が集まる価格帯（MyFXBook・60分遅れ）: 今回の取得失敗（Order Book ペアを抽出できず (DOM 変更の可能性)）'
            '・前回分: 最新 10/09・3日より古い')
    assert result['figure']['missing'] == [{'text': line, 'source_quote': f'流動性｜欠測: {line}'}]
    assert f'流動性｜欠測: {line}' in result['rows'] and f'流動性マップ: {line}' in result['notes']


def test_nothing_kept_is_a_missing_line_never_an_invented_zone():
    for data in (failed(sample()), sample()):
        result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
        assert zones(result['figure']) == [] and len(result['figure']['missing']) == 1
        assert result['figure']['missing'][0]['text'].endswith('・前回分: 記録なし')
    data = sample()
    data['myfxbook_open_orders'] = {'XAUUSD': {**ORDER_BOOK, 'timestamp': '2026-10-12T09:00:00Z'}}  # a day and a half old
    text = board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']['missing'][0]['text']
    assert '今回の取得が古い（取得 10/12 18:00 JST・24時間より前）' in text


def test_kept_clusters_take_their_state_from_the_stored_bars_after_the_fetch():
    data = real('2026-10-14T1800.data.json')
    oo = real('2026-10-13T0900.data.json')['myfxbook_open_orders']['XAUUSD']
    data['retail_analytics']['previous_pools'] = {'date': '2026-10-13', 'fetched_at': oo['timestamp'],
                                                  'bsl': oo['bsl_candidates'], 'ssl': oo['ssl_candidates']}
    stamp = bundle.analysis_cutoff(data)
    rows = {i['price_text']: i for i in zones(board.liquidity_figure(data, stamp, 'daily')['figure'])}
    assert {i['touched'] for i in rows.values()} == {''}
    # 前日高値（10/13） 4,200.45 traded above the whole 4,173.86〜4,190.61 cluster after the fetch.
    assert rows['4,173.86〜4,190.61']['state_word'] == '35件・掃引済み（10/13 13:00 JST）'
    assert rows['4,429.00〜4,434.56']['state_word'] == '15件・取得後は未到達'
    assert rows['4,104.00〜4,170.00']['state_word'] == '279件・到達・抜けていない（10/13 09:15 JST）'
    edition(data, real_source('2026-10-14T1800-r3.md'))


# ------------------------------------------------------------------ real collections, end to end

def test_real_1014_daily_shows_kept_clusters_and_entries_and_rebuilds_exactly():
    data = real('2026-10-14T1800.data.json')
    assert data['myfxbook_open_orders']['XAUUSD']['error']  # the 18:00 Order Book fetch failed
    result, full, summary, figures, _ = edition(data, real_source('2026-10-14T1800-r3.md'))
    figure = next(f for f in figures if f['id'] == 'liquidity')
    for line in (
            '流動性｜個人ロングの平均建値: 4,393.67｜差 +250.41｜個人61%ロング・含み損-5.7%\n',
            '流動性｜個人ショートの平均建値: 3,996.87｜差 -146.39｜個人39%ショート・含み損-3.7%\n',
            '流動性｜個人の注文・建玉が集まる価格帯: 4,173.86〜4,190.61｜BSL｜差 +30.60｜35件・到達後に反転（10/13 17:00 JST）\n',
            '流動性｜個人の注文・建玉が集まる価格帯: 4,104.00〜4,170.00｜現在値を含む｜279件・到達後も戻りが小さい（10/13 22:00 JST）\n',
            '流動性｜個人の注文・建玉が集まる価格帯: 4,429.00〜4,434.56｜BSL｜差 +285.74｜15件・24時間は未到達\n',
            '流動性｜表示の注記: 個人の注文・建玉は前回 10/13 の取得分（最新の取得に失敗）\n',
            '個人の取得: 平均建値 MyFXBook・10/14 18:03 JST。',
            '制約: 流動性マップ: 個人の注文・建玉が集まる価格帯は10/13の前回取得分を表示'
            '（今回の取得失敗（Order Book ペアを抽出できず (DOM 変更の可能性)））'):
        assert line in full
    shown = [i['label'] for i in figure['items'] if not i.get('offscale')]
    outside = [i['label'] for i in figure['items'] if i.get('offscale')]
    assert shown.count('個人の注文・建玉が集まる価格帯') == 2
    assert outside == ['前月高値（9月）', '個人の注文・建玉が集まる価格帯', '個人ロングの平均建値', '個人ショートの平均建値']
    assert figure['missing'] == [] and 'aux' not in figure
    # Four calendar days later the same kept clusters are no longer shown.
    late = deepcopy(data)
    late['retail_analytics']['baseline_date'] = '2026-10-10'
    figure = board.liquidity_figure(late, bundle.analysis_cutoff(late), 'daily')['figure']
    assert zones(figure) == [] and figure['missing'][0]['text'].endswith('・前回分: 最新 10/10・3日より古い')


def test_real_1013_order_book_clusters_on_the_1014_map_rebuild_exactly():
    """The 10/13 09:00 clusters as if fetched in this collection (its own data has no 1-hour bars for the map)."""
    data = real('2026-10-14T1800.data.json')
    oo = deepcopy(real('2026-10-13T0900.data.json')['myfxbook_open_orders'])
    oo['XAUUSD']['timestamp'] = '2026-10-14T09:02:30Z'
    data['myfxbook_open_orders'] = oo
    _, full, *_ = edition(data, real_source('2026-10-14T1800-r3.md'))
    assert '流動性｜個人の注文・建玉が集まる価格帯: 4,173.86〜4,190.61｜BSL｜差 +30.60｜35件\n' in full
    assert '流動性｜個人の注文・建玉が集まる価格帯: 4,104.00〜4,170.00｜現在値を含む｜279件\n' in full
    assert '／注文・建玉 MyFXBook・10/14 18:02 JST 取得（60分遅れ）。' in full
    assert '前回取得分を表示' not in full


def test_real_1013_collection_rebuilds_without_a_liquidity_map():
    data = real('2026-10-13T0900.data.json')
    result, *_ = edition(data, real_source('2026-10-13T0900.md'))
    assert [f['id'] for f in result['figures']] == ['bias', 'calendar']  # no 1-hour levels in that collection


def test_a_changed_cluster_or_entry_is_caught_by_the_rebuild():
    data = real('2026-10-14T1800.data.json')
    result, full, summary, figures, manifest = edition(data, real_source('2026-10-14T1800-r3.md'))
    for change in (lambda d: d['retail_analytics']['sweep_events'][0].update(high=4199.0),
                   lambda d: d['retail_sentiment']['XAUUSD'].update(long_pct=70.0),
                   lambda d: d['retail_analytics'].update(baseline_date='2026-10-12')):
        changed = deepcopy(data)
        change(changed)
        with pytest.raises(bundle.BundleError, match='differ'):
            bundle.check_regenerated(changed, manifest, figures, full, summary)


# ------------------------------------------------------------------ mobile width (C)

def screen_widths(page_html, width=390):
    """[page width, page width with every <details> open, right edge of the widest map cell] in a real browser.
    Skips only when Playwright or its browser is not installed; anything after the launch fails the test."""
    sync = pytest.importorskip('playwright.sync_api')
    try:
        p = sync.sync_playwright().start()
    except Exception as exc:  # no Playwright driver here
        pytest.skip(f'playwright unavailable: {type(exc).__name__}')
    try:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # no browser installed here
            pytest.skip(f'chromium unavailable: {type(exc).__name__}')
        try:
            page = browser.new_page(viewport={'width': width, 'height': 950})
            page.set_content(page_html)
            widths = [page.evaluate('document.documentElement.scrollWidth')]
            page.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = true)')
            widths.append(page.evaluate('document.documentElement.scrollWidth'))
            widths.append(page.evaluate('Math.ceil(Math.max(...[...document.querySelectorAll(".lv > span")]'
                                        '.map(n => n.getBoundingClientRect().right)))'))
            return widths
        finally:
            browser.close()
    finally:
        p.stop()


def test_a_long_draw_label_wraps_inside_a_390px_screen():
    """10/14 18:00 r2 was rejected: 「◎ 目的地の仮説（10/14 22:30〜翌01:00）・他市場の裏付けなし」 was nowrap."""
    data = real('2026-10-14T1800.data.json')
    _, full, summary, figures, _ = edition(data, real_source('2026-10-14T1800-r2.md'))
    target = next(i['target'] for f in figures if f['id'] == 'liquidity' for i in f['items'] if i.get('target'))
    assert target == '◎ 目的地の仮説（10/14 22:30〜翌01:00）・他市場の裏付けなし'
    page_html, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    page, opened, right = screen_widths(page_html)
    assert (page, opened) == (390, 390) and right <= 390


def kept_without_time(data, day='2026-10-13'):
    """The 10/13 09:00 clusters kept by the collector without a fetch time (records made before fetched_at)."""
    oo = real('2026-10-13T0900.data.json')['myfxbook_open_orders']['XAUUSD']
    data['retail_analytics']['previous_pools'] = {'date': day, 'fetched_at': None, 'current_price': 4171.37,
                                                  'bsl': oo['bsl_candidates'], 'ssl': oo['ssl_candidates']}
    return data


@pytest.mark.parametrize('name', ['with fetch time', 'without fetch time'])
def test_long_kept_cluster_states_wrap_inside_a_390px_screen(name):
    data = real('2026-10-14T1800.data.json')
    if name == 'with fetch time':
        oo = real('2026-10-13T0900.data.json')['myfxbook_open_orders']['XAUUSD']
        data['retail_analytics']['previous_pools'] = {'date': '2026-10-13', 'fetched_at': oo['timestamp'],
                                                      'bsl': oo['bsl_candidates'], 'ssl': oo['ssl_candidates']}
    else:
        kept_without_time(data)
    _, full, summary, figures, _ = edition(data, real_source('2026-10-14T1800-r3.md'))
    words = [i['state_word'] for f in figures if f['id'] == 'liquidity' for i in zones(f)]
    assert max(len(w) for w in words) >= 24  # e.g. 「279件・到達後も戻りが小さい（10/13 22:00 JST）」
    page_html, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    page, opened, right = screen_widths(page_html)
    assert (page, opened) == (390, 390) and right <= 390


# ------------------------------------------------------------------ review r10 (GPT-6 Astra)

def test_zone_rows_say_the_kind_is_unknown_and_the_legend_states_the_scraper_rules():
    """1: the provider does not say whether a count is a pending order or an open position."""
    import inspect
    from scrapers import myfxbook_open_orders as oo
    figure = board.liquidity_figure(with_order_book(sample()), NOW, 'daily', SOURCE)['figure']
    assert {(i['label'], i['touched']) for i in zones(figure)} == {('個人の注文・建玉が集まる価格帯', '')}
    assert {i['pool'] for i in zones(figure)} == {'BSL', 'SSL'}  # above the price: buy-side liquidity candidate
    legend = figure['legend']
    assert 'AsksとBidsそれぞれについて件数の多い15価格' in legend and '上下それぞれ件数の多い15価格' not in legend
    assert '取得時の価格から±10%を超える価格は除き' in legend and '直前の価格との差が0.5%未満なら同じ帯' in legend
    assert '上と下それぞれ件数の多い3帯まで' in legend and '種類は分かりません（種類不明）' in legend
    # The stated rules are the scraper's own defaults and its page note (15 per series).
    defaults = inspect.signature(oo._cluster_and_classify).parameters
    assert defaults['max_distance_pct'].default * 100 == board.ZONE_FILTER_PCT
    assert defaults['cluster_pct'].default * 100 == board.ZONE_GAP_PCT
    assert '[:3]' in inspect.getsource(oo._cluster_and_classify) and board.ZONE_TOP == 3
    assert 'Asks / Bids 各上位 15 件' in oo.__doc__


def test_kept_clusters_without_a_fetch_time_take_the_collectors_verdict_for_the_same_cluster():
    """2: legacy previous_pools (no fetched_at) together with sweep_events of the same day and edges."""
    data = kept_without_time(real('2026-10-14T1800.data.json'))
    assert data['retail_analytics']['baseline_date'] == '2026-10-13'
    result, full, *_ = edition(data, real_source('2026-10-14T1800-r3.md'))
    figure = next(f for f in result['figures'] if f['id'] == 'liquidity')
    rows = {i['price_text']: i for i in zones(figure)}
    assert {i['touched'] for i in rows.values()} == {''}
    assert rows['4,173.86〜4,190.61']['state_word'] == '35件・到達後に反転（10/13 17:00 JST）'
    assert rows['4,104.00〜4,170.00']['state_word'] == '279件・到達後も戻りが小さい（10/13 22:00 JST）'
    assert rows['4,429.00〜4,434.56']['state_word'] == '15件・24時間は未到達'
    assert all(i['state_word'] for i in rows.values())  # never an empty state
    assert ('制約: 流動性マップ: 個人の注文・建玉が集まる価格帯は10/13の前回取得分を表示'
            '（今回の取得失敗（Order Book ペアを抽出できず (DOM 変更の可能性)））') in full


@pytest.mark.parametrize('change', [
    lambda ra: ra.update(baseline_date='2026-10-12'),  # the verdicts judged another day's clusters
    lambda ra: ra['sweep_events'][0].update(high=4199.0),  # another cluster
    lambda ra: ra.update(sweep_events=[])])
def test_kept_clusters_without_a_fetch_time_or_a_matching_verdict_say_the_state_is_unconfirmed(change):
    data = kept_without_time(real('2026-10-14T1800.data.json'))
    change(data['retail_analytics'])
    result, *_ = edition(data, real_source('2026-10-14T1800-r3.md'))
    rows = {i['price_text']: i for f in result['figures'] if f['id'] == 'liquidity' for i in zones(f)}
    assert rows['4,173.86〜4,190.61']['state_word'] == '35件・状態未確認（取得時刻なし）'
    assert len(rows) == 3 and all(i['state_word'] for i in rows.values())


@pytest.mark.parametrize('oo_change,brief', [
    ({'error': 'Order Book ペアを抽出できず (DOM 変更の可能性)'}, '最新の取得に失敗'),
    ({'error': None, 'timestamp': '2026-10-14T09:02:30Z', 'bsl_candidates': [], 'ssl_candidates': []}, '最新の取得で価格帯なし'),
    ({'error': None, 'timestamp': '2026-10-12T09:02:30Z'}, '最新の取得が古い')])
def test_kept_clusters_say_briefly_why_this_collection_did_not_get_them(oo_change, brief):
    data = kept_without_time(real('2026-10-14T1800.data.json'))
    if 'bsl_candidates' not in oo_change:  # a stale fetch: clusters present, but too old to show
        current = real('2026-10-13T0900.data.json')['myfxbook_open_orders']['XAUUSD']
        oo_change = {**oo_change, 'bsl_candidates': current['bsl_candidates'], 'ssl_candidates': current['ssl_candidates']}
    data['myfxbook_open_orders']['XAUUSD'].update(oo_change)
    result, *_ = edition(data, real_source('2026-10-14T1800-r3.md'))
    figure = next(f for f in result['figures'] if f['id'] == 'liquidity')
    assert {i['touched'] for i in zones(figure)} == {''}
    # One notice line above the map says why (instead of repeating it on every row).
    text = f'個人の注文・建玉は前回 10/13 の取得分（{brief}）'
    assert figure['notices'] == [{'text': text, 'source_quote': f'流動性｜表示の注記: {text}'}]


def entries(data):
    figure = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    return [i['label'] for i in figure['figure']['items'] if i['kind'] == 'retail'], \
        [m['text'] for m in figure['figure']['missing'] if '平均建値' in m['text']], figure


def test_retail_entries_both_missing_say_why_in_one_line():
    """3: an error response, no record and no values are missing lines, never a silent gap."""
    data = sample()
    data['retail_sentiment']['XAUUSD'].update(error='HTTP 503', avg_long_entry=None, avg_short_entry=None)
    rows, missing, result = entries(data)
    assert rows == [] and missing == ['個人ロング・ショートの平均建値（MyFXBook）: 取得失敗（HTTP 503）']
    assert '流動性マップ: 個人ロング・ショートの平均建値（MyFXBook）: 取得失敗（HTTP 503）' in result['notes']
    assert not any(b['label'].endswith('の平均建値') for b in result['bindings'])
    data['retail_sentiment']['XAUUSD'] = {**data['retail_sentiment']['XAUUSD'], 'error': None}
    assert entries(data)[1] == ['個人ロング・ショートの平均建値（MyFXBook）: 値なし']
    data['retail_sentiment'] = {}
    assert entries(data)[1] == ['個人ロング・ショートの平均建値（MyFXBook）: 取得なし']


def test_an_error_response_with_values_still_shows_no_entry():
    data = sample()
    data['retail_sentiment']['XAUUSD']['error'] = 'API がエラーを返した'
    rows, missing, _ = entries(data)
    assert rows == [] and missing == ['個人ロング・ショートの平均建値（MyFXBook）: 取得失敗（API がエラーを返した）']


@pytest.mark.parametrize('key,label,other', [
    ('avg_short_entry', '個人ショートの平均建値', '個人ロングの平均建値'),
    ('avg_long_entry', '個人ロングの平均建値', '個人ショートの平均建値')])
def test_one_missing_retail_entry_is_its_own_line_and_the_other_side_stays(key, label, other):
    data = sample()
    data['retail_sentiment']['XAUUSD'][key] = None
    rows, missing, result = entries(data)
    assert rows == [other] and missing == [f'{label}（MyFXBook）: 値なし']
    assert [b['label'] for b in result['bindings'] if b['label'].endswith('の平均建値')] == [other]


def test_missing_entries_on_a_real_collection_rebuild_and_validate():
    data = real('2026-10-14T1800.data.json')
    data['retail_sentiment']['XAUUSD']['avg_short_entry'] = None
    result, full, *_ = edition(data, real_source('2026-10-14T1800-r3.md'))
    assert '流動性｜欠測: 個人ショートの平均建値（MyFXBook）: 値なし' in full
    assert '流動性｜個人ロングの平均建値: 4,393.67' in full


def test_a_zone_crossing_the_band_edge_is_not_coloured_as_inside():
    """8: the band colour uses both ends of a zone."""
    zone = lambda text, near: {'kind': 'zone', 'label': 'z', 'price_text': text, 'position': near, 'state': 'zone',
                               'state_word': '', 'icon': '▭', 'distance_text': '', 'pool': ''}
    figure = {'type': 'liquidity_map', 'title': 't', 'legend': '', 'band': {'lower': 100.0, 'upper': 200.0, 'label': '帯'},
              'items': [zone('210.00〜250.00', 210.0), zone('150.00〜250.00', 150.0), zone('120.00〜180.00', 120.0),
                        zone('50.00〜120.00', 120.0), zone('50.00〜250.00', 150.0), zone('40.00〜60.00', 60.0)]}
    html = dash.liquidity_section(figure)
    classes = re.findall(r'<li class="lv kind-zone state-zone([^"]*)"', html)
    assert classes == ['', ' part-band', ' in-band', ' part-band', ' part-band', '']
    # The left edge carries the side spine; the straddling mark sits on the right edge.
    assert '.lv.part-band{box-shadow:inset -6px 0 0 var(--band-mark)}' in dash.CSS
    assert '帯の境をまたぐ価格帯は右端だけ色を付けます' in \
        board.liquidity_figure(with_order_book(sample()), NOW, 'daily', SOURCE)['figure']['legend']


def test_no_label_on_the_map_or_chips_is_kept_on_one_line():
    css = dash.CSS
    rule = css[css.index('.target{'):css.index('}', css.index('.target{'))]
    assert 'nowrap' not in rule and 'overflow-wrap:anywhere' in rule
    assert '.lv>span{min-width:0;overflow-wrap:anywhere}' in css
    facts = css[css.index('.kv dd{'):css.index('}', css.index('.kv dd{'))]
    assert 'overflow-wrap:anywhere' in facts and 'min-width:0' in facts
