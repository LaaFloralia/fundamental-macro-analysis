"""The one-page dashboard of a BTCUSD edition: tiles, bias board, liquidity, news and the event table.

The same page as the XAU report (scripts/offchart_dashboard.py renders both). Every string the page
shows is a part of one line of the Markdown section 「11. 画面に表示した値」, written here from the frozen
facts, the code's scoring and the parent's checked text. A row whose shown value is exactly one fact's
display is also bound to that fact (btc.bundle.check_bindings).

What the page leaves out (kept in the Markdown): the per-venue Funding and book depth, the global
account ratio, Fear & Greed and the top-trader ratio unless at a 30-day extreme, CFTC (weekly, mostly
basis trades), BTC dominance and mempool fees. Max Pain is shown only for a large expiry.
"""
from __future__ import annotations

from datetime import timedelta
import math
import re

from btc import scoring
from btc.common import JST, parse_time

SECTION = '## 11. 画面に表示した値'
WEEKDAY = '月火水木金土日'
USABLE = ('ok', 'provisional', 'partial', 'stale')
LADDER_RANGE_PCT = {'daily': 5.0, 'weekly': 10.0}
BIG_EXPIRY_PCT = 10.0   # Max Pain is shown only for an expiry holding this share of all open interest
STRIKE_EXPIRY_PCT = 2.0  # strikes of smaller expiries stay in the Markdown only
EXTREME_PCT = (90, 10)   # 30-day percentile of a crowd measure that the page shows
FGI_EXTREME = (75, 25)
UP, DOWN = ('▲', '上向き', 'positive'), ('▼', '下向き', 'negative')
WEAK_UP, WEAK_DOWN = ('△', '上向き・弱', 'positive'), ('▽', '下向き・弱', 'negative')
FLAT, REF, NODATA = ('—', '中立', 'neutral'), ('—', '参考', 'neutral'), ('—', '判定外', 'neutral')
STATE_MARK = {'supportive': UP, 'adverse': DOWN, 'neutral': FLAT, 'mixed': ('—', '材料混在', 'neutral'),
              'unknown': ('—', 'データ不足', 'neutral')}
GROUPS = ('金利・ドル', '現物需要', 'BTC固有イベント', '需給・心理（方向の判定に数えない）')
TILES = (('macro', 'マクロ', '金利・ドル'), ('spot_demand', '現物需要', '現物需要'),
         ('btc_specific_event', 'BTC固有', 'BTC固有イベント'))
REASON_SHORT = {
    'same_day_quotes': '当日の気配で判定', 'common_observation_stale': '比較できる新しい値がない',
    'treasury_missing': '米国債利回りが欠測', 'dollar_proxy_missing': 'ドル指数が欠測', 'fred_unavailable': 'ドル指数が欠測',
    'no_common_dates': '比較できる共通日がない', 'inputs_missing': '入力が欠測',
    'etf_direction': 'ETFの5日合計で判定', 'supply_only_weak': 'ステーブル供給だけ', 'etf_and_supply_opposite': 'ETFとステーブル供給が逆',
    'etf_last_day_opposite': '直近日が逆向き', 'below_thresholds': '閾値未満', 'no_confirmed_btc_event': '算入する材料なし',
    'pending_parent_selection': '親の選別前', 'news_unavailable': 'ニュースが欠測', 'opposite_confirmed_events': '逆向きの材料が併存',
}
STATE_ICON = {'resting': '●', 'swept': '✓', 'broken': '×', 'forming': '◌'}
BEYOND_ICON = {'high': '↗', 'low': '↘'}
STATE_WORD = {'resting': '未到達', 'swept': '掃引済み', 'beyond': '抜けたまま', 'broken': '抜けて定着', 'forming': '形成中'}
IMPACT_MARK = {'supportive': UP, 'adverse': DOWN, 'mixed': ('—', '混在', 'neutral'), 'unknown': ('—', '不明', 'neutral')}
IMPORTANCE = {'critical': '重大', 'high': '高', 'medium': '中', 'low': '低'}
GROUP_WORD = {'macro': 'マクロ', 'spot_demand': '現物需要', 'btc_specific_event': 'BTC固有', 'context_only': '文脈のみ'}
VERIFIED = {'primary_confirmed': '一次本文で確認', 'secondary_body_confirmed': '報道本文で確認', 'headline_only': '見出しのみ',
            'disputed': '異論あり', 'retracted': '撤回'}
CODE_VERIFIED = {'primary_body_retrieved': '公式の本文取得済み', 'secondary_body_retrieved': '本文取得済み',
                 'headline_only': '見出しのみ'}
MACRO_PUBLISHERS = ('Investing.com',)
VENUE = {'binance': 'Binance', 'bybit': 'Bybit', 'okx': 'OKX', 'kraken': 'Kraken', 'bitstamp': 'Bitstamp'}
MARKET = {'spot': '現物', 'linear_perp': '無期限'}


def _md(value) -> str:
    t = parse_time(value).astimezone(JST)
    return f'{t.month:02}/{t.day:02}'


def _day(value) -> str:
    t = parse_time(value).astimezone(JST)
    return f'{t.month:02}/{t.day:02}（{WEEKDAY[t.weekday()]}）'


def _hm(value) -> str:
    return parse_time(value).astimezone(JST).strftime('%H:%M')


def _date_md(day: str) -> str:
    return f'{day[5:7]}/{day[8:10]}'


def _price(value) -> str:
    return f'{value:,.0f}'


def _usable(fact) -> bool:
    return bool(fact) and fact['status'] in USABLE and fact['value'] is not None


class Page:
    """Writes the section's lines and keeps each figure's fact bindings."""

    def __init__(self, doc, index: dict):
        self.doc, self.index = doc, index
        self.bindings: list[dict] = []

    def fact(self, fact_id):
        fact = self.index.get(fact_id or '')
        return fact if _usable(fact) else None

    def line(self, *parts) -> str:
        return self.doc.item('｜'.join(str(p) for p in parts if p not in (None, '')))

    def bind(self, figure: str, item: dict, fact, key: str = 'display') -> dict:
        """Bind the row to its fact when the row shows exactly the fact's display."""
        if fact and fact['display_value'] is not None and item.get(key) == fact['display']:
            item.update(value=fact['display_value'], display=fact['display'])
            self.bindings.append({'figure': figure, 'label': item['label'], 'fact_id': fact['fact_id'],
                                  'value': fact['display_value'], 'display': fact['display']})
        return item


# ------------------------------------------------------------------ bias

def _row(page, group, label, display, *, rule, mark=FLAT, baseline='', change='', asof='', note='', kind='value',
         series=None, fact=None):
    symbol, direction, tone = mark
    quote = page.line(f'バイアス｜{group}｜{label}: {display}' + (f'（{asof}）' if asof else ''),
                      f'比較: {baseline}' if baseline else '', f'変化: {change}' if change else '',
                      f'向き: {symbol} {direction}', note, f'規則: {rule}')
    item = {'group': group, 'label': label, 'display': display, 'baseline': baseline, 'change': change,
            'direction': direction, 'symbol': symbol, 'tone': tone, 'asof': asof, 'note': note, 'kind': kind,
            'rule': rule, 'source_quote': quote}
    if series:
        item['series'] = series
    return page.bind('bias', item, fact)


def _side(value, cut, *, invert=False, weak=False):
    """Direction for BTC of a change against its threshold; ``invert`` when a rise is a headwind."""
    if value is None or abs(value) < cut:
        return FLAT
    up = (value > 0) != invert
    if weak:
        return WEAK_UP if up else WEAK_DOWN
    return UP if up else DOWN


def macro_rows(page, st: dict) -> list:
    g = '金利・ドル'
    macro = st.get('macro', {})
    rows = []
    same = macro.get('same_day') or {}
    y_rule = f'利回りの上昇はBTCの逆風、低下は追い風。前日終値から±{scoring.POLICY["macro"]["ust2y_bp"]}bp以上'
    d_rule = f'ドル高はBTCの逆風、ドル安は追い風。前日終値から±{scoring.POLICY["macro"]["usd_pct"]:.2f}%以上'
    y, dy = page.fact(same.get('ust_2y')), page.fact(same.get('ust_2y_change'))
    d, dd = page.fact(same.get('dxy')), page.fact(same.get('dxy_change'))
    if y and dy:
        mark = _side(dy['value'], scoring.POLICY['macro']['ust2y_bp'], invert=True) if not dy['stale'] else NODATA
        rows.append(_row(page, g, '米2年債利回り', y['display'], baseline='前日終値から', change=f'{dy["value"]:+.1f}bp',
                         asof=_hm(y['observed_at'] or y['retrieved_at']), mark=mark, rule=y_rule, fact=y,
                         note='古い気配（判定に使わない）' if dy['stale'] else ''))
    elif page.fact(macro.get('ust_2y')):
        y = page.fact(macro.get('ust_2y'))
        rows.append(_row(page, g, '米2年債利回り', y['display'].split('（')[0], baseline='前日比なし', asof=_date_md(y['observation_date']),
                         mark=NODATA, rule=y_rule, note='当日の気配がない（米財務省の終値）'))
    if d and dd:
        mark = _side(dd['value'], scoring.POLICY['macro']['usd_pct'], invert=True) if not dd['stale'] else NODATA
        rows.append(_row(page, g, 'ドル指数DXY', d['display'], baseline='前日終値から', change=f'{dd["value"]:+.2f}%',
                         asof=_hm(d['observed_at'] or d['retrieved_at']), mark=mark, rule=d_rule, fact=d,
                         note='古い気配（判定に使わない）' if dd['stale'] else ''))
    elif page.index.get(macro.get('DTWEXBGS') or ''):
        b = page.index[macro['DTWEXBGS']]
        rows.append(_row(page, g, '米ドル広義指数（FRED）', b['display'].split('（')[0], baseline='前日比なし',
                         asof=_date_md(b['observation_date']), mark=NODATA, rule=d_rule,
                         note='公表が約1週間遅れ（判定に使わない）' if b['stale'] else ''))
    n, dn = page.fact(same.get('nasdaq')), page.fact(same.get('nasdaq_change'))
    n_rule = '株と同じリスク資産として動きやすい（方向の判定には数えない）'
    if n and dn:
        closed = bool(n.get('observation_date'))  # a cash close (date) or a session quote (time)
        rows.append(_row(page, g, 'Nasdaq総合（終値）' if closed else 'Nasdaq総合', n['display'], baseline='前日終値から',
                         change=f'{dn["value"]:+.2f}%', mark=REF, rule=n_rule, fact=n,
                         asof=_date_md(n['observation_date']) if closed else _hm(n['observed_at'])))
    elif page.fact(macro.get('NASDAQCOM')) and page.fact(macro.get('NASDAQCOM_change')):
        n, dn = page.fact(macro['NASDAQCOM']), page.fact(macro['NASDAQCOM_change'])
        rows.append(_row(page, g, 'Nasdaq総合（終値）', n['display'].split('（')[0], baseline='前日終値から',
                         change=f'{dn["value"]:+.2f}%', asof=_date_md(n['observation_date']), mark=REF, rule=n_rule))
    return rows


ETF_STATE_LABEL = {'ok': '全銘柄の数値がそろい合計の照合も成立（採点に使う）',
                   'incomplete': '全銘柄の値が未確定（採点にも正常な充足にも数えない）',
                   'conflict': '提供元の合計と銘柄の合計が一致しない（両方を残し採点しない・データ保留）',
                   'lagged': '期待日の行がなく1営業日遅れ（正常な充足に数えない）',
                   'stale': '期待日から2営業日以上遅れ（古い）',
                   'unknown': '米取引日の暦が収録範囲外（未確認）', 'missing': '欠測'}


def etf_state_text(etf: dict) -> str:
    """The ETF row's state in words (section 3 and the page's rule for the ETF row)."""
    fallback = etf.get('lagged_fallback')
    if fallback:
        window = etf.get('five_day_window') or []
        return (f'期待日 {fallback["expected_trade_date"]} の行は全銘柄の値が未確定。5営業日合計は前営業日 '
                f'{fallback["used_trade_date"]} までの窓（{window[0]}〜{window[-1]}）で算出'
                '（1営業日遅れ・正常な充足に数えない）')
    return ETF_STATE_LABEL.get(etf.get('status'), etf.get('status'))


def spot_rows(page, st: dict, ev: dict) -> list:
    g = '現物需要'
    rows = []
    etf = st.get('etf', {})
    comp = ev['groups']['spot_demand'].get('components', {})
    five, last = page.fact(etf.get('five_day_fact_id')), page.fact(etf.get('last_day_fact_id'))
    window = etf.get('five_day_window') or []
    if five and window:
        state = (comp.get('etf') or {}).get('state', 'unknown')
        notes = [x for x in ('1営業日遅れ' if etf.get('status') == 'lagged' else '',
                             '速報' if five['status'] == 'provisional' else '') if x]
        rows.append(_row(page, g, 'ETFフロー（5営業日）', five['display'], baseline=f'{_date_md(window[0])}〜{_date_md(window[-1])}の合計',
                         asof=_date_md(window[-1]), mark=STATE_MARK.get(state, NODATA), note='・'.join(notes), fact=five,
                         rule=f'+{scoring.ETF_THRESHOLD / 1e6:,.0f}百万USD以上で上向き、-{scoring.ETF_THRESHOLD / 1e6:,.0f}百万USD以下で下向き。'
                              f'直近日が逆向きに{scoring.ETF_LAST_DAY_OPPOSITE / 1e6:,.0f}百万USD以上なら混在。今回: {etf_state_text(etf)}'))
    if last:
        rows.append(_row(page, g, 'ETFフロー（直近日）', last['display'], baseline='1日', asof=_date_md(last['observation_date']),
                         mark=REF, fact=last, rule='5営業日の向きと逆に大きければ混在にする（単独では数えない）'))
    if etf.get('status') not in (None, 'ok', 'lagged'):
        rows.append(_row(page, g, 'ETFフロー', '未確定', kind='text', mark=NODATA,
                         rule=f'全銘柄の値がそろい合計の照合が通った日だけ使う。今回: {etf_state_text(etf)}'))
    stable = st.get('stable', {})
    total = page.fact(stable.get('total_fact_id'))
    d7, d30 = page.fact(stable.get('change_7d')), page.fact(stable.get('change_30d'))
    if total and d7 and d30:
        state = (comp.get('stable_supply') or {}).get('state', 'unknown')
        series = [{'text': f'7日 {d7["display"]}', 'tone': 'positive' if d7['value'] > 0 else 'negative' if d7['value'] < 0 else 'neutral'},
                  {'text': f'30日 {d30["display"]}', 'tone': 'positive' if d30['value'] > 0 else 'negative' if d30['value'] < 0 else 'neutral'}]
        p = scoring.POLICY['stable']
        rows.append(_row(page, g, 'ステーブルコイン供給', total['display'], baseline='7日・30日の変化',
                         change='／'.join(s['text'] for s in series), series=series, asof=_date_md(total['observation_date']),
                         mark=STATE_MARK.get(state, NODATA), fact=total,
                         note='ETFが判定した日は数えない' if ev_item(ev, 'stable_supply_alignment').get('status') == 'deduplicated' else '',
                         rule=f'7日+{p["d7_pct"]}%以上かつ30日+{p["d30_pct"]}%以上で上向き、7日-{p["d7_pct"]}%以下かつ30日-{p["d30_pct"]}%以下で下向き'))
    return rows


def ev_item(ev: dict, key: str) -> dict:
    return next((i for i in ev['items'] if i['key'] == key), {})


def event_rows(page, st: dict, ev: dict, analysis: dict) -> list:
    g = 'BTC固有イベント'
    group = ev['groups']['btc_specific_event']
    items = {n['id']: n for n in st.get('news', {}).get('items', [])}
    counted = [items[i]['title'] for i in group.get('components', {}).get('news_ids', []) if i in items]
    text = ('算入: ' + ' ／ '.join(counted)) if counted else '算入なし（一次本文で確認した重要な材料がない）'
    return [_row(page, g, 'ニュース（一次本文で確認）', text, kind='text', baseline='直近のニュース',
                 mark=STATE_MARK.get(group['state'], NODATA),
                 rule='親が選び、一次本文で確認した重要度high以上の材料だけを数える。二次報道と見出しだけの材料は数えない')]


def crowd_rows(page, st: dict, facts: list) -> list:
    g = GROUPS[3]
    rows = []
    pick = lambda metric: next((f for f in facts if f['metric'] == metric and _usable(f)), None)  # noqa: E731
    weighted = pick('funding_weighted_8h')
    cut = scoring.FUNDING_CROWDED
    if weighted:
        mark = FLAT if abs(weighted['value']) < cut else WEAK_DOWN if weighted['value'] > 0 else WEAK_UP
        rows.append(_row(page, g, 'Funding（3市場・8時間）', weighted['display'], baseline=f'過密の目安 ±{cut * 100:.2f}%', mark=mark,
                         note='ロングが過密' if weighted['value'] >= cut else 'ショートが過密' if weighted['value'] <= -cut else '',
                         fact=weighted, rule='建玉で加重した確定Funding。過密な側の逆（弱）。Binance・Bybit・OKXの線形無期限だけ'))
    parts = []
    for v in st.get('derivatives', {}).get('venues', []):
        chg = page.fact(v.get('oi_change_fact_id'))
        if chg:
            parts.append(f'{VENUE.get(v["venue"], v["venue"])} {chg["display"]}')
    if parts:
        rows.append(_row(page, g, '建玉（OI）', '・'.join(parts), baseline='24時間前から', mark=REF,
                         rule=f'建玉の増加は新規の建て。Fundingの過密と同時に{scoring.OI_CROWDED_PCT:g}%以上増えると急変に注意'))
    basis = st.get('basis', {})
    ref = next((b for b in basis.get('items', []) if b.get('instrument') == basis.get('reference')), None)
    annual = page.fact((ref or {}).get('annual_fact_id'))
    if annual:
        rows.append(_row(page, g, '先物ベーシス（年率）', annual['display'], baseline='Deribit先物と指数の差', mark=REF, fact=annual,
                         rule='高いほどレバレッジの買い需要が強い。急低下は手仕舞いのしるし（方向の判定には数えない）'))
    ratios = st.get('ratios', {})
    hi, lo = EXTREME_PCT
    for key, label in (('account_long_share', 'Binance全口座のロング比率'), ('top_position_long_share', 'Binance上位建玉のロング比率')):
        fact, perc = page.fact(ratios.get(key)), page.fact(ratios.get(f'{key}_percentile'))
        if not fact or not perc or lo < perc['value'] < hi:
            continue
        crowd = key == 'account_long_share'
        mark = (WEAK_DOWN if perc['value'] >= hi else WEAK_UP) if crowd else REF
        rows.append(_row(page, g, label, fact['display'], baseline=f'過去30日の{perc["display"]}', mark=mark, fact=fact,
                         note='多数派の逆' if crowd else '',
                         rule=(f'口座数の比率。百分位{hi}以上は多数派の逆で下向き、{lo}以下は上向き（弱）' if crowd else
                               f'建玉量の比率。百分位{hi}以上か{lo}以下のときだけ表示（参考）')))
    fgi = st.get('fgi', {})
    f = page.fact(fgi.get('fact_id'))
    high, low = FGI_EXTREME
    if f and not low < f['value'] < high:
        d7 = page.fact(fgi.get('diff_7d'))
        rows.append(_row(page, g, 'Fear & Greed', f'{f["value"]:g}', baseline='7日前から' if d7 else '',
                         change=f'{d7["value"]:+g}' if d7 else '', mark=WEAK_DOWN if f['value'] >= high else WEAK_UP,
                         note='極端な強気' if f['value'] >= high else '極端な恐怖',
                         rule=f'{high}以上は極端な強気で下向き、{low}以下は極端な恐怖で上向き（弱）。指数の約半分は価格の動きから作られる'))
    skew = st.get('options', {}).get('skew', {})
    for tenor in ('7d', '30d'):
        value, perc = page.fact(skew.get(f'skew_25d_{tenor}')), page.fact(skew.get(f'skew_25d_{tenor}_percentile'))
        if not value:
            continue
        cut = scoring.SKEW_PP
        mark = REF
        if perc and value['value'] >= cut and perc['value'] >= hi:
            mark = WEAK_DOWN
        elif perc and value['value'] <= -cut and perc['value'] <= lo:
            mark = WEAK_UP
        rows.append(_row(page, g, f'25Δスキュー（{tenor[:-1]}日）', f'{value["value"]:+.2f}pp',
                         baseline=f'過去180日の{perc["display"]}' if perc else '百分位は履歴不足', mark=mark,
                         note='プットが割高' if value['value'] > 0 else 'コールが割高',
                         rule=f'プット−コールの差。+{cut:g}pp以上かつ百分位{hi}以上は下落への備えが強い（弱）'))
        break
    dvol = pick('dvol')
    if dvol:
        perc = pick('dvol_percentile')
        rows.append(_row(page, g, 'DVOL（想定変動率）', dvol['display'], baseline=f'過去90日の{perc["display"]}' if perc else '',
                         mark=REF, fact=dvol, rule='オプションから見た年率の想定変動。高いほど大きな値動きを織り込む（方向は示さない）'))
    return rows


def _tile(page, key, label, ev, short):
    g = ev['groups'][key]
    symbol, direction, tone = STATE_MARK[g['state']]
    quote = page.line(f'バイアス｜タイル｜{label}: {symbol} {direction}', short)
    return {'label': label, 'group': dict((k, grp) for k, _, grp in TILES)[key], 'symbol': symbol, 'direction': direction,
            'tone': tone, 'origin': '', 'detail': short, 'short': short, 'mismatch': False, 'show_detail': True,
            'source_quote': quote}


def _total_tile(page, ev):
    if ev['direction_status'] == 'withheld':
        symbol, direction, tone = '—', '保留', 'neutral'
    else:
        up = ev['candidate_direction'] == 'up'
        strength = ev['direction_strength']
        symbol = ('▲' if up else '▼') if strength in ('中', '高') else ('△' if up else '▽')
        direction = ('上向き' if up else '下向き') + {'低': '・弱', '中': '', '高': '・強'}.get(strength, '')
        tone = 'positive' if up else 'negative'
    detail = f'NO-TRADE（{_stop_kind(ev)}）' if ev['no_trade'] else '停止条件なし'
    quote = page.line(f'バイアス｜タイル｜総合: {symbol} {direction}', detail)
    return {'label': '総合', 'group': None, 'symbol': symbol, 'direction': direction, 'tone': tone, 'origin': '',
            'detail': detail, 'short': '', 'mismatch': False, 'show_detail': True, 'source_quote': quote}


def _stop_kind(ev) -> str:
    """「方向の根拠が弱い」 for a withheld direction, else the stop's kind (データ保留・障害停止・イベント停止・期限切れ)."""
    reason = ev['no_trade_reason'] or ''
    held = re.match(r'方向付与保留（([^（）]+)）', reason)
    return held.group(1) if held else reason.split(':')[0]


def _stop_detail(ev) -> str:
    """The reason after 「データ保留: 」 or 「障害停止: 」; event stops are the 停止中 chip, a withheld direction is the tile."""
    reason = ev['no_trade_reason'] or ''
    if not ev['no_trade'] or reason.startswith(('方向付与保留', 'イベント停止')) or ': ' not in reason:
        return ''
    return reason.split(': ', 1)[1]


def _group_short(key, ev, st, index):
    g = ev['groups'][key]
    if key == 'macro' and g['reason_code'] == 'same_day_quotes':
        dy, dd = (index.get(f) for f in g['fact_ids'][:2])
        if dy and dd:
            return f'2年債 {dy["value"]:+.1f}bp・DXY {dd["value"]:+.2f}%'
    if key == 'btc_specific_event' and g['state'] in ('supportive', 'adverse', 'mixed'):
        return f'算入 {len(g["components"].get("news_ids", []))}件'
    return REASON_SHORT.get(g['reason_code'], g['reason_code'])


BIAS_LEGEND = ('右端の「向き」はBTCへの向きです。上の4つの枠はコードの規則で決めた材料の群ごとの向きで、マクロ（米2年債とドル指数が'
               '同じ向きにそろったとき）、現物需要（ETFの5営業日合計、なければステーブル供給）、BTC固有（一次本文で確認した重要な'
               'ニュース）の3群です。総合は3群の向きをまとめたもので、逆向きの群があれば保留、1群なら弱、2群で中、3群で強です。'
               '▲▼は閾値以上の向き、△▽は弱い向き、—は中立・参考・判定外です。「需給・心理」の行は過密や極端さの目安で、方向の判定には'
               '数えず、確度を下げる警告にだけ使います（極端なときだけ表示する行があります）。Fundingと建玉はBinance・Bybit・OKXの'
               '線形無期限だけで、市場全体ではありません。閾値は経験則の目安で、予測力は検証していません。')


def bias_figure(page, facts: dict, ev: dict, analysis: dict) -> dict:
    st, index = facts['state'], page.index
    items = macro_rows(page, st) + spot_rows(page, st, ev) + event_rows(page, st, ev, analysis) + crowd_rows(page, st, facts['facts'])
    verdicts = [_tile(page, key, label, ev, _group_short(key, ev, st, index)) for key, label, _ in TILES]
    verdicts.append(_total_tile(page, ev))
    page.line('バイアスの読み方: ' + BIAS_LEGEND)
    rules = list({i['label']: {'label': i['label'], 'rule': i['rule']} for i in items}.values())
    return {'id': 'bias', 'type': 'bias_board', 'title': 'バイアス', 'tiles_label': 'BTCへの向き（材料の群ごと）',
            'groups': [g for g in GROUPS if any(i['group'] == g for i in items)], 'verdicts': verdicts, 'items': items,
            'legend': BIAS_LEGEND, 'rules': rules,
            'sources': [{'label': 'CNBC', 'url': 'https://www.cnbc.com/quotes/US2Y'},
                        {'label': 'Farside', 'url': 'https://farside.co.uk/btc/'},
                        {'label': 'DefiLlama', 'url': 'https://defillama.com/stablecoins'},
                        {'label': 'Deribit', 'url': 'https://www.deribit.com/'}]}


# ------------------------------------------------------------------ liquidity

def _liq(page, kind, label, price_text, position, *, state, state_word, icon, price, pool='', touched='', target=''):
    distance = '' if kind == 'current' else f'差 {position - price:+,.0f}'
    quote = page.line(f'流動性｜{label}: {price_text}', pool, distance, state_word, touched, target)
    item = {'kind': kind, 'label': label, 'price_text': price_text, 'position': position, 'pool': pool, 'state': state,
            'state_word': state_word, 'icon': icon, 'touched': touched, 'distance_text': distance, 'source_quote': quote}
    if target:
        item['target'] = target
    return item


def liquidity_figure(page, facts: dict, dest: dict, mode: str, finalize_at) -> dict | None:
    st = facts['state']
    ref = page.fact(st.get('reference_price', {}).get('fact_id'))
    if not ref:
        return None
    price = ref['value']
    span = LADDER_RANGE_PCT[mode]
    items, notices, missing = [], [], []
    choice = dest['choice']
    targets, mark = set(), ''
    if choice['kind'] != 'none' and dest['status'] != 'expired':
        targets = {page.index[f]['value'] for f in choice['level_fact_ids'] if f in page.index}
        mark = f'◎ 目的地の候補（{_hm(dest["valid_until"])}まで）'
    aim = lambda position: mark if position in targets else ''  # noqa: E731
    levels = st.get('levels') or {}
    for lv in levels.get('items', []):
        state = lv.get('state', 'resting')
        icon = BEYOND_ICON.get(lv['side'], '↗') if state == 'beyond' else STATE_ICON.get(state, '●')
        touched = f'{_md(lv["touched_at"])} {_hm(lv["touched_at"])}に到達' if lv.get('touched_at') and state != 'resting' else ''
        items.append(_liq(page, 'level', lv['name'], _price(lv['price']), lv['price'], state=state,
                          state_word=STATE_WORD.get(state, state), icon=icon, price=price,
                          pool='BSL' if lv['side'] == 'high' else 'SSL', touched=touched))
    if not levels.get('items'):
        missing.append('名前付きの高値・安値: 1時間足を取得できず' if levels.get('status') != 'ok' else '名前付きの高値・安値: 該当なし')
    items.append(_liq(page, 'current', '現在値', _price(price), price, state='current', icon='▶', price=price,
                      state_word=f'{_hm(ref["retrieved_at"])} JST・3市場の中央値'))
    for w in st.get('liquidity', {}).get('walls', []):
        side = '買い板' if w['side'] == 'bid' else '売り板'
        items.append(_liq(page, 'zone', f'{VENUE.get(w["venue"], w["venue"])}{MARKET.get(w["market"], "")}の厚い{side}',
                          f'{_price(w["low"])}〜{_price(w["high"])}', w['center'], state='zone', icon='▭', price=price,
                          state_word=f'片側の{w["share_pct"]:g}%・{w["hits"]}/3回', target=aim(w['center'])))
    opt = st.get('options', {})
    strikes: dict[float, list] = {}
    for e in opt.get('expiries', []):
        day = _md(e['expiry'])
        if (e.get('share_pct') or 0) >= STRIKE_EXPIRY_PCT:
            for c in e['top_strikes']:
                strikes.setdefault(c['strike'], []).append(f'{day}満期 {c["oi_btc"]:,.0f} BTC')
        if (e.get('share_pct') or 0) >= BIG_EXPIRY_PCT and e.get('max_pain') is not None:
            items.append(_liq(page, 'reference', f'Max Pain（{day}満期）', _price(e['max_pain']), e['max_pain'], state='reference',
                              icon='┄', price=price, state_word=f'参考・建玉の{e["share_pct"]:g}%の満期'))
    for strike, parts in strikes.items():
        items.append(_liq(page, 'oi', 'オプション建玉', _price(strike), strike, state='oi', icon='◇', price=price,
                          state_word='・'.join(parts), target=aim(strike)))
    for i in items:
        i['offscale'] = abs(i['position'] - price) / price * 100 > span and i['kind'] != 'current'
    items.sort(key=lambda i: (-i['position'], {'current': 1}.get(i['kind'], 0)))
    band = None
    dvol = next((f for f in facts['facts'] if f['metric'] == 'dvol' and _usable(f)), None)
    if dvol:
        days = 7 if mode == 'weekly' else 1
        move = price * dvol['value'] / 100 * math.sqrt(days / 365)
        band = {'lower': round(price - move, 2), 'upper': round(price + move, 2),
                'label': f'想定変動 ±1σ（{"1週" if days == 7 else "1日"}・±{move:,.0f}ドル）'}
        page.line(f'流動性｜{band["label"]}: {band["lower"]:,.2f}〜{band["upper"]:,.2f}（DVOL {dvol["display"]}から換算）')
    book_at = [page.index[i] for i in st.get('liquidity', {}).get('book_fact_ids', []) if i in page.index]
    if book_at:
        text = f'板は{_hm(max(f["retrieved_at"] for f in book_at))} JSTの観測で90秒で失効。見せ板の可能性があり、3回中2回以上残った帯だけ'
        notices.append({'text': text, 'source_quote': page.line(f'流動性｜表示の注記: {text}')})
    if dest['status'] == 'expired' and choice['kind'] != 'none':
        text = '目的地の候補は根拠の観測が完成前に失効したため出していない'
        notices.append({'text': text, 'source_quote': page.line(f'流動性｜表示の注記: {text}')})
    gaps = [{'text': t, 'source_quote': page.line(f'流動性｜欠測: {t}')} for t in missing]
    cautions = [{'text': c['text'], 'source_quote': page.line(f'流動性｜注意: {c["text"]}')} for c in levels.get('cautions') or []]
    legend = ('名前付きの高値・安値、観測した厚い板、オプション建玉の集まる行使価格を価格順に並べています。価格の右の縦線は現在値より上が赤、'
              '下が緑で、濃い行が現在値です（画面を動かしても上端か下端に残ります）。「差」は現在値との差（ドル）です。'
              '高値の上（BSL）と安値の下（SSL）は損切りが置かれやすいとされる流動性の候補で、注文そのものは観測していません。'
              '●未到達、✓掃引済み（一度抜けて、最新の終値は内側）、↗↘抜けたまま（最新の終値が外側で6時間未満）、×抜けて定着（6時間以上外側）。'
              '日の区切りはUTC 0時（JST 9時）、週は月曜から。セッションは各市場の現地時間で、アジア 東京9〜15時・London ロンドン8〜13時・'
              'NY ニューヨーク8〜13時。判定はKraken BTC/USDの確定1時間足です。'
              '▭厚い板は25bpごとの帯で、3回の観測のうち2回以上、片側1%以内の量の20%以上を占めた帯です（見せ板の可能性があり、到達を保証しません）。'
              f'◇オプション建玉はDeribitの行使価格ごとの建玉（全建玉の{STRIKE_EXPIRY_PCT:g}%以上の満期の上位3つ）、┄Max Painは全建玉の'
              f'{BIG_EXPIRY_PCT:g}%以上の満期だけの参考値です。建玉は保有残高で、売買の向きやディーラーのヘッジは分かりません。'
              f'表示範囲は現在値±{span:g}%で、外側は下に一覧。帯は想定変動±1σ（DVOLから換算、到達予測ではない）で、色の付いた行が帯の内側です。'
              '清算ヒートマップは有料の推定モデルのため使っていません。')
    page.line('流動性の読み方: ' + legend)
    sub = ''
    if levels.get('last_bar_start'):
        sub = f'高安の状態は{_md(levels["last_bar_start"])} {_hm(levels["last_bar_start"])} JST開始の確定1時間足まで。'
    return {'id': 'liquidity', 'type': 'liquidity_map', 'title': '流動性（Liquidity）', 'subtitle': sub, 'items': items,
            'cautions': cautions, 'notices': notices, 'band': band, 'missing': gaps, 'legend': legend,
            'sources': [{'label': 'Kraken', 'url': 'https://www.kraken.com/'},
                        {'label': 'Deribit', 'url': 'https://www.deribit.com/'}]}


# ------------------------------------------------------------------ news

def news_figure(page, facts: dict, analysis: dict, ev: dict) -> dict | None:
    st = facts['state']
    news = st.get('news', {})
    if news.get('status') == 'missing' or not news.get('items'):
        return None
    assessed = {a['news_id']: (i, a) for i, a in enumerate(analysis['news_assessments'])}
    groups = ('クリプト', 'マクロ・地政学')
    items = []
    for n in sorted(news['items'], key=lambda x: x['published_at'], reverse=True):
        group = groups[1] if n.get('publisher') in MACRO_PUBLISHERS else groups[0]
        reaction = n.get('reaction') or {}
        after = reaction.get('return_after_5m_bps') if reaction.get('status') == 'ok' else None
        react = f'5分後 {after:+.1f}bp' if after is not None else ''
        found = assessed.get(n['id'])
        if found:
            i, a = found
            symbol, direction, tone = IMPACT_MARK[a['impact']]
            verification = VERIFIED[a['verification']]
            importance = f'{GROUP_WORD[a["group_id"]]}・重要度 {IMPORTANCE[a["importance"]]}'
            interpretation = analysis['expanded'][f'news_assessments[{i}].interpretation']
        else:
            symbol, direction, tone = '', '', 'neutral'
            verification = CODE_VERIFIED.get(n['code_verification'], n['code_verification'])
            importance = interpretation = ''
        time = f'{_md(n["published_at"])} {_hm(n["published_at"])}'
        quote = page.line(f'ニュース｜{group}｜{time}', n['publisher'], n['title'], verification, react,
                          f'向き: {symbol} {direction}' if found else '', importance, interpretation)
        item = {'group': group, 'time': time, 'publisher': n['publisher'], 'title': n['title'], 'verification': verification,
                'reaction': react, 'direction': direction, 'symbol': symbol, 'tone': tone, 'importance': importance,
                'interpretation': interpretation, 'assessed': bool(found), 'source_quote': quote}
        if n.get('url', '').startswith('https://'):
            item['url'] = n['url']
        items.append(item)
    tallies = {}
    for g in groups:
        rows = [i for i in items if i['group'] == g]
        if rows:
            tallies[g] = f'評価 {sum(1 for i in rows if i["assessed"])}件・見出し {len(rows)}件'
    cautions = [{'text': f'重大障害が継続中: {inc.get("title") or inc["incident_id"]}',
                 'source_quote': page.line(f'ニュース｜注意: 重大障害が継続中: {inc.get("title") or inc["incident_id"]}')}
                for inc in ev.get('incidents_open') or []]
    for r in st.get('incidents', {}).get('recently_released') or []:
        text = (f'障害の手動解除（解除から14日間表示）: {r["incident_id"]}「{r.get("title") or ""}」・'
                f'理由 {r.get("release_reason") or ""}・記録者 {r.get("released_by") or ""}')
        cautions.append({'text': text, 'source_quote': page.line(f'ニュース｜注意: {text}')})
    feeds = news.get('feeds') or []
    legend = (f'直近{news.get("lookback_hours", 36):g}時間の暗号資産メディア・公式RSS・Investing.comの見出し{news.get("candidate_count", 0)}件から'
              f'{len(news["items"])}件を選び（マクロ・地政学は最大5件）、親がBTCへの効き方を評価した見出しを先に出しています。'
              '「本文取得済み」は記事本文を読めたもの、「見出しのみ」は本文を確認していないものです。Investing.comの見出しは本文を取得しません。'
              '向きは親の評価で、BTC固有の群に数えるのは一次本文で確認した重要度high以上の材料だけです。'
              '「5分後」は公表時刻から5分間のBinance BTCUSDT 1分足の値動きで、ニュースが原因とは限りません。'
              f'取得できたRSS: 一般 {news.get("feeds_ok", {}).get("general", 0)}/{sum(1 for x in feeds if not x.get("official"))}・'
              f'公式 {news.get("feeds_ok", {}).get("official", 0)}/{sum(1 for x in feeds if x.get("official"))}。')
    page.line('ニュースの読み方: ' + legend)
    return {'id': 'news', 'type': 'news_list', 'title': 'ニュース', 'groups': list(groups), 'items': items, 'tallies': tallies,
            'cautions': cautions, 'legend': legend,
            'sources': [{'label': 'CoinDesk', 'url': 'https://www.coindesk.com/'},
                        {'label': 'Investing.com', 'url': 'https://www.investing.com/news/'}]}


# ------------------------------------------------------------------ events

def _short_name(name: str) -> str:
    """「PPI（Producer Price Index） 2026年9月分」 -> 「PPI 9月分」."""
    name = re.sub(r'（[A-Za-z][^）]*）', '', name)
    return re.sub(r'\s*\d{4}年', ' ', name).strip()


def event_figure(page, facts: dict, ev: dict, mode: str) -> dict:
    st = facts['state']
    as_of = ev['as_of']
    start, end = as_of - timedelta(hours=24), as_of + timedelta(days=7 if mode == 'daily' else 14)
    rows = []
    for e in st.get('events', []):
        when = parse_time(e['start_at'])
        if start <= when <= end and e['importance'] in ('critical', 'high', 'medium'):
            rows.append((when, e))
    for e in st.get('options', {}).get('expiries', []):
        when = parse_time(e['expiry'])
        if as_of <= when <= end:
            rows.append((when, {'expiry': e}))
    items, last_time = [], None
    for when, e in sorted(rows, key=lambda r: (r[0], 'expiry' in r[1])):
        date, time = _day(e.get('start_at') or e['expiry']['expiry']), _hm(when.isoformat())
        if 'expiry' in e:
            x = e['expiry']
            label = f'Deribitオプション満期（建玉の{x["share_pct"]:g}%）'
            memo = '大きな満期' if (x.get('share_pct') or 0) >= BIG_EXPIRY_PCT else ''
            quote = page.line(f'指標｜{date} {time} JST', label, '予定', '前回 —', '予想 —', '結果 —', '差 —', '向き: — 満期', memo)
            items.append({'date': date, 'time': time, 'label': label, 'status': '予定', 'previous': '—', 'forecast': '—',
                          'actual': '—', 'diff': '—', 'direction': '満期', 'symbol': '—', 'tone': 'neutral', 'memo': memo,
                          'released': False, 'group_start': (date, time) != last_time, 'source_quote': quote})
            last_time = (date, time)
            continue
        released = when <= as_of
        window = e.get('stop_window')
        memo = f'停止 {_hm(window["start_at"])}〜{_hm(window["end_at"])}' if window else ''
        status = '発表済み' if released else '予定'
        symbol, direction = '—', '結果は未収集' if released else '発表前'
        tier = 'A' if e['importance'] in ('critical', 'high') else 'B'
        quote = page.line(f'指標｜{date} {time} JST', f'Tier {tier}', _short_name(e['name']), status, '前回 —', '予想 —', '結果 —',
                          '差 —', f'向き: {symbol} {direction}', memo)
        items.append({'date': date, 'time': time, 'tier': tier, 'label': _short_name(e['name']), 'status': status,
                      'previous': '—', 'forecast': '—', 'actual': '—', 'diff': '—', 'direction': direction, 'symbol': symbol,
                      'tone': 'neutral', 'memo': memo, 'released': released, 'group_start': (date, time) != last_time,
                      'source_quote': quote})
        last_time = (date, time)
    cautions = []
    cal = st.get('calendar', {})
    if cal.get('next_24h') != 'ok':
        text = '公式予定の一部が未確認のため、載っていない指標がありえる'
        cautions.append({'text': text, 'source_quote': page.line(f'指標｜注意: {text}')})
    if not items:
        date = _day(as_of.isoformat())
        quote = page.line(f'指標｜{date}', '期間内の重要な予定なし')
        items.append({'empty': True, 'date': date, 'label': '期間内の重要な予定なし', 'source_quote': quote})
    legend = ('時刻はJST。BLS・BEA・FRBの公式予定から、米国の重要指標（Tier A＝CPI・雇用統計・FOMCなど重要度high、'
              'Tier B＝PPI・小売・PCEなど重要度medium）と、Deribitのオプション満期を載せています。期間は24時間前の発表から'
              f'{7 if mode == "daily" else 14}日先まで。BTCは米指標そのものより、指標で動く金利・ドル・株を通じて動きやすく、'
              '発表の前後は値動きと損切りの連鎖が起きやすい時間帯です。予想と結果は今の収集にないため「—」です。'
              '停止はこのレポートの規則で新規の判断を止める時間（発表15分前〜30分後、FOMCは声明30分前〜90分後）。'
              '満期は日次・週次・月次とも17:00 JSTで、全建玉の10%以上を「大きな満期」としています。')
    page.line('指標の読み方: ' + legend)
    return {'id': 'calendar', 'type': 'event_table', 'title': '米国の重要指標と満期', 'direction_header': 'BTCへの向き',
            'items': items, 'cautions': cautions, 'legend': legend,
            'sources': [{'label': 'BLS', 'url': 'https://www.bls.gov/schedule/'},
                        {'label': 'Fed', 'url': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
                        {'label': 'Deribit', 'url': 'https://www.deribit.com/'}]}

# ------------------------------------------------------------------ lead and assembly

def lead(page, facts: dict, ev: dict, analysis: dict, s8: dict) -> dict:
    """Conclusion rows (理由・反対材料), the confidence word, the lead facts and the folded limitations."""
    st = facts['state']
    reason = analysis['expanded']['thesis']
    counter = analysis['expanded']['counter_cases[0]'] if analysis.get('counter_cases') else ''
    rows = [{'label': '理由', 'text': reason, 'source_quote': page.line(f'結論｜理由: {reason}')}]
    if counter:
        rows.append({'label': '反対材料', 'text': counter, 'source_quote': page.line(f'結論｜反対材料: {counter}')})
    band = ev['band']
    conf_line = page.line(f'信頼度: {band}', f'整合度 {ev["total"]}点',
                          'NO-TRADE: ' + (f'あり（{ev["no_trade_reason"]}）' if ev['no_trade'] else 'なし'))
    from scripts.report_bundle import CONFIDENCE_JA
    chips = []
    upcoming = [e for e in st.get('events', []) if parse_time(e['start_at']) > ev['as_of']
                and e['importance'] in ('critical', 'high', 'medium')]
    if upcoming:
        first = min(parse_time(e['start_at']) for e in upcoming)
        names = '・'.join(_short_name(e['name']) for e in upcoming if parse_time(e['start_at']) == first)
        text = f'次の米指標: {names} {_day(first.isoformat())}{_hm(first.isoformat())} JST'
        chips.append({'text': text, 'source_quote': page.line(text)})
    stop = _stop_detail(ev)
    if stop:
        text = f'{_stop_kind(ev)}の理由: {stop}'
        chips.append({'text': text, 'source_quote': page.line(text)})
    gate = ev['trade_gate']
    if gate['active_windows']:
        text = '停止中: ' + '、'.join(w['label'] for w in gate['active_windows'])
        chips.append({'text': text, 'source_quote': page.line(text)})
    levels = st.get('levels') or {}
    ref = page.fact(st.get('reference_price', {}).get('fact_id'))
    by_key = {lv['key']: lv for lv in levels.get('items', [])}
    near = [(side, by_key.get(key)) for side, key in (levels.get('nearest') or {}).items() if by_key.get(key)]
    if ref and near:
        parts = [f'{"上" if side == "high" else "下"} {lv["name"]} {_price(lv["price"])}（{lv["price"] - ref["value"]:+,.0f}）'
                 for side, lv in sorted(near, key=lambda x: x[0] != 'high')]
        text = '直近の未到達: ' + '／'.join(parts)
        chips.append({'text': text, 'source_quote': page.line(text)})
    limits = []
    for line in s8['limitations']:
        text = re.sub(r'^制約(（コード）)?: ', '', line)
        text = re.sub(r'（制約）$', '', text)
        limits.append({'title': '制約', 'text': text, 'source_quote': line})
    return {'conclusion': {'title': '結論', 'rows': rows, 'source_quote': rows[0]['source_quote']},
            'confidence': {'text': f'信頼度 {CONFIDENCE_JA[band]}', 'source_quote': conf_line},
            'chips': chips, 'limitations': limits}


def build(doc, facts: dict, analysis: dict, ev: dict, index: dict, dest: dict, *, mode: str, finalize_at,
          s8: dict, title: str, as_of_label: str) -> dict:
    """Write section 11 and return the page's summary, figures and fact bindings."""
    doc.add(SECTION, '')
    page = Page(doc, index)
    head = lead(page, facts, ev, analysis, s8)
    figures = [bias_figure(page, facts, ev, analysis)]
    liquidity = liquidity_figure(page, facts, dest, mode, finalize_at)
    if liquidity:
        figures.append(liquidity)
    news = news_figure(page, facts, analysis, ev)
    if news:
        figures.append(news)
    figures.append(event_figure(page, facts, ev, mode))
    doc.add('')
    summary = {'eyebrow': f'BTCUSD {mode.upper()}', 'short_title': 'チャート外分析', 'as_of_label': as_of_label,
               'note': '向き・価格・指標は原稿と収集データの引用です。売買の確率や到達を示すものではありません。', **head}
    return {'summary': summary, 'figures': figures, 'bindings': page.bindings, 'title': title}
