"""Retail observations stay visible; unverified order zones and inferred P/L are excluded."""
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





# ------------------------------------------------------------------ an earlier collection's clusters

def failed(data, error='Order Book ペアを抽出できず (DOM 変更の可能性)'):
    data['myfxbook_open_orders'] = {'XAUUSD': {'source': ORDER_BOOK['source'], 'bsl_candidates': [], 'ssl_candidates': [],
                                               'error': error, 'timestamp': '2026-10-13T13:12:00Z'}}
    return data








# ------------------------------------------------------------------ real collections, end to end





def test_real_1013_collection_rebuilds_without_a_liquidity_map():
    data = real('2026-10-13T0900.data.json')
    result, *_ = edition(data, real_source('2026-10-13T0900.md'))
    assert [f['id'] for f in result['figures']] == ['bias', 'calendar']  # no 1-hour levels in that collection




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




# ------------------------------------------------------------------ review r10 (GPT-6 Astra)









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


@pytest.mark.parametrize('mode', ['daily', 'weekly'])
@pytest.mark.parametrize('previous', [False, True])
def test_unverified_current_and_previous_zones_never_enter_report(mode, previous):
    data = real('2026-10-14T1800.data.json')
    if not previous:
        data['myfxbook_open_orders'] = real('2026-10-13T0900.data.json')['myfxbook_open_orders']
    if mode == 'weekly':
        from scrapers.xau_levels import recompute
        from scrapers.xau_levels import level_events
        events, event_source = level_events(data)
        data['xau_levels'] = recompute(data['xau_bars_used'], data['xau_levels']['inputs']['computed_at'], data['xau_levels']['price'], events, True, event_source)
    result, full, summary, figures, manifest = edition(data, real_source('2026-10-14T1800-r3.md'), mode)
    figure = next(f for f in figures if f['id'] == 'liquidity')
    assert zones(figure) == []
    assert any(i['kind'] == 'retail' for i in figure['items'])
    assert not any('含み損' in i['state_word'] or '含み益' in i['state_word'] for i in figure['items'])
    changed = deepcopy(data)
    changed['retail_sentiment']['XAUUSD']['avg_long_entry'] += 100
    with pytest.raises(bundle.BundleError, match='differ|match'):
        bundle.check_regenerated(changed, manifest, figures, full, summary)
