"""One-page HTML (scripts/offchart_dashboard.py): every shown string is checked against its Markdown line."""
import base64
from copy import deepcopy
import hashlib
import re

import pytest

from scripts import offchart_dashboard as dash
from scripts import report_acceptance as acceptance
from scripts import report_bundle as bundle
from tests.test_offchart_board import NOW, SOURCE, sample


def edition():
    data = sample()
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    summary['limitations'] = [{'title': '制約', 'text': '注文板の集計。', 'source_quote': '判断から除外する材料：注文板の集計。'}]
    source = source.replace('XAUUSD：', '判断から除外する材料：注文板の集計。\nXAUUSD：', 1)
    return source, summary, result['figures']


def test_validate_accepts_a_bundled_edition_and_lists_each_verified_item():
    source, summary, figures = edition()
    checked = dash.validate(summary, figures, source)
    assert len(checked) == sum(len(f['items']) + len(f.get('aux') or []) for f in figures)
    assert 'liquidity.aux[0]' in checked
    assert checked[0] == 'bias[0]' and checked[-1].startswith('calendar[')


@pytest.mark.parametrize('mutate,message', [
    (lambda s, f: f[0]['items'][0].update(display='2.88%'), r'bias\[0\]: "2.88%" is not in its source line'),
    (lambda s, f: f[0]['items'][0].update(source_quote='どこにもない行'), r'source_quote is missing'),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'bars')['viz']['items'][0].update(value=99.9),
     r"bias\[3\]\.viz: values \['99\.9'\]"),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'spark')['viz'].pop('path'),
     r'sparkline without its source series'),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'probs')['viz']['segments'][0].update(value=70.0),
     r"viz: values \['70'\]"),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'probs')['viz']['segments'][0].update(label='4.25-4.50'),
     r'"4.25-4.50" is not in its source line'),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'gap')['viz'].update(value=-0.3),
     r"viz: values \['0\.3'\]"),
    (lambda s, f: f[0]['items'][0]['viz'].update(type='pie'), r'unknown diagram type pie'),
    (lambda s, f: f[1]['cautions'].append({'text': '足の欠落', 'source_quote': 'どこにもない行'}), r'cautions\[0\]: source_quote'),
    (lambda s, f: f[1]['aux'][0].update(price_text='4,000.00'), r'liquidity.aux\[0\]'),
    (lambda s, f: f[1].update(band={**f[1]['band'], 'label': '別の帯'}), r'band is not in the Markdown'),
    (lambda s, f: f[2].update(legend='独自の説明'), r'legend is not in the Markdown'),
    (lambda s, f: f[0]['rules'][0].update(rule='新しい規則'), r'rule text is not in the Markdown'),
    (lambda s, f: f[0]['verdicts'][0].update(direction='上向き・強'), r'verdicts\[0\]'),
    (lambda s, f: s['conclusion'].update(text=s['conclusion']['text'] + ' 4,321'), r'conclusion: numbers'),
    (lambda s, f: s['chips'][0].update(text='次のTier A指標: CPI（あと99時間）'), r'chips\[0\]'),
    (lambda s, f: f[2].update(type='pie'), r'Unknown figure type')])
def test_validate_rejects_strings_or_numbers_the_markdown_does_not_contain(mutate, message):
    source, summary, figures = edition()
    summary, figures = deepcopy(summary), deepcopy(figures)
    mutate(summary, figures)
    with pytest.raises(dash.DashboardError, match=message):
        dash.validate(summary, figures, source)


def test_build_renders_one_page_with_collapsed_explanations_and_the_markdown_download():
    source, summary, figures = edition()
    page, audit = dash.build(source, summary, figures, title='XAUUSD Daily 2026-10-13', source_name='daily.md')
    assert '<script' not in page and re.findall(r'<details[^>]*\bopen\b', page) == []
    assert page.count('<details') == 4  # bias rules, liquidity legend, event legend, limitations
    assert '制約・注意（1件・開いて確認）' in page and '判定ルールと出典（開いて確認）' in page
    href = re.search(r'id="source-markdown" download="daily.md" href="data:text/markdown;charset=utf-8;base64,([^"]+)"', page)
    assert base64.b64decode(href.group(1)).decode('utf-8') == source
    assert page.index('class="tiles"') < page.index('class="conclusion"') < page.index('id="bias"')
    assert page.index('id="bias"') < page.index('id="liquidity"') < page.index('id="calendar"')
    assert '<colgroup><col class="c-lbl">' in page and 'class="cell-text" colspan="2"' in page
    assert 'class="lv kind-level state-resting in-band is-target"' in page and '◎ 目的地の仮説' in page
    assert '<ul class="jump" aria-label="ページ内の移動"><li><a href="#bias">バイアス</a></li>' in page
    assert '表示範囲の外' in page and 'class="band-edge">想定変動 上限 4,237.47' in page
    assert '全文' not in page and '条件と取り消し' not in page and 'キリ' not in page
    assert audit == {'source_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                     'source_bytes': len(source.encode('utf-8')), 'figure_count': 3,
                     'figure_items_verified': dash.validate(summary, figures, source), 'numeric_check': 'pass',
                     'renderer': 'scripts/offchart_dashboard.py'}


def test_page_shows_every_text_the_browser_check_expects():
    source, summary, figures = edition()
    page, _ = dash.build(source, summary, figures, title='t', source_name='s.md')
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    missing = [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text]
    assert missing == []


def test_band_edges_bracket_the_levels_inside_the_band():
    figure = {'type': 'liquidity_map', 'title': 't', 'legend': '', 'band': {'lower': 95.0, 'upper': 105.0, 'label': '帯'},
              'items': [{'kind': 'level', 'label': name, 'price_text': f'{price:.2f}', 'position': price, 'state': 'resting',
                         'state_word': '未到達', 'icon': '●', 'distance_text': '', 'pool': 'BSL'}
                        for name, price in (('上', 110.0), ('中', 100.0), ('下', 90.0))]}
    html = dash.liquidity_section(figure)
    order = [html.index(x) for x in ('>上<', '想定変動 上限 105.00', '>中<', '想定変動 下限 95.00', '>下<')]
    assert order == sorted(order) and html.count(' in-band') == 1


@pytest.mark.parametrize('viz', [
    {'type': 'spark', 'values': [1, 2, 3]}, {'type': 'delta', 'from': 1, 'to': 2},
    {'type': 'probs', 'segments': [{'label': 'a', 'value': 80.5}, {'label': 'b', 'value': 19.5}], 'focus': 0},
    {'type': 'bars', 'items': [{'label': 'x', 'value': -1.0}, {'label': 'y', 'value': 2.0}]},
    {'type': 'track', 'value': 43.9}, {'type': 'split', 'left': 56.0, 'right': 44.0, 'track': 54.4},
    {'type': 'gap', 'value': -0.07, 'scale': 0.2}])
def test_each_mini_diagram_is_a_decorative_svg(viz):
    svg = dash.viz_svg(viz, 'negative')
    assert svg.startswith('<svg class="viz"') and 'aria-hidden="true"' in svg


def test_unknown_or_thin_diagrams_render_nothing():
    assert dash.viz_svg({'type': 'spark', 'values': [1, 2]}, 'neutral') == ''
    assert dash.viz_svg({'type': 'pie'}, 'neutral') == '' and dash.viz_svg(None, 'neutral') == ''


def test_a_mismatched_verdict_shows_its_rule_result_and_the_reports_reason():
    from tests.test_offchart_board import VERDICT_LINE
    reason = '集計と違う理由: 中銀の買いを実質金利より重く見た。'
    data = sample()
    source = SOURCE.replace(VERDICT_LINE, '時間軸別の向き: 長期 上向き・弱｜短期 上向き・弱｜総合 上向き・弱\n' + reason)
    result = bundle.observations(data, NOW, 'daily', source)
    source += result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    tiles = page[page.index('class="tiles"'):page.index('class="conclusion"')]
    assert tiles.count('is-mismatch') == 2 and tiles.count('tile-detail') == 2  # 長期 and 総合 differ from the tally
    assert '原稿の判断・集計と異なる' in tiles and f'<p class="tile-reason">{reason}</p>' in tiles
    # Without a mismatch the tiles carry no rule detail; the counts sit in the group headers.
    source, summary, figures = edition()
    page, _ = dash.build(source, summary, figures, title='t', source_name='s.md')
    assert 'class="tile-detail"' not in page and 'class="tile-reason"' not in page
    assert '<span class="grp-tally">規則の集計 下向き（弱）・要因4</span>' in page
    # A horizon with a missing material shows its rule result and what is missing.
    data = sample()
    del data['gold_cb']
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    tiles = page[page.index('class="tiles"'):page.index('class="conclusion"')]
    assert tiles.count('tile-detail') == 1 and '欠測: 中銀の金購入' in tiles


def test_liquidity_cautions_sit_above_the_ladder_and_entries_below_it():
    data = sample()
    data['xau_levels']['cautions'] = ['当日の1時間足に2本の欠落。その時間の値動きは状態に入っていない']
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    liq = page[page.index('id="liquidity"'):page.index('id="calendar"')]
    caution = '<p class="caution" role="note">当日の1時間足に2本の欠落。その時間の値動きは状態に入っていない</p>'
    assert liq.index(caution) < liq.index('class="ladder"') < liq.index('<details')
    assert '個人ロングの平均建値' not in liq[:liq.index('<details')]  # the retail average is folded away (F17)


def test_an_empty_release_window_renders_one_quoted_row():
    data = sample()
    data['economic_calendar']['events'] = [data['economic_calendar']['events'][3]]  # Existing Home Sales only
    data['macro_surprises'] = []
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    dash.validate(summary, result['figures'], source)
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    assert '<tr class="empty"><td colspan="7">該当なし（Tier A/Bの米指標なし）</td></tr>' in page
    assert '10/12からの結果と、Tier Aは10/20・Tier Bは10/15までの予定' in page
    # An empty calendar is a failed collection, not a quiet week: it is never reported as 「該当なし」.
    data['economic_calendar']['events'] = []
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    figure = next(f for f in result['figures'] if f['id'] == 'calendar')
    assert [i['label'] for i in figure['items']] == ['予定を確認できない（取得失敗）']
    assert 'カレンダー取得失敗のため予定を確認できない（Investing.com: 予定が0件）' in result['limitations']


def test_probabilities_are_drawn_as_shares_of_100_without_rescaling():
    svg = dash.viz_svg({'type': 'probs', 'segments': [{'label': 'a', 'value': 50.0}, {'label': 'b', 'value': 30.0}]}, 'neutral')
    widths = [float(w) for w in re.findall(r'class="seg(?:-focus)?"', svg) and re.findall(r'width="([\d.]+)" height="12" class="seg', svg)]
    assert widths == [pytest.approx((112 - 8) * 0.5 - 1), pytest.approx((112 - 8) * 0.3 - 1)]  # 20% of the track stays empty
    assert svg.count('seg-focus') == 1


def test_tally_votes_draw_one_square_per_series():
    svg = dash.viz_svg({'type': 'votes', 'up': 2, 'down': 1, 'flat': 0}, 'positive')
    assert svg.count('fill="var(--up)"') == 2 and svg.count('fill="var(--down)"') == 1


def test_value_parts_do_not_break_inside_and_keep_the_quoted_text():
    result = bundle.observations(sample(), NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    cell = '<span class="nw">ロング 56.0%</span>・<span class="nw">ショート 44.0%</span>'
    assert cell in page and re.sub(r'<[^>]+>', '', cell) == 'ロング 56.0%・ショート 44.0%'
