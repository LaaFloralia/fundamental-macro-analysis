"""r13 (2026-11-14): the one-page HTML set in the LAA design standard (2026.11.2), with nothing taken away.

Paper, ink and one teal face (the overall verdict, with the report's confidence); rules instead of boxes; the
standard's fonts and four text sizes; direction told by symbol, word and place, not by red and green. Each horizon
shows its materials' directions as points on one rule (drawn from the rows' own symbols). The liquidity map reads
from high to low with the levels outside the shown range above and below it, the expected-move band as the thick
part of the trunk, and each row's distance from the current price as a line. A value that is not the day's own
carries ○ and its quoted words (`fresh`). The reason a verdict differs from the tally follows the conclusion.
"""
from copy import deepcopy
import re

import pytest

from scripts import offchart_dashboard as dash
from scripts import report_acceptance as acceptance
from scripts import report_bundle as bundle
from tests.test_offchart_board import NOW, SOURCE, sample


def edition(data=None):
    result = bundle.observations(data or sample(), NOW, 'daily', SOURCE)
    full = SOURCE + result['appendix']
    summary = bundle.make_summary(full, 'daily', result['asOf'], result['lead'])
    summary['limitations'] = [{'title': '制約', 'text': n, 'source_quote': n} for n in result['limitations']]
    figures = deepcopy(result['figures'])
    dash.validate(summary, figures, full)
    page, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    return page, summary, figures, full


def visible(html):
    return acceptance.compact(re.sub(r'<[^>]+>', '', html.replace('&amp;', '&')))


# ------------------------------------------------------------------ the standard's values only

def test_colours_fonts_sizes_and_corners_come_from_the_standard():
    css = dash.CSS
    assert dash.TOKENS_CSS.lstrip().startswith('/* laa-design tokens --css web（基準 2026.11.2）')
    own = css[len(dash.TOKENS_CSS):]
    assert re.findall(r'#[0-9A-Fa-f]{3,8}\b', own) == []  # every colour through the standard's variables
    assert re.findall(r'rgba?\(', own) == []
    for banned in ('gradient', 'box-shadow', 'text-shadow', 'blur(', '@import', '@font-face', 'url('):
        assert banned not in css
    sizes = re.findall(r'font-size:([^;}]+)', own)
    assert sizes and all(s.startswith('var(--laa-') for s in sizes)
    used = {s for s in sizes if 'figure' not in s}
    assert used == {'var(--laa-display)', 'var(--laa-heading)', 'var(--laa-body)', 'var(--laa-label)'}  # four sizes
    assert all(f.startswith('var(--laa-font-') for f in re.findall(r'font-family:([^;}]+)', own))
    assert set(re.findall(r'border-radius:([^;}]+)', own)) <= {'50%', '4px'}  # dots, and the old zone node
    # The body keeps its own margins in the Journal app (which adds padding to <body>) and on the web.
    assert 'body{margin:0;padding:0;' in own


def test_the_page_has_no_red_or_green_and_no_filled_tags():
    page, *_ = edition()
    for gone in ('--up', '--down', '--above', '--below', 'band-mark', '#fff'):
        assert gone not in page
    assert '.pool{display:inline-block;font-size:var(--laa-label)' in dash.CSS  # BSL/SSL as a small label


# ------------------------------------------------------------------ verdicts

def test_the_overall_verdict_is_the_one_teal_face_and_comes_first():
    page, *_ = edition()
    tiles = page[page.index('class="tiles"'):page.index('class="conclusion"')]
    assert tiles.count('tile-main') == 1
    # One teal face; every other teal fill is a mark of a few pixels (points, the trunk, lines, the current dot).
    teal = re.findall(r'([^{}]+)\{[^{}]*background:var\(--laa-accent\)', dash.CSS)
    faces = [s.strip() for s in teal if not re.search(r'votes|lv-node|lv-gauge|band-edge|icon', s)]
    assert faces == ['.tile-main']
    main = re.search(r'<div class="tile tile-main[^"]*"><span class="tile-label">([^<]+)</span>', tiles)
    assert main and main.group(1) == '総合' and tiles.index('tile-main') < tiles.index('tile-rest')
    assert '<span class="tile-conf">信頼度 低</span>' in tiles[:tiles.index('tile-rest')]
    assert [m for m in re.findall(r'<span class="tile-label">([^<]+)</span>', tiles)] == ['総合', '長期', '短期']


def test_without_a_total_tile_the_last_one_leads_and_carries_the_confidence():
    figure = {'verdicts': [{'label': 'A', 'symbol': '▲', 'direction': '上向き', 'tone': 'positive'},
                           {'label': 'B', 'symbol': '—', 'direction': '中立', 'tone': 'neutral'}], 'items': []}
    html = dash.tiles_html(figure, {'text': '信頼度 中'})
    assert re.search(r'tile-main[^"]*"><span class="tile-label">B<', html) and '<span class="tile-conf">信頼度 中</span>' in html


def test_each_horizon_shows_its_rows_symbols_as_points_on_one_rule():
    _, _, figures, _ = edition()
    board = figures[0]
    for tile in board['verdicts']:
        if not tile.get('group'):
            continue
        rows = [i for i in board['items'] if i['group'] == tile['group']]
        html = dash.votes_html(rows)
        marks = re.findall(r'<i class="v-(\w)" style="--x:([\d.]+)%;--k:(\d+)"></i>', html)
        places = [dash.VOTE_PLACE[i['symbol']] for i in rows if i['symbol'] in dash.VOTE_PLACE]
        assert [(cls, float(x)) for cls, x, _ in marks] == [(c, 50 + p * 22.5) for p, c in places]
        for place in {p for p, _ in places}:  # points of one place stack from 0
            ks = [int(k) for (cls, x, k) in marks if float(x) == 50 + place * 22.5]
            assert ks == list(range(len(ks)))
        assert html.startswith('<span class="votes" aria-hidden="true"') and visible(html) == ''  # a shape only
    assert dash.votes_html([{'symbol': '？'}]) == '' and dash.votes_html([]) == ''


# ------------------------------------------------------------------ lead order

def test_the_lead_reads_verdicts_then_reasons_then_the_next_release_and_levels():
    page, summary, figures, _ = edition()
    lead = page[page.index('<section class="lead"'):page.index('</section>')]
    assert lead.index('class="tiles"') < lead.index('class="conclusion"') < lead.index('class="facts"')
    assert '<section' not in lead[1:]  # the job puts its no-figures note before the first </section>


# ------------------------------------------------------------------ freshness of a material

FRESH = '2営業日前の値（10/12は米国の祝日で休場）'


def with_fresh(text=FRESH):
    _, summary, figures, full = edition()
    item = figures[0]['items'][1]
    line = item['source_quote']
    quoted = f'{line}｜鮮度: {FRESH}'
    full = full.replace(line, quoted, 1)
    item.update(source_quote=quoted, fresh=text)
    return summary, figures, full


def test_a_value_not_of_the_day_shows_its_quoted_words_with_the_open_circle():
    summary, figures, full = with_fresh()
    dash.validate(summary, figures, full)
    page, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    assert f'<span class="fresh"><span class="mark" aria-hidden="true">○</span>{FRESH}</span>' in page
    assert page.count('class="fresh"') == 1 and page.count(' is-fresh') == 1
    row = page[page.rindex('<tr', 0, page.index('class="fresh"')):page.index('</tr>', page.index('class="fresh"'))]
    assert row.index('class="asof"') < row.index('class="fresh"')  # beside the date
    plain, *_ = edition()
    assert 'class="fresh"' not in plain and 'is-fresh' not in plain


def test_a_fresh_note_that_its_line_does_not_say_is_rejected():
    summary, figures, full = with_fresh('3営業日前の値')
    with pytest.raises(dash.DashboardError, match=r'bias\[1\]: "3営業日前の値" is not in its source line'):
        dash.validate(summary, figures, full)


# ------------------------------------------------------------------ liquidity map

def prices(html):
    return [float(p.replace(',', '')) for p in re.findall(r'<span class="lv-price">([\d,.]+)</span>', html)]


def test_outside_rows_sit_above_and_below_the_map_so_every_price_reads_high_to_low():
    page, _, figures, _ = edition()
    liq = page[page.index('id="liquidity"'):page.index('<details', page.index('id="liquidity"'))]
    shown = liq[liq.index('<ul class="ladder">'):liq.index('class="off-wrap')]
    above = liq[liq.index('off-above'):liq.index('off-below')]
    below = liq[liq.index('off-below'):]
    assert '個人ロングの平均建値' in above and '個人ショートの平均建値' in below
    current = next(i['position'] for i in figures[1]['items'] if i['kind'] == 'current')
    assert min(prices(above)) > current > max(prices(below))
    on_screen = prices(above) + prices(shown) + prices(below)  # the order CSS draws them in
    assert on_screen == sorted(on_screen, reverse=True)
    css = dash.CSS
    assert '.liq-map>.ladder{order:2}.off-above{order:1' in css and '.off-below{order:3' in css
    assert '<span class="arrow" aria-hidden="true">↑</span>表示範囲の外' in above
    assert '<span class="arrow" aria-hidden="true">↓</span>表示範囲の外' in below


def test_each_rows_distance_line_grows_with_its_distance_and_far_rows_end_in_a_ring():
    _, _, figures, _ = edition()
    html = dash.liquidity_section(figures[1])
    rows = re.findall(r'<span class="lv-dist">差 ([+-][\d,.]+)</span><span class="lv-gauge( over)?" aria-hidden="true" '
                      r'style="--d:([\d.]+)%">', html)
    assert len(rows) == sum(1 for i in figures[1]['items'] if i['kind'] != 'current')
    pairs = sorted((abs(float(d.replace(',', ''))), float(w), bool(o)) for d, o, w in rows)
    widths = [w for _, w, _ in pairs]
    assert widths == sorted(widths) and max(widths) == 100.0
    assert all(w == 100.0 for _, w, over in pairs if over)
    offscale = {i['distance_text'] for i in figures[1]['items'] if i.get('offscale')}
    assert all(f'差 {d}' in offscale for d, o, _ in rows if o)
    assert '<b style="--b:' in html  # the band's edge on each line
    assert visible(''.join(re.findall(r'<span class="lv-gauge[^>]*>.*?</span>', html))) == ''  # shapes only


def test_the_band_is_the_thick_teal_part_of_the_trunk_between_its_two_edges():
    _, _, figures, _ = edition()
    html = dash.liquidity_section(figures[1])
    shown = html[html.index('<ul class="ladder">'):html.index('</ul>')]
    upper, lower = shown.index('class="band-edge side-above"'), shown.index('class="band-edge side-below"')
    inside = re.findall(r'<li class="lv [^"]*"', shown[upper:lower])
    assert inside and all(' in-band' in c or ' part-band' in c for c in inside)
    outside = re.findall(r'<li class="lv [^"]*"', shown[:upper] + shown[lower:])
    assert all(' in-band' not in c for c in outside)


def test_a_band_above_every_shown_row_still_gets_both_edges():
    item = lambda name, price: {'kind': 'level', 'label': name, 'price_text': f'{price:.2f}', 'position': price,
                                'state': 'resting', 'state_word': '未到達', 'icon': '●', 'distance_text': '', 'pool': ''}
    figure = {'type': 'liquidity_map', 'title': 't', 'legend': '', 'band': {'lower': 150.0, 'upper': 160.0, 'label': '帯'},
              'items': [item('a', 120.0), item('b', 110.0)]}
    html = dash.liquidity_section(figure)
    assert html.index('>上限 160.00<') < html.index('>下限 150.00<') < html.index('>a<')


# ------------------------------------------------------------------ in a real browser

def browser_metrics(html, width):
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
            page.set_content(html)
            page.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = true)')
            return page.evaluate('''() => {
              const texts = [...document.querySelectorAll('main *')].filter(n => n.childElementCount === 0
                && n.textContent.trim() && getComputedStyle(n).display !== 'none');
              const top = s => document.querySelector(s).getBoundingClientRect().top;
              return {overflow: document.documentElement.scrollWidth > innerWidth + 1,
                      small: texts.filter(n => parseFloat(getComputedStyle(n).fontSize) < 12).length,
                      taps: [...document.querySelectorAll('summary, #source-markdown')]
                              .filter(n => n.getBoundingClientRect().height < 44).length,
                      order: [top('.off-above'), top('.liq-map > .ladder'), top('.off-below')],
                      fonts: [...new Set(texts.map(n => getComputedStyle(n).fontSize))]};
            }''')
        finally:
            browser.close()
    finally:
        p.stop()


@pytest.mark.parametrize('width', [1365, 390])
def test_the_page_fits_reads_and_orders_the_map_in_a_real_browser(width):
    page, *_ = edition()
    m = browser_metrics(page, width)
    assert not m['overflow'] and m['small'] == 0 and m['taps'] == 0
    assert m['order'] == sorted(m['order'])  # outside-above, the shown range, outside-below
