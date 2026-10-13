"""Shared human-first renderer extensions used by the chart-intel HTML (board, event table, ladder band).

The renderer lives outside this repository (~/.agents/skills/human-first-docs); its contract is
exercised here so a renderer change that breaks the report figures fails in this repo's tests.
"""
from pathlib import Path
import sys

import pytest

SKILL = Path('/Users/laa/.agents/skills/human-first-docs/scripts')
pytestmark = pytest.mark.skipif(not SKILL.exists(), reason='shared renderer is not installed')
if SKILL.exists():
    sys.path.insert(0, str(SKILL))
    import render_report
    import visual_blocks

SOURCE = ('# R\n\n## 要約\n\n本文。\n\n## 図表に使用した観測値\n\n'
          '長期｜実質金利: 2.87%（2026-10-08）｜基準: 20営業日前 2.55%｜変化: +0.32pp｜向き: ▼ 下向き｜規則: r\n\n'
          '集計｜長期: — 中立（拮抗）（▲1 △0 ▼1 ▽0）｜規則: t\n\n'
          '2026-10-14 21:30 JST｜米国｜CPI (MoM) (Sep)｜予定｜重要度 高｜前回 0.4%｜予想 0.6%｜結果 —｜予想−前回 +0.2pp｜向き: ▽ 予想どおりなら下向き｜NY KZ・停止 21:00〜22:30\n\n'
          '現在値: 4,186.69。観測: x。\n\n想定変動 上限（+1σ）: 4,246.80。観測: x。\n\n想定変動 下限（−1σ）: 4,126.58。観測: x。\n\n'
          '個人Long平均建値（上）: 4,412.64。観測: y。\n')


def board_figure():
    item = {'group': '長期', 'label': '実質金利', 'display': '2.87%', 'baseline': '20営業日前 2.55%', 'change': '+0.32pp',
            'direction': '下向き', 'symbol': '▼', 'strength': 'strong', 'tone': 'negative', 'asof': '2026-10-08', 'rule': 'r',
            'value': 0.32, 'source_quote': '長期｜実質金利: 2.87%（2026-10-08）｜基準: 20営業日前 2.55%｜変化: +0.32pp｜向き: ▼ 下向き｜規則: r'}
    tile = {'label': '長期の向き', 'direction': '中立（拮抗）', 'symbol': '—', 'tone': 'neutral', 'detail': '▲1 △0 ▼1 ▽0',
            'source_quote': '集計｜長期: — 中立（拮抗）（▲1 △0 ▼1 ▽0）｜規則: t'}
    return {'id': 'bias', 'type': 'bias_board', 'placement': 'band', 'title': '盤', 'verdicts': [tile], 'items': [item]}


def event_figure():
    quote = '2026-10-14 21:30 JST｜米国｜CPI (MoM) (Sep)｜予定｜重要度 高｜前回 0.4%｜予想 0.6%｜結果 —｜予想−前回 +0.2pp｜向き: ▽ 予想どおりなら下向き｜NY KZ・停止 21:00〜22:30'
    return {'id': 'calendar', 'type': 'event_table', 'title': '指標', 'items': [
        {'date': '2026-10-14', 'time': '21:30 JST', 'country': '米国', 'label': 'CPI (MoM) (Sep)', 'status': '予定', 'importance': '高',
         'previous': '0.4%', 'forecast': '0.6%', 'actual': '—', 'diff': '予想−前回 +0.2pp', 'direction': '予想どおりなら下向き',
         'symbol': '▽', 'strength': 'weak', 'tone': 'negative', 'window': 'NY KZ・停止 21:00〜22:30', 'released': False, 'source_quote': quote}]}


def price_figure():
    return {'id': 'price', 'type': 'price_map', 'title': '価格', 'unit': 'USD/oz',
            'band': {'lower': 4126.58, 'upper': 4246.8, 'label': '想定変動 ±1σ'}, 'items': [
                {'label': '現在値', 'value': 4186.69, 'display': '4,186.69', 'tone': 'current', 'source_quote': '現在値: 4,186.69。観測: x。'},
                {'label': '想定変動 上限（+1σ）', 'value': 4246.8, 'display': '4,246.80', 'tone': 'reference', 'source_quote': '想定変動 上限（+1σ）: 4,246.80。観測: x。'},
                {'label': '想定変動 下限（−1σ）', 'value': 4126.58, 'display': '4,126.58', 'tone': 'reference', 'source_quote': '想定変動 下限（−1σ）: 4,126.58。観測: x。'},
                {'label': '個人Long平均建値（上）', 'value': 4412.64, 'display': '4,412.64', 'offscale': True, 'detail': 'd', 'source_quote': '個人Long平均建値（上）: 4,412.64。観測: y。'}]}


def test_board_and_table_render_every_field_and_validate_against_quotes():
    figures = [board_figure(), event_figure(), price_figure()]
    checks = visual_blocks.validate(figures, SOURCE)
    assert len(checks) == 7  # 1 board row + 1 tile + 1 event + 4 price items (3 plotted, 1 offscale)
    html = visual_blocks.render(figures[0], 0)
    assert 'class="verdict neutral"' in html and '▼' in html and '2026-10-08' in html and 'data-label="基準"' in html
    html = visual_blocks.render(figures[1], 1)
    assert html.count('<th scope="col"') == 9 and 'event-group' in html and 'dir weak negative' in html and '0.4%' in html
    html = visual_blocks.render(figures[2], 2)
    assert 'band-shade' in html and 'offscale-levels' in html and 'r="6"' in html and html.count('value-dot') == 3
    bad = board_figure(); bad['items'][0]['baseline'] = '20営業日前 2.56%'
    with pytest.raises(ValueError, match='numbers not in quote'):
        visual_blocks.validate([bad], SOURCE)
    bad = price_figure(); bad['band']['upper'] = 4250.0
    with pytest.raises(ValueError, match='Band edges'):
        visual_blocks.validate([bad], SOURCE)


def test_price_map_without_new_fields_keeps_old_layout():
    fig = {'type': 'price_map', 'title': '価格', 'unit': 'USD/oz', 'items': [
        {'label': f'L{i}', 'value': v, 'display': f'{v:,.2f}', 'source_quote': f'L{i} {v:,.2f}'} for i, v in enumerate([4000, 4250, 4500])]}
    html = visual_blocks.price_map(fig, 'figure-0')
    assert 'leader-line' not in html and 'band-shade' not in html and 'offscale' not in html and 'x2="403"' in html


def test_build_html_places_band_first_and_uses_configurable_labels():
    figures = [board_figure(), price_figure()]
    summary = {'conclusion': {'title': 't', 'text': '本文。', 'source_quote': '本文。'},
               'limitations': [{'title': 'a', 'text': 'b'}], 'limitations_title': '制約', 'limitations_open': False,
               'full_report_title': '全文・出典（確認用）', 'full_report_note': 'note', 'conditions_title': '条件'}
    html, audit = render_report.build_html(SOURCE, summary=summary, figures=figures)
    assert html.index('class="band-visuals"') < html.index('class="overview-grid') < html.index('figure-price_map')
    assert '<details class="limitations"><summary><h3>制約</h3>' in html and '読み違えを防ぐために' not in html
    assert '<h2>全文・出典（確認用）</h2><p>note</p>' in html and '根拠を詳しく読む' not in html and '<h2>条件</h2>' in html
    assert audit['figure_count'] == 2
    html, _ = render_report.build_html(SOURCE, summary={'conclusion': {'title': 't', 'text': '本文。'}, 'limitations': [{'title': 'a', 'text': 'b'}]},
                                       figures=[price_figure()])
    assert 'class="overview-grid has-visual">' in html and 'class="band-visuals"' not in html
    assert '<aside class="limitations"><h3>読み違えを防ぐために</h3><ul>' in html and '<details class="limitations"' not in html and '根拠を詳しく読む' in html
    with pytest.raises(ValueError, match='Unknown summary keys'):
        render_report.build_html(SOURCE, summary={'unexpected': 1}, figures=[])
