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
        ('個人の建玉が集まる価格帯（MyFXBook・60分遅れ）', 'BSL', '+12.53（+0.30%）', '取得時上側の70%・35件', '取得 10/13 22:12 JST', '▭')
    below = rows['4,104.00〜4,170.00']
    assert (below['pool'], below['distance_text'], below['state_word']) == ('SSL', '-7.47（-0.18%）', '取得時下側の100%・279件')
    assert rows['4,429.00〜4,434.56']['offscale'] is True and not above.get('offscale')
    assert above['source_quote'] == ('流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,190.00〜4,201.50｜BSL｜+12.53（+0.30%）'
                                     '｜取得時上側の70%・35件｜取得 10/13 22:12 JST')
    for item in zones(figure):
        assert all(str(item[k]) in item['source_quote'] for k in SHOWN if item.get(k))
        assert '損切り' not in item['source_quote'] and '指値' not in item['source_quote']
    legend = figure['legend']
    assert '60分遅れ' in legend and 'ポジションの数でロットではありません' in legend and '15価格' in legend
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
    assert {i['touched'] for i in zones(figure)} == {'前回取得 10/10 09:03 JST'}
    # No stored bars in this sample: the state says so rather than guessing.
    assert {i['state_word'].split('・')[-1] for i in zones(figure)} == {'取得後の足なし'}
    data['retail_analytics']['previous_pools']['date'] = '2026-10-09'   # four calendar days: not used
    result = board.liquidity_figure(data, NOW, 'daily', SOURCE)
    assert zones(result['figure']) == []
    line = ('個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 今回の取得失敗（Order Book ペアを抽出できず (DOM 変更の可能性)）'
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
    assert {i['touched'] for i in rows.values()} == {'前回取得 10/13 09:02 JST'}
    # 前日高値（10/13） 4,200.45 traded above the whole 4,173.86〜4,190.61 cluster after the fetch.
    assert rows['4,173.86〜4,190.61']['state_word'].startswith('取得時上側の70%・35件・価格帯を抜けた・')
    assert rows['4,429.00〜4,434.56']['state_word'] == '取得時上側の30%・15件・取得後は価格帯に未到達'
    assert rows['4,104.00〜4,170.00']['state_word'].startswith('取得時下側の100%・279件・価格帯に到達・抜けていない（最初の足 10/13 ')
    edition(data, real_source('2026-10-14T1800-r3.md'))


# ------------------------------------------------------------------ real collections, end to end

def test_real_1014_daily_shows_kept_clusters_and_entries_and_rebuilds_exactly():
    data = real('2026-10-14T1800.data.json')
    assert data['myfxbook_open_orders']['XAUUSD']['error']  # the 18:00 Order Book fetch failed
    result, full, summary, figures, _ = edition(data, real_source('2026-10-14T1800-r3.md'))
    figure = next(f for f in figures if f['id'] == 'liquidity')
    for line in (
            '流動性｜個人ロングの平均建値: 4,393.67｜+250.41（+6.04%）｜61%・含み損 -5.7%｜MyFXBook・取得 10/14 18:03 JST',
            '流動性｜個人ショートの平均建値: 3,996.87｜-146.39（-3.53%）｜39%・含み損 -3.7%｜MyFXBook・取得 10/14 18:03 JST',
            '流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,173.86〜4,190.61｜BSL｜+30.60（+0.74%）'
            '｜35件・価格帯に到達後に反転（10/13 17:00 JST）｜前回取得 10/13（時刻の記録なし）',
            '流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,104.00〜4,170.00｜現在値を含む'
            '｜279件・価格帯に到達後も戻りが小さい（10/13 22:00 JST）｜前回取得 10/13（時刻の記録なし）',
            '流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,429.00〜4,434.56｜BSL｜+285.74（+6.90%）'
            '｜15件・直近24時間は価格帯に未到達｜前回取得 10/13（時刻の記録なし）'):
        assert line in full
    shown = [i['label'] for i in figure['items'] if not i.get('offscale')]
    outside = [i['label'] for i in figure['items'] if i.get('offscale')]
    assert shown.count('個人の建玉が集まる価格帯（MyFXBook・60分遅れ）') == 2
    assert outside == ['前月高値（9月）', '個人の建玉が集まる価格帯（MyFXBook・60分遅れ）', '個人ロングの平均建値', '個人ショートの平均建値']
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
    assert '流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,173.86〜4,190.61｜BSL｜+30.60（+0.74%）｜取得時上側の70%・35件｜取得 10/14 18:02 JST' in full
    assert '流動性｜個人の建玉が集まる価格帯（MyFXBook・60分遅れ）: 4,104.00〜4,170.00｜現在値を含む｜取得時下側の100%・279件｜取得 10/14 18:02 JST' in full


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

def test_a_long_draw_label_wraps_inside_a_390px_screen():
    """10/14 18:00 r2 was rejected: 「◎ 目的地の仮説（10/14 22:30〜翌01:00）・他市場の裏付けなし」 was nowrap."""
    sync = pytest.importorskip('playwright.sync_api')
    data = real('2026-10-14T1800.data.json')
    _, full, summary, figures, _ = edition(data, real_source('2026-10-14T1800-r2.md'))
    target = next(i['target'] for f in figures if f['id'] == 'liquidity' for i in f['items'] if i.get('target'))
    assert target == '◎ 目的地の仮説（10/14 22:30〜翌01:00）・他市場の裏付けなし'
    page_html, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    try:
        with sync.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 390, 'height': 950})
                page.set_content(page_html)
                widths = [page.evaluate('document.documentElement.scrollWidth')]
                page.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = true)')
                widths.append(page.evaluate('document.documentElement.scrollWidth'))
            finally:
                browser.close()
    except Exception as exc:  # no browser installed here: the static check below still runs
        pytest.skip(f'chromium unavailable: {type(exc).__name__}')
    assert widths == [390, 390]


def test_no_label_on_the_map_or_chips_is_kept_on_one_line():
    css = dash.CSS
    rule = css[css.index('.target{'):css.index('}', css.index('.target{'))]
    assert 'nowrap' not in rule and 'overflow-wrap:anywhere' in rule
    assert '.lv>span{min-width:0;overflow-wrap:anywhere}' in css
    chips = css[css.index('.chips li{'):css.index('}', css.index('.chips li{'))]
    assert 'overflow-wrap:anywhere' in chips
