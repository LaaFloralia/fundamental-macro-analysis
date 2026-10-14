"""Deterministic figures for the off-chart dashboard: bias board, liquidity map, US event table.

Every board row compares one observation with its stated baseline and maps the change to a
gold direction with the general reaction rules (scrapers.macro_surprise.US_GOLD_SIGN, the
master-prompt thresholds). Directions are judged on the unrounded values (only binary
floating-point noise is ignored); the shown value carries enough digits to sit on the same
side of zero and of each threshold. Rows without fresh data are left out and listed as
missing with the reason; nothing is invented and no row is "pending". Rows that measure the
same driver are counted as one vote (FACTOR).
The report's own per-horizon verdict is quoted from its fixed lines and marked when it
differs from the rule tally. No collection, LLM, Brain or network access here.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

from scrapers.macro_surprise import compute_surprise, event_datetime_jst, gold_sign
from scrapers.positioning_history import MIN_DISTINCT, MIN_SAMPLES
from scrapers.us_events import (GAP_NEUTRAL_PP, NOWCAST_MAX_AGE_DAYS, NOWCAST_MAX_PER_WINDOW, adopted_nowcasts,
                                 display_name, event_window, gap_display, is_statement, nowcast_age,
                                 nowcast_fetch_problem, nowcast_gap, results_start, tier, tier_scope, window_label)
from scripts.report_bundle import number

JST = ZoneInfo('Asia/Tokyo')
SYMBOL = {('up', 'strong'): '▲', ('up', 'weak'): '△', ('down', 'strong'): '▼', ('down', 'weak'): '▽',
          ('flat', 'none'): '—'}
WORD = {'up': '上向き', 'down': '下向き', 'flat': '中立'}
TONE = {'up': 'positive', 'down': 'negative', 'flat': 'neutral'}
GROUPS = {'long': '長期（数週間〜）', 'short': {'daily': '短期（当日〜1、2日）', 'weekly': '短期（今後1週間）'}}
VERDICTS = {'上向き・強': ('up', 'strong'), '上向き・弱': ('up', 'weak'), '下向き・強': ('down', 'strong'),
            '下向き・弱': ('down', 'weak'), '中立': ('flat', 'none')}
TALLY_RULE = ('同じ要因の行は1票にまとめる（米2年債・政策金利の織り込み・指標の結果＝金利・織り込み、'
              'COT・個人の比率＝建玉の偏り、nowcastとの差＝発表前の予測）。まとめた票は向きが揃えば強い方、食い違えば0で、'
              '建玉の偏りと発表前の予測は最大0.5票。▲▼は1票、△▽は0.5票。上向きと下向きの差が1票未満なら中立、'
              '2票以上で強、それ以外は弱')
BLEND_RULE = '原稿に総合の記載がない場合: 長期と短期が同じ向きならその向き、片方が中立か材料なしならもう片方の向き（弱、材料なしは総合の欄に書く）、逆向きなら中立'
CAVEAT = ('金利・ドル・織り込みは同じ材料で同時に動きやすいため、同じ要因の行は1票にまとめています。'
          '閾値と票の重みは経験則の目安で、予測力は検証していません')
LADDER_RANGE_PCT = {'daily': 2.5, 'weekly': 5.0}
STATE_ICON = {'resting': '●', 'swept': '✓', 'broken': '×', 'forming': '◌'}
BEYOND_ICON = {'high': '↗', 'low': '↘'}  # 抜けて推移: the side the price left through
QUOTE_FRESH_HOURS = 1  # the current price the liquidity distances are measured from
FLOAT_NOISE = 1e-9  # binary floating-point error only; the inputs carry at most four decimals
# A news line the report could not read beyond headlines: not a material (判定外), so it neither votes nor
# makes a horizon look covered. The prompts ask for 「中立｜未評価（理由）」 in that case.
NEWS_UNREAD = re.compile(r'^未評価|本文未確認|本文を確認でき|見出し(?:・X)?(?:と抜粋)?の範囲')
# Oldest observation still used, in days before the stamp's date (publication lags included).
MAX_AGE_DAYS = {'DFII10': 7, 'DGS2': 7, 'DXY': 7, 'DTWEXBGS': 14, 'GLD': 7, 'COT': 14}
CB_MAX_MONTHS = 4                  # IMF IRFCL is one to two months behind
QUOTE_MAX_HOURS = 24               # FedWatch table, DXY quote and retail ratio come from this collection
CLOCK_SKEW = timedelta(minutes=5)  # a record time this far after the cutoff is still treated as at the cutoff
FEDWATCH_HISTORY_DAYS = {'daily': 4, 'weekly': 10}  # own-history baseline when the table has no column
# Rows that measure the same driver give one vote: the larger vote when they agree, none when they disagree.
FACTOR = {'実質金利': '実質金利', 'ドル（20営業日）': 'ドル', 'ニュース・地政学': 'ニュース・地政学', '中銀の金購入': '中銀',
          '米2年債': '金利・織り込み', '政策金利の織り込み': '金利・織り込み', '指標の結果': '金利・織り込み',
          'ドル（短期）': 'ドル', '予測との差': '発表前の予測', 'GLD保有量': 'ETF',
          '投機筋の建玉': '建玉の偏り', '個人の比率': '建玉の偏り'}
FACTOR_CAP = {'発表前の予測': 0.5, '建玉の偏り': 0.5}
VOTE = {'▲': 1.0, '△': 0.5, '▼': -1.0, '▽': -0.5, '—': 0.0}
# Killzones in each market's clock (JST: London 16-19 in UK summer time, 17-20 in winter; NY 21-01 / 22-02).
KILLZONES = (('London KZ', ZoneInfo('Europe/London'), 8, 11), ('NY KZ', ZoneInfo('America/New_York'), 8, 12))
FEDWATCH_STRONG_BP = {'daily': 1.25, 'weekly': 2.5}  # a 5pp / 10pp shift between adjacent 25bp ranges


# ------------------------------------------------------------------ helpers

def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _float(value):
    """Numbers and numeric strings ('4177.47'); None otherwise."""
    if isinstance(value, str):
        try:
            value = float(value.replace(',', ''))
        except ValueError:
            return None
    return num(value)


def usable(value):
    return isinstance(value, dict) and not value.get('error') and value.get('stale') is not True


def present(value):
    """A non-empty record without an error (stale flags are judged by each row's own date)."""
    return isinstance(value, dict) and bool(value) and not value.get('error')


def fmt(value, digits=2, sign=False, suffix=''):
    value = number(value, digits)
    return f'{value:+,.{digits}f}{suffix}' if sign else f'{value:,.{digits}f}{suffix}'


def shown(raw, digits, *cuts, below=()):
    """(value, digits): the raw value rounded for display. More digits are used when rounding would
    move it onto zero or across a threshold (|x| >= cut, or x <= a `below` cut), so the shown value
    agrees with the verdict."""
    raw = float(raw)

    def classes(x):
        return _zero(x), tuple(_reaches(x, c) for c in cuts), tuple(x <= c + FLOAT_NOISE for c in below)
    want = classes(raw)
    for places in range(digits, 11):
        value = number(raw, places)
        if classes(value) == want:
            return value, places
    return number(raw, 10), 10


def shown_ends(raw, cur, base, places, *cuts, scale=1):
    """(change, change digits, end digits): one precision for a change and its two ends. The change agrees
    with the verdict (shown) and the ends rounded to the end digits differ by exactly the shown change
    (×scale in the change's unit: 100 for % -> bp). More digits until both hold; the ends stop at six. A source
    whose change is not the difference of its ends gets the shortest change that agrees with the verdict."""
    shift = round(math.log10(scale))
    for p in range(places, 7):
        change, cp = shown(raw, p - shift, *cuts)
        if cp == p - shift and abs(number((number(cur, p) - number(base, p)) * scale, cp) - change) < FLOAT_NOISE:
            return change, cp, p
    change, cp = shown(raw, places - shift, *cuts)
    return change, cp, max(places, cp + shift)


def _zero(x):
    return abs(x) < FLOAT_NOISE


def _reaches(x, cut):
    return abs(x) >= cut - FLOAT_NOISE


def judge(change, threshold, sign):
    """(side, strength) on the unrounded change; only floating-point noise (< 1e-9) is ignored.
    sign=+1: a rise is gold-positive; -1: a rise is gold-negative."""
    if change is None or _zero(change):
        return 'flat', 'none'
    side = 'up' if change * sign > 0 else 'down'
    return side, ('strong' if _reaches(change, threshold) else 'weak')


def mark(side, strength):
    return SYMBOL[(side, 'none' if side == 'flat' else strength)]


def word_of(side, strength):
    return WORD[side] + ('' if side == 'flat' or strength == 'strong' else '（弱）')


def _parse_jst(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=JST) if t.tzinfo is None else t.astimezone(JST)


def _md(day):
    """'2026-10-08' -> '10/08'; other text unchanged."""
    match = re.fullmatch(r'\d{4}-(\d{2})-(\d{2})', str(day or ''))
    return f'{match.group(1)}/{match.group(2)}' if match else str(day or '日付不明')


def _clean(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _stamp(value):
    t = _parse_jst(value)
    return t.strftime('%m/%d %H:%M') + ' JST' if t else '時点不明'


def _bar_span(value):
    """'2026-10-13 23:00' (UTC, the bar's start) -> '10/14 08:00〜09:00 JST' (one hour); '' when unparsable."""
    try:
        t = datetime.strptime(str(value), '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('UTC')).astimezone(JST)
    except ValueError:
        return ''
    return f'{t.strftime("%m/%d %H:%M")}〜{(t + timedelta(hours=1)).strftime("%H:%M")} JST'


def market_time(q):
    """The quote's market time (Twelve Data last_quote_at, epoch seconds) in JST; None when absent or unreadable."""
    epoch = q.get('last_quote_at')
    try:
        return None if isinstance(epoch, bool) else datetime.fromtimestamp(float(epoch), JST)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def quote_state(q, stamp):
    """(word, reference) for the price the liquidity distances are measured from. With the market time
    (Twelve Data last_quote_at): the last price of a closed market, the market time, or a reference price when
    it is over an hour old, after the cutoff or unknown (including collections that did not keep it)."""
    market = market_time(q)
    if market is None:
        return '市場時刻不明・参考価格', True
    shown = f'{market.strftime("%m/%d %H:%M")} JST'
    if market > stamp + CLOCK_SKEW:
        return f'市場 {shown} が収集時刻より後・参考価格', True
    if q.get('is_market_open') is False:
        return f'休場中の最終値（市場 {shown}）', False
    if market < stamp - timedelta(hours=QUOTE_FRESH_HOURS):
        return f'市場 {shown}・{QUOTE_FRESH_HOURS}時間より前・参考価格', True
    return f'市場 {shown}', False


def _utc_label(value):
    """'2026-10-13 12:00' (UTC) -> '10/13 21:00 JST'; '' when unparsable."""
    try:
        t = datetime.strptime(str(value), '%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('UTC'))
    except ValueError:
        return ''
    return t.astimezone(JST).strftime('%m/%d %H:%M') + ' JST'


def stale_day(value, stamp, days):
    """None when the observation date ('2026-10-08…') is at most `days` before the stamp; else why not."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return '観測日の記録なし'
    age = (stamp.date() - day).days
    if age < 0:
        return f'観測日 {day.strftime("%m/%d")} が収集日より後'
    return None if age <= days else f'最新 {day.strftime("%m/%d")}・{days}日より古い'


def stale_time(value, stamp, hours=QUOTE_MAX_HOURS):
    """None when the record time is within `hours` before the cutoff (or at most CLOCK_SKEW after it:
    the cutoff is the end of collection, so a later record is not part of this collection)."""
    t = _parse_jst(value)
    if t is None:
        return '取得時刻の記録なし'
    if t > stamp + CLOCK_SKEW:
        return f'取得 {t.strftime("%m/%d %H:%M")} JST が収集時刻より後'
    if t < stamp - timedelta(hours=hours):
        return f'取得 {t.strftime("%m/%d %H:%M")} JST・{hours}時間より前'
    return None


def stale_month(value, stamp, months):
    """None when a monthly period ('2026-M08') is at most `months` before the stamp's month."""
    match = re.fullmatch(r'(\d{4})-M(\d{2})', str(value or ''))
    if not match:
        return '対象月の記録なし'
    age = (stamp.year - int(match.group(1))) * 12 + stamp.month - int(match.group(2))
    return None if 0 <= age <= months else f'最新 {int(match.group(2))}月分・{months}ヶ月より古い'


def _meeting(text):
    """'Oct 28, 2026' -> '10/28'; other text unchanged."""
    try:
        return datetime.strptime(_clean(text), '%b %d, %Y').strftime('%m/%d')
    except ValueError:
        return _clean(text)


# ------------------------------------------------------------------ board rows

def row(group, label, value, base, change, side, strength, asof, rule, *, note='', word=None,
        viz=None, bind=None, digits=2, unit='', kind='value'):
    strength = 'none' if side == 'flat' else strength
    word = word or word_of(side, strength)
    symbol = mark(side, strength)
    quote = (f'{group}｜{label}: {value}（{asof}）｜基準: {base}｜変化: {change}｜向き: {symbol} {word}'
             + (f'｜{note}' if note else '') + f'｜規則: {rule}')
    item = {'group': group, 'label': label, 'display': value, 'baseline': base, 'change': change,
            'direction': word, 'symbol': symbol, 'side': side, 'strength': strength, 'tone': TONE[side],
            'asof': asof, 'note': note, 'rule': rule, 'source_quote': quote, 'kind': kind}
    if viz:
        item['viz'] = viz
    binding = None
    if bind is not None:
        path, raw = bind
        item['value'] = number(raw, digits)
        binding = {'path': path, 'value': item['value'], 'digits': digits, 'unit': unit, 'observedAt': asof}
    return item, binding


def _series_change(points, back):
    """Last value and the value `back` observations earlier, from [[date, value], ...] (oldest first)."""
    pts = [(d, num(v)) for d, v in (points or []) if num(v) is not None]
    if len(pts) <= back:
        return None
    return pts[-1], pts[-1 - back]


def _span(window):
    """'（09/26〜10/14）' for a [first, last] date pair (years added when they differ), else ''."""
    span = [str(x)[:10] for x in window or []]
    if len(span) != 2:
        return ''
    if span[0][:4] != span[1][:4]:
        return f'（{span[0].replace("-", "/")}〜{span[1].replace("-", "/")}）'
    return f'（{_md(span[0])}〜{_md(span[1])}）'


def _spark(points, invert, back, path=None):
    """The judged window only: the last back+1 observations."""
    vals = [num(v) for _, v in points or [] if num(v) is not None][-(back + 1):]
    if len(vals) < 3:
        return None
    viz = {'type': 'spark', 'values': vals, 'invert': invert}
    if path:
        viz['path'], viz['count'] = path, len(vals)
    return viz


def _correlation_note(data, series_id):
    for pair in (data.get('correlation') or {}).get('pairs') or []:
        r = num(pair.get('r_20d'))
        if pair.get('series_id') == series_id and r is not None:
            state = '通常どおり逆方向' if r <= -0.3 else '同じ向き（通常と逆）' if r >= 0.3 else '連動が弱い'
            return f'金との20日相関 {r:+.2f}（{state}）'
    return ''


def _meeting_date(text):
    """The date of a meeting text ('Oct 28, 2026' or '2026-10-28'); None when it is not a date."""
    for pattern in ('%b %d, %Y', '%B %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(_clean(text), pattern).date()
        except ValueError:
            continue
    return None


def _rate_ranges(target_rates):
    """Rate rows with their bounds ('_low', '_high', '_mid'), sorted; None unless every range is 'low-high'
    with finite low < high and no two ranges overlap (a range may start where the previous one ends)."""
    rates = []
    for rate in target_rates or []:
        try:
            low, high = (float(x) for x in str(rate.get('range')).split('-'))
        except (AttributeError, TypeError, ValueError):
            return None
        if not (math.isfinite(low) and math.isfinite(high) and low < high):
            return None
        rates.append({**rate, '_low': low, '_high': high, '_mid': (low + high) / 2})
    rates.sort(key=lambda r: (r['_low'], r['_high']))
    if any(b['_low'] < a['_high'] for a, b in zip(rates, rates[1:])):
        return None
    return rates


def _expected_rate(rates, key):
    """Probability-weighted midpoint (%) over the ranges, or None unless the column sums to about 100."""
    pairs = []
    for rate in rates:
        p = num(rate.get(key)) if key != 'derived' else rate.get('_prev')
        if p is None or not 0 <= p <= 100:
            return None
        pairs.append((rate['_mid'], p))
    total = sum(p for _, p in pairs)
    if not pairs or abs(total - 100) > 1.5:
        return None
    return sum(m * p for m, p in pairs) / total


def _fedwatch(data, mode, stamp):
    """(row, None) or (None, reason): change of the probability-weighted policy rate for the next
    FOMC (a fall prices easing). The baseline is the table's own column, else our history of the
    same meeting with the same set of ranges, no older than FEDWATCH_HISTORY_DAYS."""
    weekly = mode == 'weekly'
    key, window_name = ('prev_week', '前週') if weekly else ('prev_day', '前日')
    fw = data.get('fedwatch') or {}
    if not present(fw):
        return None, '取得失敗'
    late = stale_time(fw.get('timestamp'), stamp)
    if late:
        return None, late
    meeting = _clean(fw.get('next_fomc_date'))
    if not meeting:
        return None, '次回FOMCの日付なし'
    meeting_day = _meeting_date(meeting)
    if meeting_day is None:
        return None, '次回FOMCの日付を読めない'
    if meeting_day < stamp.date():
        return None, f'次回FOMCの日付 {_md(meeting_day.isoformat())} が収集日より前'
    rates = _rate_ranges(fw.get('target_rates'))
    if rates is None:
        return None, '確率表を読めない（レンジの上下・重なり）'
    now = _expected_rate(rates, 'current')
    if now is None:
        return None, '確率の合計が100%前後でない'
    label = f'政策金利の織り込み（{window_name}比）'
    before, origin = _expected_rate(rates, key), f'提供表の{window_name}列'
    if before is None:
        history = (fw.get('deltas') or {}).get(key) or {}
        by_range = history.get('by_range') or {}
        found = re.search(r'(\d{4}-\d{2}-\d{2})', str(history.get('source') or ''))
        if set(by_range) != {r.get('range') for r in rates} or any(num(v) is None for v in by_range.values()):
            return None, f'{window_name}比の比較元なし（同じレンジ構成の履歴がない）'
        if _meeting_date(history.get('meeting')) is None:
            return None, f'{window_name}比の比較元なし（履歴の会合が不明）'
        if _meeting_date(history['meeting']) != meeting_day:
            return None, f'{window_name}比の比較元なし（別の会合の履歴）'
        late = stale_day(found.group(1), stamp, FEDWATCH_HISTORY_DAYS[mode]) if found else '履歴の日付なし'
        if late:
            return None, f'{window_name}比の比較元が古い（{late}）'
        for r in rates:
            r['_prev'] = r['current'] - by_range[r['range']]
        before = _expected_rate(rates, 'derived')
        if before is None:
            return None, f'{window_name}比の比較元の確率が合わない'
        day = _md(found.group(1))
        label, origin = f'政策金利の織り込み（{day}比）', f'自前履歴 {day}'
    raw = (now - before) * 100  # basis points
    threshold = FEDWATCH_STRONG_BP[mode]
    change, places, ep = shown_ends(raw, now, before, 3, threshold, scale=100)
    side, strength = judge(raw, threshold, -1)
    distribution = '・'.join(f'{r["range"]} {r["current"]:.1f}%' for r in rates)
    segments = [{'label': r['range'], 'value': number(r['current'], 1)} for r in rates]
    return row(GROUPS['short'][mode], label, f'期待 {fmt(now, ep)}%', f'{fmt(before, ep)}%（{origin}）', f'{change:+.{places}f}bp',
               side, strength, f'次回FOMC {_meeting(meeting)}',
               '確率で重み付けした政策金利（各レンジの中央値）の変化。低下は緩和寄りの織り込みで金に追い風、上昇は逆風。'
               f'±{threshold:g}bp以上で強（隣のレンジへ確率{4 * threshold:g}ppの移動に相当）。'
               '確率の合計が100%前後でない表、次回会合の日付がない・読めない・収集日より前の表、'
               'レンジの上下や重なりが不正な表は使わない',
               note=f'確率 {distribution}' + (f'。{window_name}列の比較日時は提供元の表示になく未確認' if origin.startswith('提供表') else ''),
               viz={'type': 'probs', 'segments': segments}), None


MATERIALS = {
    'long': (('実質金利', 'FRED DFII10'), ('ドル（20営業日）', 'DXY・FRB広義ドル指数'), ('中銀の金購入', 'IMF IRFCL')),
    'short': (('米2年債', 'CNBC・FRED DGS2'), ('ドル（短期）', 'DXY'), ('政策金利の織り込み', 'Fed Rate Monitor'),
              ('GLD保有量', 'SPDR'), ('投機筋の建玉', 'CFTC COT'), ('個人の比率', 'MyFXBook')),
}


def bias_rows(data, stamp, mode, source=None):
    """Rows for the board. Returns (items, bindings, notes); notes list the missing materials and why.
    Each row records its material and factor (FACTOR)."""
    items, bindings, notes, why, origins, kinds = [], [], [], {}, {}, {}
    weekly = mode == 'weekly'
    long_g, short_g = GROUPS['long'], GROUPS['short'][mode]
    fred = data.get('fred') or {}

    def keep(name, result):
        if result:
            item, binding = result
            item['material'], item['factor'] = name, FACTOR[name]
            items.append(item)
            if binding:
                bindings.append({'figure': 'bias', 'label': item['label'], 'index': len(items) - 1, **binding})

    def skip(name, reason, kind='欠測'):
        # 欠測: no usable value. 判定外: the value was obtained but cannot be judged (no percentile, news unread).
        if name not in why:
            why[name], kinds[name] = reason, kind

    # --- long: real rates, dollar, news / geopolitics, central-bank buying
    s = fred.get('DFII10') or {}
    late = stale_day(s.get('as_of_date'), stamp, MAX_AGE_DAYS['DFII10']) if present(s) else '取得失敗'
    if late:
        skip('実質金利', late)
    elif num(s.get('change_20obs')) is not None and num(s.get('value_20obs_ago')) is not None:
        change, places, ep = shown_ends(s['change_20obs'], s['value'], s['value_20obs_ago'], 2, 0.10)
        side, strength = judge(s['change_20obs'], 0.10, -1)
        keep('実質金利', row(long_g, '実質金利（米10年TIPS）', f'{fmt(s["value"], ep)}%',
                          f'20営業日前 {fmt(s["value_20obs_ago"], ep)}%（{_md(s.get("as_of_20obs_ago"))}）', f'{change:+.{places}f}pp',
                          side, strength, _md(s.get('as_of_date')), '上昇は金の逆風、低下は追い風。20営業日で±0.10pp以上で強',
                          note=_correlation_note(data, 'DFII10'), viz=_spark(s.get('recent'), True, 20, 'fred.DFII10.recent'),
                          bind=('fred.DFII10.change_20obs', s['change_20obs']), digits=places, unit='pp'))
    else:
        skip('実質金利', '20営業日前の値なし')
    dxy = data.get('dxy') or {}
    closes = dxy.get('recent_closes') if present(dxy) else None
    pair = _series_change(closes, 20) if closes else None
    late = stale_day(pair[0][0], stamp, MAX_AGE_DAYS['DXY']) if pair else 'データなし'
    if not late:
        (d1, v1), (d0, v0) = pair
        raw = (v1 / v0 - 1) * 100
        pct, places = shown(raw, 2, 1.0)
        side, strength = judge(raw, 1.0, -1)
        keep('ドル（20営業日）', row(long_g, 'ドル指数DXY（20営業日）', fmt(v1, 3), f'20営業日前 {fmt(v0, 3)}（{_md(d0)}）',
                                f'{pct:+.{places}f}%', side, strength, _md(d1),
                                'ドル高は金の逆風、ドル安は追い風。20営業日で±1.0%以上で強',
                                viz=_spark(closes, True, 20, 'dxy.recent_closes'),
                                bind=(f'dxy.recent_closes.{len(closes) - 1}.1', v1), digits=3, unit='index'))
    else:
        s = fred.get('DTWEXBGS') or {}
        broad = stale_day(s.get('as_of_date'), stamp, MAX_AGE_DAYS['DTWEXBGS']) if present(s) else '取得失敗'
        if not broad and num(s.get('change_20obs')) is not None and (num(s.get('value_20obs_ago')) or 0) > 0:
            raw = s['change_20obs'] / s['value_20obs_ago'] * 100
            pct, places = shown(raw, 2, 1.0)
            side, strength = judge(raw, 1.0, -1)
            keep('ドル（20営業日）', row(long_g, 'ドル（FRB広義ドル指数・20営業日）', fmt(s['value']),
                                    f'20営業日前 {fmt(s["value_20obs_ago"])}（{_md(s.get("as_of_20obs_ago"))}）',
                                    f'{pct:+.{places}f}%', side, strength, _md(s.get('as_of_date')),
                                    'ドル高は金の逆風、ドル安は追い風。20営業日で±1.0%以上で強',
                                    viz=_spark(s.get('recent'), True, 20, 'fred.DTWEXBGS.recent'),
                                    bind=('fred.DTWEXBGS.change_20obs', s['change_20obs']), unit='index'))
        else:
            skip('ドル（20営業日）', f'DXY {late}／広義ドル指数 {broad or "20営業日前の値なし"}')
    news = news_verdict(source)
    if news and NEWS_UNREAD.search(news['summary']):
        skip('ニュース・地政学', '原稿が本文未確認で中立とした', kind='判定外')
    elif news:
        keep('ニュース・地政学', row(long_g, 'ニュース・地政学（原稿の判断）', news['summary'], '直近のニュース・要人発言・地政学', '—',
                                news['side'], news['strength'], '本文', '原稿が見出し・本文の確認結果から付けた向き。'
                                '金利・ドルの動きそのものは別の行で数えるため、それ以外の材料で判断する',
                                word=news['word'], kind='text'))
    cb = data.get('gold_cb') or {}
    late = stale_month(cb.get('as_of_date'), stamp, CB_MAX_MONTHS) if present(cb) else '取得失敗'
    if late:
        skip('中銀の金購入', late)
    elif num(cb.get('cumulative_3m_t')) is not None:
        change, places = shown(cb['cumulative_3m_t'], 1, 20.0)
        side, strength = judge(cb['cumulative_3m_t'], 20.0, +1)
        months = [m for m in cb.get('months') or [] if num(m.get('net_tonnes')) is not None][:3]
        span = f'{cb["cumulative_periods"][-1][-2:].lstrip("0")}〜{cb["cumulative_periods"][0][-2:].lstrip("0")}月' \
            if len(cb.get('cumulative_periods') or []) >= 2 else '直近3ヶ月'
        bars = [{'label': str(m['period'])[-2:].lstrip('0') + '月', 'value': number(m['net_tonnes'], 1)}
                for m in reversed(months)]
        keep('中銀の金購入', row(long_g, '中銀の金購入（IMF報告国・3ヶ月）', f'{change:+.{places}f} t（{span}）',
                              'ゼロ（買い越しと売り越しの境）', f'{change:+.{places}f} t', side, strength,
                              str(cb.get('as_of_date') or '時点不明').replace('-M', '年') + '月分',
                              '純購入は構造的な下支え、純売却は逆風。月次で1〜2ヶ月遅れ。3ヶ月で±20t以上で強',
                              note='月別（棒は左から） ' + '・'.join(f'{b["label"]} {b["value"]:+.1f} t' for b in bars) if bars else '',
                              viz={'type': 'bars', 'items': bars} if bars else None,
                              bind=('gold_cb.cumulative_3m_t', cb['cumulative_3m_t']), digits=places, unit='t'))
    else:
        skip('中銀の金購入', '3ヶ月合計なし')

    # --- short: same-day rates and dollar, policy pricing, surprises, nowcast, ETF, positioning
    yields = data.get('us_yields') or {}
    us2y = yields.get('US2Y') if isinstance(yields, dict) else None
    # Only a quote from the current US trading date is "today"; otherwise FRED with its own date.
    today = not weekly and isinstance(us2y, dict) and us2y.get('stale') is False and \
        not stale_time(us2y.get('as_of_jst'), stamp) and \
        all(num(us2y.get(k)) is not None for k in ('change', 'value', 'prev_close'))
    # CNBC gives the change and both ends; ends that contradict the change are not drawn.
    ends = num(us2y['value']) - num(us2y['prev_close']) if today else None
    # Also when the ends and the change fall in different verdict classes (sign or strength).
    mismatch = today and (abs(ends - num(us2y['change'])) > 0.0015 or
                          judge(ends, 0.05, -1) != judge(num(us2y['change']), 0.05, -1))
    if today and not mismatch:
        # Both ends at one precision with the change, so the printed ends give the printed change.
        change, places, ep = shown_ends(us2y['change'], us2y['value'], us2y['prev_close'], 3, 0.05)
        side, strength = judge(us2y['change'], 0.05, -1)
        keep('米2年債', row(short_g, '米2年債利回り（当日）', f'{fmt(us2y["value"], ep)}%', f'前日終値 {fmt(us2y["prev_close"], ep)}%',
                         f'{change:+.{places}f}pp', side, strength, f'{us2y["as_of_jst"][5:].replace("-", "/")} JST',
                         '上昇は金の逆風、低下は追い風。前日比±0.05pp以上で強（CNBCの当日値）',
                         viz={'type': 'delta', 'from': number(us2y['prev_close'], ep), 'to': number(us2y['value'], ep),
                              'invert': True, 'scale': 0.05},
                         bind=('us_yields.US2Y.change', us2y['change']), digits=places, unit='pp'))
    else:
        s = fred.get('DGS2') or {}
        back = 5 if weekly else 1
        pair = _series_change(s.get('recent'), back) if present(s) else None
        late = (stale_day(pair[0][0], stamp, MAX_AGE_DAYS['DGS2']) if pair
                else ('取得失敗' if not present(s) else f'直近{back + 1}観測なし'))
        if late:
            skip('米2年債', f'CNBC 前日比が当日値と前日終値の差と合わない／FRED {late}' if mismatch else late)
        else:
            (d1, v1), (d0, v0) = pair
            threshold = 0.10 if weekly else 0.05
            change, places, ep = shown_ends(v1 - v0, v1, v0, 2, threshold)
            side, strength = judge(v1 - v0, threshold, -1)
            viz = _spark(s.get('recent'), True, back, 'fred.DGS2.recent') if weekly else \
                {'type': 'delta', 'from': number(v0, ep), 'to': number(v1, ep), 'invert': True, 'scale': threshold}
            keep('米2年債', row(short_g, f'米2年債利回り（{"5営業日" if weekly else "前営業日比"}）', f'{fmt(v1, ep)}%',
                             f'{_md(d0)} {fmt(v0, ep)}%', f'{change:+.{places}f}pp', side, strength, _md(d1),
                             f'上昇は金の逆風、低下は追い風。±{threshold:.2f}pp以上で強（FRED DGS2）', viz=viz,
                             bind=(f'fred.DGS2.recent.{len(s["recent"]) - 1}.1', v1), unit='%'))
    if weekly:
        pair = _series_change(closes, 5) if closes else None
        late = stale_day(pair[0][0], stamp, MAX_AGE_DAYS['DXY']) if pair else 'データなし'
        if late:
            skip('ドル（短期）', late)
        else:
            (d1, v1), (d0, v0) = pair
            raw = (v1 / v0 - 1) * 100
            pct, places = shown(raw, 2, 0.7)
            side, strength = judge(raw, 0.7, -1)
            keep('ドル（短期）', row(short_g, 'ドル指数DXY（5営業日）', fmt(v1, 3), f'{_md(d0)} {fmt(v0, 3)}', f'{pct:+.{places}f}%',
                                side, strength, _md(d1), 'ドル高は金の逆風、ドル安は追い風。5営業日で±0.7%以上で強',
                                viz={'type': 'delta', 'from': number(v0, 3), 'to': number(v1, 3), 'invert': True,
                                     'scale': number(v0 * 0.007, 3)},
                                bind=(f'dxy.recent_closes.{len(closes) - 1}.1', v1), digits=3, unit='index'))
    else:
        # The EUR/USD estimate is not on the index's scale, so it is never shown as DXY.
        late = ('取得失敗' if not present(dxy) else 'EUR/USDからの推定値のみ（DXYの当日値なし）' if dxy.get('estimated')
                else '提供元が古い値と表示' if dxy.get('stale') is True else stale_time(dxy.get('timestamp'), stamp))
        if late or num(dxy.get('change_pct')) is None or num(dxy.get('current_price')) is None:
            skip('ドル（短期）', late or '当日の値なし')
        else:
            pct, places = shown(dxy['change_pct'], 2, 0.3)
            side, strength = judge(dxy['change_pct'], 0.3, -1)
            has_prev = num(dxy.get('prev_close')) is not None
            keep('ドル（短期）', row(short_g, 'ドル指数DXY（当日）', fmt(dxy['current_price'], 3),
                                f'前日終値 {fmt(dxy["prev_close"], 3)}' if has_prev else '前日終値',
                                f'{pct:+.{places}f}%', side, strength, f'記録 {_stamp(dxy.get("timestamp"))}',
                                'ドル高は金の逆風、ドル安は追い風。前日比±0.3%以上で強',
                                viz={'type': 'delta', 'from': number(dxy['prev_close'], 3), 'to': number(dxy['current_price'], 3),
                                     'invert': True, 'scale': number(dxy['prev_close'] * 0.003, 3)} if has_prev else None,
                                bind=('dxy.change_pct', dxy['change_pct']), digits=places, unit='%'))
    result, reason = _fedwatch(data, mode, stamp)
    if result:
        keep('政策金利の織り込み', result)
    else:
        skip('政策金利の織り込み', reason)
    for result in surprise_rows(data, stamp, mode):
        keep('指標の結果', result)
    for result in nowcast_rows(data, stamp, mode):
        keep('予測との差', result)
    etf = data.get('gold_etf') or {}
    late = stale_day(etf.get('as_of_date'), stamp, MAX_AGE_DAYS['GLD']) if present(etf) else '取得失敗'
    if late:
        skip('GLD保有量', late)
    elif num(etf.get('change_5d_t')) is not None:
        tonnes = num(etf.get('tonnes'))
        if tonnes is not None:
            change, places, ep = shown_ends(etf['change_5d_t'], tonnes, tonnes - etf['change_5d_t'], 2, 2.0)
        else:
            (change, places), ep = shown(etf['change_5d_t'], 2, 2.0), 2
        side, strength = judge(etf['change_5d_t'], 2.0, +1)
        has_20 = num(etf.get('change_20d_t')) is not None
        bars = [{'label': '5営業日', 'value': change}] + ([{'label': '20営業日', 'value': number(etf['change_20d_t'], 2)}]
                                                        if has_20 else [])
        keep('GLD保有量', row(short_g, 'GLD保有量（5営業日）', f'{fmt(tonnes, ep)} t' if tonnes is not None else '保有量',
                            f'5営業日前 {fmt(tonnes - etf["change_5d_t"], ep)} t' if tonnes is not None else '5営業日前',
                            f'{change:+.{places}f} t', side, strength, _md(etf.get('as_of_date')),
                            '保有量の増加（設定超過）は追い風、減少（解約超過）は逆風。5営業日で±2t以上で強。20営業日は参考',
                            note=f'棒は左から5営業日・20営業日。20営業日 {fmt(etf["change_20d_t"], sign=True)} t' if has_20 else '',
                            viz={'type': 'bars', 'items': bars}, bind=('gold_etf.change_5d_t', etf['change_5d_t']),
                            digits=places, unit='t'))
    else:
        skip('GLD保有量', '5営業日の変化なし')
    positioning = (data.get('offchart_features') or {}).get('positioning') or {}
    cot = data.get('cot_disaggregated') or {}
    mm = ((cot.get('data') or {}).get('managed_money')) if present(cot) else None
    p = positioning.get('cot_gold_mm') or {}
    cot_date = (cot.get('data') or {}).get('date')
    if not isinstance(mm, dict) or num(mm.get('net')) is None:
        skip('投機筋の建玉', '取得失敗' if not present(cot) else '買い越しの値なし')
    elif any(num(mm.get(k)) is None or mm[k] < 0 for k in ('long', 'short')) or abs(mm['long'] - mm['short'] - mm['net']) > 0.5:
        skip('投機筋の建玉', '買い−売り＝差引が合わない')
    elif stale_day(cot_date, stamp, MAX_AGE_DAYS['COT']):
        skip('投機筋の建玉', stale_day(cot_date, stamp, MAX_AGE_DAYS['COT']))
    elif num(p.get('percentile')) is None:
        skip('投機筋の建玉', f'買い越し{mm["net"]:,.0f}枚は取得・自前履歴{p.get("history_n") or 0}週で百分位なし'
                         f'（{MIN_SAMPLES}週以上で判定）', kind='判定外')
    elif not 0 <= p['percentile'] <= 100:
        skip('投機筋の建玉', '百分位が0〜100の外')
    elif str(p.get('report_date')) != str(cot_date):
        skip('投機筋の建玉', '百分位と建玉の報告日が違う')
    else:
        pct, places = shown(p['percentile'], 1, 90, below=(10,))
        side = 'down' if p['percentile'] >= 90 - FLOAT_NOISE else 'up' if p['percentile'] <= 10 + FLOAT_NOISE else 'flat'
        change = num(mm.get('net_change'))
        span = _span(p.get('window'))
        share = num(p.get('mm_net_pct_oi'))
        keep('投機筋の建玉', row(short_g, '投機筋の買い越し（COT Managed Money）', f'{mm["net"]:,.0f}枚',
                              (f'建玉比 {share:.1f}%の' if share is not None else '') + f'{p.get("history_n")}週百分位 '
                              f'{pct:.{places}f}{span}',
                              f'前週比 {change:+,.0f}枚' if change is not None else '前週比なし', side, 'weak',
                              _md(cot_date),
                              '百分位90以上は買いの混み合い（反転注意）で下向き、10以下は上向き。どちらも弱（0.5票）で、'
                              '反転の確認ではない。週次で数日遅れる',
                              word=None if side != 'flat' else '中立（偏りなし）',
                              viz={'type': 'track', 'value': pct, 'low': 10, 'high': 90},
                              bind=('offchart_features.positioning.cot_gold_mm.percentile', p['percentile']), digits=places,
                              unit='百分位'))
    r = (data.get('retail_sentiment') or {}).get('XAUUSD') or {}
    pr = (positioning.get('retail') or {}).get('XAUUSD') or {}
    long_pct, short_pct = num(r.get('long_pct')), num(r.get('short_pct'))
    if present(r) and r.get('source'):
        origins['個人の比率'] = str(r['source'])  # FXSSI when MyFXBook failed
    if not present(r):
        skip('個人の比率', '取得失敗')
    elif long_pct is None or short_pct is None:
        skip('個人の比率', 'ロング・ショート比率の値なし')
    elif not (0 <= long_pct <= 100 and 0 <= short_pct <= 100) or abs(long_pct + short_pct - 100) > 0.5:
        skip('個人の比率', 'ロングとショートの合計が100%でない')
    elif stale_time(r.get('timestamp'), stamp):
        skip('個人の比率', stale_time(r.get('timestamp'), stamp))
    elif pr and (pr.get('source') != r.get('source') or num(pr.get('long_pct')) is None
                 or abs(pr['long_pct'] - long_pct) > FLOAT_NOISE):
        skip('個人の比率', '百分位が現在の比率と別の観測')
    elif num(pr.get('percentile')) is None:
        skip('個人の比率', f'ロング{fmt(long_pct, 1)}%は取得・同じ取得元の自前履歴{pr.get("history_n") or 0}件で百分位なし'
                        f'（{MIN_SAMPLES}件・{MIN_DISTINCT}種以上で判定）', kind='判定外')
    elif not 0 <= pr['percentile'] <= 100:
        skip('個人の比率', '百分位が0〜100の外')
    else:
        pct, places = shown(pr['percentile'], 1, 90, below=(10,))
        side = 'down' if pr['percentile'] >= 90 - FLOAT_NOISE else 'up' if pr['percentile'] <= 10 + FLOAT_NOISE else 'flat'
        keep('個人の比率', row(short_g, f'個人のロング比率（{r.get("source") or "提供元不明"}）',
                            f'ロング {fmt(long_pct, 1)}%・ショート {fmt(short_pct, 1)}%',
                            f'自前履歴{pr.get("history_n")}件{_span(pr.get("window")) or "（期間未確認）"}の百分位 {pct:.{places}f}', '—', side, 'weak',
                            f'記録 {_stamp(r.get("timestamp"))}',
                            '多数派の逆。百分位90以上（ロング過多）は下向き、10以下は上向き。どちらも弱（0.5票）。その間は中立',
                            word=None if side != 'flat' else '中立（偏りなし）',
                            viz={'type': 'split', 'left': number(long_pct, 1), 'right': number(short_pct, 1), 'track': pct},
                            bind=('retail_sentiment.XAUUSD.long_pct', long_pct), digits=1, unit='%'))
    got = {item['material'] for item in items}
    for group, materials in MATERIALS.items():
        for name, origin in materials:
            if name not in got:
                origin = origins.get(name, origin)
                detail = f'{origin}・{why[name]}' if why.get(name) else origin
                notes.append(f'{kinds.get(name, "欠測")}: {name}（{detail}）。'
                             f'{GROUPS["long"] if group == "long" else short_g}の集計に入れていない')
    reason = nowcast_missing(data, stamp, mode)
    if reason:
        notes.append(f'欠測: 予測との差（Cleveland Fed・{reason}）。{short_g}の集計に入れていない')
    if source is not None and 'ニュース・地政学' not in got:
        detail = f'原稿の「ニュース・地政学:」行・{why["ニュース・地政学"]}' if why.get('ニュース・地政学') \
            else '原稿の「ニュース・地政学:」行'
        notes.append(f'{kinds.get("ニュース・地政学", "欠測")}: ニュース・地政学（{detail}）。{long_g}の集計に入れていない')
    return items, bindings, notes


def surprise_rows(data, stamp, mode):
    """Released US Tier A/B releases with a pre-release forecast record: one row per release time,
    from the previous weekday (Daily) or the past seven days (Weekly).

    Releases at the same time (CPI and core CPI, payrolls and unemployment) are one shock:
    the majority of the members gives the direction; strong only when two or more members all agree
    (one series alone is weak evidence, however large its miss).
    """
    start = results_start(stamp, mode)
    groups = OrderedDict()
    records = data.get('macro_surprises') or []
    for k, ev in sorted(enumerate(records), key=lambda x: str(x[1].get('released_at_jst'))):
        released = _parse_jst(ev.get('released_at_jst'))
        if (released is None or tier(ev) is None or not (start <= released <= stamp)
                or not str(ev.get('forecast_provenance', '')).startswith('発表前記録')):
            continue
        s = compute_surprise(ev)
        sign = gold_sign(ev)
        if s is None or sign == 0:
            continue
        groups.setdefault(released, []).append((ev, s, sign, k))
    out = []
    for released in sorted(groups, reverse=True)[:3]:
        members = groups[released]
        sides = ['flat' if s['diff'] == 0 else ('up' if s['diff'] * sign > 0 else 'down') for _, s, sign, _ in members]
        up, down, flat = sides.count('up'), sides.count('down'), sides.count('flat')
        if up == down:
            side, strength = 'flat', 'none'
        else:
            side = 'up' if up > down else 'down'
            strength = 'strong' if len(members) >= 2 and max(up, down) == len(members) else 'weak'
        names = '・'.join(dict.fromkeys(display_name(ev.get('indicator')).split('（')[0] for ev, _, _, _ in members))
        if len(members) == 1:
            ev, s, sign, _ = members[0]
            display, base, change = f'結果 {ev.get("actual")}', f'予想 {ev.get("forecast")}（発表前記録）', f'{s["diff"]:+g}{s["unit"]}'
            viz = {'type': 'gap', 'value': s['diff'] * sign, 'scale': max(abs(s['diff']), 0.1)}
        else:
            display = f'{len(members)}系列: 追い風 {up}・逆風 {down}・予想どおり {flat}'
            base = '予想（発表前記録）'
            change = '／'.join(f'{display_name(ev.get("indicator"))} {s["diff"]:+g}{s["unit"]}' for ev, s, _, _ in members)
            viz = {'type': 'votes', 'up': up, 'down': down, 'flat': flat}
        word = '予想どおり（中立）' if side == 'flat' and up == down == 0 else ('追い風と逆風が同数（中立）' if side == 'flat' else None)
        item, _ = row(GROUPS['short'][mode], f'指標の結果: {names}', display, base, change, side, strength,
                      f'{released.strftime("%m/%d %H:%M")} JST',
                      '予想より強い米指標はドル・金利高で金の逆風、弱ければ追い風（一般則）。同時発表は1行にまとめ、'
                      '2系列以上がすべて同じ向きなら強、1系列だけの場合・予想どおりの系列を含む場合・多数決は弱',
                      word=word, viz=viz)
        # A text binding: check_bindings re-reads each record (actual, forecast, difference).
        out.append((item, {'members': [f'macro_surprises.{k}' for *_, k in members]}))
    return out


def nowcast_missing(data, stamp, mode):
    """Why a scheduled CPI / PCE release (a mapped nowcast series, inside the event-table window) has no
    nowcast gap, or None when none is scheduled or every one is adopted. The first NOWCAST_MAX_PER_WINDOW
    releases per series and month are the ones the board would show."""
    from scrapers.inflation_nowcast import series_key, target_month
    calendar = data.get('economic_calendar')
    if not present(calendar):
        return None
    scheduled = {}
    for when, ev in sorted(((event_datetime_jst(e), e) for e in calendar.get('events') or []),
                           key=lambda x: (x[0] is None, x[0] or stamp)):
        level, key = (tier(ev) if when else None), series_key(ev.get('indicator'))
        if level is None or key is None or not (stamp < when < event_window(stamp, mode, level)[1]):
            continue
        scheduled.setdefault((key, target_month(ev.get('indicator'), when.date())), (when, ev))
    if not scheduled:
        return None
    adopted = {(gap['series'], gap['month']) for _, _, gap in
               adopted_nowcasts(calendar, data.get('inflation_nowcast'), stamp, mode)}
    wanted = list(scheduled.items())[:NOWCAST_MAX_PER_WINDOW]
    nowcast = data.get('inflation_nowcast')
    problem = (f'取得失敗（{nowcast["error"]}）' if isinstance(nowcast, dict) and nowcast.get('error')
               else nowcast_fetch_problem(nowcast, stamp))
    reasons = {}
    for key, (when, ev) in wanted:
        if key in adopted:
            continue
        gap = None if problem else nowcast_gap(ev, nowcast)
        age = nowcast_age(gap.get('as_of'), stamp) if gap else None
        reason = (problem or ('対象月の予測か市場予想がない' if not gap else '予測の更新日を読めない' if age is None
                              else f'予測の更新が{NOWCAST_MAX_AGE_DAYS}日より前' if age > NOWCAST_MAX_AGE_DAYS
                              else '予測の更新日が収集日より後' if age < 0 else '同じ月の別の発表を採用'))
        reasons.setdefault(reason, []).append(display_name(ev.get('indicator')).split('（')[0])
    return '・'.join(f'{"・".join(names)}は{reason}' for reason, names in reasons.items()) or None


def nowcast_rows(data, stamp, mode):
    """Upcoming CPI / PCE (month-over-month) inside the event-table window: Cleveland Fed nowcast
    minus the consensus, a weak forecast-based vote. us_events.adopted_nowcasts decides which (the same
    list the parent input and the event table use). The gap is judged exactly; the shown nowcast has
    enough decimals that the shown difference is the difference of the shown values."""
    out = []
    for when, ev, gap in adopted_nowcasts(data.get('economic_calendar'), data.get('inflation_nowcast'), stamp, mode):
        shown_now, shown_consensus, shown_diff = gap_display(gap)
        value = float(shown_diff)
        side = 'flat' if abs(gap['gap']) < GAP_NEUTRAL_PP else ('up' if gap['gap'] < 0 else 'down')
        out.append(row(GROUPS['short'][mode], f'予測との差: {display_name(ev.get("indicator"))}',
                       f'nowcast {shown_now}%', f'市場予想 {shown_consensus}%', f'{shown_diff}pp', side, 'weak',
                       f'{when.strftime("%m/%d %H:%M")} JST 発表・nowcast {gap["as_of"]}時点',
                       'Cleveland連銀の予測が市場予想より低ければ下振れ寄りで金に追い風、高ければ逆風。±0.05pp未満は中立。'
                       f'発表前の参考で、票は弱（0.5）。{NOWCAST_MAX_AGE_DAYS}日より古い予測は使わない',
                       word='中立（予想並み）' if side == 'flat' else None,
                       viz={'type': 'gap', 'value': -value, 'scale': max(abs(value), 0.2)},
                       bind=(f'inflation_nowcast.months.{gap["month"]}.{gap["series"]}.value', gap['nowcast']),
                       digits=len(shown_now.partition('.')[2]), unit='%'))
    return out


def factor_votes(items, group):
    """[(factor, vote)] in first-row order: rows of one factor give one vote, the larger one when
    they agree and none when they disagree; FACTOR_CAP limits a factor's vote."""
    factors = OrderedDict()
    for item in items:
        if item['group'] == group and item['symbol'] in VOTE:
            factors.setdefault(item.get('factor') or item['label'], []).append(VOTE[item['symbol']])
    out = []
    for name, votes in factors.items():
        signs = {(v > 0) - (v < 0) for v in votes if v}
        vote = max(votes, key=abs) if len(signs) == 1 else 0.0
        cap = FACTOR_CAP.get(name, 1.0)
        out.append((name, max(-cap, min(cap, vote))))
    return out


def vote_mark(vote):
    return {1.0: '▲', 0.5: '△', -1.0: '▼', -0.5: '▽'}.get(vote, '—')


def tally(items, group):
    factors = factor_votes(items, group)
    if not factors:
        # No material is not a neutral verdict: blend() still reads the side as flat (arithmetic unchanged),
        # but `available` keeps the two apart wherever the result is explained.
        return {'side': 'flat', 'strength': 'none', 'symbol': '—', 'word': '材料なし', 'detail': '材料なし',
                'tone': 'neutral', 'factors': [], 'available': False}
    up = sum(v for _, v in factors if v > 0)
    down = -sum(v for _, v in factors if v < 0)
    diff = up - down
    if abs(diff) < 1:
        side, strength = 'flat', 'none'
    else:
        side, strength = ('up' if diff > 0 else 'down'), ('strong' if abs(diff) >= 2 else 'weak')
    detail = '・'.join(f'{name} {vote_mark(v)}' for name, v in factors)
    return {'side': side, 'strength': strength, 'symbol': mark(side, strength), 'word': word_of(side, strength),
            'detail': detail, 'tone': TONE[side], 'factors': factors, 'available': True}


def _verdict_word(text):
    """(side, strength) for exactly one of the five verdict words; None otherwise."""
    return VERDICTS.get(_clean(text))


def _one_line(pattern, source, what):
    """The single match of a line pattern; ValueError when the report repeats the line."""
    found = list(re.finditer(pattern, source or '', re.M))
    if len(found) > 1:
        raise ValueError(f'Report repeats the {what} line')
    return found[0] if found else None


def source_verdicts(source):
    """{'長期': (side, strength, text), ...} from the one line
    「時間軸別の向き: 長期 上向き・強｜短期 …｜総合 …」. A repeated line or horizon is an error."""
    match = _one_line(r'^時間軸別の向き[:：](.+)$', source, '時間軸別の向き')
    if not match:
        return {}
    out = {}
    for part in re.split(r'[｜|]', match.group(1)):
        found = re.match(r'\s*(長期|短期|総合)\s*[:：]?\s*(.+)$', part)
        if found:
            if found.group(1) in out:
                raise ValueError(f'時間軸別の向き repeats {found.group(1)}')
            parsed = _verdict_word(found.group(2))
            if parsed:
                out[found.group(1)] = (*parsed, _clean(found.group(2)))
    return out


def news_verdict(source):
    """「ニュース・地政学: 上向き・弱｜要旨」 -> side, strength, word and the summary text."""
    match = _one_line(r'^ニュース・地政学[:：]\s*([^｜|\n]+)[｜|]\s*(.+)$', source, 'ニュース・地政学')
    if not match:
        return None
    parsed = _verdict_word(match.group(1))
    if not parsed:
        return None
    side, strength = parsed
    return {'side': side, 'strength': strength, 'word': _clean(match.group(1)), 'summary': _clean(match.group(2))}


def reason_line(source):
    """The report's one-line reason for a verdict that differs from the rule tally."""
    match = _one_line(r'^集計と違う理由[:：]\s*(.+)$', source, '集計と違う理由')
    return match.group(0).strip() if match and _clean(match.group(1)) else None


def blend(long_t, short_t):
    a, b = long_t['side'], short_t['side']
    if a == b:
        strength = 'strong' if long_t['strength'] == short_t['strength'] == 'strong' else 'weak'
        return a, ('none' if a == 'flat' else strength)
    if a == 'flat':
        return b, 'weak'
    if b == 'flat':
        return a, 'weak'
    return 'flat', 'none'


def blend_note(long_t, short_t):
    """Which horizon had no material (so the 総合 came from the other alone); '' when both had."""
    empty = [name for name, t in (('長期', long_t), ('短期', short_t)) if not t.get('available', True)]
    if len(empty) == 2:
        return '長期・短期とも材料なし'
    return f'{empty[0]}は材料なし（{"短期" if empty[0] == "長期" else "長期"}だけで判定）' if empty else ''


def rule_verdicts(items, mode):
    """{'長期': tally, '短期': tally, '総合': (side, strength), '総合の注記': text} from the rows alone."""
    long_t, short_t = tally(items, GROUPS['long']), tally(items, GROUPS['short'][mode])
    return {'長期': long_t, '短期': short_t, '総合': blend(long_t, short_t), '総合の注記': blend_note(long_t, short_t)}


def _rule_pair(rules, name):
    rule = rules[name]
    return (rule['side'], rule['strength']) if isinstance(rule, dict) else rule


def verdict_mismatches(data, stamp, mode, source):
    """Horizons where the report's verdict differs from the rule tally (with the news row)."""
    items, _, _ = bias_rows(data, stamp, mode, source)
    rules = rule_verdicts(items, mode)
    own = source_verdicts(source)
    return [name for name in ('長期', '短期', '総合') if name in own and own[name][:2] != _rule_pair(rules, name)]


def bias_figure(data, stamp, mode, source=None):
    items, bindings, notes = bias_rows(data, stamp, mode, source)
    if not items:
        return None
    groups = {'長期': GROUPS['long'], '短期': GROUPS['short'][mode]}
    rules = rule_verdicts(items, mode)
    blend_text = rules['総合の注記']
    own = source_verdicts(source)
    reason = reason_line(source)
    # Per horizon: '欠測: A・B。判定外: C' (欠測 = no usable value, 判定外 = obtained but not judged).
    missing = {}
    for name, group in groups.items():
        kinds = {}
        for n in notes:
            if f'{group}の集計' in n:
                kind, _, rest = n.partition(': ')
                kinds.setdefault(kind, []).append(rest.split('（')[0])
        missing[name] = '。'.join(f'{kind}: {"・".join(names)}' for kind, names in kinds.items())
    verdicts, rows = [], []
    for name in ('長期', '短期', '総合'):
        side, strength = _rule_pair(rules, name)
        rule_word = rules[name]['word'] if name in groups else word_of(side, strength)
        if name == '総合' and blend_text == '長期・短期とも材料なし':
            rule_word = '材料なし'
        short = ''
        if name in own:
            o_side, o_strength, text = own[name]
            differs = (o_side, o_strength) != (side, strength)
            main = {'symbol': mark(o_side, o_strength), 'word': text, 'tone': TONE[o_side],
                    'origin': '原稿の判断・集計と異なる' if differs else
                    '原稿の判断（集計は材料なし）' if rule_word == '材料なし' else '原稿の判断'}
        else:
            differs = False
            main = {'symbol': mark(side, strength) if rule_word != '材料なし' else '—', 'word': rule_word,
                    'tone': TONE[side], 'origin': '規則集計' if name in groups else '長期×短期の規則'}
        if name in groups:
            factors = rules[name]['factors']
            sub = (f'規則の集計: {rule_word}' + (f'（{rules[name]["detail"]}）' if factors else '')
                   + (f'。{missing[name]}' if missing[name] else ''))
            short = f'規則の集計 {rule_word}' + (f'・要因{len(factors)}' if factors else '')
        else:
            sub = f'長期×短期の規則: {rule_word}' + (f'。{blend_text}' if blend_text and rule_word != '材料なし' else '')
        quote = f'バイアス｜{name}: {main["symbol"]} {main["word"]}（{main["origin"]}）｜{sub}' + (f'｜{short}' if short else '')
        rows.append(quote)
        # The detail is shown when the verdict differs, when a horizon has no material, or when one is missing.
        show = differs or (name in groups and (rule_word == '材料なし' or bool(missing[name]))) \
            or (name == '総合' and bool(blend_text))
        # A verdict that follows the tally only names what is missing (the tally is in the group header).
        detail = (missing[name] if name in groups and missing[name] and not differs
                  and rule_word != '材料なし' else sub)
        verdicts.append({'label': name, 'group': groups.get(name), 'symbol': main['symbol'], 'direction': main['word'],
                         'tone': main['tone'], 'origin': main['origin'], 'detail': detail, 'short': short,
                         'mismatch': differs, 'show_detail': show, 'source_quote': quote})
    legend = ('各行は「現在の値」を「基準」と比べ、その変化を金の一般則で向きに写したものです。▲▼は閾値以上の強い向き、△▽は閾値未満の弱い向き、'
              '—は中立です。向きは丸める前の値で判定します。データがない材料と古い材料は行ごと省き、欠測として理由と一緒に示します。'
              + TALLY_RULE + '。' + BLEND_RULE + '。' + CAVEAT + '。'
              '小図は、推移線がその行だけの自動尺度（行ごとの最小〜最大に合わせるため、傾きを行どうしで比べることはできません）、'
              '2点の比較線がその行の閾値を基準にした尺度、棒が期間ごとの量、帯が百分位や比率です。'
              '大きな記号は原稿（分析本文）の判断で、規則の集計と違う場合は「集計と異なる」と理由を示します。')
    rows.append('バイアス盤の読み方: ' + legend)
    figure = {'id': 'bias', 'type': 'bias_board', 'kicker': '期間ごとのバイアス',
              'title': '判断材料と金（XAUUSD）への向き',
              'subtitle': '各行が判断材料。右端が金への向き（▲▼ 強い、△▽ 弱い、— 中立）',
              'groups': [groups['長期'], groups['短期']], 'verdicts': verdicts, 'items': items,
              'legend': legend, 'rules': [{'label': i['label'], 'rule': i['rule']} for i in items],
              'sources': [{'label': 'FRED', 'url': 'https://fred.stlouisfed.org/'},
                          {'label': 'CNBC', 'url': 'https://www.cnbc.com/quotes/US2Y'},
                          {'label': 'Investing.com', 'url': 'https://www.investing.com/'},
                          {'label': 'Investing.com Fed Rate Monitor', 'url': 'https://www.investing.com/central-banks/fed-rate-monitor'},
                          {'label': 'Cleveland Fed', 'url': 'https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting'},
                          {'label': 'SPDR', 'url': 'https://www.spdrgoldshares.com/'},
                          {'label': 'IMF IRFCL', 'url': 'https://data.imf.org/'},
                          {'label': 'CFTC', 'url': 'https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm'},
                          {'label': 'MyFXBook', 'url': 'https://www.myfxbook.com/community/outlook/XAUUSD'}]}
    if reason and any(v['mismatch'] for v in verdicts):
        figure['reason'] = {'text': reason, 'source_quote': reason}
    return {'figure': figure, 'rows': [i['source_quote'] for i in items] + rows, 'bindings': bindings, 'notes': notes}


def format_board_lines(data, mode):
    """Parent-model input: the same rows and tallies the HTML board will show (news row excluded)."""
    from scripts.report_bundle import analysis_cutoff
    lines = ['### バイアス盤の材料（コード集計・HTMLに表示）']
    try:
        stamp = analysis_cutoff(data)
        items, _, notes = bias_rows(data, stamp, mode)
    except Exception as exc:  # the board is rebuilt at render time; input stays usable
        lines.append(f'- 集計できず（{type(exc).__name__}）')
        return lines
    for item in items:
        lines.append('- ' + item['source_quote'].split('｜規則: ')[0])
    for name, group in (('長期', GROUPS['long']), ('短期', GROUPS['short'][mode])):
        t = tally(items, group)
        lines.append(f'- 集計 {name}: 要因ごと {t["detail"]} → {t["symbol"]} {t["word"]}')
    lines.extend(f'- {note}' for note in notes)
    lines.append('- 長期にはこの後、原稿の「ニュース・地政学:」行が1要因として加わる（金利・ドルの動きそのものは数えない）。'
                 + TALLY_RULE + '。')
    lines.append('- 「時間軸別の向き:」は原稿の判断。ニュース行を加えた集計と違う向きにするときは、セクション0に'
                 '`集計と違う理由: <1行>` を書く（HTMLに表示される）')
    return lines


# ------------------------------------------------------------------ liquidity map

def _draw(source):
    """The report's draw from its one 「その日の目的地」 section: direction, level, price and the
    hypothesis window (JST). Two sections are an error; a section without a level is no draw."""
    blocks = list(re.finditer(r'^## その日の目的地.*?(?=^## |\Z)', source or '', re.M | re.S))
    if not blocks:
        return None
    if len(blocks) > 1:
        raise ValueError('Report has more than one その日の目的地 section')
    block = blocks[0].group(0)
    # Level names carry dates such as 10/12, so the field separator is " / " with spaces.
    match = re.search(r'方向:\s*(up|down|上|下)\S*\s+/\s+水準:\s*(.+?)\s+/\s+価格:\s*([0-9][0-9,]*\.?\d*)', block)
    if not match:
        return None
    try:
        price = float(match.group(3).replace(',', ''))
    except ValueError:
        return None
    found = re.search(r'対象時間（JST）[:：]\s*(\S+)\s*[～〜~]\s*(\S+)', block)
    start, end = (_parse_jst(found.group(1)), _parse_jst(found.group(2))) if found else (None, None)
    # numeric_validation writes 「使ったSMT事実: なし」 when no silver/dollar fact supports the draw.
    smt = re.search(r'使ったSMT事実[:：]\s*(\S.*)', block)
    return {'direction': 'up' if match.group(1) in ('up', '上') else 'down', 'level': _clean(match.group(2)),
            'price': price, 'window': (start, end) if start and end and start < end else None,
            'unsupported': bool(smt) and smt.group(1).strip() == 'なし', 'quote': _clean(match.group(0))}


def _window_text(window):
    """'10/13 22:40〜翌06:00' (JST)."""
    start, end = window
    if end.date() == start.date():
        return f'{start.strftime("%m/%d %H:%M")}〜{end.strftime("%H:%M")}'
    if end.date() == start.date() + timedelta(days=1):
        return f'{start.strftime("%m/%d %H:%M")}〜翌{end.strftime("%H:%M")}'
    return f'{start.strftime("%m/%d %H:%M")}〜{end.strftime("%m/%d %H:%M")}'


def _liq(kind, label, price_text, position, *, pool='', state='', state_word='', icon='', touched='',
         distance_text='', proximity=None, offscale=False, value=None):
    """One map row. The quote repeats every displayed string, so the renderer can verify each of them."""
    parts = [f'流動性｜{label}: {price_text}'] + [x for x in (pool, distance_text, state_word, touched) if x]
    item = {'kind': kind, 'label': label, 'price_text': price_text, 'position': number(position), 'pool': pool,
            'state': state, 'state_word': state_word, 'icon': icon, 'touched': touched,
            'distance_text': distance_text, 'proximity': proximity, 'source_quote': '｜'.join(parts)}
    if offscale:
        item['offscale'] = True
    if value is not None:
        item['value'] = number(value)
    return item


GVZ_MAX_AGE_DAYS = 5  # scrapers.fred.SERIES_CONFIG['GVZCLS']['stale_days']: the rule of the FRED series


def _band_late(liq, stamp):
    """Why the GVZ expected-move band cannot be shown (stale flag of its source, observation date
    older than the series rule, or no value); None when it can."""
    if not num(liq.get('expected_move_1sd')):
        error = liq.get('gvz_error')
        return f'GVZ の取得に失敗（{_clean(error)[:60]}）' if error else 'GVZ の値なし'
    if liq.get('gvz_stale') is True:
        return 'GVZ は提供元が古い値と表示'
    late = stale_day(liq.get('gvz_as_of'), stamp, GVZ_MAX_AGE_DAYS)
    return f'GVZ {late}' if late else None


def liquidity_figure(data, stamp, mode, source=None):
    lv = data.get('xau_levels') or {}
    if not present(lv) or not lv.get('levels'):
        return None
    q = data.get('report_quote') or data.get('_raw_quote_XAUUSD') or {}
    price = _float(q.get('close')) or num(lv.get('price'))
    if price is None:
        return None
    span = LADDER_RANGE_PCT[mode]
    weekly = mode == 'weekly'
    liq = data.get('liquidity_levels') or {}
    band_late = _band_late(liq, stamp)
    move = None if band_late else num(liq.get('expected_move_1sd'))
    if weekly and move:
        move = number(move * math.sqrt(5), 1)
    items, aux, bindings, notes = [], [], [], []
    if band_late:
        notes.append(f'流動性マップ: 想定変動の帯を出していない（{band_late}）')

    def far(position):
        return abs(position - price) / price * 100 > span

    def away(position):
        return f'{number(position - price, 2):+,.2f}（{number((position - price) / price * 100, 2):+.2f}%）'

    for index, level in enumerate(lv['levels']):
        value = num(level.get('price'))
        if value is None:
            continue
        touched = f'最初に抜けた足 {level["touched_at"]} JST（{level["touched_session"]}）' if level.get('touched_at') else ''
        state_word = level['state_word'] + (f'・{level["note"]}' if level.get('note') else '')
        icon = BEYOND_ICON.get(level['side'], '↗') if level.get('state') == 'beyond' else STATE_ICON.get(level.get('state'), '●')
        items.append(_liq('level', level['name'], fmt(value), value, pool='BSL' if level['side'] == 'high' else 'SSL',
                          state=level.get('state'), state_word=state_word, icon=icon,
                          touched=touched, distance_text=away(value),
                          proximity=min(abs(value - price) / move, 2.0) if move else None,
                          offscale=far(value), value=value))
        bindings.append({'figure': 'liquidity', 'label': level['name'], 'path': f'xau_levels.levels.{index}.price',
                         'value': number(value), 'digits': 2, 'unit': 'USD/oz', 'observedAt': lv.get('last_bar_utc'),
                         'state_path': f'xau_levels.levels.{index}.state', 'state': level.get('state')})
    # The distances are measured from this price; quote_state says when it is a reference only.
    observed, reference = quote_state(q, stamp)
    market = market_time(q)
    items.append(_liq('current', '現在値', fmt(price), price, state='current', state_word=observed, icon='▶', value=price))
    bindings.append({'figure': 'liquidity', 'label': '現在値', 'path': 'report_quote.close', 'value': number(price),
                     'digits': 2, 'unit': 'USD/oz', 'observedAt': market.isoformat() if market else '',  # unknown: not the fetch
                     'fetchedAt': str(q.get('fetched_at') or '')})  # a fetch record only, not the quote's date
    mo = num(lv.get('midnight_open'))
    if mo is not None and not far(mo):
        items.append(_liq('reference', 'NY 0時の始値', fmt(mo), mo, state='reference', state_word='当日の始値（参考）',
                          icon='┄', distance_text=away(mo), value=mo))
        bindings.append({'figure': 'liquidity', 'label': 'NY 0時の始値', 'path': 'xau_levels.midnight_open',
                         'value': number(mo), 'digits': 2, 'unit': 'USD/oz', 'observedAt': lv.get('last_bar_utc')})
    r = (data.get('retail_sentiment') or {}).get('XAUUSD') or {}
    entries = [(key, label, num(r.get(key))) for key, label in
               (('avg_long_entry', '個人ロングの平均建値'), ('avg_short_entry', '個人ショートの平均建値'))] if present(r) else []
    entries = [x for x in entries if x[2] is not None]
    retail_late = stale_time(r.get('timestamp'), stamp) if entries else None  # the bias row's rule: 24 hours from its fetch
    if retail_late:
        notes.append(f'流動性マップ: 個人の平均建値を出していない（{retail_late}）')
    for key, label, v in [] if retail_late else entries:
        aux.append(_liq('retail', label, fmt(v), v, state='retail',
                        state_word=f'{r.get("source") or "提供元不明"}・{_stamp(r.get("timestamp"))}',
                        icon='◆', distance_text=away(v), value=v))
        bindings.append({'figure': 'liquidity', 'label': label, 'path': f'retail_sentiment.XAUUSD.{key}', 'value': number(v),
                         'digits': 2, 'unit': 'USD/oz', 'observedAt': str(r.get('timestamp') or '')})
    draw = _draw(source)
    if draw:
        # The draw must be one of the listed levels (same name and price) on its side of the price;
        # the parent stage validates this, so a mismatch here is an error, never a silent omission.
        match = next((i for i in items if i['kind'] == 'level' and i['label'] == draw['level']
                      and abs(i['position'] - draw['price']) < 0.005), None)
        if match is None:
            raise ValueError('The draw is not one of the listed levels')
        if (draw['direction'] == 'up') != (match['position'] > price):
            raise ValueError('The draw is on the wrong side of the price')
        target = ('◎ 目的地の仮説' + (f'（{_window_text(draw["window"])}）' if draw.get('window') else '')
                  + ('・他市場の裏付けなし' if draw.get('unsupported') else ''))
        match['target'] = target
        match['source_quote'] += f'｜{target}'
    items.sort(key=lambda i: (-i['position'], {'current': 1}.get(i['kind'], 0)))
    cautions = [{'text': text, 'source_quote': f'流動性｜注意: {text}'} for text in lv.get('cautions') or []]
    rows = [c['source_quote'] for c in cautions] + [i['source_quote'] for i in items + aux]
    band = None
    if move:
        band = {'lower': number(price - move), 'upper': number(price + move),
                'label': f'想定変動 ±1σ（{"1週" if weekly else "1日"}・±{move:,.1f}ドル）'}
        rows.append(f'流動性｜{band["label"]}: {fmt(band["lower"])}〜{fmt(band["upper"])}'
                    f'（GVZ {liq.get("gvz")}・{liq.get("gvz_as_of")}）')
    last_bar = _bar_span(lv.get('last_bar_utc'))
    legend = ('名前付きの高値・安値を価格順に並べています。高値の上（BSL）と安値の下（SSL）は損切りが置かれやすいとされる流動性の候補で、'
              '注文そのものは観測していません。●未到達（確定足で一度も抜けていない）、✓掃引済み（一度抜けて、最新の終値は内側）、'
              '↗↘抜けて推移（最新の終値が外側で6時間未満。↗は高値の上、↘は安値の下）、×抜けて定着（6時間以上外側で終値）、◌形成中。'
              '時刻は最初に抜けた足です。形成期間や形成後に足が欠けた水準は、状態の後に欠測時間を付け、冒頭の直近候補から外します。'
              '判定は確定した1時間足（発表15分足の直後はその時間の残りを15分足）、日足の区切りはNY 17:00。'
              'セッションは各市場の現地時間で、アジア 東京9〜16時・London ロンドン8〜13時・NY ニューヨーク8〜13時'
              '（JSTでは夏時間 16〜21時・21〜翌2時、冬時間 17〜22時・22〜翌3時）。'
              f'表示範囲は現在値±{span:g}%で、外側は下に一覧。帯は想定変動±1σ（GVZから換算、到達予測ではない）。'
              '距離の棒は現在値からの遠さで、中央の目盛りが想定変動1σ、右端が2σ以上です。'
              + (f'計算に使った最終確定足は{last_bar}（Twelve Data 1時間足）。' if last_bar else '') +
              ('発表足の高値・安値は直近7日のTier A指標の発表15分足（なければ発表時刻を含む1時間足）です。' if weekly else
               '発表足の高値・安値は米重要指標（Tier A・B）の発表15分足（なければ発表時刻を含む1時間足）で、'
               '翌取引日の終わりまで表示します。') +
              'NY 0時の始値は当日の参考値で、判定には使いません。'
              '◆個人の平均建値（参考）は個人口座の建玉の平均で、流動性の位置ではありません。'
              f'平均建値は取得から{QUOTE_MAX_HOURS}時間以内、帯のGVZは観測日が{GVZ_MAX_AGE_DAYS}日以内のときだけ出し、'
              '古い・欠けているときは理由を制約に書きます。'
              'Order Block・FVGはチャートで判断する領域のため載せていません。')
    rows.append('流動性マップの読み方: ' + legend)
    figure = {'id': 'liquidity', 'type': 'liquidity_map', 'kicker': '意識する価格',
              'title': '流動性の位置（Liquidity）',
              'subtitle': (f'上ほど高い価格。状態は{last_bar}の確定1時間足まで。' if last_bar else '上ほど高い価格。')
                          + f'●はまだ抜けていない水準、帯は{"1週" if weekly else "1日"}の想定変動'
                          + ('。距離は参考価格（現在値の行）から' if reference else ''),
              'items': items, 'aux': aux, 'cautions': cautions, 'band': band, 'legend': legend,
              'sources': [{'label': 'Twelve Data', 'url': 'https://twelvedata.com/'},
                          {'label': 'FRED GVZCLS', 'url': 'https://fred.stlouisfed.org/series/GVZCLS'},
                          {'label': 'MyFXBook', 'url': 'https://www.myfxbook.com/community/outlook/XAUUSD'}]}
    return {'figure': figure, 'rows': rows, 'bindings': bindings, 'notes': notes}


def nearest_chip(data, stamp=None):
    """「直近の未到達: 上 … ／下 …」 from the levels and the quote; None without levels. The distances say
    when they are measured from a reference price (quote_state)."""
    from scrapers.xau_levels import nearest_resting
    lv = data.get('xau_levels') or {}
    q = data.get('report_quote') or data.get('_raw_quote_XAUUSD') or {}
    price = _float(q.get('close')) or num(lv.get('price'))
    near = nearest_resting(lv, price)
    if not near:
        return None
    parts = [f'{"上" if side == "high" else "下"} {near[side]["name"]} {fmt(near[side]["price"])}'
             f'（{number(near[side]["price"] - price, 2):+,.2f}）' for side in ('high', 'low') if side in near]
    reference = stamp is not None and q.get('close') is not None and quote_state(q, stamp)[1]
    return '直近の未到達: ' + '／'.join(parts) + ('・距離は参考価格から' if reference else '')


# ------------------------------------------------------------------ US event table

def _killzone(when):
    for name, zone, start, end in KILLZONES:
        if start <= when.astimezone(zone).hour < end:
            return name
    return 'KZ外'


def _stop_span(ev, when, blackouts):
    """(start, end) of the stop window: the recorded blackout, else 30 minutes before to 60 minutes
    after the release (120 for FOMC decisions, press conferences and the Fed chair)."""
    found = blackouts.get((_clean(ev.get('indicator')), when.isoformat()))
    if found:
        start, end = _parse_jst(found.get('window_start')), _parse_jst(found.get('window_end'))
        if start and end and start < end:
            return start, end
    words = ('interest rate decision', 'press conference', 'fomc', 'fed chair')
    after = 120 if any(w in str(ev.get('indicator', '')).lower() for w in words) else 60
    return when - timedelta(minutes=30), when + timedelta(minutes=after)


def _clock(t, day):
    """'22:30' on the release date; 前日 / 翌 mark the other days."""
    return ('前日' if t.date() < day else '翌' if t.date() > day else '') + t.strftime('%H:%M')


def release_rows(data, stamp, mode):
    """(when, tier, path, event) inside the tier windows: the calendar's rows plus released records
    (macro_surprises) the calendar no longer lists, in time order."""
    calendar = data.get('economic_calendar') or {}
    sources = [(f'economic_calendar.events.{j}', ev) for j, ev in enumerate(calendar.get('events') or [])]
    sources += [(f'macro_surprises.{k}', ev) for k, ev in enumerate(data.get('macro_surprises') or [])]
    out, seen = [], set()
    for path, ev in sources:
        level, when = tier(ev), event_datetime_jst(ev)
        if level is None or when is None or (_clean(ev.get('indicator')), when) in seen:
            continue
        start, end = event_window(stamp, mode, level)
        if start <= when < end:
            seen.add((_clean(ev.get('indicator')), when))
            out.append((when, level, path, ev))
    return sorted(out, key=lambda x: (x[0], x[1]))


def release_groups(data, stamp, mode):
    """{when: {'tier', 'names', 'span'}} per release time: the stop window is the union of the
    windows of the releases at that time."""
    blackouts = {(_clean(b.get('event')), str(b.get('time_jst', ''))): b
                 for b in (data.get('offchart_features') or {}).get('event_blackouts') or []}
    groups = OrderedDict()
    for when, level, _, ev in release_rows(data, stamp, mode):
        start, end = _stop_span(ev, when, blackouts)
        g = groups.setdefault(when, {'tier': level, 'names': [], 'span': (start, end)})
        g['tier'] = min(g['tier'], level)
        g['span'] = (min(g['span'][0], start), max(g['span'][1], end))
        name = display_name(ev.get('indicator')).split('（')[0]
        if name not in g['names']:
            g['names'].append(name)
    return groups


def _candle_for(levels, when):
    """The release-candle high/low from xau_levels for the release at `when` (JST)."""
    stamp = when.strftime('%m/%d %H:%M')
    found = {}
    for lv in (levels or {}).get('levels') or []:
        if 'NEWS_' in lv.get('key', '') and f'（{stamp}）' in lv['name']:
            found[lv['side']] = lv
    return found


def event_period(stamp, mode):
    """The table's period in words, with dates (the selection uses the same whole JST days)."""
    return window_label(stamp, mode)


def calendar_shortfall(calendar):
    """None when the calendar fetch covered the whole window; else ('failed' | 'incomplete' | 'unknown', line
    for the reader). An error, an empty list or no record at all is a failed fetch (never "no event"); a week tab
    the collector could not find leaves part of the window unchecked; events without the per-tab counts
    (tab_counts, not recorded by older collectors) cannot show that every tab was read."""
    calendar = calendar if isinstance(calendar, dict) else {}
    # Records restored from the archive (from_history) say nothing about what the page listed.
    events = [ev for ev in calendar.get('events') or [] if not (isinstance(ev, dict) and ev.get('from_history'))]
    tabs = [str(t) for t in calendar.get('missing_tabs') or []]
    # A week tab that was opened but gave no ★★★ row (all countries, a whole week) was not read.
    tabs += [str(t) for t, n in (calendar.get('tab_counts') or {}).items() if n == 0 and str(t) not in tabs]
    why = None
    if calendar.get('error'):
        why = _clean(calendar['error'])[:80]
    elif not events:
        why = '予定が0件' + (f'・週タブ {"・".join(tabs)} が見つからない' if tabs else '') if calendar else '取得の記録なし'
    if why:
        return 'failed', f'カレンダー取得失敗のため予定を確認できない（Investing.com: {why}）'
    if tabs:
        return 'incomplete', f'カレンダー取得が不完全で、一部の予定を確認できない（Investing.com: 週タブ {"・".join(tabs)} を読めない）'
    if not calendar.get('tab_counts'):
        return 'unknown', 'カレンダーの取得範囲を確認できず、予定の抜けがないか分からない（Investing.com: 週タブごとの件数の記録なし）'
    return None


def event_figure(data, stamp, mode):
    calendar = data.get('economic_calendar')
    calendar = calendar if isinstance(calendar, dict) else {}
    rows_in = release_rows(data, stamp, mode)
    if not calendar and not rows_in:
        return None
    shortfall = calendar_shortfall(calendar)
    weekly = mode == 'weekly'
    surprises = {(_clean(e.get('indicator')), str(e.get('released_at_jst', ''))): (k, e)
                 for k, e in enumerate(data.get('macro_surprises') or [])}
    levels = data.get('xau_levels')
    # The calendar record itself, so a calendar with an error adopts nothing here either (as on the board).
    nowcasts = {id(ev): gap for _, ev, gap in adopted_nowcasts(calendar, data.get('inflation_nowcast'), stamp, mode)}
    groups = release_groups(data, stamp, mode)
    items, rows, bindings, notes, last_time = [], [], [], [], None
    period = event_period(stamp, mode)
    clean = lambda obj, key: '—' if obj.get(key) in (None, '', 'N/A') else str(obj.get(key))
    for when, level, path, ev in rows_in:
        label = display_name(ev.get('indicator'))
        released = when <= stamp
        previous, forecast, actual = clean(ev, 'previous'), clean(ev, 'forecast'), clean(ev, 'actual')
        memo, first = [], when != last_time
        last_time = when
        if first:
            # One stop window per release time, shown on its first row while it has not ended.
            start, end = groups[when]['span']
            if start <= stamp < end:
                memo.append(f'停止中（{_clock(start, when.date())}〜{_clock(end, when.date())}）')
            elif stamp < start:
                memo.append(f'停止 {_clock(start, when.date())}〜{_clock(end, when.date())}・{_killzone(when)}')
        if released:
            k, s = surprises.get((_clean(ev.get('indicator')), when.isoformat()), (None, None))
            if s:
                previous, forecast, actual = clean(s, 'previous'), clean(s, 'forecast'), clean(s, 'actual')
                path = f'macro_surprises.{k}'
            record = s or ev
            surprise = compute_surprise(record) if actual != '—' else None
            if actual == '—' and is_statement(ev):
                # A statement, a speech or minutes: no number, and nothing here confirms what was said.
                diff, symbol, word, tone = '予定時刻を経過・実施と内容は未確認（声明・発言など）', '—', '内容未確認', 'neutral'
            elif actual == '—':
                diff, symbol, word, tone = '結果を未取得（発表時刻は経過）', '—', '結果未取得', 'neutral'
            elif surprise:
                sign = gold_sign(record)
                provenance = '' if s and str(s.get('forecast_provenance', '')).startswith('発表前記録') else '・予想は発表後の取得'
                diff = f'結果−予想 {surprise["diff"]:+g}{surprise["unit"]}{provenance}'
                if sign == 0:
                    symbol, word, tone = '—', '規則なし', 'neutral'
                elif surprise['diff'] == 0:
                    symbol, word, tone = '—', '予想どおり', 'neutral'
                else:
                    side = 'up' if surprise['diff'] * sign > 0 else 'down'
                    # One series is weak evidence; the vote of all series at this time is the board's 指標の結果 row.
                    symbol, word, tone = mark(side, 'weak'), f'{WORD[side]}（弱）', TONE[side]
            else:
                diff, symbol, word, tone = '差は計算不可（予想なし）', '—', '判定なし', 'neutral'
            candle = _candle_for(levels, when)
            if candle.get('high') and candle.get('low'):
                frame = '発表15分足' if '15分足' in candle['high']['name'] else '発表時刻を含む1時間足'
                memo.append(f'{frame} 高 {fmt(candle["high"]["price"])}（{candle["high"]["state_word"]}）'
                            f'／安 {fmt(candle["low"]["price"])}（{candle["low"]["state_word"]}）')
        else:
            gap = nowcasts.get(id(ev))
            if gap:
                # The same digits as the board row: the shown difference is that of the shown values.
                shown_now, _, shown_diff = gap_display(gap)
                side = 'flat' if abs(gap['gap']) < GAP_NEUTRAL_PP else ('up' if gap['gap'] < 0 else 'down')
                diff = f'nowcast {shown_now}%（予想との差 {shown_diff}pp）'
                symbol, word, tone = mark(side, 'weak'), ('中立（予想並み）' if side == 'flat' else f'{WORD[side]}（弱・予測）'), TONE[side]
            else:
                diff, symbol, word, tone = '発表前', '—', '発表待ち', 'neutral'
        memo = '・'.join(memo)
        date_text, time_text = when.strftime('%m/%d'), when.strftime('%H:%M')
        weekday = '月火水木金土日'[when.weekday()]
        quote = (f'米指標｜{date_text}（{weekday}） {time_text} JST｜Tier {level}｜{label}｜{"発表済み" if released else "予定"}'
                 f'｜前回 {previous}｜予想 {forecast}｜結果 {actual}｜{diff}｜向き: {symbol} {word}' + (f'｜{memo}' if memo else ''))
        rows.append(quote)
        bindings.append({'figure': 'calendar', 'label': label, 'index': len(items), 'path': path})
        items.append({'date': f'{date_text}（{weekday}）', 'time': time_text, 'tier': level, 'label': label,
                      'status': '発表済み' if released else '予定', 'previous': previous, 'forecast': forecast,
                      'actual': actual, 'diff': diff, 'direction': word, 'symbol': symbol, 'tone': tone, 'memo': memo,
                      'released': released, 'group_start': first, 'source_quote': quote})
    cautions = []
    if shortfall:
        text = shortfall[1]
        cautions.append({'text': text, 'source_quote': f'米指標｜注意: {text}'})
        rows.append(cautions[0]['source_quote'])
        if shortfall[0] != 'incomplete':
            notes.append(text)  # also among the limitations; the missing-tab note below covers a partial fetch
    # The page's week tabs start on the cutoff week's Monday; earlier days come only from the archive.
    start = results_start(stamp, mode)
    monday = datetime.combine(stamp.astimezone(JST).date() - timedelta(days=stamp.astimezone(JST).weekday()),
                              datetime.min.time(), JST)
    chips = []
    if start < monday:
        chips.append(f'米指標の結果: {start:%m/%d}〜{monday - timedelta(days=1):%m/%d}は記録からの復元のみ')
        text = (f'{start:%m/%d}〜{monday - timedelta(days=1):%m/%d}はカレンダーの週タブの外で、表には発表済みの記録から復元した分だけを'
                '載せている（声明・議事要旨など数値のない発表は漏れることがある）')
        cautions.append({'text': text, 'source_quote': f'米指標｜注意: {text}'})
        rows.append(cautions[-1]['source_quote'])
        notes.append(text)
    if not items:
        # Only a complete fetch can say 「該当なし」; any other cannot confirm that nothing is scheduled.
        unread = {'failed': '取得失敗', 'incomplete': '取得が不完全', 'unknown': '取得範囲が未確認'}
        label = f'予定を確認できない（{unread[shortfall[0]]}）' if shortfall else '該当なし（Tier A/Bの米指標なし）'
        quote = f'米指標｜{period}｜{label}'
        rows.append(quote)
        items.append({'date': period, 'time': '', 'tier': '', 'label': label, 'status': '',
                      'previous': '', 'forecast': '', 'actual': '', 'diff': '', 'direction': '', 'symbol': '',
                      'tone': 'neutral', 'memo': '', 'released': False, 'empty': True, 'source_quote': quote})
    if calendar.get('missing_tabs'):
        notes.append(f'米指標: Investing.comの週タブ（{"・".join(calendar["missing_tabs"])}）が見つからず、その週の予定は表に入っていない')
    legend = ('米国の指標だけを2段階で選んでいます。Tier A＝FOMC・FRB議長・雇用統計・CPI、Tier B＝小売・GDP・PCE・PPI・ISM・耐久財・'
              'FOMC議事要旨・ミシガン大期待インフレ・新規失業保険申請・ADP・JOLTS。住宅・国債入札・在庫・地区連銀・S&P PMIなどは載せません。'
              '予定はInvesting.comの重要度★3の行だけから取るため、提供元が★2以下としたTier A/Bの発表は載りません。'
              f'期間は{period}（JSTの日付単位）、{tier_scope("weekly" if weekly else "daily")}。カレンダーから消えた発表済みの指標も、記録した結果があれば載せます'
              '（カレンダーの取得に失敗したときも）。取得が失敗・不完全なとき、取得範囲を確認できないときは「該当なし」とせず、予定を確認できないと示します。'
              '発表済みの向きは系列ごとの「結果−予想」に米指標の一般則（予想より強い＝ドル・金利高で金の逆風）を当てたもの（△▽）。'
              '同時刻の系列をまとめた票はバイアス盤の「指標の結果」行（2系列以上がそろえば強）。'
              '予定の向きはCleveland連銀のnowcast（CPI・PCEの前月比）と市場予想の差で、発表前の参考（弱）です。'
              f'nowcastが{NOWCAST_MAX_AGE_DAYS}日より古いときは付けません。予想と前回の差は織り込み済みのため向きに使いません。'
              + ('発表足の高値・安値は直近7日のTier Aだけを流動性の一覧に残します。' if weekly else
                 '発表足の高値・安値は発表15分足（なければ発表時刻を含む1時間足）で、翌取引日の終わりまで流動性の一覧に残します。')
              + '停止は発表30分前〜60分後（FOMC・議長は120分後）。同時刻の発表は窓を合わせて最初の行に1回だけ示し、'
              '発表後も窓が終わるまでは「停止中」と表示します。'
              'KZは各市場の現地時間で、London 英国8〜11時（JST 夏16〜19時・冬17〜20時）・NY 米東部8〜12時（JST 夏21〜翌1時・冬22〜翌2時）。')
    rows.append('米指標表の読み方: ' + legend)
    figure = {'id': 'calendar', 'type': 'event_table', 'kicker': '気をつける指標',
              'title': '米国の重要指標' + (f'（{period}）' if weekly else '（直近の結果とこの先の予定）'),
              'subtitle': f'時刻はJST。{tier_scope("weekly" if weekly else "daily")}（Investing.comの重要度★3のみ）', 'items': items, 'cautions': cautions, 'legend': legend,
              'sources': [{'label': 'Investing.com', 'url': 'https://www.investing.com/economic-calendar/'},
                          {'label': 'Cleveland Fed nowcast', 'url': 'https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting'}]}
    return {'figure': figure, 'rows': rows, 'bindings': bindings, 'notes': notes, 'chips': chips,
            'coverage': shortfall[0] if shortfall else 'complete'}


def lead_chips(data, stamp, mode):
    """Chips for the top of the page: a stop window in force now and the next US release
    (Tier A or B, inside the table's window). Releases at the same time share one chip."""
    chips = []
    for when, g in release_groups(data, stamp, mode).items():
        names = '・'.join(sorted(g['names'], key=lambda name: name.startswith('コア')))  # headline before core
        start, end = g['span']
        day = f'{when.strftime("%m/%d")}（{"月火水木金土日"[when.weekday()]}）'
        if start <= stamp < end:
            chips.append(f'停止中: {names}（{day}{when.strftime("%H:%M")} JST 発表）〜{_clock(end, stamp.date())}')
        elif when > stamp:
            chips.append(f'次の米指標: {names}（Tier {g["tier"]}） {day}{when.strftime("%H:%M")} JST')
            break
    return chips
