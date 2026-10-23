"""Readability round r11 (owner, 2026-10-15): fewer words, more space, clearer structure, nothing lost.

Tiles without 「原稿の判断」 and with the confidence word in 総合; no in-page links; the conclusion as labelled
rows (理由・反対材料) when written in the new format; the lead facts as a key-value list whose pieces are parts of
the chip text; short section titles; horizon blocks on the bias board; side spines and a sticky current row on the
liquidity map; one notice line for kept retail clusters; plain wording for the retail average entries.
"""
from copy import deepcopy
import re

import pytest

from scripts import offchart_board as board
from scripts import offchart_dashboard as dash
from scripts import report_acceptance as acceptance
from scripts import report_bundle as bundle
from tests.test_offchart_board import NOW, ORDER_BOOK, SOURCE, sample

NEW_LINE = ('XAUUSD：中立。理由：米金利の上昇と個人の買い偏りが重く、上値を抑える。'
            '反対材料：中銀の買い（6〜8月+154.2t）が下値を支える。')


def page_of(source=SOURCE, data=None):
    data = data or sample()
    result = bundle.observations(data, NOW, 'daily', source)
    full = source + result['appendix']
    summary = bundle.make_summary(full, 'daily', result['asOf'], result['lead'])
    summary['limitations'] = [{'title': '制約', 'text': n, 'source_quote': n} for n in result['limitations']]
    figures = result['figures']
    dash.validate(summary, figures, full)
    page, _ = dash.build(full, summary, figures, title='t', source_name='s.md')
    return page, summary, figures, full


def visible(page):
    return acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))


# ------------------------------------------------------------------ conclusion

def test_a_new_format_conclusion_is_two_labelled_rows_without_the_opening_verdict():
    assert dash.conclusion_parts(NEW_LINE) == [
        ('理由', '米金利の上昇と個人の買い偏りが重く、上値を抑える。'),
        ('反対材料', '中銀の買い（6〜8月+154.2t）が下値を支える。')]
    page, summary, figures, _ = page_of(SOURCE.replace('XAUUSD：長期は下向き・弱、短期は上向き・弱で総合は中立。', NEW_LINE))
    lead = page[page.index('class="lead"'):page.index('id="bias"')]
    assert '<dl class="conclusion cl-split">' in lead and '<dt>理由</dt>' in lead and '<dt>反対材料</dt>' in lead
    assert 'XAUUSD：中立。' not in lead  # the tiles above show the verdict
    texts = acceptance.shown_texts(summary, figures)
    assert '米金利の上昇と個人の買い偏りが重く、上値を抑える。' in texts and summary['conclusion']['text'] not in texts
    assert [t for t in texts if acceptance.compact(t) not in visible(page)] == []


def test_an_older_conclusion_stays_one_paragraph():
    assert dash.conclusion_parts('XAUUSD：長期は下向き・弱、短期は上向き・弱で総合は中立。') is None
    assert dash.conclusion_parts('XAUUSD：下向き・弱。反対材料：中銀。理由：金利。') is None  # wrong order
    page, summary, figures, _ = page_of()
    assert '<p class="conclusion">XAUUSD：長期は下向き・弱、短期は上向き・弱で総合は中立。</p>' in page
    assert summary['conclusion']['text'] in acceptance.shown_texts(summary, figures)


def test_an_opening_other_than_the_verdict_word_is_kept_as_its_own_row():
    parts = dash.conclusion_parts('XAUUSD：中立。CPI後は様子見。理由：金利高。反対材料：中銀の買い。')
    assert parts == [('', 'XAUUSD：中立。CPI後は様子見。'), ('理由', '金利高。'), ('反対材料', '中銀の買い。')]


# ------------------------------------------------------------------ tiles, links

def test_tiles_drop_the_report_origin_line_and_the_total_tile_carries_the_confidence():
    page, *_ = page_of()
    tiles = page[page.index('class="tiles"'):page.index('class="conclusion"')]
    assert '原稿の判断' not in tiles and 'tile-origin' not in tiles
    assert tiles.count('<span class="tile-conf">信頼度 低</span>') == 1
    total = tiles[tiles.index('>総合<'):]
    assert '<span class="tile-conf">信頼度 低</span>' in total
    assert 'class="conf"' not in page  # no badge in the conclusion box


def test_no_in_page_links_and_no_kickers():
    page, *_ = page_of()
    assert 'class="jump"' not in page and 'href="#' not in page and 'class="kicker"' not in page
    assert '<h2 id="bias-title">バイアス</h2>' in page and '<h2 id="liq-title">流動性（Liquidity）</h2>' in page
    assert '<h2 id="ev-title">米国の重要指標</h2>' in page and '<th scope="col">時刻（JST）</th>' in page


def test_bias_board_is_one_table_per_horizon_with_its_heading_and_tally():
    page, _, figures, _ = page_of()
    section = page[page.index('id="bias"'):page.index('id="liquidity"')]
    assert section.count('<div class="horizon">') == 2 and section.count('<table class="board"') == 2
    assert '<span class="hz-name">長期（数週間〜）</span><span class="grp-tally">規則の集計' in section
    assert ('<th scope="col">材料</th><th scope="col">現在</th><th scope="col">比較</th><th scope="col">向き</th>'
            in section)
    assert '右端が金への向き' not in section and 'class="sub"' not in section
    assert '右端の「向き」列が金（XAUUSD）への向きです' in figures[0]['legend'] and '▲▼は閾値以上' in figures[0]['legend']
    assert '.horizon+.horizon{margin-top:40px}' in dash.CSS and '.horizon+.horizon{margin-top:28px}' in dash.CSS


# ------------------------------------------------------------------ lead facts

@pytest.mark.parametrize('text,label,lines', [
    ('次の米指標: 小売売上高・PPI（Tier B） 10/15（木）21:30 JST', '次の米指標',
     [[('10/15（木）21:30 JST', True), ('小売売上高・PPI（Tier B）', False)]]),
    ('直近の未到達: 上 コアCPI・CPI 発表15分足高値（10/14 21:30） 4,172.83（+81.89）／下 前週安値（10/05週） 4,061.73（-29.21）',
     '未到達の水準',
     [[('上', False), ('4,172.83', True), ('（+81.89）', False), ('コアCPI・CPI 発表15分足高値（10/14 21:30）', False)],
      [('下', False), ('4,061.73', True), ('（-29.21）', False), ('前週安値（10/05週）', False)]]),
    ('直近の未到達: 上 前日高値（10/13） 4,200.45（+98.35）・距離は参考価格から', '未到達の水準',
     [[('上', False), ('4,200.45', True), ('（+98.35）', False), ('前日高値（10/13）', False)], [('距離は参考価格から', False)]]),
    ('停止中: CPI（10/14（水）21:30 JST 発表）〜22:30', '停止中', [[('CPI（10/14（水）21:30 JST 発表）〜22:30', False)]]),
    ('米指標の結果: 10/07〜10/11は記録からの復元のみ', '米指標の結果', [[('10/07〜10/11は記録からの復元のみ', False)]]),
    ('米指標の予定を確認できない（カレンダー取得失敗）', '注意', [[('米指標の予定を確認できない（カレンダー取得失敗）', False)]])])
def test_each_lead_fact_is_split_into_parts_of_its_chip(text, label, lines):
    assert dash.chip_parts(text) == (label, lines)
    assert all(piece in text for line in lines for piece, _ in line)


def test_the_lead_list_replaces_the_pills_and_validate_checks_its_pieces():
    page, summary, figures, full = page_of()
    assert '<ul class="chips">' not in page and '<dl class="facts">' in page
    assert '<dt>未到達の水準</dt>' in page
    nearest = next(c['text'] for c in summary['chips'] if c['text'].startswith('直近の未到達'))
    for piece in dash.chip_texts(nearest):
        assert piece in nearest and acceptance.compact(piece) in visible(page)
    broken = deepcopy(summary)
    broken['chips'][0].update(text='次の米指標: どこにもない', source_quote='どこにもない')
    with pytest.raises(dash.DashboardError, match=r'chips\[0\]'):
        dash.validate(broken, figures, full)


# ------------------------------------------------------------------ liquidity map

def test_rows_carry_a_side_spine_above_or_below_the_current_price():
    item = lambda kind, text, pos: {'kind': kind, 'label': text, 'price_text': text, 'position': pos, 'state': 's',
                                    'state_word': '', 'icon': '', 'distance_text': '', 'pool': ''}
    figure = {'type': 'liquidity_map', 'title': 't', 'legend': '', 'band': None,
              'items': [item('level', '120.00', 120.0), item('zone', '95.00〜105.00', 100.0), item('current', '100.00', 100.0),
                        item('level', '80.00', 80.0), {**item('level', '300.00', 300.0), 'offscale': True}]}
    html = dash.liquidity_section(figure)
    classes = re.findall(r'<li class="lv kind-(\w+) state-s([^"]*)"', html)
    assert classes == [('level', ' side-above'), ('zone', ' side-at'), ('current', ''), ('level', ' side-below'),
                       ('level', ' side-above')]  # the last one: the 表示範囲の外 list
    css = dash.CSS
    assert '.lv.side-above{--trunk:var(--above)}' in css and '.lv.side-below{--trunk:var(--below)}' in css  # r12: the tree's trunk


def test_the_current_row_is_sticky_and_no_ancestor_clips_it():
    css = dash.CSS
    rule = css[css.index('.ladder>.kind-current{'):css.index('}', css.index('.ladder>.kind-current{'))]
    assert 'position:sticky' in rule and 'top:0' in rule and 'bottom:0' in rule and 'z-index' in rule
    for selector in ('main.dash{', '.data-figure{', '.ladder{', '.lead{'):
        block = css[css.index(selector):css.index('}', css.index(selector))]
        assert 'overflow:' not in block and 'overflow-x' not in block and 'overflow-y' not in block
    page, *_ = page_of()
    assert page.count('class="lv kind-current') == 1






def test_average_entries_say_who_holds_them_and_the_average_pl():
    figure = board.liquidity_figure(sample(), NOW, 'daily', SOURCE)['figure']
    words = {i['label']: i['state_word'] for i in figure['items'] if i['kind'] == 'retail'}
    assert words == {'個人ロングの平均建値': '個人56%ロング',  # r12: the owner's shorter wording
                     '個人ショートの平均建値': '個人44%ショート'}
    data = sample()
    data['retail_sentiment']['XAUUSD']['avg_long_entry'] = 4000.0
    words = {i['label']: i['state_word'] for i in board.liquidity_figure(data, NOW, 'daily', SOURCE)['figure']['items']
             if i['kind'] == 'retail'}
    assert words['個人ロングの平均建値'] == '個人56%ロング'


def test_the_liquidity_subtitle_keeps_only_the_as_of_hour():
    figure = board.liquidity_figure(sample(), NOW, 'daily', SOURCE)['figure']
    assert not figure['subtitle'].startswith('上ほど高い価格') and figure['subtitle'].startswith('状態は')
    assert 'kicker' not in figure and figure['title'] == '流動性（Liquidity）'
