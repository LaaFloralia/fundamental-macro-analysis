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
# Numbers each mini diagram encodes; every one must be a number of the row's quoted line.
# Sparklines carry a source path instead and are rechecked against the collection (report_bundle.check_bindings).
VIZ_NUMBERS = {
    'delta': lambda v: [v.get('from'), v.get('to')],
    'track': lambda v: [v.get('value')],
    'split': lambda v: [v.get('left'), v.get('right'), v.get('track')],
    'gap': lambda v: [abs(v['value']) if isinstance(v.get('value'), (int, float)) else v.get('value')],
    'votes': lambda v: [v.get('up'), v.get('down'), v.get('flat')],
    'bars': lambda v: [b.get('value') for b in v.get('items') or []],
    'probs': lambda v: [x.get('value') for x in v.get('segments') or []],
}
VIZ_LABELS = {'bars': 'items', 'probs': 'segments'}


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
    """Labels and numbers drawn by a mini diagram must be in the quoted line; sparklines must name their series."""
    if not isinstance(viz, dict) or not viz:
        return
    kind = viz.get('type')
    if kind == 'spark':
        if not viz.get('path') or not isinstance(viz.get('count'), int) or len(viz.get('values') or []) != viz['count']:
            raise DashboardError(f'{where}.viz: sparkline without its source series')
        return
    if kind not in VIZ_NUMBERS:
        raise DashboardError(f'{where}.viz: unknown diagram type {kind}')
    if kind in VIZ_LABELS:
        _check_text(where + '.viz', quote, [x.get('label') for x in viz.get(VIZ_LABELS[kind]) or []])
    values = VIZ_NUMBERS[kind](viz)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in values if v is not None):
        raise DashboardError(f'{where}.viz: diagram values must be numbers')
    allowed = numbers(quote)
    if kind == 'gap':  # drawn by size; the sign is the bar's side
        allowed = {n.lstrip('-') for n in allowed}
    extra = numbers(' '.join(str(v) for v in values if v is not None)) - allowed
    if extra:
        raise DashboardError(f'{where}.viz: values {sorted(extra)} are not in its source line')


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
    confidence = summary.get('confidence')
    if confidence:
        # 「信頼度 低」 from 「信頼度: Low ｜ スコア 0 …」: the word must be the line's (report_bundle.CONFIDENCE_JA).
        from scripts.report_bundle import CONFIDENCE_JA
        word = {ja: en for en, ja in CONFIDENCE_JA.items()}.get(str(confidence.get('text') or '').removeprefix('信頼度 '))
        if not word or not re.match(rf'\s*信頼度[:：]\s*\**{re.escape(word)}\b', quoted('confidence', confidence)):
            raise DashboardError('confidence: the word is not in its source line')
    for index, chip in enumerate(summary.get('chips') or []):
        _check_text(f'chips[{index}]', quoted(f'chips[{index}]', chip), [chip.get('text')])
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
        rows = [(f'{figure.get("id")}[{index}]', item) for index, item in enumerate(figure.get('items') or [])]
        rows += [(f'{figure.get("id")}.aux[{index}]', item) for index, item in enumerate(figure.get('aux') or [])]
        for where, item in rows:
            quote = quoted(where, item)
            _check_text(where, quote, [item.get(k) for k in ITEM_TEXT[kind]])
            _check_viz(where, quote, item.get('viz'))
            checked.append(where)
        for index, tile in enumerate(figure.get('verdicts') or []):
            where = f'{figure.get("id")}.verdicts[{index}]'
            _check_text(where, quoted(where, tile), [tile.get(k) for k in VERDICT_TEXT])
        extras = [(f'{figure.get("id")}.cautions[{i}]', c) for i, c in enumerate(figure.get('cautions') or [])]
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
    if kind == 'delta':
        # From the baseline (left) to now (right); the rise is the move over its strong threshold
        # (`scale`), full height at 1.5 times. Without a scale only the direction is drawn.
        a, b = float(viz['from']), float(viz['to'])
        scale = float(viz.get('scale') or 0) or abs(b - a) or 1
        rise = max(-1.5, min(1.5, (b - a) / scale)) / 1.5 * (H / 2 - 5)
        y0, y1 = H / 2 + rise / 2, H / 2 - rise / 2
        return _svg(f'<line x1="18" x2="{W - 18}" y1="{y0:.1f}" y2="{y0:.1f}" class="base"/>'
                    f'<line x1="18" y1="{y0:.1f}" x2="{W - 18}" y2="{y1:.1f}" stroke="{color}" stroke-width="2"/>'
                    f'<circle cx="18" cy="{y0:.1f}" r="3" class="dot0"/><circle cx="{W - 18}" cy="{y1:.1f}" r="3.6" fill="{color}"/>')
    if kind == 'probs':
        # Widths are shares of 100%: an incomplete table leaves the rest of the track empty.
        segs = [s for s in viz.get('segments') or [] if isinstance(s.get('value'), (int, float))]
        top = max((s['value'] for s in segs), default=None)
        x, parts = 4, [f'<rect x="4" y="9" width="{W - 8}" height="12" class="track"/>']
        for s in segs:
            w = (W - 8) * min(max(s['value'], 0), 100) / 100
            parts.append(f'<rect x="{x:.1f}" y="9" width="{max(w - 1, 0):.1f}" height="12" '
                         f'class="{"seg-focus" if s["value"] == top else "seg"}"/>')
            x += w
        return _svg(''.join(parts))
    if kind == 'votes':
        # One square per series of a release: green helps gold, red hurts, grey as forecast.
        marks = ['var(--up)'] * int(viz.get('up') or 0) + ['var(--down)'] * int(viz.get('down') or 0) + \
            ['var(--flat)'] * int(viz.get('flat') or 0)
        return _svg(''.join(f'<rect x="{4 + i * 14}" y="9" width="11" height="11" rx="2" fill="{c}"/>'
                            for i, c in enumerate(marks[:7])))
    if kind == 'bars':
        items = [b for b in viz.get('items') or [] if isinstance(b.get('value'), (int, float))]
        if not items:
            return ''
        top = max(abs(b['value']) for b in items) or 1
        slot = (W - 8) / len(items)
        mid = 15
        parts = [f'<line x1="2" x2="{W - 2}" y1="{mid}" y2="{mid}" class="base"/>']
        for i, b in enumerate(items):
            h = abs(b['value']) / top * 11
            y0 = mid - h if b['value'] >= 0 else mid
            fill = 'var(--up)' if b['value'] > 0 else 'var(--down)' if b['value'] < 0 else 'var(--flat)'
            parts.append(f'<rect x="{4 + i * slot + slot * 0.2:.1f}" y="{y0:.1f}" width="{slot * 0.6:.1f}" '
                         f'height="{max(h, 1):.1f}" fill="{fill}"/>')
        return _svg(''.join(parts))
    if kind == 'track':
        value = float(viz['value'])
        low, high = float(viz.get('low', 10)), float(viz.get('high', 90))
        x = lambda p: 4 + (W - 8) * p / 100
        return _svg(f'<rect x="4" y="12" width="{W - 8}" height="6" class="track"/>'
                    f'<rect x="4" y="12" width="{x(low) - 4:.1f}" height="6" class="zone"/>'
                    f'<rect x="{x(high):.1f}" y="12" width="{x(100) - x(high):.1f}" height="6" class="zone"/>'
                    f'<circle cx="{x(value):.1f}" cy="15" r="4.2" fill="{color}" stroke="#fff" stroke-width="1.2"/>')
    if kind == 'split':
        # Long and short as shares of their own sum, so a ratio that rounds to 100.1% or 99.9% is drawn as given
        # (no side is cut); a lone value is its share of 100%.
        left = max(float(viz['left']), 0)
        right = max(float(viz['right']), 0) if viz.get('right') is not None else 0
        total = left + right if right else 100
        share, rest = (left / total, right / total) if total > 0 else (0, 0)
        pct = float(viz.get('track', 50))
        x = lambda p: 4 + (W - 8) * p / 100
        return _svg(f'<rect x="4" y="4" width="{W - 8}" height="7" class="track"/>'
                    f'<rect x="4" y="4" width="{(W - 8) * share:.1f}" height="7" class="long"/>'
                    f'<rect x="{4 + (W - 8) * share:.1f}" y="4" width="{(W - 8) * rest:.1f}" height="7" class="short"/>'
                    f'<rect x="4" y="19" width="{W - 8}" height="5" class="track"/>'
                    f'<rect x="4" y="19" width="{x(10) - 4:.1f}" height="5" class="zone"/>'
                    f'<rect x="{x(90):.1f}" y="19" width="{x(100) - x(90):.1f}" height="5" class="zone"/>'
                    f'<circle cx="{x(pct):.1f}" cy="21.5" r="3.6" fill="{color}" stroke="#fff" stroke-width="1.2"/>')
    if kind == 'gap':
        value, scale = float(viz['value']), float(viz.get('scale') or 1)
        mid = W / 2
        w = max(min(abs(value) / scale, 1) * (W / 2 - 6), 1.5)
        x0 = mid if value >= 0 else mid - w
        fill = 'var(--up)' if value > 0 else 'var(--down)' if value < 0 else 'var(--flat)'
        return _svg(f'<line x1="4" x2="{W - 4}" y1="15" y2="15" class="base"/>'
                    f'<line x1="{mid}" x2="{mid}" y1="6" y2="24" class="tick"/>'
                    f'<rect x="{x0:.1f}" y="10" width="{w:.1f}" height="10" fill="{fill}"/>')
    return ''


# ------------------------------------------------------------------ sections

def _sources(figure):
    links = ' / '.join(f'<a href="{e(s["url"])}">{e(s["label"])}</a>' for s in figure.get('sources') or []
                       if re.match(r'^https://', str(s.get('url', ''))))
    return f'<p class="src">出典: {links}</p>' if links else ''


def _details(title, body):
    return f'<details class="howto"><summary>{e(title)}</summary><div class="howto-body">{body}</div></details>'


def tiles_html(figure):
    """The per-horizon verdicts, shown first on the page (above the conclusion).

    A tile repeats its rule result when the report's verdict differs from it (with the report's
    reason below), when the horizon has no material, or when a material is missing.
    """
    if not figure or not figure.get('verdicts'):
        return ''
    tiles = ''.join(
        f'<div class="tile tone-{e(t.get("tone"))}{" is-mismatch" if t.get("mismatch") else ""}">'
        f'<span class="tile-label">{e(t["label"])}</span>'
        f'<span class="tile-symbol" aria-hidden="true">{e(t["symbol"])}</span>'
        f'<span class="tile-word">{e(t["direction"])}</span>'
        f'<span class="tile-origin">{e(t["origin"])}</span>'
        + (f'<span class="tile-detail">{e(t["detail"])}</span>' if t.get('show_detail', t.get('mismatch')) else '') + '</div>'
        for t in figure['verdicts'])
    reason = figure.get('reason')
    note = f'<p class="tile-reason">{e(reason["text"])}</p>' if reason else ''
    return f'<div class="tiles" role="group" aria-label="期間ごとの金の向き">{tiles}</div>{note}'


def bias_section(figure):
    body = []
    details = {t.get('group'): t.get('short') or t.get('detail') for t in figure.get('verdicts') or [] if t.get('group')}
    for group in figure.get('groups') or []:
        rows = [i for i in figure['items'] if i['group'] == group]
        if not rows:
            continue
        tally = f'<span class="grp-tally">{e(details[group])}</span>' if details.get(group) else ''
        body.append(f'<tr class="grp"><th colspan="5" scope="colgroup">{e(group)}{tally}</th></tr>')
        for i in rows:
            note = f'<span class="note">{e(i["note"])}</span>' if i.get('note') else ''
            if i.get('kind') == 'text':
                # A judgement in words (news): one wide cell, no numeric baseline.
                middle = f'<td class="cell-text" colspan="2">{e(i["display"])}</td>'
            else:
                middle = (f'<td class="cell-now">{keep_parts(i["display"])}</td>'
                          f'<td class="cell-base"><span class="base-text">{e(i["baseline"])}</span>'
                          f'<span class="chg">{e(i["change"])}</span></td>')
            wide = ' wide' if len(str(i.get('display') or '')) > 12 and i.get('kind') != 'text' else ''
            body.append(
                f'<tr class="tone-{e(i["tone"])}{" text-row" if i.get("kind") == "text" else ""}{wide}">'
                f'<th scope="row"><span class="lbl">{e(i["label"])}</span>'
                f'<span class="asof">{e(i["asof"])}</span>{note}</th>'
                f'<td class="cell-viz">{viz_svg(i.get("viz"), i["tone"])}</td>{middle}'
                f'<td class="cell-dir"><span class="dir-symbol" aria-hidden="true">{e(i["symbol"])}</span>'
                f'<span class="dir-word">{e(i["direction"])}</span></td></tr>')
    rules = ''.join(f'<li><b>{e(r["label"])}</b>: {e(r["rule"])}</li>' for r in figure.get('rules') or [])
    howto = _details('判定ルールと出典（開いて確認）',
                     f'<p>{e(figure.get("legend"))}</p><ul class="rules">{rules}</ul>{_sources(figure)}')
    return (f'<section class="data-figure fig-bias" id="bias" aria-labelledby="bias-title">'
            f'<div class="fig-head"><span class="kicker">{e(figure.get("kicker"))}</span>'
            f'<h2 id="bias-title">{e(figure["title"])}</h2><p class="sub">{e(figure.get("subtitle"))}</p></div>'
            f'<div class="board-wrap"><table class="board"><colgroup><col class="c-lbl"><col class="c-viz"><col class="c-now">'
            f'<col class="c-base"><col class="c-dir"></colgroup><thead><tr><th scope="col">材料</th><th scope="col">推移</th>'
            f'<th scope="col">現在</th><th scope="col">基準と変化</th><th scope="col">金への向き</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>{howto}</section>')


def liquidity_section(figure):
    band = figure.get('band') or {}
    lower, upper = band.get('lower'), band.get('upper')
    rows, upper_done, lower_done = [], False, False
    shown = [i for i in figure['items'] if not i.get('offscale')]
    for i in shown:
        if upper is not None and not upper_done and i['position'] <= upper:
            rows.append(f'<li class="band-edge">想定変動 上限 {upper:,.2f}</li>')
            upper_done = True
        if lower is not None and not lower_done and i['position'] < lower:
            rows.append(f'<li class="band-edge">想定変動 下限 {lower:,.2f}</li>')
            lower_done = True
        inside = lower is not None and upper is not None and lower <= i['position'] <= upper
        prox = ''
        if isinstance(i.get('proximity'), (int, float)):
            prox = (f'<span class="prox" aria-hidden="true"><span style="width:{max(3, min(i["proximity"], 2) * 50):.0f}%">'
                    '</span><i></i></span>')
        pool = f'<span class="pool pool-{e(i["pool"].lower())}">{e(i["pool"])}</span>' if i.get('pool') else ''
        touched = f'<span class="touched">{e(i["touched"])}</span>' if i.get('touched') else ''
        target = f'<span class="target">{e(i["target"])}</span>' if i.get('target') else ''
        rows.append(
            f'<li class="lv kind-{e(i["kind"])} state-{e(i["state"])}{" in-band" if inside else ""}{" is-target" if target else ""}">'
            f'<span class="lv-price">{e(i["price_text"])}</span>'
            f'<span class="lv-name"><span class="lv-label">{e(i["label"])}</span>{pool}{target}</span>'
            f'<span class="lv-dist">{e(i["distance_text"])}{prox}</span>'
            f'<span class="lv-state"><span class="icon" aria-hidden="true">{e(i["icon"])}</span>{e(i["state_word"])}{touched}</span></li>')
    if lower is not None and not lower_done:
        rows.append(f'<li class="band-edge">想定変動 下限 {lower:,.2f}</li>')
    off = [i for i in figure['items'] if i.get('offscale')]
    offscale = ''
    if off:
        offscale = ('<div class="offscale"><span>表示範囲の外</span><ul>' + ''.join(
            f'<li>{e(i["label"])} {e(i["price_text"])}（{e(i["distance_text"])}）'
            + (f' {e(i["icon"])} {e(i["state_word"])}' if i.get('state_word') else '')
            + (f' {e(i["pool"])}' if i.get('pool') else '')
            + (f'（{e(i["touched"])}）' if i.get('touched') else '')
            + (f' <span class="target">{e(i["target"])}</span>' if i.get('target') else '') + '</li>' for i in off) + '</ul></div>')
    band_note = f'<p class="band-note">帯: {e(band["label"])} {lower:,.2f}〜{upper:,.2f}</p>' if band else ''
    aux = figure.get('aux') or []
    aux_html = ('<p class="aux">参考（流動性の位置ではない）: ' + ' ／ '.join(
        f'{e(i["icon"])} {e(i["label"])} {e(i["price_text"])}（{e(i["distance_text"])}・{e(i["state_word"])}）'
        for i in aux) + '</p>') if aux else ''
    cautions = ''.join(f'<p class="caution" role="note">{e(c["text"])}</p>' for c in figure.get('cautions') or [])
    # The retail average entry is not a liquidity level: it stays out of the main view, in the folded details.
    howto = _details('水準の決め方と状態の意味' + ('・個人の平均建値' if aux else '') + '（開いて確認）',
                     f'<p>{e(figure.get("legend"))}</p>{aux_html}{_sources(figure)}')
    return (f'<section class="data-figure fig-liq" id="liquidity" aria-labelledby="liq-title">'
            f'<div class="fig-head"><span class="kicker">{e(figure.get("kicker"))}</span>'
            f'<h2 id="liq-title">{e(figure["title"])}</h2><p class="sub">{e(figure.get("subtitle"))}</p></div>{cautions}'
            f'<div class="ladder-head" aria-hidden="true"><span>価格</span><span>水準</span><span>現在値からの距離</span><span>状態</span></div>'
            f'<ul class="ladder">{"".join(rows)}</ul>{band_note}{offscale}{howto}</section>')


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
            f'<div class="fig-head"><span class="kicker">{e(figure.get("kicker"))}</span>'
            f'<h2 id="ev-title">{e(figure["title"])}</h2><p class="sub">{e(figure.get("subtitle"))}</p></div>{cautions}'
            f'<div class="board-wrap"><table class="events"><thead><tr><th scope="col">時刻</th><th scope="col">指標</th>'
            f'<th scope="col">前回</th><th scope="col">予想</th><th scope="col">結果</th><th scope="col">差・予測</th>'
            f'<th scope="col">金への向き</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>{howto}</section>')


SECTIONS = {'bias_board': bias_section, 'liquidity_map': liquidity_section, 'event_table': event_section}

CSS = """
:root{--ink:#22352f;--muted:#5d6863;--line:#dfe3dc;--paper:#fffefb;--canvas:#f3f2ec;--accent:#3f6650;
--up:#2c7a57;--down:#b0454f;--flat:#8b928d;--wash:#f1f4ee;--band:#eef3fb;
font:16px/1.7 "Hiragino Sans","Noto Sans JP","Yu Gothic",Meiryo,sans-serif;color:var(--ink);background:var(--canvas);
font-variant-numeric:tabular-nums;font-synthesis:none}
*{box-sizing:border-box}body{margin:0}a{color:var(--accent);text-underline-offset:.2em}
.reports-back{display:block;max-width:1100px;margin:0 auto;padding:14px 20px;font-size:14px}
main.dash{max-width:1100px;margin:0 auto 48px;background:var(--paper);border:1px solid var(--line);padding:28px 36px 20px}
.mast{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;border-bottom:2px solid var(--ink);padding-bottom:14px}
.eyebrow,.kicker{display:block;color:var(--accent);font-size:.7rem;font-weight:700;letter-spacing:.1em}
h1{font-size:1.6rem;margin:2px 0 0;line-height:1.3}h2{font-size:1.2rem;margin:2px 0 2px;line-height:1.4}
.asof-chip{font-size:.95rem;font-weight:600;white-space:nowrap}
.lead{margin:18px 0 6px}.conclusion{margin:12px 0 0;padding:16px 20px;background:var(--wash);border-left:5px solid var(--accent);
font-size:1.02rem;line-height:1.8;white-space:pre-line}
.conf{display:inline-block;margin-right:8px;padding:0 8px;border:1px solid var(--line);border-radius:4px;background:#fff;
font-size:.8rem;font-weight:700;line-height:1.7;vertical-align:1px;white-space:nowrap}
.chips{list-style:none;padding:0;margin:10px 0 0;display:flex;flex-wrap:wrap;gap:8px}
.chips li{background:#fff;border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:.85rem}
.data-figure{margin-top:30px}.fig-head .sub{margin:2px 0 12px;color:var(--muted);font-size:.85rem}
.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.tile{border:1px solid var(--line);border-top:5px solid var(--flat);background:#fff;padding:10px 14px;display:grid;
grid-template-columns:auto 1fr;grid-template-areas:"label label" "sym word" "origin origin" "detail detail";column-gap:10px;align-items:center}
.tile.tone-positive{border-top-color:var(--up)}.tile.tone-negative{border-top-color:var(--down)}
.tile-label{grid-area:label;font-size:.8rem;font-weight:700;color:var(--muted)}
.tile-symbol{grid-area:sym;font-size:2.2rem;line-height:1.1}.tone-positive .tile-symbol{color:var(--up)}
.tone-negative .tile-symbol{color:var(--down)}.tone-neutral .tile-symbol{color:var(--flat)}
.tile-word{grid-area:word;font-size:1.15rem;font-weight:700;line-height:1.35}
.tile-origin{grid-area:origin;font-size:.72rem;color:var(--muted)}.tile-detail{grid-area:detail;font-size:.78rem;color:var(--muted);line-height:1.5}
.tile.is-mismatch .tile-origin{color:#8a5a00;font-weight:700}.tile-reason{margin:8px 0 0;font-size:.82rem;color:#5b4a1f;background:#fbf6e9;
border-left:4px solid #c9a227;padding:6px 12px}
.grp-tally{font-weight:400;color:var(--muted);margin-left:12px;font-size:.78rem}
.caution{margin:0 0 8px;padding:6px 12px;background:#fbf0ef;border-left:4px solid var(--down);font-size:.82rem}
.aux{font-size:.8rem;color:var(--muted);margin:8px 0 0}.events tr.empty td{color:var(--muted);font-size:.86rem}
.board-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%}.board{table-layout:fixed}
.board .c-lbl{width:27%}.board .c-viz{width:128px}.board .c-now{width:19%}.board .c-base{width:24%}.board .c-dir{width:15%}th,td{text-align:left;vertical-align:middle;padding:8px 10px;border-bottom:1px solid var(--line)}
thead th{font-size:.72rem;color:var(--muted);font-weight:600;border-bottom:1px solid var(--ink)}
tr.grp th,tr.day th{background:var(--wash);font-size:.82rem;font-weight:700;padding:6px 10px}
.board th[scope=row]{font-weight:600;min-width:13em}.lbl{display:block}.asof,.note{display:block;font-size:.72rem;color:var(--muted);font-weight:400}
.note{color:var(--accent)}.cell-viz{width:120px}.viz{display:block}.viz .base{stroke:#c9cfc9;stroke-width:1;stroke-dasharray:2 2}
.viz .tick{stroke:#9aa29c;stroke-width:1}.viz .dot0{fill:#b9c0ba}.viz .track{fill:#e7ebe5}.viz .zone{fill:#d5dbd4}
.viz .seg{fill:#cdd6d0}.viz .seg-focus{fill:var(--accent)}.viz .long{fill:#7f9f8c}.viz .short{fill:#c9a3a9}
.cell-now{font-weight:700;overflow-wrap:anywhere}.nw{white-space:nowrap}.cell-text{font-size:.92rem;line-height:1.6}.cell-base{font-size:.85rem}.base-text{display:block;color:var(--muted)}
.chg{font-weight:700}.cell-dir{white-space:nowrap}.dir-symbol{font-size:1.25rem;margin-right:6px;vertical-align:-2px}
.tone-positive .dir-symbol,.tone-positive .chg{color:var(--up)}.tone-negative .dir-symbol,.tone-negative .chg{color:var(--down)}
.tone-neutral .dir-symbol{color:var(--flat)}.dir-word{font-size:.88rem;font-weight:600}
.ladder-head{display:grid;grid-template-columns:7.5em minmax(0,1.6fr) 10.5em minmax(0,1.3fr);gap:10px;font-size:.72rem;color:var(--muted);
padding:0 10px 4px;border-bottom:1px solid var(--ink)}
.ladder{list-style:none;margin:0;padding:0}
.lv{display:grid;grid-template-columns:7.5em minmax(0,1.6fr) 10.5em minmax(0,1.3fr);gap:10px;align-items:center;padding:7px 10px;
border-bottom:1px solid var(--line);font-size:.9rem}
.lv.in-band{background:var(--band)}.lv-price{font-weight:700}.lv-label{margin-right:6px}
.pool{display:inline-block;font-size:.68rem;font-weight:700;padding:0 6px;border-radius:3px;color:#fff;vertical-align:1px}
.pool-bsl{background:var(--down)}.pool-ssl{background:var(--up)}
.lv-dist{font-size:.82rem;color:var(--muted)}.prox{display:block;position:relative;height:5px;background:#e9ece6;margin-top:4px}
.prox span{display:block;height:5px;background:#8fa79a}.prox i{position:absolute;left:50%;top:-2px;width:1px;height:9px;background:#55625b}
.in-band .prox span{background:var(--accent)}
.lv-state{font-size:.82rem}.icon{display:inline-block;width:1.2em;font-weight:700}.touched{display:block;font-size:.72rem;color:var(--muted)}
.state-resting .icon{color:var(--accent)}.state-swept .icon{color:var(--flat)}.state-swept .lv-price,.state-broken .lv-price{color:var(--muted)}
.kind-current{background:var(--ink)!important;color:#fff;border-radius:3px}.kind-current .lv-dist,.kind-current .touched{color:#dfe7e3}
.kind-reference{color:var(--muted);font-size:.82rem}.kind-retail .lv-label,.kind-orders .lv-label,.kind-draw .lv-label{font-style:normal}
.kind-draw,.is-target{outline:2px solid var(--accent);outline-offset:-2px}
.target{display:inline-block;margin-left:6px;font-size:.72rem;font-weight:700;color:var(--accent);white-space:nowrap}
.band-edge{list-style:none;font-size:.72rem;color:#55709c;border-bottom:1px dashed #9db1d6;padding:2px 10px;text-align:right}
.band-note{font-size:.78rem;color:var(--muted);margin:6px 0 0}
.offscale{margin-top:10px;font-size:.82rem;color:var(--muted)}.offscale>span{font-weight:700}
.offscale ul{margin:4px 0 0;padding-left:1.2em}
.events td,.events th{font-size:.88rem}.ev-time{white-space:nowrap;font-weight:600}.ev-name span{display:block}
.ev-name{font-weight:600;min-width:12em}.memo{display:block;font-size:.74rem;color:var(--muted);font-weight:400}
.tier{display:inline-block!important;font-size:.66rem;font-weight:700;padding:0 6px;border-radius:3px;margin-bottom:2px;background:var(--ink);color:#fff}
.tier-b{background:#8d968f}.ev-num{white-space:nowrap}.ev-diff{font-size:.8rem}tr.released{background:#fbfbf8}
details.howto,details.limits{margin-top:10px;border:1px solid var(--line);background:#fff}
details summary{cursor:pointer;min-height:44px;display:flex;align-items:center;padding:0 14px;font-size:.85rem;color:var(--accent);font-weight:600}
.howto-body,.limits-body{padding:2px 16px 12px;font-size:.85rem;color:var(--muted)}.rules{padding-left:1.2em}
.src{font-size:.78rem}.limits{margin-top:26px}.note-line{font-size:.78rem;color:var(--muted);margin:14px 0 0}
.no-figures{margin:12px 0 0;padding:10px 14px;border:1px solid var(--line);background:#fff}
.jump{display:flex;gap:16px;margin:12px 0 0;padding:0;list-style:none;font-size:.85rem}
.jump a{display:inline-block;min-height:32px;line-height:32px}.events tr.same-time td,.events tr.same-time th{border-top:0}
.events tr.same-time{background:inherit}
footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;border-top:1px solid var(--line);margin-top:26px;padding-top:12px;
font-size:.78rem;color:var(--muted)}
@media(max-width:700px){
main.dash{padding:16px 14px;border:0}.mast{align-items:flex-start;flex-direction:column;gap:4px}h1{font-size:1.3rem}
.conclusion{padding:12px 14px;font-size:.95rem}.tiles{gap:6px}.tile{padding:8px 8px;grid-template-columns:1fr;
grid-template-areas:"label" "sym" "word" "origin" "detail"}.tile-symbol{font-size:1.7rem}.tile-word{font-size:.92rem}
.tile-detail{font-size:.7rem}
.board thead,.events thead{display:none}.board,.board tbody,.events,.events tbody{display:block}
.board tr{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"lbl dir" "viz now" "base base";
gap:2px 10px;padding:10px 4px;border-bottom:1px solid var(--line)}
.board tr.grp{display:block;padding:0}.board tr.grp th{display:block}.grp-tally{display:block;margin-left:0}
.board th[scope=row]{grid-area:lbl;border:0;padding:0;min-width:0}.board .cell-dir{grid-area:dir;border:0;padding:0;text-align:right}
.board .cell-viz{grid-area:viz;border:0;padding:0;width:auto}.board .cell-now{grid-area:now;border:0;padding:0;text-align:right}
.board .cell-base{grid-area:base;border:0;padding:0}.base-text{display:inline;margin-right:8px}
.board .cell-text{grid-column:1/-1;grid-row:2;border:0;padding:0}.board tr.text-row .cell-viz{display:none}
.board tr.wide{grid-template-areas:"lbl dir" "viz viz" "now now" "base base"}.board tr.wide .cell-now{text-align:left}
.ladder-head{display:none}.lv{grid-template-columns:6.6em minmax(0,1fr);grid-template-areas:"price name" "dist state";gap:2px 10px;font-size:.86rem}
.lv-price{grid-area:price}.lv-name{grid-area:name}.lv-dist{grid-area:dist}.lv-state{grid-area:state}
.events tr{display:grid;grid-template-columns:3.4em minmax(0,1fr) auto;grid-template-areas:"time name dir" "time nums nums" "time diff diff";
gap:2px 8px;padding:8px 4px;border-bottom:1px solid var(--line)}.events tr.day{display:block;padding:0}.events tr.day th{display:block}
.events td,.events th{border:0;padding:0}.ev-time{grid-area:time}.ev-name{grid-area:name;min-width:0}.events .cell-dir{grid-area:dir;text-align:right}
.ev-num{display:inline-block;font-size:.8rem}.ev-num::before{content:attr(data-k) " ";color:var(--muted);font-size:.7rem}
.events tr>.ev-num:nth-of-type(2){grid-column:2/4;grid-row:2}.events tr>.ev-num:nth-of-type(3){grid-column:2/4;grid-row:2;margin-left:6.5em}
.events tr>.ev-num:nth-of-type(4){grid-column:2/4;grid-row:2;margin-left:13em}.ev-diff{grid-area:diff}
}
@media print{.reports-back,details{display:none}main.dash{border:0}}
"""


def build(source: str, summary: dict, figures: list, *, title: str, source_name: str) -> tuple[str, dict]:
    checked = validate(summary, figures, source)
    conclusion = summary.get('conclusion') or {}
    confidence = summary.get('confidence') or {}
    badge = f'<span class="conf">{e(confidence["text"])}</span>' if confidence.get('text') else ''
    chips = ''.join(f'<li>{e(c["text"])}</li>' for c in summary.get('chips') or [])
    tiles = tiles_html(next((f for f in figures if f.get('type') == 'bias_board'), None))
    sections = ''.join(SECTIONS[f['type']](f) for f in figures)
    names = {'bias_board': 'バイアス', 'liquidity_map': '流動性', 'event_table': '米指標'}
    jump = ''.join(f'<li><a href="#{e(f["id"])}">{names[f["type"]]}</a></li>' for f in figures)
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
<section class="lead" aria-label="結論">{tiles}{f'<p class="conclusion">{badge}{e(conclusion["text"])}</p>' if conclusion.get('text') else ''}{f'<ul class="chips">{chips}</ul>' if chips else ''}{f'<ul class="jump" aria-label="ページ内の移動">{jump}</ul>' if jump else ''}</section>
{sections}
{limits_html}
{note}
<footer><span>{e(title)}</span><a id="source-markdown" download="{e(source_name)}" href="data:text/markdown;charset=utf-8;base64,{encoded}">原稿（Markdown）を保存</a></footer>
</main></body></html>'''
    audit = {'source_sha256': digest, 'source_bytes': len(source.encode('utf-8')), 'figure_count': len(figures),
             'figure_items_verified': checked, 'numeric_check': 'pass', 'renderer': 'scripts/offchart_dashboard.py'}
    return document, audit
