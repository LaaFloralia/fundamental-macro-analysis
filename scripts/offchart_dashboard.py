"""One-page HTML dashboard for the off-chart report (no scripts, no network, no Markdown body).

The page answers three questions in order: which way per horizon (bias board), where the
liquidity is (named levels with their sweep state), and which US releases to watch. The BTCUSD page
(btc.dashboard) adds a news list and its own tiles, column and asset words through the figure fields.
Every displayed figure string is verified against its quoted line in the Markdown appendix,
and every number against the numbers of that line; the full Markdown is embedded only as a
download link. Explanations sit in collapsed <details> blocks.

The look follows the LAA design standard (laa-design, web, 2026.11.2): its token CSS verbatim, one teal face
(the overall verdict), rules instead of boxes, direction told by symbol, word and place. Shapes drawn from the
page's own values (vote points, the liquidity trunk and distance lines) are positions only and carry no text.
"""
from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
import hashlib
import html
import re

FIGURE_TYPES = ('bias_board', 'liquidity_map', 'event_table', 'news_list')
ITEM_TEXT = {
    'bias_board': ('label', 'display', 'baseline', 'change', 'direction', 'symbol', 'asof', 'note', 'fresh'),
    'liquidity_map': ('label', 'price_text', 'pool', 'state_word', 'touched', 'distance_text', 'target'),
    'event_table': ('date', 'time', 'label', 'status', 'previous', 'forecast', 'actual', 'diff', 'direction',
                    'symbol', 'memo'),
    'news_list': ('time', 'publisher', 'title', 'verification', 'reaction', 'direction', 'symbol', 'importance',
                  'interpretation'),
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


CONCLUSION_LABELS = ('理由', '反対材料')


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
CONCLUSION_HEAD = re.compile(r'^\s*[A-Z]{6}\s*[:：][^。]*。?\s*$')


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
NEAREST_SIDE = re.compile(r'^(?P<side>[上下]) (?P<name>.+) (?P<price>[\d,]+(?:\.\d+)?)(?P<dist>（[+\-−][\d,.]+）)$')
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
    for index, row in enumerate((conclusion or {}).get('rows') or []):
        if row.get('label') not in CONCLUSION_LABELS:
            raise DashboardError(f'conclusion.rows[{index}]: label "{row.get("label")}" is not one of {CONCLUSION_LABELS}')
        _check_text(f'conclusion.rows[{index}]', quoted(f'conclusion.rows[{index}]', row), [row.get('text')])
    if conclusion and not conclusion.get('rows'):
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
        for group, tally in (figure.get('tallies') or {}).items():
            # The tally beside a news group is counted from the group's own rows, never taken as given.
            members = [i for i in figure.get('items') or [] if i.get('group') == group]
            expected = f'評価 {sum(1 for i in members if i.get("assessed"))}件・見出し {len(members)}件'
            if kind != 'news_list' or tally != expected:
                raise DashboardError(f'{figure.get("id")}: tally of {group} is not "{expected}"')
        for where, item in rows:
            if item.get('url') and not re.match(r'^https://[^\s"<>]+$', str(item['url'])):
                raise DashboardError(f'{where}: link is not an https URL')
    return checked


# ------------------------------------------------------------------ mini diagrams (decorative, drawn from the row)

W, H = 112, 30


def _svg(body):
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
    """The row's trend line (its own observations, its own scale); nothing for any other diagram."""
    if not isinstance(viz, dict) or viz.get('type') != 'spark':
        return ''
    vals = [float(v) for v in viz.get('values') or []]
    if len(vals) < 3:
        return ''
    y = _scale(vals)
    step = (W - 8) / (len(vals) - 1)
    pts = ' '.join(f'{4 + i * step:.1f},{y(v):.1f}' for i, v in enumerate(vals))
    return _svg(f'<line x1="4" x2="{W - 4}" y1="{y(vals[0]):.1f}" y2="{y(vals[0]):.1f}" class="base"/>'
                f'<polyline points="{pts}" fill="none" class="trend" stroke-width="1.5" stroke-linejoin="round"/>'
                f'<circle cx="4" cy="{y(vals[0]):.1f}" r="2" class="dot0"/>'
                f'<circle cx="{W - 4}" cy="{y(vals[-1]):.1f}" r="3" class="dot1"/>')


# Each material's direction symbol as a point on one rule (down at the left, up at the right): strong at the ends,
# weak half-way, neutral in the middle. Points of one place stack upwards. Drawn from the rows' own symbols only;
# the tally (which merges rows of one factor) stays in words beside the horizon's table.
VOTE_PLACE = {'▼': (-2, 's'), '▽': (-1, 'w'), '—': (0, 'n'), '△': (1, 'w'), '▲': (2, 's')}


def votes_html(rows):
    stack, marks = {}, []
    for row in rows:
        place = VOTE_PLACE.get(str(row.get('symbol') or '').strip())
        if not place:
            continue
        k = stack.get(place[0], 0)
        stack[place[0]] = k + 1
        marks.append(f'<i class="v-{place[1]}" style="--x:{50 + place[0] * 22.5:g}%;--k:{k}"></i>')
    if not marks:
        return ''
    return (f'<span class="votes" aria-hidden="true" style="--n:{max(stack.values())}"><span class="v-axis"></span>'
            + ''.join(marks) + '</span>')


# ------------------------------------------------------------------ sections

def _sources(figure):
    links = ' / '.join(f'<a href="{e(s["url"])}">{e(s["label"])}</a>' for s in figure.get('sources') or []
                       if re.match(r'^https://', str(s.get('url', ''))))
    return f'<p class="src">出典: {links}</p>' if links else ''


def _details(title, body):
    return f'<details class="howto"><summary>{e(title)}</summary><div class="howto-body">{body}</div></details>'


MISMATCH_MARK = '集計と異なる'
TOTAL_LABEL = '総合'


def _tile_origin(t):
    """The origin line: none for the report's own verdict (「原稿の判断」), 「集計と異なる」 when it differs from the
    tally, and the rule name when the tile shows the tally itself (no report verdict)."""
    origin = str(t.get('origin') or '')
    if t.get('mismatch') and MISMATCH_MARK in origin:
        return f'<span class="tile-origin">{MISMATCH_MARK}</span>'
    if origin and not origin.startswith('原稿の判断'):
        return f'<span class="tile-origin">{e(origin)}</span>'
    return ''


def _tile(t, *, main=False, conf=None, rows=()):
    origin = _tile_origin(t)
    detail = f'<span class="tile-detail">{e(t["detail"])}</span>' if t.get('show_detail', t.get('mismatch')) else ''
    head = (f'<span class="tile-head"><span class="tile-symbol" aria-hidden="true">{e(t["symbol"])}</span>'
            f'<span class="tile-word">{e(t["direction"])}</span></span>')
    cls = (f'tile{" tile-main" if main else ""} tone-{e(t.get("tone"))}'
           f'{" is-mismatch" if t.get("mismatch") else ""}')
    label = f'<span class="tile-label">{e(t["label"])}</span>'
    conf_html = f'<span class="tile-conf">{e(conf)}</span>' if conf else ''
    if main:
        return f'<div class="{cls}">{label}{head}{origin}{detail}{conf_html}</div>'
    info = f'<span class="tile-info">{origin}{detail}</span>' if origin or detail else ''
    return f'<div class="{cls}">{label}{votes_html(rows)}{head}{info}</div>'


def tiles_html(figure, confidence=None):
    """The verdicts, first on the page: the overall one (総合, else the last) on the one teal face with the report's
    confidence word, the horizons beside it as ruled rows, each with its materials' directions as points on a rule.

    A tile repeats its rule result when the report's verdict differs from it, when the horizon has no material, or
    when a material is missing (show_detail)."""
    if not figure or not figure.get('verdicts'):
        return ''
    conf = (confidence or {}).get('text')
    verdicts = figure['verdicts']
    main = next((t for t in verdicts if t.get('label') == TOTAL_LABEL), verdicts[-1])
    items = figure.get('items') or []
    rest = ''.join(_tile(t, rows=[i for i in items if t.get('group') and i.get('group') == t['group']])
                   for t in verdicts if t is not main)
    label = figure.get('tiles_label', '期間ごとの金の向き')
    return (f'<div class="tiles" role="group" aria-label="{e(label)}">{_tile(main, main=True, conf=conf)}'
            + (f'<div class="tile-rest">{rest}</div>' if rest else '') + '</div>')


def reason_html(figure):
    """Why the report's verdict differs from the tally: a note after the conclusion, not ahead of it."""
    reason = (figure or {}).get('reason')
    return f'<p class="tile-reason">{e(reason["text"])}</p>' if reason else ''


def _fig_head(figure, anchor):
    """Title and, when the figure has one, its subtitle (kickers are no longer shown)."""
    sub = f'<p class="sub">{e(figure["subtitle"])}</p>' if figure.get('subtitle') else ''
    return f'<div class="fig-head"><h2 id="{anchor}">{e(figure["title"])}</h2>{sub}</div>'


BOARD_HEAD = ('<colgroup><col class="c-lbl"><col class="c-now"><col class="c-base"><col class="c-dir"></colgroup>'
              '<thead><tr><th scope="col">材料</th><th scope="col">現在</th><th scope="col">比較</th>'
              '<th scope="col">向き</th></tr></thead>')


def _fresh(i):
    """A value that is not the day's own (holiday, older observation): ○ and the row's own words."""
    if not i.get('fresh'):
        return ''
    return f'<span class="fresh"><span class="mark" aria-hidden="true">○</span>{e(i["fresh"])}</span>'


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
                    # One line per series with its own direction mark; the separator stays in the text, out of sight.
                    sep = '<span class="vh">／</span>'
                    change = '<span class="chg chg-series">' + sep.join(
                        f'<span class="ser s-{e(x["tone"])}">{e(x["text"])}</span>' for x in i['series']) + '</span>'
                middle = (f'<td class="cell-now"><span class="now-text">{keep_parts(i["display"])}</span>'
                          f'{viz_svg(i.get("viz"), i["tone"])}</td>'
                          f'<td class="cell-base"><span class="base-text">{e(i["baseline"])}</span>{change}</td>')
            body.append(
                f'<tr class="tone-{e(i["tone"])}{" text-row" if i.get("kind") == "text" else ""}'
                f'{" is-fresh" if i.get("fresh") else ""}">'
                f'<th scope="row"><span class="lbl">{e(i["label"])}</span>'
                f'<span class="asof">{e(i["asof"])}</span>{_fresh(i)}{note}</th>{middle}'
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
    """The row's side of the current price: above, below, or at (a zone that contains the price; the current row
    itself has none)."""
    if price is None or i.get('kind') == 'current':
        return ''
    low, high = _ends(i)
    if low > price:
        return ' side-above'
    if high < price:
        return ' side-below'
    return ' side-at'


def _distance(i, price):
    low, high = _ends(i)
    return 0.0 if low <= price <= high else min(abs(low - price), abs(high - price))


def _gauge(i, price, reach, half):
    """How far the row is from the current price: a teal line on a thin rule, its length the distance over the
    farthest shown row (or the band, if wider); the tan tick is the band's edge. Rows beyond the reach end in a ring.
    Only a shape: the distance in numbers is the row's own 「差」 text."""
    if price is None or not reach or i.get('kind') == 'current':
        return ''
    d = _distance(i, price)
    tick = f'<b style="--b:{min(half / reach, 1) * 100:.1f}%"></b>' if half else ''
    return (f'<span class="lv-gauge{" over" if d > reach * 1.0001 else ""}" aria-hidden="true" '
            f'style="--d:{min(d / reach, 1) * 100:.1f}%">{tick}<i></i></span>')


def _ladder_row(i, band='', price=None, gauge=''):
    pool = f'<span class="pool pool-{e(i["pool"].lower())}">{e(i["pool"])}</span>' if i.get('pool') else ''
    touched = f'<span class="touched">{e(i["touched"])}</span>' if i.get('touched') else ''
    target = f'<span class="target">{e(i["target"])}</span>' if i.get('target') else ''
    side = _side_class(i, price)
    # A tree: the price on the left, the row's node (its state symbol) on the trunk, the level and its state on the
    # right, then its distance in numbers and as a line.
    return (f'<li class="lv kind-{e(i["kind"])} state-{e(i["state"])}{side}{band}{" is-target" if target else ""}">'
            f'<span class="lv-price">{_price(i["price_text"])}</span>'
            f'<span class="lv-node" aria-hidden="true"><span class="icon">{e(i["icon"])}</span></span>'
            f'<span class="lv-name"><span class="lv-label">{e(i["label"])}</span>{pool}{target}</span>'
            f'<span class="lv-dist">{e(i["distance_text"])}</span>{gauge}'
            f'<span class="lv-state">{e(i["state_word"])}{touched}</span></li>')


def liquidity_section(figure):
    """The map in price order, high at the top: the levels outside the shown range above it and below it (each
    under 「表示範囲の外」), the shown range in between with the expected-move band as the thick part of the trunk and
    its two edges; then what could not be shown and why."""
    band = figure.get('band') or {}
    lower, upper = band.get('lower'), band.get('upper')
    items = figure['items']
    price = next((i['position'] for i in items if i.get('kind') == 'current'), None)
    shown = [i for i in items if not i.get('offscale')]
    reach = half = None
    if price is not None:
        far = [_distance(i, price) for i in shown if i.get('kind') != 'current']
        half = max(upper - price, price - lower) if lower is not None and upper is not None else None
        reach = max(far + ([half] if half else []), default=0) or None
    gauge = lambda i: _gauge(i, price, reach, half)
    rows, upper_done, lower_done = [], False, False
    # The edge's price sits in the price column and its label right of the trunk (a full-width line crossed it).
    edge = lambda word, value: (f'<li class="band-edge {"side-above" if word == "上限" else "side-below"}">'
                                f'<span class="be-value">{word} {value:,.2f}</span> '
                                f'<span class="be-label">{e(band["label"])}</span></li>')
    for i in shown:
        if upper is not None and not upper_done and i['position'] <= upper:
            rows.append(edge('上限', upper))
            upper_done = True
        if lower is not None and not lower_done and i['position'] < lower:
            if upper is not None and not upper_done:
                rows.append(edge('上限', upper))
                upper_done = True
            rows.append(edge('下限', lower))
            lower_done = True
        rows.append(_ladder_row(i, _band_class(i, lower, upper), price, gauge(i)))
    if upper is not None and not upper_done and lower is not None and not lower_done:
        rows.append(edge('上限', upper))
    if lower is not None and not lower_done:
        rows.append(edge('下限', lower))
    off = sorted((i for i in items if i.get('offscale')), key=lambda i: -_ends(i)[1])
    above = [i for i in off if price is not None and _ends(i)[0] > price]
    below = [i for i in off if i not in above]

    def outside(group, where, arrow):
        if not group:
            return ''
        return (f'<div class="off-wrap off-{where}"><div class="offscale"><span><span class="arrow" aria-hidden="true">'
                f'{arrow}</span>表示範囲の外</span><ul class="ladder">'
                + ''.join(_ladder_row(i, price=price, gauge=gauge(i)) for i in group) + '</ul></div></div>')
    # In the document the shown range comes first (the reader's first look); on screen the outside rows sit where
    # their prices are (CSS order), so every price on the map reads from high to low.
    ladder = (f'<div class="liq-map"><ul class="ladder">{"".join(rows)}</ul>'
              f'{outside(above, "above", "↑")}{outside(below, "below", "↓")}</div>')
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
            f'{_fig_head(figure, "liq-title")}{cautions}{ladder}{missing}{howto}</section>')


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
        bare = all(i.get(k) in (None, '', '—') for k in ('previous', 'forecast', 'actual', 'diff'))  # no numbers to show
        pending = not bare and all(i.get(k) in (None, '', '—') for k in ('actual', 'diff'))  # previous/forecast only
        rows.append(
            f'<tr class="{"released" if i.get("released") else "upcoming"} tone-{e(i["tone"])}{" same-time" if same else ""}'
            f'{" no-nums" if bare else ""}{" no-result" if pending else ""}">'
            f'<td class="ev-time">{"" if same else e(i["time"])}</td>'
            f'<th scope="row" class="ev-name">{tier}<span>{e(i["label"])}</span>{memo}</th>'
            f'<td class="ev-num ev-prev" data-k="前回">{e(i["previous"])}</td>'
            f'<td class="ev-num ev-fcst" data-k="予想">{e(i["forecast"])}</td>'
            f'<td class="ev-num ev-act" data-k="結果">{e(i["actual"])}</td><td class="ev-diff">{e(i["diff"])}</td>'
            f'<td class="cell-dir"><span class="dir-symbol" aria-hidden="true">{e(i["symbol"])}</span>'
            f'<span class="dir-word">{e(i["direction"])}</span></td></tr>')
    howto = _details('指標の選び方と向きの意味（開いて確認）', f'<p>{e(figure.get("legend"))}</p>{_sources(figure)}')
    cautions = ''.join(f'<p class="caution" role="note">{e(c["text"])}</p>' for c in figure.get('cautions') or [])
    return (f'<section class="data-figure fig-events" id="calendar" aria-labelledby="ev-title">'
            f'{_fig_head(figure, "ev-title")}{cautions}'
            f'<div class="board-wrap"><table class="events"><thead><tr><th scope="col">時刻（JST）</th><th scope="col">指標</th>'
            f'<th scope="col" class="r">前回</th><th scope="col" class="r">予想</th><th scope="col" class="r">結果</th>'
            f'<th scope="col">差・予測</th>'
            f'<th scope="col">{e(figure.get("direction_header", "金への向き"))}</th></tr></thead><tbody>{"".join(rows)}</tbody>'
            f'</table></div>{howto}</section>')


def _news_row(i):
    title = (f'<a href="{e(i["url"])}">{e(i["title"])}</a>' if i.get('url') else e(i['title']))
    meta = '・'.join(e(x) for x in (i.get('publisher'), i.get('verification'), i.get('reaction')) if x)
    judged = '・'.join(e(x) for x in (i.get('importance'), i.get('interpretation')) if x)
    return (f'<li class="ni tone-{e(i.get("tone", "neutral"))}"><span class="ni-time">{e(i["time"])}</span>'
            f'<span class="ni-dir"><span class="dir-symbol" aria-hidden="true">{e(i.get("symbol", "—"))}</span>'
            f'<span class="dir-word">{e(i.get("direction", ""))}</span></span>'
            f'<span class="ni-title">{title}</span><span class="ni-meta">{meta}</span>'
            + (f'<span class="ni-eval">{judged}</span>' if judged else '') + '</li>')


def news_section(figure):
    """Headlines per group (newest first): the assessed ones with their judgement, the others folded below."""
    blocks = []
    for n, group in enumerate(figure.get('groups') or []):
        rows = [i for i in figure['items'] if i['group'] == group]
        if not rows:
            continue
        main = [i for i in rows if i.get('assessed')]
        rest = [i for i in rows if not i.get('assessed')]
        listed = f'<ul class="news">{"".join(_news_row(i) for i in main)}</ul>' if main else ''
        more = _details(f'評価していない見出し（{len(rest)}件・開いて確認）',
                        f'<ul class="news">{"".join(_news_row(i) for i in rest)}</ul>') if rest else ''
        tally = figure.get('tallies', {}).get(group)
        blocks.append(f'<div class="horizon"><h3 class="hz-head" id="news-h{n + 1}"><span class="hz-name">{e(group)}</span>'
                      + (f'<span class="grp-tally">{e(tally)}</span>' if tally else '') + f'</h3>{listed}{more}</div>')
    cautions = ''.join(f'<p class="caution" role="note">{e(c["text"])}</p>' for c in figure.get('cautions') or [])
    howto = _details('選び方と評価の意味（開いて確認）', f'<p>{e(figure.get("legend"))}</p>{_sources(figure)}')
    return (f'<section class="data-figure fig-news" id="news" aria-labelledby="news-title">{_fig_head(figure, "news-title")}'
            f'{cautions}{"".join(blocks)}{howto}</section>')


SECTIONS = {'bias_board': bias_section, 'liquidity_map': liquidity_section, 'event_table': event_section,
            'news_list': news_section}

# The LAA design standard's web values, pasted from `laa-design tokens --css web` (standard 2026.11.2) and not
# edited; the page's size class is laa-web-1280, and under 700 px the same class takes the web-390 values.
TOKENS_CSS = """
/* laa-design tokens --css web（基準 2026.11.2）。値を書き換えない。 */
:root {
  --laa-bg: #F6F2E9;
  --laa-surface: #FFFDF8;
  --laa-wash: #EBE6DC;
  --laa-text: #213536;
  --laa-text-muted: #596968;
  --laa-accent: #00585E;
  --laa-on-accent: #F6F2E9;
  --laa-rule: #D7D5CC;
  --laa-rule-warm: #B99B7A;
  --laa-selection: #DFEAE5;
  --laa-attention: #6C244C;
  --laa-font-heading: "Georgia", "Times New Roman", "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif;
  --laa-font-body: "Georgia", "Times New Roman", "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif;
  --laa-font-label: "Georgia", "Times New Roman", "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", serif;
  --laa-font-number: "Iowan Old Style", "Hiragino Mincho ProN", "Yu Mincho", "Georgia", serif;
  --laa-font-code: ui-monospace, "SFMono-Regular", "Menlo", monospace;
  --laa-space-hair: 4px;
  --laa-space-small: 8px;
  --laa-space-base: 16px;
  --laa-space-block: 24px;
  --laa-space-section: 32px;
  --laa-space-group: 48px;
  --laa-space-page: 72px;
  --laa-rule-line: 1px solid #D7D5CC;
  --laa-line-body: 1.75;
  --laa-line-heading: 1.3;
  --laa-label-tracking: 0.11em;
  --laa-t-quick: 120ms;
  --laa-t-base: 220ms;
  --laa-t-gentle: 300ms;
  --laa-t-slow: 420ms;
}
/* web-1280 */ .laa-web-1280 { --laa-margin: 68px; --laa-display: 40px; --laa-title: 34px; --laa-heading: 24px; --laa-subheading: 18px; --laa-lead: 16px; --laa-body: 15px; --laa-small: 14px; --laa-label: 12px; --laa-mark: 24px; --laa-figure-1: 21px; --laa-figure-2: 34px; --laa-figure-3: 55px; }
/* web-390 */ .laa-web-390 { --laa-margin: 20px; --laa-display: 34px; --laa-title: 28px; --laa-heading: 20px; --laa-subheading: 18px; --laa-lead: 16px; --laa-body: 15px; --laa-small: 14px; --laa-label: 12px; --laa-mark: 20px; --laa-figure-1: 21px; --laa-figure-2: 34px; --laa-figure-3: 55px; }
@media(max-width:700px){.laa-web-1280{--laa-margin:20px;--laa-display:34px;--laa-title:28px;--laa-heading:20px;--laa-subheading:18px;--laa-lead:16px;--laa-body:15px;--laa-small:14px;--laa-label:12px;--laa-mark:20px;--laa-figure-1:21px;--laa-figure-2:34px;--laa-figure-3:55px}}
"""

# Paper, ink and one teal face; rules instead of boxes. Four text sizes (display, heading, body, label) and the
# smallest figure size for the current price. Direction is told by symbol, word and place, never by colour alone.
CSS = TOKENS_CSS + """
:root{background:var(--laa-bg);color:var(--laa-text);color-scheme:light}
*{box-sizing:border-box}
body{margin:0;padding:0;background:var(--laa-bg);color:var(--laa-text);font-family:var(--laa-font-body);font-size:var(--laa-body);
line-height:var(--laa-line-body);font-synthesis:none;-webkit-font-smoothing:antialiased;-webkit-text-size-adjust:100%}
a{color:var(--laa-accent);text-underline-offset:.2em;text-decoration-thickness:1px}
.lv-price,.be-value,.now-text,.chg,.ev-time,.ev-num,.kv-line b,.lv-dist,.ni-time{font-family:var(--laa-font-number);
font-variant-numeric:lining-nums tabular-nums}
.reports-back{display:flex;align-items:center;max-width:1280px;min-height:44px;margin:0 auto;padding:0 var(--laa-margin);
font-size:var(--laa-label);color:var(--laa-text-muted)}
main.dash{max-width:1280px;margin:0 auto;padding:24px var(--laa-margin) 72px;overflow-wrap:break-word}
.eyebrow{display:block;font-size:var(--laa-label);line-height:var(--laa-line-heading);letter-spacing:var(--laa-label-tracking);
color:var(--laa-text-muted)}
h1{margin:12px 0 0;font-family:var(--laa-font-heading);font-size:var(--laa-display);font-weight:400;line-height:1.15;letter-spacing:0}
h2{margin:0;font-family:var(--laa-font-heading);font-size:var(--laa-heading);font-weight:400;line-height:var(--laa-line-heading)}
.asof-chip{margin:12px 0 0;color:var(--laa-text-muted)}
.lead{margin-top:48px}
.tiles{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,7fr);column-gap:48px;align-items:stretch}
.tile-main{display:flex;flex-direction:column;align-items:flex-start;padding:24px 32px;background:var(--laa-accent);
color:var(--laa-on-accent)}
.tile-label{font-size:var(--laa-label);line-height:var(--laa-line-heading);letter-spacing:var(--laa-label-tracking);color:var(--laa-text-muted)}
.tile-head{display:flex;align-items:baseline;gap:12px;font-family:var(--laa-font-heading);font-size:var(--laa-heading);
line-height:var(--laa-line-heading)}
.tile-symbol{flex:none}
.tile-origin{display:block;font-size:var(--laa-label);line-height:1.5;color:var(--laa-attention)}
.tile-detail{display:block;font-size:var(--laa-label);line-height:1.5;color:var(--laa-text-muted)}
.tile-main .tile-label,.tile-main .tile-origin,.tile-main .tile-detail{color:var(--laa-on-accent)}
.tile-main .tile-head{margin:16px 0 24px;font-size:var(--laa-display);line-height:1.15}
.tile-main .tile-origin{margin-bottom:4px}
.tile-conf{align-self:stretch;margin-top:auto;padding-top:16px;border-top:1px solid var(--laa-rule-warm)}
.tile-main .tile-detail+.tile-conf,.tile-main .tile-origin+.tile-conf{margin-top:24px}
.tile-rest{display:flex;flex-direction:column;border-top:1px solid var(--laa-rule)}
.tile-rest .tile{flex:1;display:grid;grid-template-columns:3.5em 12rem minmax(0,1fr);grid-template-areas:"label head votes" "label info info";
column-gap:24px;align-content:center;align-items:center;padding:16px 0;border-bottom:1px solid var(--laa-rule)}
.tile-rest .tile-label{grid-area:label}
.tile-rest .tile-head{grid-area:head}
.tile-info{grid-area:info;padding-top:8px}
.votes{grid-area:votes;position:relative;z-index:0;display:block;height:calc(16px + var(--n) * 10px);margin:0 24px;max-width:320px}
.votes::before{content:"";position:absolute;left:0;right:0;bottom:8px;height:1px;background:var(--laa-rule)}
.votes::after{content:"";position:absolute;z-index:-1;left:50%;bottom:4px;width:1px;height:9px;background:var(--laa-rule-warm)}
.v-axis{position:absolute;left:0;right:0;bottom:0;height:16px}
.v-axis::before,.v-axis::after{position:absolute;bottom:3px;font-size:var(--laa-label);line-height:1;color:var(--laa-text-muted)}
.v-axis::before{content:"▼";right:calc(100% + 8px)}
.v-axis::after{content:"▲";left:calc(100% + 8px)}
.votes i{position:absolute;left:var(--x);bottom:calc(4px + var(--k) * 10px);width:8px;height:8px;margin-left:-4px;border-radius:50%}
.votes .v-s{background:var(--laa-accent)}
.votes .v-w{background:var(--laa-bg);border:1.5px solid var(--laa-accent)}
.votes .v-n{background:var(--laa-text-muted)}
.conclusion{margin:48px 0 0}
p.conclusion{padding:16px 0;border-top:1px solid var(--laa-rule);border-bottom:1px solid var(--laa-rule);white-space:pre-line}
.cl-split{border-top:1px solid var(--laa-rule)}
.cl-row,.kv{display:grid;grid-template-columns:8em minmax(0,46em);column-gap:24px;align-items:baseline;padding:16px 0;
border-bottom:1px solid var(--laa-rule)}
.cl-row dt,.kv dt{font-size:var(--laa-label);color:var(--laa-text-muted)}
.cl-row dd{margin:0;min-width:0}
.cl-plain{grid-template-columns:minmax(0,46em)}.cl-plain dt{display:none}
.facts{margin:0;padding:0}
.kv dd{margin:0;min-width:0;overflow-wrap:anywhere}
.kv-line{display:block}.kv-line+.kv-line{margin-top:4px}.kv-line b{font-weight:700}
.tile-reason{margin:24px 0 0;padding:0 0 0 16px;max-width:54em;border-left:1px solid var(--laa-attention)}
.conf-line{margin:24px 0 0}
.data-figure{margin-top:72px;padding-top:24px;border-top:1px solid var(--laa-rule-warm)}
.fig-head{margin-bottom:24px}.fig-head .sub{margin:8px 0 0;color:var(--laa-text-muted)}
.horizon+.horizon{margin-top:48px}
.hz-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin:0 0 8px;font-size:var(--laa-body);font-weight:400;
line-height:var(--laa-line-heading)}
.hz-name{font-weight:700}.grp-tally{font-size:var(--laa-label);color:var(--laa-text-muted)}
.caution,.liq-notice,.liq-missing,.no-figures{margin:0 0 16px;padding:0 0 0 16px;max-width:54em;border-left:1px solid var(--laa-attention)}
.liq-notice{border-left-color:var(--laa-accent);color:var(--laa-text-muted)}
.liq-missing{margin:24px 0 0}
.aux{font-size:var(--laa-label)}
.board-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse}
.board{table-layout:fixed}
.board .c-lbl{width:30%}.board .c-now{width:21%}.board .c-base{width:29%}.board .c-dir{width:20%}
th,td{padding:16px 16px 16px 0;text-align:left;vertical-align:top;border-bottom:1px solid var(--laa-rule)}
thead th{padding:0 16px 8px 0;font-size:var(--laa-label);font-weight:400;line-height:var(--laa-line-heading);
color:var(--laa-text-muted);border-bottom:1px solid var(--laa-rule-warm)}
th.r{text-align:right}
.board th[scope=row]{font-weight:400}
.lbl{display:block}
.asof,.note,.fresh{display:block;font-size:var(--laa-label);line-height:1.5;color:var(--laa-text-muted)}
.asof{margin-top:4px}.fresh{color:var(--laa-attention)}.fresh .mark{margin-right:4px}
.now-text{display:block}.nw{white-space:nowrap}
.viz{display:block;margin-top:8px}
.viz .base{stroke:var(--laa-rule);stroke-width:1;stroke-dasharray:2 2}
.viz .trend{stroke:var(--laa-accent)}.viz .dot0{fill:var(--laa-rule-warm)}.viz .dot1{fill:var(--laa-accent)}
.base-text{display:block;font-size:var(--laa-label);line-height:1.5;color:var(--laa-text-muted)}
.chg{display:block;margin-top:4px}
.chg-series .ser{display:block}
.ser::before{display:inline-block;width:1.4em;color:var(--laa-accent)}
.ser.s-positive::before{content:"△"}.ser.s-negative::before{content:"▽"}
.ser.s-neutral::before{content:"—";color:var(--laa-text-muted)}
.board .chg .s-neutral{color:var(--laa-text-muted)}
.cell-dir{white-space:nowrap}
.dir-symbol{display:inline-block;width:1.4em;color:var(--laa-accent)}
.tone-neutral .dir-symbol{color:var(--laa-text-muted)}
.vh{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.liq-map{display:flex;flex-direction:column}
.liq-map>.ladder{order:2}.off-above{order:1;margin-bottom:24px}.off-below{order:3;margin-top:24px}
.ladder{list-style:none;margin:0;padding:0;--px:7.5rem;--node:24px;--gap:16px}
.lv{position:relative;display:grid;grid-template-columns:var(--px) var(--node) minmax(0,28rem) minmax(0,1fr) 5.5rem 7.5rem;
grid-template-areas:"price node name state dist gauge";column-gap:var(--gap);align-items:baseline;padding:12px 0;
border-bottom:1px solid var(--laa-rule)}
.lv>span{min-width:0;overflow-wrap:anywhere}
.lv-price{grid-area:price;text-align:right}
.lv-node{grid-area:node;align-self:stretch;position:relative;margin:-12px 0 -13px}
.lv-node::before{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;margin-left:-.5px;background:var(--laa-rule)}
.lv.in-band .lv-node::before{width:2px;margin-left:-1px;background:var(--laa-accent)}
.lv.part-band .lv-node::before{width:2px;margin-left:-1px;background:var(--laa-rule-warm)}
.lv-node .icon{position:absolute;left:50%;top:15px;width:20px;height:20px;margin-left:-10px;display:flex;align-items:center;
justify-content:center;border:1px solid var(--laa-rule);border-radius:50%;background:var(--laa-bg);font-size:var(--laa-label);
line-height:1;color:var(--laa-text)}
.state-swept .lv-price,.state-broken .lv-price,.state-forming .lv-price,.kind-reference .lv-price,
.state-swept .icon,.state-broken .icon,.state-forming .icon,.kind-reference .icon{color:var(--laa-text-muted)}
.kind-reference .icon{border-style:dashed}
.kind-zone .icon{height:28px;top:11px;border-radius:4px}
.is-target .icon,.kind-draw .icon{border:2px solid var(--laa-accent);color:var(--laa-accent)}
.lv-name{grid-area:name}.lv-label{margin-right:8px}
.pool{display:inline-block;font-size:var(--laa-label);letter-spacing:var(--laa-label-tracking);color:var(--laa-text-muted)}
.target{display:block;max-width:100%;font-size:var(--laa-label);line-height:1.5;color:var(--laa-accent);overflow-wrap:anywhere}
.lv-state{grid-area:state;font-size:var(--laa-label)}
.touched{display:block;color:var(--laa-text-muted)}
.kind-reference .lv-label,.kind-reference .lv-state{color:var(--laa-text-muted)}
.lv-dist{grid-area:dist;text-align:right;font-size:var(--laa-label);color:var(--laa-text-muted)}
.lv-gauge{grid-area:gauge;position:relative;align-self:start;height:9px;margin-top:9px}
.lv-gauge::before{content:"";position:absolute;left:0;right:0;top:4px;height:1px;background:var(--laa-rule)}
.lv-gauge i{position:absolute;left:0;top:3.5px;width:var(--d);height:2px;background:var(--laa-accent)}
.lv-gauge i::after{content:"";position:absolute;right:-3px;top:-2px;width:6px;height:6px;border-radius:50%;background:var(--laa-accent)}
.lv-gauge.over i::after{background:var(--laa-bg);border:1.5px solid var(--laa-accent)}
.lv-gauge b{position:absolute;left:var(--b);top:0;width:1px;height:9px;background:var(--laa-rule-warm)}
.kind-current{margin-top:-1px;background:var(--laa-bg);border-top:1px solid var(--laa-accent);border-bottom:1px solid var(--laa-accent)}
.kind-current .lv-price{color:var(--laa-accent);font-size:var(--laa-figure-1);line-height:var(--laa-line-heading)}
.kind-current .lv-label{color:var(--laa-accent)}
.kind-current .icon{background:var(--laa-accent);border-color:var(--laa-accent);color:var(--laa-on-accent)}
.ladder>.kind-current{position:-webkit-sticky;position:sticky;top:0;bottom:0;z-index:2}
.band-edge{position:relative;display:grid;grid-template-columns:var(--px) var(--node) minmax(0,1fr);column-gap:var(--gap);
align-items:baseline;padding:8px 0;font-size:var(--laa-label);line-height:1.5;color:var(--laa-accent);border-bottom:1px solid var(--laa-rule-warm)}
.band-edge.side-below{margin-top:-1px;border-top:1px solid var(--laa-rule-warm);border-bottom-color:var(--laa-rule)}
.be-value{grid-column:1;text-align:right;white-space:nowrap}
.be-label{grid-column:3;min-width:0;overflow-wrap:anywhere;color:var(--laa-text-muted)}
.band-edge::before,.band-edge::after{content:"";position:absolute;left:calc(var(--px) + var(--gap) + var(--node) / 2 - .5px);
width:1px;background:var(--laa-rule)}
.band-edge::before{top:0;height:50%}.band-edge::after{top:50%;bottom:-1px}
.band-edge.side-above::after,.band-edge.side-below::before{width:2px;margin-left:-.5px;background:var(--laa-accent)}
.offscale>span{display:block;padding:8px 0;font-size:var(--laa-label);line-height:1.5;color:var(--laa-text-muted);
border-bottom:1px solid var(--laa-rule)}
.offscale .arrow{margin-right:8px}
.offscale .lv-node::before{width:0;margin-left:0;background:none;border-left:1px dashed var(--laa-rule)}
.events td,.events th{padding:12px 16px 12px 0}
.events thead th{padding:0 16px 8px 0}
.events tr.day th{padding:24px 0 8px;font-size:var(--laa-label);font-weight:700;line-height:1.5}
.ev-time{white-space:nowrap}
.ev-name{font-weight:400;min-width:12em}.ev-name>span{display:block}
.tier{font-size:var(--laa-label);line-height:1.5;letter-spacing:var(--laa-label-tracking);color:var(--laa-text-muted)}
.tier-a{color:var(--laa-accent)}
.memo{font-size:var(--laa-label);line-height:1.5;color:var(--laa-text-muted)}
.ev-num{text-align:right;white-space:nowrap}
.events tr.empty td{color:var(--laa-text-muted)}
.events tr.same-time td,.events tr.same-time th{padding-top:4px}
details.howto,details.limits{margin-top:24px;border-top:1px solid var(--laa-rule);border-bottom:1px solid var(--laa-rule)}
.data-figure>details.howto{margin-top:0;border-top:0}
.data-figure>.caution+details.howto,.data-figure>.liq-missing+details.howto{margin-top:24px;border-top:1px solid var(--laa-rule)}
details>summary{display:flex;align-items:center;gap:8px;min-height:44px;cursor:pointer;list-style:none;color:var(--laa-accent)}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"+";width:1em;font-family:var(--laa-font-number)}
details[open]>summary::before{content:"−"}
.howto-body{padding:0 0 24px;max-width:54em;color:var(--laa-text-muted)}
.howto-body p{margin:0 0 16px}.howto-body ul{margin:0 0 16px;padding-left:1.2em}.howto-body li+li{margin-top:8px}
.rules b{font-weight:700;color:var(--laa-text)}
.src{display:flex;flex-wrap:wrap;align-items:center;gap:0 8px;font-size:var(--laa-label)}
.src a,.ni-title a{display:inline-flex;align-items:center;min-height:44px}
details.limits{margin-top:72px}
.note-line{margin:24px 0 0;font-size:var(--laa-label);color:var(--laa-text-muted)}
.news{list-style:none;margin:0;padding:0;border-top:1px solid var(--laa-rule)}
.ni{display:grid;grid-template-columns:6.5em minmax(0,1fr) 8em;grid-template-areas:"time title dir" "time meta dir" "time eval dir";
column-gap:16px;padding:16px 0;border-bottom:1px solid var(--laa-rule)}
.ni-time{grid-area:time;white-space:nowrap}.ni-title{grid-area:title;min-width:0;overflow-wrap:anywhere}
.ni-title a{color:var(--laa-text)}.ni-meta{grid-area:meta;font-size:var(--laa-label);color:var(--laa-text-muted)}
.ni-eval{grid-area:eval;margin-top:4px;font-size:var(--laa-label);min-width:0;overflow-wrap:anywhere}
.ni-dir{grid-area:dir;white-space:nowrap;text-align:right}
footer{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px 24px;margin-top:48px;padding-top:8px;
border-top:1px solid var(--laa-rule);font-size:var(--laa-label);color:var(--laa-text-muted)}
#source-markdown{display:inline-flex;align-items:center;min-height:44px;font-size:var(--laa-body)}
@media(max-width:700px){
main.dash{padding-top:8px}
.lead{margin-top:32px}
.tiles{grid-template-columns:minmax(0,1fr);row-gap:24px}
.tile-main{margin:0 calc(-1 * var(--laa-margin));padding:24px var(--laa-margin)}
.tile-rest .tile{grid-template-columns:3.5em minmax(0,1fr);grid-template-areas:"label head" ". votes" ". info";row-gap:8px;column-gap:16px}
.votes{margin:0 24px;max-width:none}
.tile-info{padding-top:0}
.conclusion{margin-top:32px}
.cl-row,.kv{grid-template-columns:minmax(0,1fr);row-gap:4px}
.data-figure{margin-top:48px}
.horizon+.horizon{margin-top:32px}
.hz-head{margin:0;padding-bottom:8px;border-bottom:1px solid var(--laa-rule-warm)}
.board thead,.events thead{display:none}
.board,.board tbody,.events,.events tbody{display:block}
.board tr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"lbl dir" "now now" "base base";gap:4px 16px;
padding:16px 0;border-bottom:1px solid var(--laa-rule)}
.board th[scope=row]{grid-area:lbl;min-width:0;padding:0;border:0}
.board .cell-dir{grid-area:dir;padding:0;border:0;text-align:right}
.board .cell-now{grid-area:now;padding:0;border:0}
.board .cell-now .viz{display:inline-block;margin:0 0 0 16px;vertical-align:middle}
.now-text{display:inline}
.board .cell-base{grid-area:base;padding:0;border:0}
.base-text{display:inline;margin-right:8px}
.chg{display:inline}.chg-series{display:block}
.board .cell-text{grid-column:1/-1;grid-row:2;padding:0;border:0}
.ladder{--px:5.25rem;--node:24px;--gap:8px}
.lv{grid-template-columns:var(--px) var(--node) minmax(0,1fr);grid-template-areas:"price node name" "dist node state" "gauge node state"}
.lv-gauge{margin-top:6px}
.events tr{position:relative;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;padding:12px 0 12px calc(3.5em + 12px);
border-bottom:1px solid var(--laa-rule)}
.events tr::after{content:"";order:3;flex-basis:100%;height:0}
.events td,.events th{padding:0;border:0}
.ev-time{position:absolute;left:0;top:12px}
.ev-name{order:1;flex:1 1 0;min-width:0}
.events .cell-dir{order:2;flex:none;text-align:right}
.ev-num{order:4;font-size:var(--laa-label)}
.ev-num::before{content:attr(data-k) " ";color:var(--laa-text-muted)}
.ev-diff{order:5;flex-basis:100%}
.events tr.day,.events tr.empty{display:block;padding:0;border:0}
.events tr.day::after,.events tr.empty::after{display:none}
.events tr.day th{display:block;padding:24px 0 8px;border-bottom:1px solid var(--laa-rule)}
.events tr.empty td{display:block;padding:12px 0}
.events tr.no-nums .ev-num,.events tr.no-nums .ev-diff{display:none}
.events tr.no-result .ev-act,.events tr.no-result .ev-diff{display:none}
.events tr.same-time{padding-top:4px}
.ni{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"time dir" "title title" "meta meta" "eval eval";row-gap:4px}
}
@media print{.reports-back,details{display:none}.ladder>.kind-current{position:static}}
"""


def conclusion_html(text, rows=None):
    """The labelled rows (理由・反対材料) of a conclusion in the current format, else the line as one paragraph.
    ``rows`` (BTCUSD): the labelled rows given directly, each quoting its own line."""
    if rows:
        body = ''.join(f'<div class="cl-row"><dt>{e(r["label"])}</dt><dd>{e(r["text"])}</dd></div>' for r in rows)
        return f'<dl class="conclusion cl-split">{body}</dl>'
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
    # Verdicts, then why (the conclusion), then the next release and the nearest levels, then why a verdict differs
    # from the tally.
    lead_html = (conclusion_html(conclusion.get('text'), conclusion.get('rows')) + chips_html(summary.get('chips') or [])
                 + reason_html(board))
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
<html lang="ja" class="laa-web-1280"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><meta name="description" content="{e(title)}"><title>{e(title)}</title><style>{CSS}</style></head>
<body><main class="dash">
<header class="mast"><span class="eyebrow">{e(summary.get('eyebrow', 'REPORT'))}</span><h1>{e(summary.get('short_title', title))}</h1><p class="asof-chip">{e(summary.get('as_of_label', ''))}</p></header>
<section class="lead" aria-label="結論">{tiles}{lead_html}</section>
{sections}
{limits_html}
{note}
<footer><span>{e(title)}</span><a id="source-markdown" download="{e(source_name)}" href="data:text/markdown;charset=utf-8;base64,{encoded}">原稿（Markdown）を保存</a></footer>
</main></body></html>'''
    audit = {'source_sha256': digest, 'source_bytes': len(source.encode('utf-8')), 'figure_count': len(figures),
             'figure_items_verified': checked, 'numeric_check': 'pass', 'renderer': 'scripts/offchart_dashboard.py'}
    return document, audit
