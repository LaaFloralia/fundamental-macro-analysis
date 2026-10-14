"""r12 (owner feedback on the 2026-10-15 preview): shorter liquidity rows drawn as a tree, 「中立」 alone, and no
release-candle line in the US table (the liquidity map lists the candle's high and low)."""
import re

from scripts import offchart_board as board
from scripts import offchart_dashboard as dash
from tests.test_offchart_board import NOW, SOURCE, sample, with_order_book


def liquidity(data=None):
    return board.liquidity_figure(data or with_order_book(sample()), NOW, 'daily', SOURCE)['figure']


def test_rows_carry_one_difference_and_no_first_bar_tier_or_fetch_line():
    figure = liquidity()
    for item in figure['items']:
        assert item['kind'] == 'current' or item['distance_text'] == '現在値を含む' or \
            re.fullmatch(r'差 [+-][\d,]+\.\d\d', item['distance_text']), item['distance_text']
        assert item['touched'] == '' and '最初に' not in item['state_word'] and 'Tier' not in item['state_word']
    assert 'Tier' not in ''.join(i['source_quote'] for i in figure['items'])


def test_a_sweep_and_a_break_read_apart():
    assert board.shown_state('swept', '掃引済み') == '抜けて戻した'
    assert board.shown_state('beyond', '抜けて推移・欠測2時間') == '抜けたまま・欠測2時間'
    assert board.shown_state('broken', '抜けて定着') == '抜けて定着' and board.shown_state('resting', '未到達') == '未到達'
    legend = liquidity()['legend']
    assert '✓抜けて戻した（掃引。一度抜けて、最新の終値は内側）' in legend and '↗↘抜けたまま（最新の終値が外側で6時間未満' in legend
    assert '掃引済み' not in legend and '抜けて推移' not in legend


def test_retail_entries_read_as_share_and_pl_in_a_few_characters():
    words = {i['label']: i['state_word'] for i in liquidity()['items'] if i['kind'] == 'retail'}
    assert words == {'個人ロングの平均建値': '個人56%ロング・含み損-5.3%', '個人ショートの平均建値': '個人44%ショート・含み損-3.8%'}
    assert '「含み損-6.6%」は平均建値から表示した現在値までの値動きの率（レバレッジを掛ける前）' in liquidity()['legend']


def test_the_map_is_a_tree_with_a_node_per_row_and_a_trunk_on_both_band_edges():
    figure = liquidity()
    html = dash.liquidity_section(figure)
    assert html.count('<span class="lv-node" aria-hidden="true"><span class="icon">') == len(figure['items'])
    assert 'ladder-head' not in html and 'class="band-edge side-above"' in html and 'class="band-edge side-below"' in html
    css = dash.CSS
    assert '.lv-node{grid-area:node' in css and '.kind-current .lv-node{background:none}' in css


def test_neutral_bias_rows_say_neutral_alone():
    rows = board.bias_rows(sample(), NOW, 'daily', SOURCE)[0]
    assert not any('偏りなし' in i['direction'] for i in rows)
    assert any(i['direction'] == '中立' for i in rows)


def test_the_us_table_has_no_release_candle_line():
    figure = board.event_figure(sample(), NOW, 'daily')['figure']
    assert not any('発表15分足' in (i.get('memo') or '') or '発表時刻を含む1時間足' in (i.get('memo') or '')
                   for i in figure['items'])
