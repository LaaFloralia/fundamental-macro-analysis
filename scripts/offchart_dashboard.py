"""One-page HTML dashboard for the off-chart report (no scripts, no network, no Markdown body).

The page answers three questions in order: which way per horizon (bias board), where the
liquidity is (named levels with their sweep state), and which US releases to watch.
Every displayed figure string is verified against its quoted line in the Markdown appendix,
and every number against the numbers of that line; the full Markdown is embedded only as a
download link. Explanations sit in collapsed <details> blocks.
"""
from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
import hashlib
import html
import re

FIGURE_TYPES = ('bias_board', 'liquidity_map', 'event_table')
ITEM_TEXT = {
    'bias_board': ('label', 'display', 'baseline', 'change', 'direction', 'symbol', 'asof', 'note'),
    'liquidity_map': ('label', 'price_text', 'pool', 'state_word', 'touched', 'distance_text', 'target'),
    'event_table': ('date', 'time', 'label', 'status', 'previous', 'forecast', 'actual', 'diff', 'direction',
                    'symbol', 'memo'),
}
VERDICT_TEXT = ('label', 'symbol', 'direction', 'origin', 'detail', 'short')
# The only mini diagram drawn is a trend line of several observations (the row's text carries the two ends);
# it names its source series and is rechecked against the collection (report_bundle.check_bindings).
VIZ_TYPES = ('spark',)
# Diagrams of schemaVersion 5 editions (before r10). They are still checked as then (every number they encode
# must be in the row's quoted line) so a stored edition validates and renders again, but they are not drawn:
# the row's text carries the same numbers. Current editions never carry them (check_regenerated rebuilds).
LEGACY_VIZ_NUMBERS = {
    'delta': lambda v: [v.get('from'), v.get('to')],
    'track': lambda v: [v.get('value')],
    'split': lambda v: [v.get('left'), v.get('right'), v.get('track')],
    'gap': lambda v: [abs(v['value']) if isinstance(v.get('value'), (int, float)) else v.get('value')],
    'votes': lambda v: [v.get('up'), v.get('down'), v.get('flat')],
    'bars': lambda v: [b.get('value') for b in v.get('items') or []],
    'probs': lambda v: [x.get('value') for x in v.get('segments') or []],
}
LEGACY_VIZ_LABELS = {'bars': 'items', 'probs': 'segments'}
SERIES_TONES = ('positive', 'negative', 'neutral')


class DashboardError(ValueError):
    pass


def e(value) -> str:
    return html.escape(str(value), quote=True)


def keep_parts(value) -> str:
    """Escaped text whose 「・」-separated parts never break inside (ロング 61.0%・ショート 39.0%)."""
    parts = str(value).split('・')
    if len(parts) == 1:
        return e(value)
    return '・'.join(f'<span class="nw">{e(part)}</span>' for part in parts)


def canonical(text) -> str:
    return re.sub(r'\s+', '', str(text or '')).replace('−', '-')


def numbers(text) -> set:
    found = set()
    for token in re.findall(r'[-+−]?\d+(?:,\d{3})*(?:\.\d+)?', str(text or '')):
        token = token.replace('−', '-').replace(',', '').lstrip('+')
        try:
            value = Decimal(token).normalize()
        except InvalidOperation:
            continue
        found.add(format(value.copy_abs() if value == 0 else value, 'f'))  # 70.0 -> '70', not '7E+1'
    return found


def _check_text(where, quote, values):
    canon = canonical(quote)
    for value in values:
        if value in (None, ''):
            continue
        if canonical(value) not in canon:
            raise DashboardError(f'{where}: "{value}" is not in its source line')
        extra = numbers(value) - numbers(quote)
        if extra:
            raise DashboardError(f'{where}: numbers {sorted(extra)} are not in its source line')


def _check_viz(where, quote, viz):
    """A trend line must name its source series (the values are rechecked against the collection); a diagram of a
    schemaVersion 5 edition must carry only labels and numbers of its quoted line."""
    if not isinstance(viz, dict) or not viz:
        return
    kind = viz.get('type')
    if kind in VIZ_TYPES:
        if not viz.get('path') or not isinstance(viz.get('count'), int) or len(viz.get('values') or []) != viz['count']:
            raise DashboardError(f'{where}.viz: sparkline without its source series')
        return
    if kind not in LEGACY_VIZ_NUMBERS:
        raise DashboardError(f'{where}.viz: unknown diagram type {kind}')
    if kind in LEGACY_VIZ_LABELS:
        _check_text(where + '.viz', quote, [x.get('label') for x in viz.get(LEGACY_VIZ_LABELS[kind]) or []])
    values = LEGACY_VIZ_NUMBERS[kind](viz)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values if v is not None):
        raise DashboardError(f'{where}.viz: diagram values must be numbers')
    allowed = numbers(quote)
    if kind == 'gap':  # drawn by size; the sign is the bar's side
        allowed = {n.lstrip('-') for n in allowed}
    extra = numbers(' '.join(str(v) for v in values if v is not None)) - allowed
    if extra:
        raise DashboardError(f'{where}.viz: values {sorted(extra)} are not in its source line')


def _check_series(where, item):
    """The per-series parts of a released-indicator row: each is a part of its change text, with a known tone."""
    series = item.get('series')
    if series is None:
        return
    if (not isinstance(series, list) or len(series) < 2 or any(not isinstance(x, dict) for x in series)
            or '／'.join(str(x.get('text')) for x in series) != item.get('change')
            or any(x.get('tone') not in SERIES_TONES for x in series)):
        raise DashboardError(f'{where}.series: parts differ from its change text')


CONCLUSION_SPLIT = re.compile(r'^(?P<head>.*?)理由[:：](?P<reason>.+?)反対材料[:：](?P<counter>.+)$', re.S)
CONCLUSION_HEAD = re.compile(r'^\s*XAUUSD\s*[:：][^。]*。?\s*$')


def conclusion_parts(text):
    """The conclusion as labelled rows when it reads 「XAUUSD：<総合の語>。理由：…。反対材料：…。」: [(label, text)].
    The opening 「XAUUSD：<語>。」 is left out (the tiles above show the verdict); any other opening stays as an
    unlabelled first row. None for other conclusions (shown as one paragraph). Every text is a part of the line."""
    match = CONCLUSION_SPLIT.match(str(text or ''))
    if not match or not match['reason'].strip() or not match['counter'].strip():
        return None
    head = match['head'].strip()
    parts = [] if not head or CONCLUSION_HEAD.match(head) else [('', head)]
    return parts + [('理由', match['reason'].strip()), ('反対材料', match['counter'].strip())]


# Fixed row labels of the lead list for chips whose own prefix is not used as the label.
CHIP_LABELS = {'直近の未到達': '未到達の水準'}
NEXT_RELEASE = re.compile(r'^(?P<names>.+?)\s+(?P<when>\d{2}/\d{2}（.）\d{2}:\d{2} JST)$')
NEAREST_SIDE = re.compile(r'^(?P<side>[上下]) (?P<name>.+) (?P<price>[\d,]+\.\d+)(?P<dist>（[+\-−][\d,.]+）)$')
NEAREST_TAIL = '・距離は参考価格から'


def chip_parts(text):
    """One lead chip as (label, lines): each line is a list of (piece, bold). The label is the chip's own prefix
    (「次の米指標」「停止中」…, CHIP_LABELS for 直近の未到達) or 注意 when it has none; every piece is a part of the
    chip text, only reordered: the next release shows its time first, an unreached level its price first."""
    text = str(text or '')
    prefix, sep, rest = text.partition(': ')
    if not sep or len(prefix) > 12:
        return '注意', [[(text, False)]]
    label = CHIP_LABELS.get(prefix, prefix)
    if prefix == '次の米指標':
        match = NEXT_RELEASE.match(rest)
        if match:
            return label, [[(match['when'], True), (match['names'], False)]]
    if prefix == '直近の未到達':
        tail = rest.endswith(NEAREST_TAIL)
        body = rest[:-len(NEAREST_TAIL)] if tail else rest
        sides = [NEAREST_SIDE.match(part) for part in body.split('／')]
        if all(sides):
            lines = [[(m['side'], False), (m['price'], True), (m['dist'], False), (m['name'], False)] for m in sides]
            return label, lines + ([[(NEAREST_TAIL.lstrip('・'), False)]] if tail else [])
    return label, [[(rest, False)]]


def chip_texts(text):
    """The strings a chip shows (label excluded), for the browser check."""
    return [piece for line in chip_parts(text)[1] for piece, _ in line]


def validate(summary: dict, figures: list, source: str) -> list:
    """Raise on any displayed string or number that its quoted Markdown line does not contain."""
    canon_source = canonical(source)
    checked = []

    def quoted(where, item):
        quote = item.get('source_quote')
        if not isinstance(quote, str) or not quote.strip() or canonical(quote) not in canon_source:
            raise DashboardError(f'{where}: source_quote is missing from the Markdown')
        return quote

    conclusion = summary.get('conclusion')
    if conclusion:
        quote = quoted('conclusion', conclusion)
        if numbers(conclusion.get('text')) - numbers(quote):
            raise DashboardError('conclusion: numbers are not in its source line')
        for _, part in conclusion_parts(conclusion.get('text')) or []:
            if part not in conclusion['text']:
                raise DashboardError(f'conclusion: "{part}" is not in the conclusion line')
    confidence = summary.get('confidence')
    if confidence:
        # 「信頼度 低」 from 「信頼度: Low ｜ スコア 0 …」: the word must be the line's (report_bundle.CONFIDENCE_JA).
        from scripts.report_bundle import CONFIDENCE_JA
        word = {ja: en for en, ja in CONFIDENCE_JA.items()}.get(str(confidence.get('text') or '').removeprefix('信頼度 '))
        if not word or not re.match(rf'\s*信頼度[:：]\s*\**{re.escape(word)}\b', quoted('confidence', confidence)):
            raise DashboardError('confidence: the word is not in its source line')
    for index, chip in enumerate(summary.get('chips') or []):
        _check_text(f'chips[{index}]', quoted(f'chips[{index}]', chip), [chip.get('text')])
        label, lines = chip_parts(chip.get('text'))
        if label not in str(chip.get('text')) and label not in ('注意', *CHIP_LABELS.values()):
            raise DashboardError(f'chips[{index}]: label "{label}" is not in the chip')
        for piece, _ in (x for line in lines for x in line):
            if piece not in str(chip.get('text')):
                raise DashboardError(f'chips[{index}]: "{piece}" is not in the chip')
    for index, note in enumerate(summary.get('limitations') or []):
        _check_text(f'limitations[{index}]', quoted(f'limitations[{index}]', note), [note.get('text')])
    source_numbers = numbers(source)
    for figure in figures:
        kind = figure.get('type')
        if kind not in FIGURE_TYPES:
            raise DashboardError(f'Unknown figure type: {kind}')
        loose = ' '.join(str(figure.get(k) or '') for k in ('kicker', 'title', 'subtitle', 'legend'))
        if numbers(loose) - source_numbers:
            raise DashboardError(f'{figure.get("id")}: heading numbers are not in the Markdown')
        if figure.get('legend') and canonical(figure['legend']) not in canon_source:
            raise DashboardError(f'{figure.get("id")}: legend is not in the Markdown')
        # `aux`: the retail average entries below the map in schemaVersion 5 editions (now rows of the map).
        rows = [(f'{figure.get("id")}[{index}]', item) for index, item in enumerate(figure.get('items') or [])]
        rows += [(f'{figure.get("id")}.aux[{index}]', item) for index, item in enumerate(figure.get('aux') or [])]
        for where, item in rows:
            quote = quoted(where, item)
            _check_text(where, quote, [item.get(k) for k in ITEM_TEXT[kind]])
            _check_viz(where, quote, item.get('viz'))
            _check_series(where, item)
            checked.append(where)
        for index, tile in enumerate(figure.get('verdicts') or []):
            where = f'{figure.get("id")}.verdicts[{index}]'
            _check_text(where, quoted(where, tile), [tile.get(k) for k in VERDICT_TEXT])
        extras = [(f'{figure.get("id")}.{key}[{i}]', c) for key in ('cautions', 'notices', 'missing')
                  for i, c in enumerate(figure.get(key) or [])]
        if figure.get('reason'):
            extras.append((f'{figure.get("id")}.reason', figure['reason']))
        for where, extra in extras:
            _check_text(where, quoted(where, extra), [extra.get('text')])
        band = figure.get('band')
        if band:
            line = next((r for r in source.splitlines() if canonical(band['label']) in canonical(r)), None)
            if line is None:
                raise DashboardError(f'{figure.get("id")}: band is not in the Markdown')
            _check_text(f'{figure.get("id")}.band', line, [band['label'], f'{band["lower"]:,.2f}', f'{band["upper"]:,.2f}'])
        for rule in figure.get('rules') or []:
            if canonical(rule.get('rule')) not in canon_source:
                raise DashboardError(f'{figure.get("id")}: rule text is not in the Markdown')
    return checked


# ------------------------------------------------------------------ mini diagrams (decorative SVG)

W, H = 112, 30
COLOR = {'positive': 'var(--up)', 'negative': 'var(--down)', 'neutral': 'var(--flat)', 'caution': 'var(--flat)'}


def _svg(body, label=''):
    return (f'<svg class="viz" viewBox="0 0 {W} {H}" width="{W}" height="{H}" aria-hidden="true" '
            f'focusable="false">{body}</svg>')


def _scale(values, lo_pad=0.08):
    lo, hi = min(values), max(values)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * lo_pad
    lo, hi = lo - pad, hi + pad
    return lambda v: H - 3 - (v - lo) / (hi - lo) * (H - 6)


def viz_svg(viz, tone):
    if not isinstance(viz, dict):
        return ''
    color = COLOR.get(tone, 'var(--flat)')
    kind = viz.get('type')
    if kind == 'spark':
        vals = [float(v) for v in viz.get('values') or []]
        if len(vals) < 3:
            return ''
        y = _scale(vals)
        step = (W - 8) / (len(vals) - 1)
        pts = ' '.join(f'{4 + i * step:.1f},{y(v):.1f}' for i, v in enumerate(vals))
        return _svg(f'<line x1="4" x2="{W - 4}" y1="{y(vals[0]):.1f}" y2="{y(vals[0]):.1f}" class="base"/>'
                    f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/>'
                    f'<circle cx="4" cy="{y(vals[0]):.1f}" r="2.2" class="dot0"/>'
                    f'<circle cx="{W - 4}" cy="{y(vals[-1]):.1f}" r="3" fill="{color}"/>')
    return ''


# ------------------------------------------------------------------ sections

def _sources(figure):
    links = ' / '.join(f'<a href="{e(s["url"])}">{e(s["label"])}</a>' for s in figure.get('sources') or []
                       if re.match(r'^https://', str(s.get('url', ''))))
    return f'<p class="src">出典: {links}</p>' if links else ''


def _details(title, body):
    return f'<details class="howto"><summary>{e(title)}</summary><div class="howto-body">{body}</div></details>'


MISMATCH_MARK = '集計と異なる'


def _tile_origin(t):
    """The origin line: none for the report's own verdict (「原稿の判断」), 「集計と異なる」 when it differs from the
    tally, and the rule name when the tile shows the tally itself (no report verdict)."""
    origin = str(t.get('origin') or '')
    if t.get('mismatch') and MISMATCH_MARK in origin:
        return f'<span class="tile-origin">{MISMATCH_MARK}</span>'
    if origin and not origin.startswith('原稿の判断'):
        return f'<span class="tile-origin">{e(origin)}</span>'
    return ''


def tiles_html(figure, confidence=None):
    """The per-horizon verdicts, shown first on the page (above the conclusion).

    A tile repeats its rule result when the report's verdict differs from it (with the report's
    reason below), when the horizon has no material, or when a material is missing. The report's
    confidence word sits in the 総合 tile.
    """
    if not figure or not figure.get('verdicts'):
        return ''
    conf = (confidence or {}).get('text')
    tiles = ''.join(
        f'<div class="tile tone-{e(t.get("tone"))}{" is-mismatch" if t.get("mismatch") else ""}">'
        f'<span class="tile-label">{e(t["label"])}</span>'
        f'<span class="tile-symbol" aria-hidden="true">{e(t["symbol"])}</span>'
        f'<span class="tile-word">{e(t["direction"])}</span>'
        + _tile_origin(t)
        + (f'<span class="tile-detail">{e(t["detail"])}</span>' if t.get('show_detail', t.get('mismatch')) else '')
        + (f'<span class="tile-conf">{e(conf)}</span>' if conf and t.get('label') == '総合' else '') + '</div>'
        for t in figure['verdicts'])
    reason = figure.get('reason')
    note = f'<p class="tile-reason">{e(reason["text"])}</p>' if reason else ''
    return f'<div class="tiles" role="group" aria-label="期間ごとの金の向き">{tiles}</div>{note}'


def _fig_head(figure, anchor):
    """Title and, when the figure has one, its subtitle (kickers are no longer shown)."""
    sub = f'<p class="sub">{e(figure["subtitle"])}</p>' if figure.get('subtitle') else ''
    return f'<div class="fig-head"><h2 id="{anchor}">{e(figure["title"])}</h2>{sub}</div>'


BOARD_HEAD = ('<colgroup><col class="c-lbl"><col class="c-now"><col class="c-base"><col class="c-dir"></colgroup>'
              '<thead><tr><th scope="col">材料</th><th scope="col">現在</th><th scope="col">比較</th>'
              '<th scope="col">向き</th></tr></thead>')


def bias_section(figure):
    """One block per horizon: its name with the tally beside it, then its own table."""
    blocks = []
    details = {t.get('group'): t.get('short') or t.get('detail') for t in figure.get('verdicts') or [] if t.get('group')}
    for n, group in enumerate(figure.get('groups') or []):
        rows = [i for i in figure['items'] if i['group'] == group]
        if not rows:
            continue
        tally = f'<span class="grp-tally">{e(details[group])}</span>' if details.get(group) else ''
        body = []
        for i in rows:
            note = f'<span class="note">{e(i["note"])}</span>' if i.get('note') else ''
            if i.get('kind') == 'text':
                # A judgement in words (news): one wide cell, no numeric baseline.
                middle = f'<td class="cell-text" colspan="2">{e(i["display"])}</td>'
            else:
                change = f'<span class="chg">{e(i["change"])}</span>' if i.get('change') not in (None, '', '—') else ''
                if i.get('series'):
                    # One line per series in its own colour; the separator stays in the text, out of sight.
                    sep = '<span class="vh">／</span>'
                    change = '<span class="chg chg-series">' + sep.join(
                        f'<span class="ser s-{e(x["tone"])}">{e(x["text"])}</span>' for x in i['series']) + '</span>'
                middle = (f'<td class="cell-now"><span class="now-text">{keep_parts(i["display"])}</span>'
                          f'{viz_svg(i.get("viz"), i["tone"])}</td>'
                          f'<td class="cell-base"><span class="base-text">{e(i["baseline"])}</span>{change}</td>')
            body.append(
                f'<tr class="tone-{e(i["tone"])}{" text-row" if i.get("kind") == "text" else ""}">'
                f'<th scope="row"><span class="lbl">{e(i["label"])}</span>'
                f'<span class="asof">{e(i["asof"])}</span>{note}</th>{middle}'
                f'<td class="cell-dir"><span class="dir-symbol" aria-hidden="true">{e(i["symbol"])}</span>'
                f'<span class="dir-word">{e(i["direction"])}</span></td></tr>')
        blocks.append(f'<div class="horizon"><h3 class="hz-head" id="bias-h{n + 1}"><span class="hz-name">{e(group)}</span>'
                      f'{tally}</h3><div class="board-wrap"><table class="board" aria-labelledby="bias-h{n + 1}">'
                      f'{BOARD_HEAD}<tbody>{"".join(body)}</tbody></table></div></div>')
    rules = ''.join(f'<li><b>{e(r["label"])}</b>: {e(r["rule"])}</li>' for r in figure.get('rules') or [])
    howto = _details('判定ルールと出典（開いて確認）',
                     f'<p>{e(figure.get("legend"))}</p><ul class="rules">{rules}</ul>{_sources(figure)}')
    return (f'<section class="data-figure fig-bias" id="bias" aria-labelledby="bias-title">{_fig_head(figure, "bias-title")}'
            f'{"".join(blocks)}{howto}</section>')


def _price(text):
    """A price or a price range; a range may break after its 〜."""
    return '〜<wbr>'.join(e(part) for part in str(text).split('〜'))


def _ends(i):
    """The row's lowest and highest price: both ends of a zone (from its shown price text), else its position."""
    if i.get('kind') == 'zone':
        try:
            ends = [float(part.replace(',', '')) for part in str(i['price_text']).split('〜')]
        except ValueError:
            ends = []
        if ends:
            return min(ends), max(ends)
    return i['position'], i['position']


def _band_class(i, lower, upper):
    """in-band when the whole row is inside the expected-move band, part-band when a zone crosses its edge."""
    if lower is None or upper is None:
        return ''
    low, high = _ends(i)
    if lower <= low and high <= upper:
        return ' in-band'
    return ' part-band' if low <= upper and high >= lower else ''


def _side_class(i, price):
    """The row's side of the current price for the left spine: above (warm), below (cool), or at (a zone that
    contains the price, and the current row itself)."""
    if price is None or i.get('kind') == 'current':
        return ''
    low, high = _ends(i)
    if low > price:
        return ' side-above'
    if high < price:
        return ' side-below'
    return ' side-at'


def _ladder_row(i, band='', price=None):
    pool = f'<span class="pool pool-{e(i["pool"].lower())}">{e(i["pool"])}</span>' if i.get('pool') else ''
    touched = f'<span class="touched">{e(i["touched"])}</span>' if i.get('touched') else ''
    target = f'<span class="target">{e(i["target"])}</span>' if i.get('target') else ''
    side = _side_class(i, price)
    # A tree: the price on the left, the row's node (its state symbol) on the trunk, the level and its state on the right.
    return (f'<li class="lv kind-{e(i["kind"])} state-{e(i["state"])}{side}{band}{" is-target" if target else ""}">'
            f'<span class="lv-price">{_price(i["price_text"])}</span>'
            f'<span class="lv-node" aria-hidden="true"><span class="icon">{e(i["icon"])}</span></span>'
            f'<span class="lv-name"><span class="lv-label">{e(i["label"])}</span>{pool}{target}</span>'
            f'<span class="lv-dist">{e(i["distance_text"])}</span>'
            f'<span class="lv-state">{e(i["state_word"])}{touched}</span></li>')


def liquidity_section(figure):
    """The ladder (price order, the expected-move band as two edges), then the rows outside the shown range in the
    same columns, then what could not be shown and why."""
    band = figure.get('band') or {}
    lower, upper = band.get('lower'), band.get('upper')
    price = next((i['position'] for i in figure['items'] if i.get('kind') == 'current'), None)
    rows, upper_done, lower_done = [], False, False
    edge = lambda word, value: (f'<li class="band-edge {"side-above" if word == "上限" else "side-below"}">'
                                f'{e(band["label"])} {word} {value:,.2f}</li>')
    for i in [i for i in figure['items'] if not i.get('offscale')]:
        if upper is not None and not upper_done and i['position'] <= upper:
            rows.append(edge('上限', upper))
            upper_done = True
        if lower is not None and not lower_done and i['position'] < lower:
            rows.append(edge('下限', lower))
            lower_done = True
        rows.append(_ladder_row(i, _band_class(i, lower, upper), price))
    if lower is not None and not lower_done:
        rows.append(edge('下限', lower))
    off = [i for i in figure['items'] if i.get('offscale')]
    offscale = ('<div class="offscale"><span>表示範囲の外</span><ul class="ladder">'
                + ''.join(_ladder_row(i, price=price) for i in off) + '</ul></div>') if off else ''
    missing = ''.join(f'<p class="liq-missing">欠測: {e(m["text"])}</p>' for m in figure.get('missing') or [])
    cautions = ''.join(f'<p class="caution" role="note">{e(c["text"])}</p>' for c in figure.get('cautions') or [])
    cautions += ''.join(f'<p class="liq-notice" role="note">{e(c["text"])}</p>' for c in figure.get('notices') or [])
    # schemaVersion 5 editions: the retail average entries were a reference line in the folded legend.
    aux = figure.get('aux') or []
    aux_html = ('<p class="aux">参考（流動性の位置ではない）: ' + ' ／ '.join(
        f'{e(i["icon"])} {e(i["label"])} {e(i["price_text"])}（{e(i["distance_text"])}・{e(i["state_word"])}）'
        for i in aux) + '</p>') if aux else ''
    howto = _details('水準の決め方と状態の意味' + ('・個人の平均建値' if aux else '') + '（開いて確認）',
                     f'<p>{e(figure.get("legend"))}</p>{aux_html}{_sources(figure)}')
    return (f'<section class="data-figure fig-liq" id="liquidity" aria-labelledby="liq-title">'
            f'{_fig_head(figure, "liq-title")}{cautions}'
            f'<ul class="ladder">{"".join(rows)}</ul>{offscale}{missing}{howto}</section>')


def event_section(figure):
    rows, last = [], None
    for i in figure['items']:
        if i.get('empty'):
            rows.append(f'<tr class="day"><th colspan="7" scope="colgroup">{e(i["date"])}</th></tr>'
                        f'<tr class="empty"><td colspan="7">{e(i["label"])}</td></tr>')
            continue
        if i['date'] != last:
            rows.append(f'<tr class="day"><th colspan="7" scope="colgroup">{e(i["date"])}</th></tr>')
            last = i['date']
        tier = f'<span class="tier tier-{e(i["tier"].lower())}">Tier {e(i["tier"])}</span>' if i.get('tier') else ''
        memo = f'<span class="memo">{e(i["memo"])}</span>' if i.get('memo') else ''
        same = i.get('group_start') is False  # a release at the same time as the row above
        rows.append(
            f'<tr class="{"released" if i.get("released") else "upcoming"} tone-{e(i["tone"])}{" same-time" if same else ""}">'
            f'<td class="ev-time">{"" if same else e(i["time"])}</td>'
            f'<th scope="row" class="ev-name">{tier}<span>{e(i["label"])}</span>{memo}</th>'
            f'<td class="ev-num" data-k="前回">{e(i["previous"])}</td><td class="ev-num" data-k="予想">{e(i["forecast"])}</td>'
            f'<td class="ev-num" data-k="結果">{e(i["actual"])}</td><td class="ev-diff">{e(i["diff"])}</td>'
            f'<td class="cell-dir"><span class="dir-symbol" aria-hidden="true">{e(i["symbol"])}</span>'
            f'<span class="dir-word">{e(i["direction"])}</span></td></tr>')
    howto = _details('指標の選び方と向きの意味（開いて確認）', f'<p>{e(figure.get("legend"))}</p>{_sources(figure)}')
    cautions = ''.join(f'<p class="caution" role="note">{e(c["text"])}</p>' for c in figure.get('cautions') or [])
    return (f'<section class="data-figure fig-events" id="calendar" aria-labelledby="ev-title">'
            f'{_fig_head(figure, "ev-title")}{cautions}'
            f'<div class="board-wrap"><table class="events"><thead><tr><th scope="col">時刻（JST）</th><th scope="col">指標</th>'
            f'<th scope="col">前回</th><th scope="col">予想</th><th scope="col">結果</th><th scope="col">差・予測</th>'
            f'<th scope="col">金への向き</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{howto}</section>')


SECTIONS = {'bias_board': bias_section, 'liquidity_map': liquidity_section, 'event_table': event_section}

CSS = """
:root{--ink:#22352f;--muted:#5d6863;--line:#dfe3dc;--paper:#fffefb;--canvas:#f3f2ec;--accent:#3f6650;
--up:#2c7a57;--down:#b0454f;--flat:#8b928d;--wash:#f1f4ee;--band:#eef3fb;--band-mark:#9db1d6;
--above:#d9a3a8;--below:#9cc5ae;--at:#c5cbc6;
font:16px/1.7 "Hiragino Sans","Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:var(--ink);background:var(--canvas);
font-variant-numeric:tabular-nums;font-synthesis:none}
*{box-sizing:border-box}body{margin:0}a{color:var(--accent);text-underline-offset:.2em}
.reports-back{display:block;max-width:1100px;margin:0 auto;padding:14px 20px;font-size:14px}
main.dash{max-width:1100px;margin:0 auto 48px;background:var(--paper);border:1px solid var(--line);padding:32px 40px 24px;
overflow-wrap:break-word}
.mast{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;border-bottom:2px solid var(--ink);padding-bottom:14px}
.eyebrow{display:block;color:var(--accent);font-size:.7rem;font-weight:700;letter-spacing:.1em}
h1{font-size:1.6rem;margin:2px 0 0;line-height:1.3}h2{font-size:1.3rem;margin:0;line-height:1.4}
.asof-chip{font-size:.95rem;font-weight:600;white-space:nowrap}
.lead{margin:24px 0 0}
.conclusion{margin:18px 0 0;padding:16px 20px;background:var(--wash);border-left:5px solid var(--accent);
font-size:1.02rem;line-height:1.8;white-space:pre-line}
.cl-split{display:grid;gap:10px;white-space:normal}.cl-row{display:grid;grid-template-columns:5.5em minmax(0,1fr);gap:12px;align-items:baseline}
.cl-row dt{font-size:.78rem;font-weight:700;color:var(--muted);letter-spacing:.04em}.cl-row dd{margin:0}
.cl-plain{grid-template-columns:minmax(0,1fr)}.cl-plain dt{display:none}
.conf-line{margin:0;font-size:.85rem;font-weight:700;color:var(--muted)}
.facts{margin:16px 0 0;padding:0;display:grid;gap:0}
.kv{display:grid;grid-template-columns:7.5em minmax(0,1fr);gap:12px;align-items:baseline;padding:8px 4px;border-bottom:1px solid var(--line)}
.kv:last-child{border-bottom:0}
.kv dt{font-size:.78rem;font-weight:700;color:var(--muted)}.kv dd{margin:0;font-size:.92rem;min-width:0;overflow-wrap:anywhere}
.kv-line{display:block}.kv-line+.kv-line{margin-top:2px}.kv-line b{font-weight:700}
.data-figure{margin-top:64px}.fig-head{margin-bottom:14px}.fig-head .sub{margin:4px 0 0;color:var(--muted);font-size:.85rem}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.tile{border:1px solid var(--line);border-top:5px solid var(--flat);background:#fff;padding:12px 16px;display:grid;
grid-template-columns:auto 1fr;grid-template-areas:"label label" "sym word" "origin origin" "detail detail" "conf conf";column-gap:10px;align-items:center}
.tile.tone-positive{border-top-color:var(--up)}.tile.tone-negative{border-top-color:var(--down)}
.tile-label{grid-area:label;font-size:.8rem;font-weight:700;color:var(--muted)}
.tile-symbol{grid-area:sym;font-size:2.2rem;line-height:1.1}.tone-positive .tile-symbol{color:var(--up)}
.tone-negative .tile-symbol{color:var(--down)}.tone-neutral .tile-symbol{color:var(--flat)}
.tile-word{grid-area:word;font-size:1.15rem;font-weight:700;line-height:1.35}
.tile-origin{grid-area:origin;font-size:.72rem;color:var(--muted)}.tile-detail{grid-area:detail;font-size:.78rem;color:var(--muted);line-height:1.5;margin-top:2px}
.tile-conf{grid-area:conf;font-size:.78rem;font-weight:700;color:var(--muted);margin-top:4px}
.tile.is-mismatch .tile-origin{color:#8a5a00;font-weight:700}.tile-reason{margin:10px 0 0;font-size:.82rem;color:#5b4a1f;background:#fbf6e9;
border-left:4px solid #c9a227;padding:6px 12px}
.horizon+.horizon{margin-top:40px}
.hz-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;margin:0 0 8px;font-size:1.05rem;line-height:1.4}
.hz-name{font-weight:700}.grp-tally{font-weight:400;color:var(--muted);font-size:.8rem}
.caution{margin:0 0 10px;padding:6px 12px;background:#fbf0ef;border-left:4px solid var(--down);font-size:.82rem}
.liq-notice{margin:0 0 10px;padding:6px 12px;background:var(--wash);border-left:4px solid var(--flat);font-size:.82rem;color:var(--muted)}
.liq-missing{font-size:.8rem;color:var(--muted);margin:10px 0 0}.aux{font-size:.8rem;color:var(--muted);margin:8px 0 0}.events tr.empty td{color:var(--muted);font-size:.86rem}
.board-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%}.board{table-layout:fixed}
.board .c-lbl{width:31%}.board .c-now{width:22%}.board .c-base{width:30%}.board .c-dir{width:17%}th,td{text-align:left;vertical-align:middle;padding:10px 10px;border-bottom:1px solid var(--line)}
thead th{font-size:.72rem;color:var(--muted);font-weight:600;border-bottom:1px solid var(--ink);padding-top:4px;padding-bottom:4px}
tr.day th{background:var(--wash);font-size:.82rem;font-weight:700;padding:6px 10px}
.board th[scope=row]{font-weight:600;min-width:13em}.lbl{display:block}.asof,.note{display:block;font-size:.72rem;color:var(--muted);font-weight:400}
.note{color:var(--accent)}.now-text{display:block}.viz{display:block;margin-top:4px}.viz .base{stroke:#c9cfc9;stroke-width:1;stroke-dasharray:2 2}
.viz .tick{stroke:#9aa29c;stroke-width:1}.viz .dot0{fill:#b9c0ba}.viz .track{fill:#e7ebe5}.viz .zone{fill:#d5dbd4}
.viz .seg{fill:#cdd6d0}.viz .seg-focus{fill:var(--accent)}.viz .long{fill:#7f9f8c}.viz .short{fill:#c9a3a9}
.cell-now{font-weight:700;overflow-wrap:anywhere}.nw{white-space:nowrap}.cell-text{font-size:.92rem;line-height:1.6}.cell-base{font-size:.85rem}.base-text{display:block;color:var(--muted)}
.chg{font-weight:700}.cell-dir{white-space:nowrap}.dir-symbol{font-size:1.25rem;margin-right:6px;vertical-align:-2px}
.tone-positive .dir-symbol,.tone-positive .chg{color:var(--up)}.tone-negative .dir-symbol,.tone-negative .chg{color:var(--down)}
.tone-neutral .dir-symbol{color:var(--flat)}.chg-series .ser{display:block}.board .chg .s-positive{color:var(--up)}
.board .chg .s-negative{color:var(--down)}.board .chg .s-neutral{color:var(--flat)}
.vh{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}.dir-word{font-size:.88rem;font-weight:600}
.ladder{list-style:none;margin:0;padding:0;--px:7rem;--node:30px;--gap:8px}
.lv{--trunk:var(--line);display:grid;grid-template-columns:var(--px) var(--node) minmax(0,1fr);
grid-template-areas:"price node name" "dist node state";column-gap:var(--gap);row-gap:0;align-items:baseline;padding:8px 10px 8px 0;font-size:.9rem}
.lv.side-above{--trunk:var(--above)}.lv.side-below{--trunk:var(--below)}.lv.side-at{--trunk:var(--at)}
.lv.in-band{background:var(--band)}.lv.part-band{box-shadow:inset -6px 0 0 var(--band-mark)}
.lv-price{grid-area:price;text-align:right;font-weight:700}.lv-label{margin-right:6px}.lv-name{grid-area:name}
.lv-node{grid-area:node;align-self:stretch;position:relative;margin:-8px 0;
background:linear-gradient(var(--trunk),var(--trunk)) 100% calc(8px + .85em)/50% 2px no-repeat,linear-gradient(var(--trunk),var(--trunk)) 50% 0/3px 100% no-repeat}
.lv-node .icon{position:absolute;left:50%;top:calc(8px + .85em);width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;
background:var(--paper);border:2px solid var(--trunk);display:flex;align-items:center;justify-content:center;font-size:.68rem;line-height:1;
font-weight:700;color:var(--ink)}
.side-above .lv-node .icon{color:var(--down)}.side-below .lv-node .icon{color:var(--up)}
.state-swept .lv-node .icon,.state-broken .lv-node .icon,.state-forming .lv-node .icon,.kind-reference .lv-node .icon{color:var(--flat)}
.kind-retail .lv-node .icon,.kind-zone .lv-node .icon{color:#7a5c9e}.kind-zone .lv-node .icon{border-radius:6px;height:28px;margin-top:-14px}
.kind-reference .lv-node .icon{border-style:dashed}
.pool{display:inline-block;font-size:.68rem;font-weight:700;padding:0 6px;border-radius:3px;color:#fff;vertical-align:1px}
.pool-bsl{background:var(--down)}.pool-ssl{background:var(--up)}
.lv-dist{grid-area:dist;text-align:right;font-size:.78rem;color:var(--muted)}.lv>span{min-width:0;overflow-wrap:anywhere}
.lv-state{grid-area:state;font-size:.82rem}.touched{display:block;font-size:.72rem;color:var(--muted)}
.state-swept .lv-price,.state-broken .lv-price{color:var(--muted)}
.kind-current{background:var(--ink)!important;color:#fff;border-radius:3px}
.kind-current .lv-dist,.kind-current .touched{color:#dfe7e3}.kind-current .lv-node{background:none}
.kind-current .lv-node .icon{background:#fff;border-color:#fff;color:var(--ink)}
.ladder>.kind-current{position:-webkit-sticky;position:sticky;top:0;bottom:0;z-index:2;box-shadow:0 1px 6px rgba(0,0,0,.18)}
.kind-reference{color:var(--muted);font-size:.82rem}
.kind-draw,.is-target{outline:2px solid var(--accent);outline-offset:-2px}
.target{display:inline-block;max-width:100%;margin-left:6px;font-size:.72rem;font-weight:700;color:var(--accent);overflow-wrap:anywhere}
.band-edge{list-style:none;font-size:.72rem;color:#55709c;border-bottom:1px dashed var(--band-mark);padding:2px 12px;text-align:right;
--trunk:var(--line);background:linear-gradient(var(--trunk),var(--trunk)) calc(var(--px) + var(--gap) + var(--node) / 2 - 1.5px) 0/3px 100% no-repeat}
.band-edge.side-above{--trunk:var(--above)}.band-edge.side-below{--trunk:var(--below)}
.offscale{margin-top:20px}.offscale>span{display:block;font-size:.78rem;font-weight:700;color:var(--muted);padding:0 10px 2px;
border-bottom:1px solid var(--ink)}
.events td,.events th{font-size:.88rem}.ev-time{white-space:nowrap;font-weight:600}.ev-name span{display:block}
.ev-name{font-weight:600;min-width:12em}.memo{display:block;font-size:.74rem;color:var(--muted);font-weight:400}
.tier{display:inline-block!important;font-size:.66rem;font-weight:700;padding:0 6px;border-radius:3px;margin-bottom:2px;background:var(--ink);color:#fff}
.tier-b{background:#8d968f}.ev-num{white-space:nowrap}.ev-diff{font-size:.8rem}tr.released{background:#fbfbf8}
details.howto,details.limits{margin-top:16px;border:1px solid var(--line);background:#fff}
details summary{cursor:pointer;min-height:44px;display:flex;align-items:center;padding:0 14px;font-size:.85rem;color:var(--accent);font-weight:600}
.howto-body,.limits-body{padding:2px 16px 12px;font-size:.85rem;color:var(--muted)}.rules{padding-left:1.2em}
.src{font-size:.78rem}.limits{margin-top:56px}.note-line{font-size:.78rem;color:var(--muted);margin:16px 0 0}
.no-figures{margin:12px 0 0;padding:10px 14px;border:1px solid var(--line);background:#fff}
.events tr.same-time td,.events tr.same-time th{border-top:0}
.events tr.same-time{background:inherit}
footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);margin-top:32px;padding-top:12px;
font-size:.78rem;color:var(--muted)}
@media(max-width:700px){
main.dash{padding:18px 14px;border:0}.mast{align-items:flex-start;flex-direction:column;gap:4px}h1{font-size:1.3rem}
h2{font-size:1.2rem}.data-figure{margin-top:44px}.horizon+.horizon{margin-top:28px}.limits{margin-top:40px}
.conclusion{padding:12px 14px;font-size:.95rem}.cl-row{grid-template-columns:1fr;gap:0}
.kv{grid-template-columns:5.6em minmax(0,1fr);gap:8px}.kv dd{font-size:.88rem}.kv-name{display:block;font-size:.82rem;color:var(--muted)}.kv-line+.kv-line{margin-top:8px}
.tiles{gap:6px}.tile{padding:8px 8px;grid-template-columns:1fr;
grid-template-areas:"label" "sym" "word" "origin" "detail" "conf"}.tile-symbol{font-size:1.7rem}.tile-word{font-size:.92rem}
.tile-detail{font-size:.7rem}.tile-conf{font-size:.72rem}
.board thead,.events thead{display:none}.board,.board tbody,.events,.events tbody{display:block}
.board tr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"lbl dir" "now now" "base base";
gap:2px 10px;padding:12px 4px;border-bottom:1px solid var(--line)}
.board th[scope=row]{grid-area:lbl;border:0;padding:0;min-width:0}.board .cell-dir{grid-area:dir;border:0;padding:0;text-align:right}
.board .cell-now{grid-area:now;border:0;padding:0}.board .cell-now .viz{display:inline-block;margin:0 0 0 10px;vertical-align:middle}
.now-text{display:inline}
.board .cell-base{grid-area:base;border:0;padding:0}.base-text{display:inline;margin-right:8px}
.board .cell-text{grid-column:1/-1;grid-row:2;border:0;padding:0}
.hz-head{border-bottom:1px solid var(--ink);padding-bottom:6px;margin-bottom:0}
.offscale>span{border-bottom-color:var(--line)}.ladder{--px:5.6rem;--node:28px;--gap:6px}.lv{font-size:.86rem;padding:8px 4px 8px 0}
.events tr{display:grid;grid-template-columns:3.4em minmax(0,1fr) auto;grid-template-areas:"time name dir" "time nums nums" "time diff diff";
gap:2px 8px;padding:10px 4px;border-bottom:1px solid var(--line)}.events tr.day{display:block;padding:0}.events tr.day th{display:block}
.events td,.events th{border:0;padding:0}.ev-time{grid-area:time}.ev-name{grid-area:name;min-width:0}.events .cell-dir{grid-area:dir;text-align:right}
.ev-num{display:inline-block;font-size:.8rem}.ev-num::before{content:attr(data-k) " ";color:var(--muted);font-size:.7rem}
.events tr>.ev-num:nth-of-type(2){grid-column:2/4;grid-row:2}.events tr>.ev-num:nth-of-type(3){grid-column:2/4;grid-row:2;margin-left:6.5em}
.events tr>.ev-num:nth-of-type(4){grid-column:2/4;grid-row:2;margin-left:13em}.ev-diff{grid-area:diff}
}
@media print{.reports-back,details{display:none}main.dash{border:0}.ladder>.kind-current{position:static}}
"""


def conclusion_html(text):
    """The labelled rows (理由・反対材料) of a conclusion in the current format, else the line as one paragraph."""
    if not text:
        return ''
    parts = conclusion_parts(text)
    if not parts:
        return f'<p class="conclusion">{e(text)}</p>'
    rows = ''.join(f'<div class="cl-row{"" if label else " cl-plain"}"><dt>{e(label)}</dt><dd>{e(part)}</dd></div>'
                   for label, part in parts)
    return f'<dl class="conclusion cl-split">{rows}</dl>'


def chips_html(chips):
    """The lead facts as a key-value list: the chip's label on the left, its pieces (reordered only) on the right."""
    rows = []
    for chip in chips:
        label, lines = chip_parts(chip.get('text'))
        value = ''.join('<span class="kv-line">' + ''.join(
            ('' if n == 0 or piece.startswith('（') else ' ') + (
                f'<b>{e(piece)}</b>' if bold else
                f'<span class="kv-name">{e(piece)}</span>' if n == len(line) - 1 and n > 0 else f'<span>{e(piece)}</span>')
            for n, (piece, bold) in enumerate(line)) + '</span>' for line in lines)
        rows.append(f'<div class="kv"><dt>{e(label)}</dt><dd>{value}</dd></div>')
    return f'<dl class="facts">{"".join(rows)}</dl>' if rows else ''


def build(source: str, summary: dict, figures: list, *, title: str, source_name: str) -> tuple[str, dict]:
    checked = validate(summary, figures, source)
    conclusion = summary.get('conclusion') or {}
    confidence = summary.get('confidence') or {}
    board = next((f for f in figures if f.get('type') == 'bias_board'), None)
    tiles = tiles_html(board, confidence)
    if confidence.get('text') and not tiles:
        # No tiles to carry it (no bias board): the word stays above the conclusion.
        tiles = f'<p class="conf-line">{e(confidence["text"])}</p>'
    lead_html = conclusion_html(conclusion.get('text')) + chips_html(summary.get('chips') or [])
    sections = ''.join(SECTIONS[f['type']](f) for f in figures)
    limits = summary.get('limitations') or []
    limits_html = ''
    if limits:
        limits_html = _details(f'制約・注意（{len(limits)}件・開いて確認）',
                               '<ul>' + ''.join(f'<li>{e(x["text"])}</li>' for x in limits) + '</ul>'
                               ).replace('class="howto"', 'class="limits"', 1)
    note = f'<p class="note-line">{e(summary["note"])}</p>' if summary.get('note') else ''
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    encoded = base64.b64encode(source.encode('utf-8')).decode('ascii')
    document = f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><meta name="description" content="{e(title)}"><title>{e(title)}</title><style>{CSS}</style></head>
<body><main class="dash">
<header class="mast"><div><span class="eyebrow">{e(summary.get('eyebrow', 'REPORT'))}</span><h1>{e(summary.get('short_title', title))}</h1></div><div class="asof-chip">{e(summary.get('as_of_label', ''))}</div></header>
<section class="lead" aria-label="結論">{tiles}{lead_html}</section>
{sections}
{limits_html}
{note}
<footer><span>{e(title)}</span><a id="source-markdown" download="{e(source_name)}" href="data:text/markdown;charset=utf-8;base64,{encoded}">原稿（Markdown）を保存</a></footer>
</main></body></html>'''
    audit = {'source_sha256': digest, 'source_bytes': len(source.encode('utf-8')), 'figure_count': len(figures),
             'figure_items_verified': checked, 'numeric_check': 'pass', 'renderer': 'scripts/offchart_dashboard.py'}
    return document, audit
