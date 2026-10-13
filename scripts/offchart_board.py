"""Deterministic off-chart bias board and economic-event table for the report figures.

Every row compares one observation with its stated baseline and maps the change to a
gold direction with the general reaction rules the pipeline already uses
(scrapers.macro_surprise.US_GOLD_SIGN, the master-prompt thresholds). The rules are
presentation rules written into each row; they are not trading rules, and the
verdict tiles only count the rows shown. No collection, LLM, Brain or network here.
"""
from __future__ import annotations

from datetime import timedelta
import math
import re
from zoneinfo import ZoneInfo

from scrapers.macro_surprise import compute_surprise, event_datetime_jst, gold_sign, parse_value
from scripts.report_bundle import number

JST = ZoneInfo('Asia/Tokyo')
COUNTRY = {'United States': '米国', 'Euro Zone': 'ユーロ圏', 'Germany': '独', 'United Kingdom': '英',
           'Japan': '日本', 'China': '中国', 'Australia': '豪', 'Canada': '加', 'Switzerland': 'スイス',
           'New Zealand': 'NZ', 'France': '仏', 'Italy': '伊', 'Spain': '西'}
LONG_WINDOW_WORDS = ('interest rate decision', 'press conference', 'fomc', 'monetary policy statement')
SYMBOL = {('up', 'strong'): '▲', ('up', 'weak'): '△', ('down', 'strong'): '▼', ('down', 'weak'): '▽',
          ('flat', 'none'): '—', ('unknown', 'none'): '？'}
WORD = {'up': '上向き', 'down': '下向き', 'flat': '中立', 'unknown': '判定保留'}
TONE = {'up': 'positive', 'down': 'negative', 'flat': 'neutral', 'unknown': 'caution'}
LONG_GROUP = '長期（構造: 実質金利・ドル・政策織り込み・フロー）'
SHORT_GROUP = {'daily': '短期（当日の材料と今後24時間）', 'weekly': '今週の材料と来週の予定'}
TALLY_RULE = '強い向き（▲▼）1票・弱い向き（△▽）0.5票で比べ、差が1票未満なら中立、2票以上で強い向き'


def stamp_text(value):
    """Readable JST time for a recorded ISO timestamp; other strings pass through."""
    try:
        from datetime import datetime
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return str(value or '時点未取得')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.astimezone(JST).strftime('%Y-%m-%d %H:%M') + ' JST'


def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def usable(value):
    return isinstance(value, dict) and not value.get('error') and value.get('stale') is not True


def fmt(value, digits=2, sign=False, suffix=''):
    value = number(value, digits)
    return f'{value:+,.{digits}f}{suffix}' if sign else f'{value:,.{digits}f}{suffix}'


def judge(change, threshold, sign):
    """Map a change to (side, strength). sign=+1: a rise is gold-positive; -1: a rise is gold-negative."""
    if change is None:
        return 'unknown', 'none'
    if change == 0:
        return 'flat', 'none'
    side = 'up' if change * sign > 0 else 'down'
    return side, ('strong' if abs(change) >= threshold else 'weak')


def mark(side, strength):
    if side in ('flat', 'unknown'):
        strength = 'none'
    return SYMBOL[(side, strength)]


def row(group, label, display, baseline, change, side, strength, asof, rule, *, word=None,
        value=None, path=None, digits=2, unit=''):
    if side in ('flat', 'unknown'):
        strength = 'none'
    word = word or WORD[side]
    symbol = mark(side, strength)
    quote = (f'{group}｜{label}: {display}（{asof}）｜基準: {baseline}｜変化: {change}'
             f'｜向き: {symbol} {word}｜規則: {rule}')
    item = {'group': group, 'label': label, 'display': display, 'baseline': baseline, 'change': change,
            'direction': word, 'symbol': symbol, 'side': side, 'strength': strength,
            'tone': TONE[side], 'asof': asof, 'rule': rule, 'source_quote': quote}
    binding = None
    if value is not None and path:
        item['value'] = number(value, digits)
        binding = {'path': path, 'value': item['value'], 'digits': digits, 'unit': unit, 'observedAt': asof}
    return item, binding


def _series(fred, key):
    s = (fred or {}).get(key)
    if not isinstance(s, dict) or num(s.get('value')) is None or s.get('error'):
        return None
    return s


def _asof(series):
    return f"{series.get('as_of_date') or '時点未取得'}{'・古い値' if series.get('stale') else ''}"


def _lowest_range(fedwatch):
    rates = fedwatch.get('target_rates') or []
    parsed = []
    for index, rate in enumerate(rates):
        try:
            parsed.append((float(str(rate.get('range')).split('-')[0]), index, rate))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return None, None
    _, index, rate = min(parsed)
    return index, rate


def _fedwatch_row(group, data, key, threshold, label, rule_window):
    fw = data.get('fedwatch') or {}
    if not usable(fw):
        return None
    index, rate = _lowest_range(fw)
    if rate is None or num(rate.get('current')) is None:
        return None
    delta = (fw.get('deltas') or {}).get(key) or {}
    change = num((delta.get('by_range') or {}).get(rate.get('range')))
    current = number(rate['current'], 1)
    if change is None:
        baseline, change_text = f'{rule_window}は取得不可', '—'
    else:
        change = number(change, 1)
        baseline = f'{rule_window} {current - change:.1f}%（{delta.get("source") or "出所未記載"}）'
        change_text = f'{change:+.1f}pp'
    side, strength = judge(change, threshold, +1)
    return row(group, label, f'{rate["range"]}: {current:.1f}%', baseline, change_text, side, strength,
               f'次回FOMC {fw.get("next_fomc_date") or "未取得"}',
               f'低い方の金利レンジの確率上昇は緩和方向の織り込みで金に追い風。{rule_window}±{threshold:.0f}pp未満は弱',
               value=rate['current'], path=f'fedwatch.target_rates.{index}.current', digits=1, unit='%')


def bias_rows(data, stamp, mode):
    """Rows for the board. Returns (items, bindings, limitations)."""
    items, bindings, limitations = [], [], []
    weekly = mode == 'weekly'
    short = SHORT_GROUP['weekly' if weekly else 'daily']
    fred = data.get('fred') or {}

    def keep(result):
        if result:
            item, binding = result
            items.append(item)
            if binding:
                bindings.append({'figure': 'bias', 'label': item['label'], **binding})

    s = _series(fred, 'DFII10')
    if s and num(s.get('change_20obs')) is not None and num(s.get('value_20obs_ago')) is not None:
        change = number(s['change_20obs'], 2)
        side, strength = judge(change, 0.10, -1)
        keep(row(LONG_GROUP, '実質金利（米10年TIPS）', f'{fmt(s["value"])}%',
                 f'20営業日前 {fmt(s["value_20obs_ago"])}%（{s.get("as_of_20obs_ago") or "日付未取得"}）',
                 f'{change:+.2f}pp', side, strength, _asof(s), '上昇は金の逆風、低下は追い風。±0.10pp未満は弱',
                 value=s['change_20obs'], path='fred.DFII10.change_20obs', unit='pp'))
    s = _series(fred, 'DTWEXBGS')
    if s and num(s.get('change_20obs')) is not None and num(s.get('value_20obs_ago')):
        change = number(s['change_20obs'], 2)
        pct = number(change / s['value_20obs_ago'] * 100, 2)
        side, strength = judge(pct, 1.0, -1)
        keep(row(LONG_GROUP, 'ドル（FRB広義ドル指数）', fmt(s['value']),
                 f'20営業日前 {fmt(s["value_20obs_ago"])}（{s.get("as_of_20obs_ago") or "日付未取得"}）',
                 f'{change:+.2f}（{pct:+.2f}%）', side, strength, _asof(s),
                 'ドル高は金の逆風、ドル安は追い風。20営業日で±1.0%未満は弱',
                 value=s['change_20obs'], path='fred.DTWEXBGS.change_20obs', unit='index'))
    keep(_fedwatch_row(LONG_GROUP, data, 'prev_week', 10.0, '政策金利の織り込み（前週比）', '前週'))
    etf = data.get('gold_etf')
    if usable(etf) and num(etf.get('change_20d_t')) is not None:
        change = number(etf['change_20d_t'], 2)
        tonnes = num(etf.get('tonnes'))
        display = f'{fmt(tonnes)} t' if tonnes is not None else '保有量は未取得'
        baseline = f'20営業日前 {fmt(tonnes - change)} t' if tonnes is not None else '20営業日前の保有量'
        side, strength = judge(change, 5.0, +1)
        keep(row(LONG_GROUP, 'GLD保有量（20営業日累計）', display, baseline, f'{change:+.2f} t', side, strength,
                 str(etf.get('as_of_date') or '時点未取得'), '保有増は機関の買い、減は売り。20営業日で±5t未満は弱',
                 value=etf['change_20d_t'], path='gold_etf.change_20d_t', unit='t'))
    cb = data.get('gold_cb')
    if usable(cb) and num(cb.get('cumulative_3m_t')) is not None:
        change = number(cb['cumulative_3m_t'], 1)
        regime = {'net_buying': '買い越し', 'net_selling': '売り越し', 'neutral': '中立'}.get(cb.get('regime'), str(cb.get('regime') or '判定なし'))
        side, strength = judge(change, 20.0, +1)
        keep(row(LONG_GROUP, '中銀の金購入（IMF報告国・確定3ヶ月累計）', f'{change:+.1f} t', 'ゼロ（買い越しと売り越しの境）',
                 f'レジーム {regime}', side, strength, str(cb.get('as_of_date') or '時点未取得'),
                 '純購入は構造的な下支え、純売却は逆風。月次で1〜2ヶ月遅れ。±20t未満は弱',
                 value=cb['cumulative_3m_t'], path='gold_cb.cumulative_3m_t', digits=1, unit='t'))

    if not weekly:
        s = _series(fred, 'DGS2')
        if s and num(s.get('change')) is not None and num(s.get('prev_value')) is not None:
            change = number(s['change'], 2)
            side, strength = judge(change, 0.05, -1)
            keep(row(short, '米2年債利回り（前営業日比）', f'{fmt(s["value"])}%',
                     f'前営業日 {fmt(s["prev_value"])}%（{s.get("prev_as_of_date") or "日付未取得"}）', f'{change:+.2f}pp',
                     side, strength, _asof(s), '上昇は金の逆風。FREDは1〜2営業日遅れ。±0.05pp未満は弱',
                     value=s['change'], path='fred.DGS2.change', unit='pp'))
        dxy = data.get('dxy')
        if usable(dxy) and num(dxy.get('change_pct')) is not None and num(dxy.get('current_price')) is not None:
            pct = number(dxy['change_pct'], 2)
            side, strength = judge(pct, 0.3, -1)
            change = f'{fmt(dxy["change"], sign=True)}（{pct:+.2f}%）' if num(dxy.get('change')) is not None else f'{pct:+.2f}%'
            keep(row(short, 'ドル指数DXY（前日比）', fmt(dxy['current_price']),
                     f'前日終値 {fmt(dxy["prev_close"])}' if num(dxy.get('prev_close')) is not None else '前日終値は未取得',
                     change, side, strength, f'記録 {stamp_text(dxy.get("timestamp"))}',
                     'ドル高は金の逆風、ドル安は追い風。±0.3%未満は弱',
                     value=dxy['change_pct'], path='dxy.change_pct', unit='%'))
        keep(_fedwatch_row(short, data, 'prev_day', 5.0, '政策金利の織り込み（前日比）', '前日'))

    hours = int(data.get('macro_surprise_hours') or (7 * 24 if weekly else 36))
    span = f'直近{hours // 24}日' if hours % 24 == 0 and hours >= 48 else f'直近{hours}時間'
    surprises = [e for e in data.get('macro_surprises') or []
                 if e.get('country') == 'United States' and isinstance(e.get('surprise'), dict)
                 and str(e.get('forecast_provenance', '')).startswith('発表前記録')]
    for ev in surprises[:4]:
        s = ev['surprise']
        sign, diff = gold_sign(ev), num(s.get('diff'))
        if diff is None:
            continue
        side, strength = ('flat', 'none') if diff == 0 or sign == 0 else ('up' if diff * sign > 0 else 'down', 'strong')
        word = {'flat': '予想どおり' if sign else '規則なし（この指標は対象外）'}.get(side)
        released = str(ev.get('released_at_jst') or '')[:16].replace('T', ' ')
        keep(row(short, f'指標サプライズ: {re.sub(r"\s+", " ", str(ev.get("indicator", "")))}', f'結果 {ev.get("actual")}',
                 f'予想 {ev.get("forecast")}（発表前記録）', f'結果−予想 {diff:+g}{s["unit"]}', side, strength,
                 f'{released} JST 発表', '米指標の一般則: 予想より強い結果はドル・金利高で金に逆風。実際の反応は別に確認', word=word))
    if not surprises and items:  # placeholder only when the board has real rows
        keep(row(short, f'指標サプライズ（発表前記録のある米指標・{span}）', '該当なし', '—', '—', 'flat', 'none',
                 f'収集 {stamp.strftime("%Y-%m-%d %H:%M")} JST', '該当がなければ票なし', word='材料なし'))

    horizon = timedelta(days=7) if weekly else timedelta(hours=24)
    upcoming = []
    for ev in (data.get('economic_calendar') or {}).get('events') or []:
        when = event_datetime_jst(ev)
        if when is None or ev.get('country') != 'United States' or not (stamp < when <= stamp + horizon):
            continue
        forecast, previous = parse_value(ev.get('forecast')), parse_value(ev.get('previous'))
        if forecast is None or previous is None or forecast[1] != previous[1]:
            continue
        upcoming.append((when, ev, round(forecast[0] - previous[0], 6), 'pp' if forecast[1] == '%' else forecast[1]))
    for when, ev, diff, unit in sorted(upcoming, key=lambda x: x[0])[:5 if weekly else 3]:
        sign = gold_sign(ev)
        side, strength = ('flat', 'none') if diff == 0 or sign == 0 else ('up' if diff * sign > 0 else 'down', 'weak')
        word = {'flat': '予想は前回並み' if sign else '規則なし（この指標は対象外）'}.get(side) or f'予想どおりなら{WORD[side]}'
        keep(row(short, f'予想 vs 前回: {re.sub(r"\s+", " ", str(ev.get("indicator", "")))}', f'予想 {ev.get("forecast")}',
                 f'前回 {ev.get("previous")}', f'予想−前回 {diff:+g}{unit}', side, strength,
                 f'{when.strftime("%Y-%m-%d %H:%M")} JST 発表予定',
                 '市場は予想を先に織り込む。予想が前回より強ければ金に逆風（米指標の一般則）。発表前の弱い票', word=word))
    if usable(etf) and num(etf.get('change_5d_t')) is not None:
        change = number(etf['change_5d_t'], 2)
        tonnes = num(etf.get('tonnes'))
        side, strength = judge(change, 2.0, +1)
        keep(row(short, 'GLD保有量（5営業日累計）', f'{fmt(tonnes)} t' if tonnes is not None else '保有量は未取得',
                 f'5営業日前 {fmt(tonnes - change)} t' if tonnes is not None else '5営業日前の保有量', f'{change:+.2f} t',
                 side, strength, str(etf.get('as_of_date') or '時点未取得'), '保有増は機関の買い、減は売り。5営業日で±2t未満は弱',
                 value=etf['change_5d_t'], path='gold_etf.change_5d_t', unit='t'))
    positioning = (data.get('offchart_features') or {}).get('positioning') or {}
    r = (data.get('retail_sentiment') or {}).get('XAUUSD')
    if usable(r) and num(r.get('long_pct')) is not None:
        p = (positioning.get('retail') or {}).get('XAUUSD') or {}
        pct, n = num(p.get('percentile')), p.get('history_n')
        asof = f'観測日 {r["as_of_date"]}' if r.get('as_of_date') else f'記録 {stamp_text(r.get("timestamp"))}'
        if pct is None:
            keep(row(short, '個人のロング比率', f'ロング {fmt(r["long_pct"], 1)}%（{r.get("source") or "提供元未確認"}）',
                     f'自前履歴{n if n is not None else "不明"}件・判定保留', '百分位なし', 'unknown', 'none', asof,
                     '百分位90以上・10以下だけ多数派と逆の票。履歴不足は票なし'))
            limitations.append('個人ロング比率の百分位は履歴不足で判定保留。偏りの有無は未判定です')
        else:
            pct = number(pct, 1)
            side = 'down' if pct >= 90 else 'up' if pct <= 10 else 'flat'
            word = None if side != 'flat' else '極端値なし'
            keep(row(short, '個人のロング比率', f'ロング {fmt(r["long_pct"], 1)}%（{r.get("source") or "提供元未確認"}）',
                     f'自前履歴{n}件の百分位 {pct:.1f}', '極端値（90以上・10以下）に該当' if side != 'flat' else '90以上・10以下に該当せず',
                     side, 'strong', asof, '百分位90以上は多数派ロングの逆で下向き、10以下は上向き。それ以外は票なし', word=word,
                     value=p.get('percentile'), path='offchart_features.positioning.retail.XAUUSD.percentile', digits=1, unit='百分位'))
    cot = data.get('cot_disaggregated')
    mm = ((cot or {}).get('data') or {}).get('managed_money') if usable(cot) else None
    if isinstance(mm, dict) and num(mm.get('net')) is not None:
        p = positioning.get('cot_gold_mm') or {}
        pct, n = num(p.get('percentile')), p.get('history_n')
        net = number(mm['net'], 0)
        change = num(mm.get('net_change'))
        oi = f'（OI比 {fmt(mm["net_pct_oi"], 1)}%）' if num(mm.get('net_pct_oi')) is not None else ''
        change_text = f'前週比 {change:+,.0f}枚' if change is not None else '前週比は未取得'
        baseline = f'前週 {net - change:,.0f}枚' if change is not None else '前週値は未取得'
        asof = f'観測 {cot["data"].get("date") or "日付未取得"}'
        rule = '投機筋の純ロング。百分位90以上は買いの混雑で下向きの警告、10以下は上向き。それ以外は票なし。週次で遅れる'
        if pct is None:
            keep(row(short, '投機筋（COT Managed Money 純ロング）', f'純ロング {net:,.0f}枚{oi}',
                     f'{baseline}／百分位は判定保留（履歴{n if n is not None else "不明"}週）', change_text, 'unknown', 'none', asof, rule))
        else:
            pct = number(pct, 1)
            side = 'down' if pct >= 90 else 'up' if pct <= 10 else 'flat'
            keep(row(short, '投機筋（COT Managed Money 純ロング）', f'純ロング {net:,.0f}枚{oi}',
                     f'{baseline}／{n}週の百分位 {pct:.1f}', change_text, side, 'strong', asof, rule,
                     word=None if side != 'flat' else '極端値なし',
                     value=p.get('percentile'), path='offchart_features.positioning.cot_gold_mm.percentile', digits=1, unit='百分位'))
    return items, bindings, limitations


def tally(items, group):
    counts = {'▲': 0, '△': 0, '▼': 0, '▽': 0}
    for item in items:
        if item['group'] == group and item['symbol'] in counts:
            counts[item['symbol']] += 1
    up = counts['▲'] + counts['△'] / 2
    down = counts['▼'] + counts['▽'] / 2
    diff = up - down
    if not any(counts.values()):
        side, strength, word = 'flat', 'none', '中立（材料なし）'
    elif abs(diff) < 1:
        side, strength, word = 'flat', 'none', '中立（拮抗）'
    else:
        side = 'up' if diff > 0 else 'down'
        strength = 'strong' if abs(diff) >= 2 else 'weak'
        word = WORD[side] + ('' if strength == 'strong' else '（弱）')
    detail = ' '.join(f'{k}{v}' for k, v in counts.items())
    return {'side': side, 'strength': strength, 'symbol': mark(side, strength), 'word': word, 'detail': detail,
            'tone': TONE[side]}


def source_verdict(source):
    """The report's own verdict for the third tile, quoted from a fixed-format line when present."""
    if not source:
        return None
    match = re.search(r'^(時間軸別の向き[:：].+)$', source, re.M)
    if match:
        line = match.group(1).strip()
        found = dict(re.findall(r'(長期|短期|総合)\s*[:：]?\s*([^｜|]+)', line.split('：', 1)[-1].split(':', 1)[-1]))
        overall = found.get('総合', '記載なし').strip()
        detail = '／'.join(f'{k} {found[k].strip()}' for k in ('長期', '短期') if k in found)
        return _verdict_tile(overall, detail, line)
    match = re.search(r'^(方向付与[:：].+)$', source, re.M)
    if match:
        line = match.group(1).strip()
        head = re.split(r'[｜|]', line)[0]
        text = re.split(r'[:：]', head, maxsplit=1)[-1].strip()
        inner = re.search(r'可\s*[（(]([^）)]*)[）)]', text)
        overall = inner.group(1) if inner else text.split('（')[0].split('(')[0].strip() or text
        return _verdict_tile(overall, head.strip(), line)
    match = re.search(r'^(XAUUSD\s*[:：]\s*[^\n]+)$', source, re.M)
    if match:
        line = match.group(1).strip()
        overall = re.split(r'[:：]', line, maxsplit=1)[-1].split('。')[0].strip()
        return _verdict_tile(overall, '', line)
    return None


def _verdict_tile(overall, detail, quote):
    side = 'up' if '上向き' in overall or 'Bullish' in overall else 'down' if '下向き' in overall or 'Bearish' in overall \
        else 'flat' if '中立' in overall or 'Neutral' in overall else 'unknown'
    return {'label': '原稿の判断（分析本文の結論）', 'direction': overall, 'symbol': mark(side, 'strong'),
            'tone': TONE[side], 'detail': detail, 'source_quote': quote}


def bias_figure(data, stamp, mode, source=None):
    items, bindings, limitations = bias_rows(data, stamp, mode)
    if not items:
        return None
    rows = []
    verdicts = []
    for group, label in ((LONG_GROUP, '長期の向き（規則集計）'), (SHORT_GROUP['weekly' if mode == 'weekly' else 'daily'], '短期の向き（規則集計）')):
        t = tally(items, group)
        quote = f'集計｜{group}: {t["symbol"]} {t["word"]}（{t["detail"]}）｜規則: {TALLY_RULE}'
        rows.append(quote)
        verdicts.append({'label': label, 'direction': t['word'], 'symbol': t['symbol'], 'tone': t['tone'],
                         'detail': t['detail'], 'source_quote': quote})
    own = source_verdict(source)
    verdicts.append(own or {'label': '原稿の判断（分析本文の結論）', 'direction': '記載なし', 'symbol': '？',
                            'tone': 'caution', 'detail': '原稿に固定形式の向きの行がありません',
                            'source_quote': rows[0]})
    figure = {'id': 'bias', 'type': 'bias_board', 'placement': 'band', 'kicker': '一目で見る向き',
              'title': 'チャート外バイアス盤（長期・短期）',
              'subtitle': f'向きは金（XAUUSD）に対する方向 / 収集: {stamp.strftime("%Y-%m-%d %H:%M")} JST（各行の時点は右端）',
              'caption': ('各行は「現在の値」を「基準」と比べ、その変化を金の一般則で向きに写したものです。▲▼は規則の閾値以上、△▽は閾値未満の弱い向き、'
                          '—は中立・票なし、？は判定保留。集計は表示した行だけを数え、確率や的中率ではありません。'
                          '比率（個人・COT）は極端値のときだけ逆方向の票で、通常は警告用です。原稿の判断は分析本文の結論で、規則集計と食い違うことがあります。'),
              'source_label': 'FRED / Investing.com / CME FedWatch / SPDR / IMF / CFTC / MyFXBook', 'source_url': 'https://fred.stlouisfed.org/',
              'verdicts': verdicts, 'items': items}
    return {'figure': figure, 'rows': [i['source_quote'] for i in items] + rows, 'bindings': bindings,
            'limitations': limitations}


def _killzone(when):
    hour = when.hour
    if 16 <= hour < 19:
        return 'London KZ'
    if hour >= 21 or hour < 1:
        return 'NY KZ'
    return 'KZ外'


def _stop_window(ev, when, blackouts):
    found = blackouts.get((str(ev.get('indicator', '')), when.isoformat()))
    if found and found.get('window_start') and found.get('window_end'):
        start, end = found['window_start'][11:16], found['window_end'][11:16]
    else:
        after = 120 if any(w in str(ev.get('indicator', '')).lower() for w in LONG_WINDOW_WORDS) else 60
        start = (when - timedelta(minutes=30)).strftime('%H:%M')
        end = (when + timedelta(minutes=after)).strftime('%H:%M')
    return f'停止 {start}〜{end}'


def _direction_from(gold_direction, country=None):
    fallback = '規則なし（この指標は対象外）' if country == 'United States' else '規則なし（米指標以外）'
    return {'金に上向き': ('▲', '上向き', 'positive'), '金に下向き': ('▼', '下向き', 'negative'),
            '予想どおり': ('—', '予想どおり', 'neutral')}.get(gold_direction, ('—', fallback, 'neutral'))


def event_figure(data, stamp, mode):
    calendar = data.get('economic_calendar')
    if not usable(calendar):
        return None
    weekly = mode == 'weekly'
    past = timedelta(days=7 if weekly else 2)
    surprises = {(str(e.get('indicator', '')), str(e.get('released_at_jst', ''))): e for e in data.get('macro_surprises') or []}
    blackouts = {(str(b.get('event', '')), str(b.get('time_jst', ''))): b
                 for b in (data.get('offchart_features') or {}).get('event_blackouts') or []}
    rows, items = [], []
    selected = []
    for ev in calendar.get('events') or []:
        when = event_datetime_jst(ev)
        if when is not None and stamp - past <= when <= stamp + timedelta(days=7):
            selected.append((when, ev))
    selected.sort(key=lambda pair: pair[0])
    if len(selected) > 18:
        released = [pair for pair in selected if pair[0] <= stamp]
        selected = released + [pair for pair in selected if pair[0] > stamp][:18 - len(released)]
    for when, ev in selected:
        clean = lambda key: '—' if ev.get(key) in (None, '', 'N/A') else str(ev.get(key))
        label = re.sub(r'\s+', ' ', str(ev.get('indicator', ''))).strip()
        country = COUNTRY.get(ev.get('country'), str(ev.get('country') or '国不明'))
        importance = {'high': '高', 'medium': '中', 'low': '低'}.get(ev.get('importance'), '高')
        released = when <= stamp
        previous, forecast, actual = clean('previous'), clean('forecast'), clean('actual')
        window = _killzone(when)
        if released:
            status = '発表済み'
            s = surprises.get((str(ev.get('indicator', '')), when.isoformat()))
            surprise = s.get('surprise') if s else (compute_surprise(ev) if actual != '—' else None)
            if actual == '—':
                actual, diff, (symbol, word, tone) = '未取得', '差: 結果未取得', ('—', '結果待ち', 'neutral')
            elif surprise:
                provenance = '発表前記録の予想' if s and str(s.get('forecast_provenance', '')).startswith('発表前記録') else '予想は発表後に取得'
                diff = f'結果−予想 {surprise["diff"]:+g}{surprise["unit"]}（{provenance}）'
                symbol, word, tone = _direction_from(surprise.get('gold_direction'), ev.get('country'))
            else:
                diff, (symbol, word, tone) = '差: 計算不可（予想欠測または単位不一致）', ('—', '判定なし', 'neutral')
        else:
            status = '予定'
            window = f'{window}・{_stop_window(ev, when, blackouts)}'
            f, p = parse_value(ev.get('forecast')), parse_value(ev.get('previous'))
            if f is not None and p is not None and f[1] == p[1]:
                d = round(f[0] - p[0], 6)
                diff = f'予想−前回 {d:+g}{"pp" if f[1] == "%" else f[1]}'
                sign = gold_sign(ev)
                if sign == 0:
                    symbol, word, tone = '—', ('規則なし（この指標は対象外）' if ev.get('country') == 'United States' else '規則なし（米指標以外）'), 'neutral'
                elif d == 0:
                    symbol, word, tone = '—', '予想は前回並み', 'neutral'
                else:
                    side = 'up' if d * sign > 0 else 'down'
                    symbol, word, tone = mark(side, 'weak'), f'予想どおりなら{WORD[side]}', TONE[side]
            else:
                diff = '差: 予想なし' if f is None else '差: 前回なし'
                symbol, word, tone = '—', '判定なし', 'neutral'
        date, time = when.strftime('%Y-%m-%d'), when.strftime('%H:%M') + ' JST'
        quote = (f'{date} {time}｜{country}｜{label}｜{status}｜重要度 {importance}｜前回 {previous}｜予想 {forecast}'
                 f'｜結果 {actual}｜{diff}｜向き: {symbol} {word}｜{window}')
        rows.append(quote)
        items.append({'date': date, 'time': time, 'country': country, 'label': label, 'status': status,
                      'importance': importance, 'previous': previous, 'forecast': forecast, 'actual': actual,
                      'diff': diff, 'direction': word, 'symbol': symbol, 'tone': tone, 'window': window,
                      'released': released, 'source_quote': quote})
    span = f'直近{past.days}日' if past.days else '直近'
    if not items:
        quote = f'経済指標: {span}と今後7日の範囲に提供カレンダーの重要度3/3指標の掲載なし（収集 {stamp.strftime("%Y-%m-%d %H:%M")} JST）。'
        rows.append(quote)
        items.append({'date': '—', 'time': '', 'country': '—', 'label': '掲載なし', 'status': '—', 'importance': '—',
                      'previous': '—', 'forecast': '—', 'actual': '—', 'diff': '—', 'direction': '—', 'symbol': '—',
                      'tone': 'neutral', 'window': '—', 'released': False, 'source_quote': quote})
    figure = {'id': 'calendar', 'type': 'event_table', 'kicker': '経済指標', 'title': f'経済指標（{span}の結果と今後7日の予定）',
              'subtitle': f'JST / 前回・予想・結果と金への一般則の向き / 収集: {stamp.strftime("%Y-%m-%d %H:%M")} JST',
              'caption': ('収集範囲はInvesting.comの重要度3/3（★★★）だけなので重要度は全件「高」です。差は発表済みなら結果−予想、予定なら予想−前回。'
                          '向きは米指標の一般則（予想より強い結果はドル・金利高で金に逆風）で、米指標以外は規則なし。実際の金利・ドル・金の反応は別に確認します。'
                          '市場は予想を先に織り込むため、予定行の向きは発表前の参考です。発表済み指標の発表足の高値・安値は翌日以降も意識されやすい価格帯（PD array候補）で、水準はチャートで確認します。'
                          'KZはLondon 16:00〜19:00・NY 21:00〜01:00 JST。停止は発表30分前〜60分後（FOMC等は120分後）で、入力から計算した候補です。'),
              'source_label': 'Investing.com Calendar', 'source_url': 'https://www.investing.com/economic-calendar/',
              'items': items}
    return {'figure': figure, 'rows': rows}
