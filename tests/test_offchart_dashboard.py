"""One-page HTML (scripts/offchart_dashboard.py): every shown string is checked against its Markdown line."""
import base64
from copy import deepcopy
import hashlib
import json
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
    assert len(checked) == sum(len(f['items']) for f in figures)
    assert any(f['items'][int(w.split('[')[1][:-1])]['kind'] == 'retail' for f in figures if f['id'] == 'liquidity'
               for w in checked if w.startswith('liquidity['))
    assert checked[0] == 'bias[0]' and checked[-1].startswith('calendar[')


@pytest.mark.parametrize('mutate,message', [
    (lambda s, f: f[0]['items'][0].update(display='2.88%'), r'bias\[0\]: "2.88%" is not in its source line'),
    (lambda s, f: f[0]['items'][0].update(source_quote='どこにもない行'), r'source_quote is missing'),
    (lambda s, f: next(i for i in f[0]['items'] if i.get('viz', {}).get('type') == 'spark')['viz'].pop('path'),
     r'sparkline without its source series'),
    # A schemaVersion 5 diagram is accepted only with the labels and numbers of its own line.
    (lambda s, f: f[0]['items'][4].update(viz={'type': 'bars', 'items': [{'label': '6月', 'value': 44.6}]}),
     r'bias\[4\]\.viz: "6月" is not in its source line'),
    (lambda s, f: f[0]['items'][4].update(viz={'type': 'delta', 'from': 1.5, 'to': 9876.5}),
     r'bias\[4\]\.viz: values'),
    (lambda s, f: next(i for i in f[1]['items'] if i['kind'] == 'retail').update(viz={'type': 'pie'}),
     r'unknown diagram type pie'),
    (lambda s, f: f[0]['items'][0]['viz'].update(type='pie'), r'unknown diagram type pie'),
    (lambda s, f: f[1]['cautions'].append({'text': '足の欠落', 'source_quote': 'どこにもない行'}), r'cautions\[0\]: source_quote'),
    (lambda s, f: next(i for i in f[1]['items'] if i['kind'] == 'retail').update(state_word='個人の56%がロング・平均で含み益 +5.3%'),
     r'"個人の56%がロング・平均で含み益 \+5\.3%" is not in its source line'),
    (lambda s, f: f[1].update(notices=[{'text': '前回分を表示', 'source_quote': 'どこにもない行'}]), r'notices\[0\]: source_quote'),
    (lambda s, f: f[1].update(missing=[{'text': '個人の建玉', 'source_quote': 'どこにもない行'}]), r'missing\[0\]: source_quote'),
    (lambda s, f: f[1].update(band={**f[1]['band'], 'label': '別の帯'}), r'band is not in the Markdown'),
    (lambda s, f: f[2].update(legend='独自の説明'), r'legend is not in the Markdown'),
    (lambda s, f: f[0]['rules'][0].update(rule='新しい規則'), r'rule text is not in the Markdown'),
    (lambda s, f: f[0]['verdicts'][0].update(direction='上向き・強'), r'verdicts\[0\]'),
    (lambda s, f: s['conclusion'].update(text=s['conclusion']['text'] + ' 4,321'), r'conclusion: numbers'),
    (lambda s, f: s['chips'][0].update(text='次のTier A指標: CPI（あと99時間）'), r'chips\[0\]'),
    (lambda s, f: s['confidence'].update(text='信頼度 高'), r'confidence: the word'),
    (lambda s, f: s['confidence'].update(text='信頼度 Low'), r'confidence: the word'),
    (lambda s, f: s['confidence'].update(source_quote='信頼度: High'), r'confidence: source_quote is missing'),
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
    assert '<p class="conclusion">XAUUSD：' in page and 'class="conf"' not in page  # an older-format line, no badge
    assert '<span class="tile-conf">信頼度 低</span>' in page
    assert page.index('id="bias"') < page.index('id="liquidity"') < page.index('id="calendar"')
    assert '<colgroup><col class="c-lbl">' in page and 'class="cell-text" colspan="2"' in page
    assert 'class="lv kind-level state-resting side-above in-band is-target"' in page and '◎ 目的地の仮説' in page
    assert 'class="jump"' not in page and 'href="#bias"' not in page and 'ページ内の移動' not in page
    assert '表示範囲の外' in page and 'class="band-edge side-above">想定変動 ±1σ（1日・±60.0ドル） 上限 4,237.47' in page
    assert 'class="prox"' not in page and 'band-note' not in page and '<th scope="col">推移</th>' not in page
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
    figure = {'type': 'liquidity_map', 'title': 't', 'legend': '', 'band': {'lower': 95.0, 'upper': 105.0, 'label': '想定変動 ±1σ'},
              'items': [{'kind': 'level', 'label': name, 'price_text': f'{price:.2f}', 'position': price, 'state': 'resting',
                         'state_word': '未到達', 'icon': '●', 'distance_text': '', 'pool': 'BSL'}
                        for name, price in (('上', 110.0), ('中', 100.0), ('下', 90.0))]}
    html = dash.liquidity_section(figure)
    order = [html.index(x) for x in ('>上<', '想定変動 ±1σ 上限 105.00', '>中<', '想定変動 ±1σ 下限 95.00', '>下<')]
    assert order == sorted(order) and html.count(' in-band') == 1


def test_the_trend_line_is_a_decorative_svg_and_other_diagrams_are_gone():
    svg = dash.viz_svg({'type': 'spark', 'values': [1, 2, 3]}, 'negative')
    assert svg.startswith('<svg class="viz"') and 'aria-hidden="true"' in svg
    for gone in ({'type': 'delta', 'from': 1, 'to': 2}, {'type': 'bars', 'items': [{'label': 'x', 'value': 1.0}]},
                 {'type': 'probs', 'segments': []}, {'type': 'track', 'value': 43.9}, {'type': 'gap', 'value': -0.07},
                 {'type': 'split', 'left': 56.0, 'right': 44.0}, {'type': 'votes', 'up': 1}):
        assert dash.viz_svg(gone, 'neutral') == ''


def test_unknown_or_thin_diagrams_render_nothing():
    assert dash.viz_svg({'type': 'spark', 'values': [1, 2]}, 'neutral') == ''
    assert dash.viz_svg({'type': 'pie'}, 'neutral') == '' and dash.viz_svg(None, 'neutral') == ''


def test_the_board_has_no_trend_column_and_the_line_sits_under_its_value():
    source, summary, figures = edition()
    page, _ = dash.build(source, summary, figures, title='t', source_name='s.md')
    board_html = page[page.index('id="bias"'):page.index('id="liquidity"')]
    assert board_html.count('<th scope="col">') == 8 and 'cell-viz' not in board_html  # two horizon tables
    assert board_html.count('<svg class="viz"') == 2  # real rate and DXY over 20 observations
    assert '<td class="cell-now"><span class="now-text">2.87%</span><svg class="viz"' in board_html
    # A change without a value (「—」) is not drawn as a lone dash.
    assert '<span class="chg">—</span>' not in board_html


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
    assert '<span class="tile-origin">集計と異なる</span>' in tiles and f'<p class="tile-reason">{reason}</p>' in tiles
    assert '原稿の判断' not in tiles
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


def test_liquidity_cautions_sit_above_the_ladder_and_far_entries_below_it():
    data = sample()
    data['xau_levels']['cautions'] = ['当日の1時間足に2本の欠落。その時間の値動きは状態に入っていない']
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    liq = page[page.index('id="liquidity"'):page.index('id="calendar"')]
    caution = '<p class="caution" role="note">当日の1時間足に2本の欠落。その時間の値動きは状態に入っていない</p>'
    assert liq.index(caution) < liq.index('class="ladder"') < liq.index('<details')
    main_view = liq[:liq.index('<details')]
    assert main_view.index('class="ladder"') < main_view.index('class="offscale"') < main_view.index('個人ロングの平均建値')


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


def test_value_parts_do_not_break_inside_and_keep_the_quoted_text():
    result = bundle.observations(sample(), NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    page, _ = dash.build(source, summary, result['figures'], title='t', source_name='s.md')
    cell = '<span class="nw">ロング 56.0%</span>・<span class="nw">ショート 44.0%</span>'
    assert cell in page and re.sub(r'<[^>]+>', '', cell) == 'ロング 56.0%・ショート 44.0%'


# ------------------------------------------------------------------ review r10: series of one release time

def series_edition():
    """Three CPI series at one time: two above forecast (headwind) and one as forecast."""
    data = sample()
    cpi = lambda name, actual, forecast: {
        'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States', 'indicator': name,
        'actual': actual, 'previous': '0.3%', 'forecast': forecast, 'released_at_jst': '2026-10-13T21:30:00+09:00',
        'forecast_provenance': '発表前記録（2026-10-12 09:10 取得・Investing.com）'}
    data['macro_surprises'] = [cpi('Core CPI (MoM) (Sep)', '0.4%', '0.3%'), cpi('CPI (MoM) (Sep)', '0.3%', '0.3%'),
                               cpi('CPI (YoY) (Sep)', '3.1%', '3.0%')]
    result = bundle.observations(data, NOW, 'daily', SOURCE)
    source = SOURCE + result['appendix']
    summary = bundle.make_summary(source, 'daily', result['asOf'], result['lead'])
    bundle.check_bindings(data, result, result['figures'])
    return source, summary, json.loads(json.dumps(result['figures'], ensure_ascii=False))


def test_each_series_of_a_release_is_its_own_line_and_as_forecast_is_neutral():
    source, summary, figures = series_edition()
    row = next(i for i in figures[0]['items'] if i['label'].startswith('指標の結果'))
    assert row['change'] == 'コアCPI（前月比・9月） +0.1pp／CPI（前月比・9月） +0pp／CPI（前年比・9月） +0.1pp'
    assert [(x['text'], x['tone']) for x in row['series']] == [
        ('コアCPI（前月比・9月） +0.1pp', 'negative'), ('CPI（前月比・9月） +0pp', 'neutral'),
        ('CPI（前年比・9月） +0.1pp', 'negative')]
    assert row['tone'] == 'negative'
    dash.validate(summary, figures, source)
    page, _ = dash.build(source, summary, figures, title='t', source_name='s.md')
    assert ('<span class="chg chg-series"><span class="ser s-negative">コアCPI（前月比・9月） +0.1pp</span>'
            '<span class="vh">／</span><span class="ser s-neutral">CPI（前月比・9月） +0pp</span>'
            '<span class="vh">／</span><span class="ser s-negative">CPI（前年比・9月） +0.1pp</span></span>') in page
    css = dash.CSS
    assert '.chg-series .ser{display:block}' in css and '.board .chg .s-neutral{color:var(--flat)}' in css
    # The browser check still finds the whole change text (the separator is in the text, out of sight).
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    assert [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text] == []


@pytest.mark.parametrize('change', [
    lambda r: r['series'][1].update(text='CPI（前月比・9月） +0.2pp'),
    lambda r: r['series'][1].update(tone='positive-ish'),
    lambda r: r['series'].pop(),
    lambda r: r.update(series='CPI')])
def test_series_parts_must_be_the_rows_change_text(change):
    source, summary, figures = series_edition()
    change(next(i for i in figures[0]['items'] if i['label'].startswith('指標の結果')))
    with pytest.raises(dash.DashboardError, match=r'series: parts differ'):
        dash.validate(summary, figures, source)
