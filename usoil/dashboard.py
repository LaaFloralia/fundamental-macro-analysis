"""The one-page dashboard of a USOIL edition: tiles, bias board, liquidity, news and the event table.

The same page as the XAU report (scripts/offchart_dashboard.py renders both). Every string the page
shows is a part of one line of the Markdown section 「11. 画面に表示した値」, written here from the frozen
facts, the code's scoring and the parent's checked text. A row whose shown value is exactly one fact's
display is also bound to that fact (usoil.bundle.check_bindings).

The existing page components are reused for WTI inventory, curve, COT and OVX.
Unsupported data stays explicitly missing. Yahoo/CNBC contract alignment is unverified.
"""
from __future__ import annotations

from datetime import timedelta
import re

from usoil import scoring
from usoil.common import JST, parse_time

SECTION = '## 11. 画面に表示した値'
WEEKDAY = '月火水木金土日'
USABLE = ('ok', 'provisional', 'partial', 'stale')
LADDER_RANGE_PCT = {'daily': 5.0, 'weekly': 10.0}
UP, DOWN = ('▲', '上向き', 'positive'), ('▼', '下向き', 'negative')
WEAK_UP, WEAK_DOWN = ('△', '上向き・弱', 'positive'), ('▽', '下向き・弱', 'negative')
FLAT, REF, NODATA = ('—', '中立', 'neutral'), ('—', '参考', 'neutral'), ('—', '判定外', 'neutral')
STATE_MARK = {'supportive': UP, 'adverse': DOWN, 'neutral': FLAT, 'mixed': ('—', '材料混在', 'neutral'),
              'unknown': ('—', 'データ不足', 'neutral')}
GROUPS = ('ドル・実金利', '在庫・供給', '原油固有イベント', 'COT・OVX（混雑・急変リスク）')
TILES = (('macro', 'マクロ', 'ドル・実金利'), ('inventory_supply', '在庫・供給', '在庫・供給'),
         ('oil_specific_event', '原油固有', '原油固有イベント'))
REASON_SHORT = {
    'same_day_quotes': 'DXYと実金利で判定', 'same_day_dxy_real10': 'DXYと実金利で判定',
    'common_observation_stale': '新しい共通観測日がない', 'inputs_missing': '入力が欠測',
    'inventory_direction': 'EIA在庫で判定', 'curve_only_weak': '限月構造のみ・弱',
    'inventory_and_curve_opposite': '在庫と限月構造が逆', 'below_thresholds': '閾値未満',
    'no_confirmed_oil_event': '算入する一次材料なし', 'pending_parent_selection': '親の選別前',
    'news_unavailable': 'ニュースが欠測', 'opposite_confirmed_events': '逆向きの材料が併存',
}
STATE_ICON = {'resting': '●', 'swept': '✓', 'broken': '×', 'forming': '◌'}
BEYOND_ICON = {'high': '↗', 'low': '↘'}
STATE_WORD = {'resting': '未到達', 'swept': '掃引済み', 'beyond': '抜けたまま', 'broken': '抜けて定着', 'forming': '形成中'}
IMPACT_MARK = {'supportive': UP, 'adverse': DOWN, 'mixed': ('—', '混在', 'neutral'), 'unknown': ('—', '不明', 'neutral')}
IMPORTANCE = {'critical': '重大', 'high': '高', 'medium': '中', 'low': '低'}
GROUP_WORD = {'macro': 'マクロ', 'inventory_supply': '在庫・供給', 'oil_specific_event': '原油固有', 'context_only': '文脈のみ'}
VERIFIED = {'primary_confirmed': '一次本文で確認', 'secondary_body_confirmed': '報道本文で確認', 'headline_only': '見出しのみ',
            'disputed': '異論あり', 'retracted': '撤回'}
CODE_VERIFIED = {'primary_body_retrieved': '公式の本文取得済み', 'secondary_body_retrieved': '本文取得済み',
                 'headline_only': '見出しのみ'}
MACRO_PUBLISHERS = ('Investing.com',)


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
    return f'{value:,.2f}'


def _usable(fact) -> bool:
    return bool(fact) and fact['status'] in USABLE and fact['value'] is not None


def _fresh(fact) -> bool:
    """Current enough to carry a direction (as usoil.facts._fresh): not stale and ok/provisional."""
    return _usable(fact) and fact['status'] in ('ok', 'provisional') and not fact.get('stale')


def _when(fact) -> str:
    return _hm(fact['observed_at']) if fact.get('observed_at') else _date_md(fact.get('observation_date') or '')


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
    """Direction for USOIL of a change against its threshold; ``invert`` when a rise is a headwind."""
    if value is None or abs(value) < cut:
        return FLAT
    up = (value > 0) != invert
    if weak:
        return WEAK_UP if up else WEAK_DOWN
    return UP if up else DOWN


def _material_rows(page, group, block, fields, *, rule, mark=REF):
    rows = []
    for key, label in fields:
        fact = page.fact(block.get(key))
        if fact:
            rows.append(_row(page, group, label, fact['display'], asof=_when(fact),
                             mark=mark if _fresh(fact) else NODATA, rule=rule, fact=fact,
                             note='古い値・採点外' if not _fresh(fact) else ''))
        else:
            rows.append(_row(page, group, label, '欠測・履歴不足', mark=NODATA, kind='text', rule=rule))
    return rows


def macro_rows(page, st: dict) -> list:
    macro = st.get('macro', {})
    mark = STATE_MARK.get(macro.get('status'), NODATA)
    rule = (f'DXY変化±{scoring.POLICY["macro"]["usd_pct"]:.2f}%と米10年実質利回り変化±'
            f'{scoring.POLICY["macro"]["real10_bp"]:g}bpがそろうと方向を与える。逆符号は混在')
    rows = _material_rows(page, GROUPS[0], macro.get('same_day') or {},
                          (('dxy', 'ドル指数DXY'), ('dxy_change', 'DXY前日比')), rule=rule, mark=mark)
    rows += _material_rows(page, GROUPS[0], macro,
        (('DFII10', '米10年実質利回り'), ('DFII10_change', '実質利回り変化')), rule=rule, mark=mark)
    rows += _material_rows(page, GROUPS[0], macro,
        (('DTWEXBGS', '米ドル広義指数'), ('VIXCLS', 'VIX'), ('SP500', 'S&P 500'), ('BAMLH0A0HYM2', 'HY OAS')),
        rule='米ドル広義指数はDXY欠測時の共通日比較に用いる。VIX・S&P 500・HY OASは表示のみ')
    return rows


def spot_rows(page, st: dict, ev: dict) -> list:
    mark = STATE_MARK.get(ev['groups']['inventory_supply']['state'], NODATA)
    inventory = st.get('inventory') or {}
    component = ev['groups']['inventory_supply'].get('components', {}).get('inventory', {})
    scored_key = 'change_4w' if component.get('window') == '4w' else 'change_crude'
    inventory_rule = (f'原油在庫±{scoring.INVENTORY_THRESHOLD:g}百万bbl、Weeklyは4週±'
                      f'{scoring.INVENTORY_4W_THRESHOLD:g}百万bbl。原油と製品が大きく逆方向なら混在')
    rows = []
    for key, label in (('change_crude', 'EIA商業原油在庫・週次変化'), ('change_products', '製品在庫・週次変化'),
                       ('change_4w', '原油在庫・4週累積変化')):
        rule = '製品在庫は原油と逆方向かを確かめる補助材料。単独の方向票・加点には使わない' if key == 'change_products' else inventory_rule
        rows += _material_rows(page, GROUPS[1], inventory, ((key, label),), rule=rule,
                               mark=mark if key == scored_key else REF)
    rows += _material_rows(page, GROUPS[1], st.get('curve') or {},
        (('spread_pct', 'WTI第1−第2限月差率'), ('second_third_pct', 'WTI第2−第3限月差率')),
        rule=f'第1−第2限月差率±{scoring.CURVE_THRESHOLD:g}%。在庫と同方向なら重複加点せず、逆方向なら混在。限月構造だけなら弱い方向')
    return rows


def ev_item(ev: dict, key: str) -> dict:
    return next((i for i in ev['items'] if i['key'] == key), {})


def event_rows(page, st: dict, ev: dict, analysis: dict) -> list:
    g = '原油固有イベント'
    group = ev['groups']['oil_specific_event']
    items = {n['id']: n for n in st.get('news', {}).get('items', [])}
    counted = [items[i]['title'] for i in group.get('components', {}).get('news_ids', []) if i in items]
    text = ('算入: ' + ' ／ '.join(counted)) if counted else '算入なし（一次本文で確認した重要な材料がない）'
    return [_row(page, g, 'ニュース（一次本文で確認）', text, kind='text', baseline='直近のニュース',
                 mark=STATE_MARK.get(group['state'], NODATA),
                 rule='親が選び、一次本文で確認した重要度high以上の材料だけを数える。二次報道と見出しだけの材料は数えない')]


def crowd_rows(page, st: dict, facts: list) -> list:
    rows = _material_rows(page, GROUPS[3], st.get('cot') or {},
        (('mm_net_ratio', 'WTI MMネット/建玉'), ('mm_net_ratio_percentile', 'COT履歴百分位'),
         ('oi_change_pct', 'COT建玉・週次変化'), ('mm_net_change', 'MMネット・週次変化')),
        rule='火曜基準の週次値。候補と同方向の90/10百分位かつ建玉+5%以上のみ混雑減点。10日超は採点外')
    rows += _material_rows(page, GROUPS[3], st.get('vol') or {},
        (('level', 'OVX'), ('percentile', 'OVX履歴百分位'), ('change_1d', 'OVX日次変化'), ('change_1w', 'OVX週次変化')),
        rule='90百分位以上かつ日次+10%以上は急変リスク。方向は示さず、日次変化はFRED履歴から算出')
    rows.append(_row(page, GROUPS[3], 'リテール', '未実装・採点不採用', mark=NODATA, kind='text',
                     rule='小標本のリテール比率を原油全体の方向へ読み替えない'))
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
    if key == 'oil_specific_event' and g['state'] in ('supportive', 'adverse', 'mixed'):
        return f'算入 {len(g["components"].get("news_ids", []))}件'
    return REASON_SHORT.get(g['reason_code'], g['reason_code'])


BIAS_LEGEND = ('向きはWTI原油への方向。マクロ（ドル・実金利）、在庫・供給（EIA・限月構造）、原油固有（一次本文確認済みニュース）'
               'の3群を統合し、逆向きの群があれば保留。COT混雑とOVXは確度を下げる警告で、独立した方向票ではありません。'
               '採用閾値とスコアは未校正です。欠測・古い値・履歴不足は補完せず採点外にします。')


def bias_figure(page, facts: dict, ev: dict, analysis: dict) -> dict:
    st, index = facts['state'], page.index
    items = macro_rows(page, st) + spot_rows(page, st, ev) + event_rows(page, st, ev, analysis) + crowd_rows(page, st, facts['facts'])
    verdicts = [_tile(page, key, label, ev, _group_short(key, ev, st, index)) for key, label, _ in TILES]
    verdicts.append(_total_tile(page, ev))
    page.line('バイアスの読み方: ' + BIAS_LEGEND)
    rules = list({i['label']: {'label': i['label'], 'rule': i['rule']} for i in items}.values())
    return {'id': 'bias', 'type': 'bias_board', 'title': 'バイアス', 'tiles_label': 'USOILへの向き（材料の群ごと）',
            'groups': [g for g in GROUPS if any(i['group'] == g for i in items)], 'verdicts': verdicts, 'items': items,
            'legend': BIAS_LEGEND, 'rules': rules,
            'sources': [{'label': 'CNBC', 'url': 'https://www.cnbc.com/quotes/@CL.1'},
                        {'label': 'EIA', 'url': 'https://www.eia.gov/petroleum/supply/weekly/'},
                        {'label': 'CFTC', 'url': 'https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm'},
                        {'label': 'Cboe', 'url': 'https://www.cboe.com/us/indices/dashboard/ovx/'}]}



# ------------------------------------------------------------------ liquidity

def _liq(page, kind, label, price_text, position, *, state, state_word, icon, price, pool='', touched='', target=''):
    distance = ''  # cross-contract differences are unverified
    quote = page.line(f'流動性｜{label}: {price_text}', pool, distance, state_word, touched, target)
    item = {'kind': kind, 'label': label, 'price_text': price_text, 'position': position, 'pool': pool, 'state': state,
            'state_word': state_word, 'icon': icon, 'touched': touched, 'distance_text': distance, 'source_quote': quote}
    if target:
        item['target'] = target
    return item


def liquidity_figure(page, facts: dict, dest: dict, mode: str, finalize_at) -> dict | None:
    st = facts['state']
    ref = page.fact(st.get('reference_price', {}).get('fact_id'))
    levels = st.get('levels') or {}
    if not ref and not levels.get('items'):
        return None
    items = []
    price = ref['value'] if ref else 0
    for lv in levels.get('items', []):
        fact = page.fact(lv.get('fact_id'))
        if not fact:
            continue
        display = fact['display']
        state = lv.get('state', 'reference')
        item = _liq(page, 'reference' if state == 'reference' else 'level', lv['name'], display, fact['value'],
                    state=state, state_word=lv['state_word'] + '・限月比較未確認', icon=STATE_ICON.get(state, '┄'), price=price)
        page.bind('liquidity', item, fact, key='price_text')
        items.append(item)
    if ref:
        provider = 'Yahoo CL=F・フォールバック' if st.get('reference_price', {}).get('fallback') else 'CNBC @CL.1・単一ソース'
        item = _liq(page, 'current', '参照価格', ref['display'], price, state='current', icon='▶', price=price,
                    state_word=provider)
        page.bind('liquidity', item, ref, key='price_text')
        items.append(item)
    for item in items:
        item['offscale'] = False
    items.sort(key=lambda i: -i['position'])
    note = ('Yahoo CL=F連続限月とCNBCフロント限月の整合は未確認。並記は参考で、距離・上下の目的地・最寄り未到達は未判定。'
            '板・清算位置・オプション建玉は未取得。')
    notices = [{'text': note, 'source_quote': page.line('流動性｜表示の注記: ' + note)}]
    cautions = [{'text': c['text'], 'source_quote': page.line('流動性｜注意: ' + c['text'])} for c in levels.get('cautions') or []]
    legend = ('前日・前週・前月はET日付バーから算出。前週は月〜金の5本がそろう場合だけ確定し、セッション高安は作りません。'
              '高安の到達状態はYahoo閉バー同士の観測で、CNBC価格との比較は未確認。ラウンドは参照価格±3%の$1刻み、$5刻みを主要とします。'
              '週間レンジ幅は観測できますが、参照価格のレンジ内位置は未判定。17:00 ETメンテ欠測と形成中バーは補完しません。')
    page.line('流動性の読み方: ' + legend)
    return {'id': 'liquidity', 'type': 'liquidity_map', 'title': '原油の参考水準', 'subtitle': '限月整合未確認・USD/bbl',
            'items': items, 'cautions': cautions, 'notices': notices, 'band': None, 'missing': [], 'legend': legend,
            'sources': [{'label': 'Yahoo Finance', 'url': 'https://finance.yahoo.com/quote/CL=F/'},
                        {'label': 'CNBC', 'url': 'https://www.cnbc.com/quotes/@CL.1'}]}


# ------------------------------------------------------------------ news

def incident_notes(st: dict, ev: dict) -> list:
    """Open incidents and manual releases (14 days). Shown in the news section, or as lead facts without news."""
    notes = [f'重大障害が継続中: {inc.get("title") or inc["incident_id"]}' for inc in ev.get('incidents_open') or []]
    for r in st.get('incidents', {}).get('recently_released') or []:
        notes.append(f'障害の手動解除（解除から14日間表示）: {r["incident_id"]}「{r.get("title") or ""}」・'
                     f'理由 {r.get("release_reason") or ""}・記録者 {r.get("released_by") or ""}')
    return notes


def news_figure(page, facts: dict, analysis: dict, ev: dict) -> dict | None:
    st = facts['state']
    news = st.get('news', {})
    if news.get('status') == 'missing' or not news.get('items'):
        return None
    assessed = {a['news_id']: (i, a) for i, a in enumerate(analysis['news_assessments'])}
    groups = ('原油・エネルギー', 'マクロ・地政学')
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
    cautions = [{'text': t, 'source_quote': page.line(f'ニュース｜注意: {t}')} for t in incident_notes(st, ev)]
    feeds = news.get('feeds') or []
    legend = (f'直近{news.get("lookback_hours", 36):g}時間のOilPrice・EIA・CFTC・Investing.comの見出し{news.get("candidate_count", 0)}件から'
              f'{len(news["items"])}件を選び（マクロ・地政学は最大5件）、親がUSOILへの効き方を評価した見出しを先に出しています。'
              '「本文取得済み」は記事本文を読めたもの、「見出しのみ」は本文を確認していないものです。Investing.comの見出しは本文を取得しません。'
              '向きは親の評価で、USOIL固有の群に数えるのは一次本文で確認した重要度high以上の材料だけです。'
              '原油の公表前後1分足は未取得で、分単位の反応は表示しません。'
              f'取得できたRSS: 一般 {news.get("feeds_ok", {}).get("general", 0)}/{sum(1 for x in feeds if not x.get("official"))}・'
              f'公式 {news.get("feeds_ok", {}).get("official", 0)}/{sum(1 for x in feeds if x.get("official"))}。')
    page.line('ニュースの読み方: ' + legend)
    return {'id': 'news', 'type': 'news_list', 'title': 'ニュース', 'groups': list(groups), 'items': items, 'tallies': tallies,
            'cautions': cautions, 'legend': legend,
            'sources': [{'label': 'OilPrice.com', 'url': 'https://oilprice.com/'},
                        {'label': 'Investing.com', 'url': 'https://www.investing.com/news/'}]}


# ------------------------------------------------------------------ events

def _short_name(name: str) -> str:
    """「PPI（Producer Price Index） 2026年9月分」 -> 「PPI 9月分」."""
    name = re.sub(r'（[A-Za-z][^）]*）', '', name)
    return re.sub(r'\s*\d{4}年', ' ', name).strip()


# ForexFactory titles of releases the official calendars do not list (others keep the English title).
FF_JA = {'Retail Sales m/m': '小売売上高（前月比）', 'Core Retail Sales m/m': 'コア小売売上高（前月比）',
         'Unemployment Claims': '新規失業保険申請件数', 'Philly Fed Manufacturing Index': 'フィラデルフィア連銀製造業指数',
         'Empire State Manufacturing Index': 'NY連銀製造業景況指数', 'ISM Manufacturing PMI': 'ISM製造業景況指数',
         'ISM Services PMI': 'ISM非製造業景況指数', 'JOLTS Job Openings': 'JOLTS求人件数',
         'ADP Non-Farm Employment Change': 'ADP雇用統計', 'CB Consumer Confidence': '消費者信頼感指数（CB）',
         'Prelim UoM Consumer Sentiment': 'ミシガン大消費者信頼感（速報）',
         'Revised UoM Consumer Sentiment': 'ミシガン大消費者信頼感（確報）',
         'Durable Goods Orders m/m': '耐久財受注（前月比）', 'Core Durable Goods Orders m/m': 'コア耐久財受注（前月比）',
         'FOMC Meeting Minutes': 'FOMC議事要旨', 'Building Permits': '住宅着工許可件数', 'Housing Starts': '住宅着工件数',
         'Existing Home Sales': '中古住宅販売件数', 'New Home Sales': '新築住宅販売件数',
         'Pending Home Sales m/m': '住宅販売保留指数（前月比）', 'Trade Balance': '貿易収支',
         'Industrial Production m/m': '鉱工業生産（前月比）', 'Prelim UoM Inflation Expectations': 'ミシガン大期待インフレ率（速報）', 'Crude Oil Inventories': 'EIA原油在庫', 'OPEC Meetings': 'OPEC会合'}


def _ff_name(title: str) -> str:
    speaker = re.match(r'^Fed Chair(?:man)? (\w+) Speaks$', title)
    if speaker:
        return f'FRB議長（{speaker.group(1)}）の発言'
    return FF_JA.get(title, title)


def event_figure(page, facts: dict, ev: dict, mode: str) -> dict:
    st = facts['state']
    as_of = ev['as_of']
    start, end = as_of - timedelta(hours=24), as_of + timedelta(days=7 if mode == 'daily' else 14)
    rows = []
    for e in st.get('events', []):
        when = parse_time(e['start_at'])
        if start <= when <= end and e['importance'] in ('critical', 'high', 'medium'):
            rows.append((when, e))
    for e in st.get('calendar_extra', []):
        when = parse_time(e['start_at'])
        if start <= when <= end:
            rows.append((when, dict(e, extra=True)))
    items, last_time = [], None
    for when, e in sorted(rows, key=lambda r: (r[0], 'expiry' in r[1])):
        date, time = _day(e['start_at']), _hm(when.isoformat())
        released = when <= as_of
        window = e.get('stop_window')
        memo = f'停止 {_hm(window["start_at"])}〜{_hm(window["end_at"])}' if window else ''
        if e.get('extra'):
            memo = '停止なし（ForexFactoryの予定）'
        status = '発表済み' if released else '予定'
        symbol, direction = '—', '結果は未収集' if released else '発表前'
        tier = 'A' if e['importance'] in ('critical', 'high') else 'B'
        cons = e.get('consensus') or e
        previous, forecast = cons.get('previous') or '—', cons.get('forecast') or '—'
        label = _ff_name(e['name']) if e.get('extra') else _short_name(e['name'])
        if e.get('consensus') and previous + forecast != '——':
            label = f'{label}（{e["consensus"]["label"]}）'
        quote = page.line(f'指標｜{date} {time} JST', f'Tier {tier}', label, status, f'前回 {previous}', f'予想 {forecast}',
                          '結果 —', '差 —', f'向き: {symbol} {direction}', memo)
        items.append({'date': date, 'time': time, 'tier': tier, 'label': label, 'status': status,
                      'previous': previous, 'forecast': forecast, 'actual': '—', 'diff': '—', 'direction': direction, 'symbol': symbol,
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
    legend = ('時刻はJST。ForexFactory週間予定からEIA・米重要指標を表示し、原油の在庫実績はEIA CSVを別途参照します。'
              'EIAは掲載時刻優先、無ければ水曜10:30 ETの暫定時刻。停止はEIA前15分〜後30分、OPEC暫定前2時間〜後6時間、'
              'COT前15分〜後30分。未取得の結果は空欄を補完せず「—」。次週範囲が未確認ならデータ保留です。')
    page.line('指標の読み方: ' + legend)
    return {'id': 'calendar', 'type': 'event_table', 'title': 'EIA・原油イベントと米重要指標', 'direction_header': 'USOILへの向き',
            'items': items, 'cautions': cautions, 'legend': legend,
            'sources': [{'label': 'BLS', 'url': 'https://www.bls.gov/schedule/'},
                        {'label': 'Fed', 'url': 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'},
                        {'label': 'ForexFactory', 'url': 'https://www.forexfactory.com/calendar'},
                        {'label': 'EIA', 'url': 'https://www.eia.gov/petroleum/supply/weekly/'}]}

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
    if ev.get('market_closed_note'):
        text = '週末注記: 金曜終値基準・停止ゲート変更なし'
        chips.append({'text': text, 'source_quote': page.line(text)})
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
    else:  # no news section: the incident notices still show, as lead facts
        head['chips'] += [{'text': t, 'source_quote': page.line(t)} for t in incident_notes(facts['state'], ev)]
    figures.append(event_figure(page, facts, ev, mode))
    doc.add('')
    summary = {'eyebrow': f'USOIL {mode.upper()}', 'short_title': 'チャート外分析', 'as_of_label': as_of_label,
               'note': '向き・価格・指標は原稿と収集データの引用です。売買の確率や到達を示すものではありません。', **head}
    return {'summary': summary, 'figures': figures, 'bindings': page.bindings, 'title': title}
