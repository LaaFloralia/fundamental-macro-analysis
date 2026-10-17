"""Deterministic oil policy: three directional groups, eight fixed score items and fail-closed gates."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
from usoil.common import JST, parse_time
UTC = timezone.utc
NY = ZoneInfo('America/New_York')
POLICY_VERSION = 'usoil-offchart-1.0'
GROUPS = ('macro', 'inventory_supply', 'oil_specific_event')
GROUP_LABELS = {'macro':'マクロ（ドル・実金利）','inventory_supply':'需給（EIA在庫・限月構造）',
                'oil_specific_event':'原油固有の一次イベント'}
ITEM_KEYS = ('macro_alignment','inventory_alignment','oil_event_alignment','curve_alignment',
             'cot_crowding','vol_tail_risk','data_quality','event_risk')
ITEM_LABELS = dict(zip(ITEM_KEYS,('ドル・実金利','EIA在庫','原油固有イベント','限月構造','COT混雑','OVXテールリスク',
                                 'データ充足','イベント近接')))
ITEM_GROUPS = dict(zip(ITEM_KEYS,('macro','inventory_supply','oil_specific_event','inventory_supply','cot','vol','quality','event_risk')))
BUNDLES = ('price','calendar','macro','inventory','curve','cot','vol','news')
BUNDLE_LABELS = dict(zip(BUNDLES,('参照価格','カレンダー','マクロ','EIA在庫','限月構造','COT','OVX','ニュース')))
BAND_CONFIDENCE = {'High':0.75,'Med':0.60,'Med-cautious':0.40,'Low':0.20}
BAND_ORDER = ('High','Med','Med-cautious','Low')
STRENGTH_BIAS = {'低':0.25,'中':0.5,'高':0.75}
INVENTORY_THRESHOLD = 4.0
INVENTORY_4W_THRESHOLD = 8.0
CURVE_THRESHOLD = 0.5
OI_CROWDED_PCT = 5.0
EVENT_WINDOW_HOURS = {'daily':36,'weekly':168}
VALIDITY_HOURS = {'daily':4,'weekly':24}
SIGN = {'supportive':1,'adverse':-1}
SCORABLE = ('ok','provisional')
POLICY = {
 'version':POLICY_VERSION,'macro':{'usd_pct':0.30,'real10_bp':5,'fallback':'fred_common_dates_max_4_days'},
 'inventory':{'crude_1w_million_bbl':4.0,'products_opposite_million_bbl':4.0,'crude_4w_million_bbl':8.0,'fresh_days':8},
 'curve':{'spread_pct':0.5,'fresh_hours':6,'inventory_dedup':True},
 'cot':{'net_over_oi_percentile':[90,10],'weeks':156,'min_weeks':104,'oi_wow_pct':5.0,'fresh_days':10,
        'net_wow_contracts_display_only':20000},
 'vol':{'percentile':90,'business_days':252,'min_days':60,'change_1d_pct':10,'fresh_seconds':900,'directionless':True},
 'bands':{'High':[5,3],'Med':[4,2],'Med-cautious':[3,2]}, 'confidence':BAND_CONFIDENCE,
 'caps':{'withheld':0.20,'reference_fallback':0.60,'manual_calendar_cache':0.60,'unassessed_cot_or_vol':0.60},
 'stop_windows':{'eia_release':[-15,30],'cot_release':[-15,30],'opec_meeting':[-120,360],
                 'macro_release':[-15,30],'fomc_statement':[-30,90],'fomc_press':[-15,'statement+120']},
 'validity_hours':VALIDITY_HOURS}
POLICY_HASH = hashlib.sha256(json.dumps(POLICY,sort_keys=True).encode()).hexdigest()


def _f(index, fact_id):
    return index.get(fact_id) if fact_id else None


def scorable(fact):
    return bool(fact) and fact['status'] in SCORABLE and not fact.get('stale') and fact.get('value') is not None


def _val(index, fact_id):
    f = _f(index,fact_id)
    return f['value'] if scorable(f) else None


def bundles(facts):
    st, index = facts['state'], {f['fact_id']:f for f in facts['facts']}
    ok = lambda key,field: scorable(_f(index,st.get(key,{}).get(field)))
    news = st.get('news',{}).get('feeds_ok') or {}
    return {'price':ok('reference_price','fact_id'),'calendar':st.get('calendar',{}).get('next_24h')=='ok',
            'macro':st.get('macro',{}).get('status') not in (None,'unknown'),
            'inventory':ok('inventory','change_crude'),'curve':ok('curve','spread_pct'),
            'cot':ok('cot','mm_net_ratio') and ok('cot','mm_net_ratio_percentile'),
            'vol':ok('vol','level') and ok('vol','percentile'),
            'news':news.get('general',0)>=2 and news.get('official',0)>=1}


def hard_invalid(facts, as_of, groups=None):
    st, reasons = facts['state'], []
    index = {f['fact_id']:f for f in facts['facts']}
    if st.get('reference_price',{}).get('status') != 'ok' or not scorable(_f(index,st.get('reference_price',{}).get('fact_id'))):
        reasons.append('reference_price_unavailable')
    if st.get('calendar',{}).get('next_24h') != 'ok':
        reasons.append('calendar_coverage_unknown')
    if groups and all(g['state']=='unknown' for g in groups.values()):
        reasons.append('all_groups_unknown')
    for f in facts['facts']:
        if any(f.get(k) and parse_time(f[k]) > as_of+timedelta(seconds=5) for k in ('observed_at','period_end','available_at')) \
                or f.get('observation_date') and f['observation_date'] > as_of.date().isoformat():
            reasons.append('future_observation')
            break
    if any(f['status']=='invalid' for f in facts['facts']):
        reasons.append('invalid_fact')
    if st.get('carry',{}).get('status')=='invalid':
        reasons.append('carry_state_invalid')
    return reasons


def macro_group(facts):
    m = facts['state'].get('macro',{})
    state = m.get('status','unknown')
    return {'state':state,'direction':SIGN.get(state,0),'reason_code':m.get('reason','macro_unknown'),
            'fact_ids':m.get('change_fact_ids',[]),'components':{k:m.get(k) for k in ('window','common_date','previous_date')}}


def inventory_component(facts, mode='daily'):
    st, idx = facts['state'].get('inventory',{}), {f['fact_id']:f for f in facts['facts']}
    single, products, four = (_f(idx,st.get(k)) for k in ('change_crude','change_products','change_4w'))
    chosen, threshold, window = (four,8.0,'4w') if mode=='weekly' and scorable(four) else (single,4.0,'1w')
    ids = [f['fact_id'] for f in (chosen,products) if f]
    if not scorable(chosen):
        return {'state':'unknown','direction':0,'reason_code':'eia_wpsr_missing_or_stale','fact_ids':ids,'window':window}
    value = chosen['value']
    state = 'supportive' if value <= -threshold else 'adverse' if value >= threshold else 'neutral'
    # The adopted four-week trend takes priority over a one-week products conflict when it exists.
    if window=='1w' and scorable(products) and abs(value)>=4 and abs(products['value'])>=4 and value*products['value']<0:
        state = 'mixed'
    return {'state':state,'direction':SIGN.get(state,0),'reason_code':f'inventory_{window}_{state}','fact_ids':ids,'window':window}


def curve_component(facts):
    fact = _f({f['fact_id']:f for f in facts['facts']},facts['state'].get('curve',{}).get('spread_pct'))
    ids = [fact['fact_id']] if fact else []
    if not scorable(fact):
        return {'state':'unknown','direction':0,'reason_code':'curve_missing_or_stale','fact_ids':ids}
    value = fact['value']
    state = 'supportive' if value>=0.5 else 'adverse' if value<=-0.5 else 'neutral'
    return {'state':state,'direction':SIGN.get(state,0),'reason_code':f'curve_{state}','fact_ids':ids}


def inventory_group(facts, mode='daily'):
    inv, curve = inventory_component(facts,mode), curve_component(facts)
    comps, ids = {'inventory':inv,'curve':curve}, inv['fact_ids']+curve['fact_ids']
    if inv['direction'] and curve['direction'] and inv['direction'] != curve['direction']:
        state,reason = 'mixed','inventory_and_curve_opposite'
    elif inv['state']=='mixed':
        state,reason = 'mixed','inventory_products_opposite'
    elif inv['direction']:
        state,reason = inv['state'],'inventory_direction'
    elif curve['direction']:
        # Parent resolution: section 2.2's three-group algorithm wins over section 6's inconsistent
        # 'maximum two' prose. A curve-only direction still counts as this one group; quality penalises missing EIA.
        state,reason = curve['state'],'curve_only_weak'
    elif inv['state']=='unknown':
        state,reason = 'unknown',inv['reason_code']
    else:
        state,reason = 'neutral','below_thresholds'
    return {'state':state,'direction':SIGN.get(state,0),'reason_code':reason,'fact_ids':ids,'components':comps}


def _item_ids(item: dict) -> set:
    ids = {f'url:{item["url"]}'} if item.get('url') else set()
    if item.get('event_cluster_id'):
        ids.add(f'cluster:{item["event_cluster_id"]}')
    sha = (item.get('body') or {}).get('text_sha256')
    if sha:
        ids.add(f'body:{sha}')
    return ids


def known_match(item: dict, assessment: dict, known: list[dict]) -> dict | None:
    """The adopted known event this item re-reports: shared URL / code cluster / primary body hash,
    or a ``known_event_ids`` link set by the parent (different URL or updated headline). A URL or body
    match beats a cluster or link match; ties go to the latest ``first_known_at`` (R2-03)."""
    from usoil import carry
    return carry.best_match(_item_ids(item), assessment.get('known_event_ids'), known)


def eligible_news(facts: dict, assessments: list | None, mode: str, as_of: datetime, carried) -> list[dict]:
    """Parent-selected clusters that pass the code's factual conditions (design 8.2).

    Known events (adopted earlier, ``state.news.carried``; R-05) never extend the scoring period:
    the window is measured from ``first_known_at``, whatever URL or headline re-reports it. A follow-up
    counts as new only when the parent marks ``follow_up_new_facts`` and the code sees a primary body
    that differs from every known body of that event and was published after it became known.
    ``carried`` as a set of URLs (legacy unit-test input) excludes those items outright.
    """
    items = {n['id']: n for n in facts['state'].get('news', {}).get('items', [])}
    known = facts['state'].get('news', {}).get('carried') or []
    if isinstance(carried, list):
        known = carried
    legacy = carried if isinstance(carried, (set, frozenset)) else set()
    window = timedelta(hours=EVENT_WINDOW_HOURS[mode])
    out = []
    for a in assessments or []:
        item = items.get(a['news_id'])
        if item is None or a['group_id'] != 'oil_specific_event' or a['impact'] not in ('supportive', 'adverse'):
            continue
        if a['importance'] not in ('high', 'critical') or a['verification'] != 'primary_confirmed':
            continue
        if item['code_verification'] != 'primary_body_retrieved':
            continue
        if item.get('url') in legacy:
            continue
        published = parse_time(item['published_at'])
        effective, carry = published, None
        match = known_match(item, a, known)
        if match is not None:
            from usoil import carry as carried_state
            first = parse_time(match['first_known_at'])
            if carried_state.is_follow_up(item, a, match, known):
                carry = {'known_id': match['known_id'], 'kind': 'follow_up_new_facts'}
            else:
                effective = min(published, first)
                carry = {'known_id': match['known_id'], 'kind': 'carry_forward', 'first_known_at': match['first_known_at']}
        if not (as_of - window <= effective and published <= as_of):
            continue
        out.append({**a, 'url': item.get('url'), 'carry': carry})
    return out


def event_group(facts: dict, assessments: list | None, mode: str, as_of: datetime, carried: set) -> dict:
    news = facts['state'].get('news', {})
    if news.get('status') == 'missing':
        return {'state': 'unknown', 'direction': 0, 'reason_code': 'news_unavailable', 'fact_ids': [], 'components': {}}
    if assessments is None:
        return {'state': 'neutral', 'direction': 0, 'reason_code': 'pending_parent_selection', 'fact_ids': [],
                'components': {'clusters': []}}
    chosen = eligible_news(facts, assessments, mode, as_of, carried)
    clusters: dict[str, set] = {}
    for a in chosen:
        clusters.setdefault(a['event_cluster_id'], set()).add(a['impact'])
    impacts = set().union(*clusters.values()) if clusters else set()
    ids = sorted({x for a in chosen for x in a.get('fact_ids', [])})
    comps = {'clusters': sorted(clusters), 'news_ids': [a['news_id'] for a in chosen],
             'carry_forward': [{'news_id': a['news_id'], **a['carry']} for a in chosen if a.get('carry')]}
    if not impacts:
        return {'state': 'neutral', 'direction': 0, 'reason_code': 'no_confirmed_oil_event', 'fact_ids': ids, 'components': comps}
    if impacts == {'supportive'}:
        return {'state': 'supportive', 'direction': 1, 'reason_code': 'confirmed_supportive_event', 'fact_ids': ids,
                'components': comps}
    if impacts == {'adverse'}:
        return {'state': 'adverse', 'direction': -1, 'reason_code': 'confirmed_adverse_event', 'fact_ids': ids,
                'components': comps}
    return {'state': 'mixed', 'direction': 0, 'reason_code': 'opposite_confirmed_events', 'fact_ids': ids, 'components': comps}


def incident_id(item: dict) -> str:
    """Identity of one incident publication: code cluster (normalised title), URL and ``published_at`` as stored on
    the item. The identical publication keeps its id across editions (a closed incident stays closed, R3-02); the
    same title and URL with another publication time is a new incident and holds again (R3-02b, fail closed)."""
    key = f'{item.get("event_cluster_id")}|{item.get("url")}|{item.get("published_at")}'
    return 'inc-' + hashlib.sha256(key.encode()).hexdigest()[:12]


def new_incidents(facts: dict, assessments: list | None) -> list[dict]:
    """Critical adverse/mixed incidents confirmed from a primary body in this edition (design 7.4)."""
    items = {n['id']: n for n in facts['state'].get('news', {}).get('items', [])}
    out = []
    for a in assessments or []:
        item = items.get(a['news_id'])
        if item and a['importance'] == 'critical' and a['verification'] == 'primary_confirmed' \
                and item['code_verification'] == 'primary_body_retrieved' and a['impact'] in ('adverse', 'mixed'):
            ident = incident_id(item)
            if any(x['incident_id'] == ident for x in out):
                continue
            out.append({'incident_id': ident, 'news_id': item['id'], 'event_cluster_id': item.get('event_cluster_id'),
                        'url': item.get('url'), 'title': item.get('title', '')[:200], 'published_at': item['published_at'],
                        'affected_source_ids': sorted(set(a.get('affected_source_ids') or []))})
    return out


def incident_status(facts: dict, assessments: list | None, recoveries: list | None = None) -> dict:
    """Open incidents = this edition's new ones + persisted open ones (R-04), minus validly released ones.

    A persisted incident is released only by ``recoveries`` entries that pass ``carry.recovery_problems``
    (official primary-body recovery after the incident + fresh data for every affected source).
    """
    from usoil import carry
    current = new_incidents(facts, assessments)
    persisted = facts['state'].get('incidents', {}).get('open', [])
    by_id = {r['incident_id']: r for r in recoveries or []}
    released, rejected, still = [], [], []
    for inc in persisted:
        rec = by_id.get(inc['incident_id'])
        if rec is None:
            still.append(inc)
            continue
        problems = carry.recovery_problems(inc, rec, facts)
        if problems:
            rejected.append({'incident_id': inc['incident_id'], 'problems': problems})
            still.append(inc)
        else:
            released.append(dict(rec))
    open_ids = {i['incident_id'] for i in still}
    # An incident already closed (recovery or owner's manual release) never re-opens from the same article (R3-02).
    closed = set(facts['state'].get('incidents', {}).get('released_ids') or []) | {r['incident_id'] for r in released}
    new = [i for i in current if i['incident_id'] not in open_ids and i['incident_id'] not in closed]
    return {'new': new, 'open': still + new, 'released': released, 'rejected': rejected}


def critical_incident(facts: dict, assessments: list | None, recoveries: list | None = None) -> bool:
    return bool(incident_status(facts, assessments, recoveries)['open'])


def windows(facts: dict) -> list[dict]:
    raw = [dict(e['stop_window']) for e in facts['state'].get('events', []) if e.get('stop_window')]
    raw.sort(key=lambda w: w['start_at'])
    merged = []
    for w in raw:
        if merged and parse_time(w['start_at']) <= parse_time(merged[-1]['end_at']):
            last = merged[-1]
            if parse_time(w['end_at']) > parse_time(last['end_at']):
                last['end_at'] = w['end_at']
            last['event_ids'] = sorted(set(last['event_ids']) | set(w['event_ids']))
            last['rule_id'] = last['rule_id'] if last['rule_id'] == w['rule_id'] else 'merged_windows'
        else:
            merged.append(w)
    return merged



def cot_item(facts, candidate=0):
    st,idx = facts['state'].get('cot',{}),{f['fact_id']:f for f in facts['facts']}
    keys = ('mm_net_ratio','mm_net_ratio_percentile','oi_change_pct')
    ids = [st[k] for k in keys if st.get(k)]
    values = [_val(idx,st.get(k)) for k in keys]
    if any(v is None for v in values):
        return {'points':0,'status':'unassessed','reason_code':'cot_missing_stale_or_warming_up','fact_ids':ids}
    if not candidate:
        return {'points':0,'status':'direction_unset','reason_code':'direction_unset','fact_ids':ids}
    _,per,growth = values
    hit = (per>=90 if candidate>0 else per<=10) and growth>=5
    return {'points':-1 if hit else 0,'status':'warning' if hit else 'neutral',
            'reason_code':'same_side_cot_crowding_with_oi_growth' if hit else 'cot_below_threshold','fact_ids':ids}


def vol_item(facts, candidate=0):
    st,idx = facts['state'].get('vol',{}),{f['fact_id']:f for f in facts['facts']}
    keys = ('level','percentile','change_1d')
    ids = [st[k] for k in keys if st.get(k)]
    values = [_val(idx,st.get(k)) for k in keys]
    if any(v is None for v in values):
        return {'points':0,'status':'unassessed','reason_code':'ovx_missing_stale_or_warming_up','fact_ids':ids}
    hit = values[1]>=90 and values[2]>=10
    return {'points':-1 if hit else 0,'status':'warning' if hit else 'neutral',
            'reason_code':'ovx_extreme_and_rising' if hit else 'ovx_below_threshold','fact_ids':ids}


def evaluate(facts: dict, *, mode: str, assessments: list | None = None, finalize_at: datetime | None = None,
             carried=None, recoveries: list | None = None) -> dict:
    """Full decision state. ``assessments`` None = before the parent's selection (preview).

    ``carried`` defaults to the known events in ``state.news.carried`` (R-05); ``recoveries`` are the
    parent's ``incident_recoveries`` checked against persisted incidents (R-04).
    """
    as_of = parse_time(facts['as_of']).astimezone(UTC)
    finalize_at = (finalize_at or as_of).astimezone(UTC)
    groups = {'macro': macro_group(facts), 'inventory_supply': inventory_group(facts, mode),
              'oil_specific_event': event_group(facts, assessments, mode, as_of, carried)}
    present = bundles(facts)
    invalid = hard_invalid(facts, as_of, groups)
    directions = [g['direction'] for g in groups.values() if g['direction']]
    if invalid:
        strength, status, reason_label, candidate = '保留', 'withheld', 'データ保留', 0
        reason_codes = invalid
    elif 1 in directions and -1 in directions:
        strength, status, reason_label, candidate = '保留', 'withheld', '材料混在', 0
        reason_codes = ['opposite_groups']
    elif not directions:
        unknown = any(g['state'] == 'unknown' for g in groups.values())
        strength, status, candidate = '保留', 'withheld', 0
        reason_label = 'データ不足（unknown）' if unknown else '根拠なし（閾値未満）'
        reason_codes = ['group_unknown' if unknown else 'below_thresholds']
    else:
        candidate = directions[0]
        strength = {1: '低', 2: '中', 3: '高'}[len(directions)]
        status, reason_label = 'available', None
        reason_codes = [f'{len(directions)}_group_{"up" if candidate > 0 else "down"}']
    # Fixed 8 items.
    items = []
    inv, curve = groups['inventory_supply']['components']['inventory'], groups['inventory_supply']['components']['curve']
    opposite_supply = inv.get('direction') and curve.get('direction') and inv['direction'] != curve['direction']

    def align(key, direction, ids, weight, dedup=False, mixed=False):
        if not candidate:
            return {'key': key, 'points': 0, 'status': 'direction_unset', 'reason_code': 'direction_unset',
                    'fact_ids': ids, 'group_id': ITEM_GROUPS[key]}
        if dedup:
            return {'key': key, 'points': 0, 'status': 'deduplicated', 'reason_code': 'inventory_component_active',
                    'fact_ids': ids, 'group_id': ITEM_GROUPS[key]}
        if mixed:
            return {'key': key, 'points': 0, 'status': 'mixed', 'reason_code': 'inventory_and_curve_opposite',
                    'fact_ids': ids, 'group_id': ITEM_GROUPS[key]}
        if direction == candidate:
            return {'key': key, 'points': weight, 'status': 'aligned', 'reason_code': 'same_direction', 'fact_ids': ids,
                    'group_id': ITEM_GROUPS[key]}
        if direction == -candidate:
            return {'key': key, 'points': -weight, 'status': 'opposed', 'reason_code': 'opposite_direction',
                    'fact_ids': ids, 'group_id': ITEM_GROUPS[key]}
        return {'key': key, 'points': 0, 'status': 'neutral', 'reason_code': 'neutral_mixed_or_unknown', 'fact_ids': ids,
                'group_id': ITEM_GROUPS[key]}

    items.append(align('macro_alignment', groups['macro']['direction'], groups['macro']['fact_ids'], 2))
    items.append(align('inventory_alignment', inv.get('direction', 0), inv.get('fact_ids', []), 2,
                       mixed=bool(opposite_supply)))
    items.append(align('oil_event_alignment', groups['oil_specific_event']['direction'],
                       groups['oil_specific_event']['fact_ids'], 2))
    items.append(align('curve_alignment', curve.get('direction', 0), curve.get('fact_ids', []), 1,
                       dedup=bool(inv.get('direction')) and not opposite_supply, mixed=bool(opposite_supply)))
    items.append({'key': 'cot_crowding', 'group_id': 'cot', **cot_item(facts, candidate)})
    # OVX measures a directionless tail risk and is evaluated even when the candidate is unset.
    items.append({'key': 'vol_tail_risk', 'group_id': 'vol', **vol_item(facts, candidate)})
    count = sum(present.values())
    dq = 0 if count == 8 else -1 if count == 7 else -2
    items.append({'key': 'data_quality', 'points': dq, 'status': 'warning' if dq else 'neutral',
                  'reason_code': f'bundles_{count}_of_8', 'fact_ids': [], 'group_id': 'quality'})
    merged = windows(facts)
    active = [w for w in merged if parse_time(w['start_at']) <= as_of <= parse_time(w['end_at'])]
    incidents = incident_status(facts, assessments, recoveries)
    incident = bool(incidents['open'])
    high_soon = [e for e in facts['state'].get('events', []) if e['importance'] in ('high', 'critical')
                 and as_of < parse_time(e['start_at']) <= as_of + timedelta(hours=2)]
    er = -2 if active or incident else -1 if high_soon else 0
    items.append({'key': 'event_risk', 'points': er, 'status': 'warning' if er else 'neutral',
                  'reason_code': 'in_stop_window' if active else 'critical_incident' if incident else
                  'high_event_within_2h' if high_soon else 'no_event_pressure', 'fact_ids': [], 'group_id': 'event_risk'})
    total = sum(i['points'] for i in items)
    supporting = len(directions) if candidate else 0
    if total >= 5 and supporting == 3:
        band = 'High'
    elif total >= 4 and supporting >= 2:
        band = 'Med'
    elif total == 3 and supporting >= 2:
        band = 'Med-cautious'
    else:
        band = 'Low'
    if strength == '低':
        band = 'Low'
    confidence = BAND_CONFIDENCE[band]
    caps = []
    if status == 'withheld':
        caps.append('withheld_max_0.20')
        confidence = min(confidence, 0.20)
    if facts['state'].get('reference_price', {}).get('fallback'):
        caps.append('reference_fallback_max_0.60')
        confidence = min(confidence, 0.60)
    if facts['state'].get('calendar', {}).get('manual_cache'):
        caps.append('manual_calendar_cache_max_0.60')
        confidence = min(confidence, 0.60)
    if any(i['status'] == 'unassessed' for i in items if i['key'] in ('cot_crowding', 'vol_tail_risk')):
        caps.append('unassessed_cot_or_vol_max_0.60')
        confidence = min(confidence, 0.60)
    if invalid:
        caps.append('hard_invalid_zero')
        confidence = 0.0
    # Lower the band to the highest one whose ordinal confidence fits under the cap (design 7.3).
    allowed = [name for name in BAND_ORDER[BAND_ORDER.index(band):] if BAND_CONFIDENCE[name] <= confidence + 1e-9]
    band = allowed[0] if allowed else 'Low'
    confidence = 0.0 if invalid else min(confidence, BAND_CONFIDENCE[band])
    bias = 0.0 if status == 'withheld' else candidate * STRENGTH_BIAS[strength]
    # Validity and gate.
    next_high = [parse_time(e['start_at']) for e in facts['state'].get('events', [])
                 if e['importance'] in ('high', 'critical') and parse_time(e['start_at']) > as_of]
    valid_until = min([as_of + timedelta(hours=VALIDITY_HOURS[mode])] + next_high)
    if incident:
        gate, gate_codes = 'incident_hold', (['critical_incident_confirmed'] if incidents['new'] else []) + (
            ['incident_unresolved'] if len(incidents['open']) > len(incidents['new']) else [])
    elif invalid:
        gate, gate_codes = 'data_hold', invalid
    elif active:
        gate, gate_codes = 'scheduled_pause', [w['rule_id'] for w in active]
    elif finalize_at > valid_until:
        gate, gate_codes = 'expired', ['valid_until_passed_at_finalize']
    else:
        gate, gate_codes = 'clear', []
    upcoming = [w for w in merged if as_of < parse_time(w['start_at']) <= as_of + timedelta(hours=24)]
    recheck = min([parse_time(w['end_at']) for w in active] + [parse_time(w['start_at']) for w in upcoming] + [valid_until])
    quality_fail = bool(invalid)
    no_trade = band == 'Low' or gate != 'clear' or quality_fail or status == 'withheld'
    reason = None
    if no_trade:
        if gate == 'incident_hold':
            reason = '障害停止: 一次本文で確認した重大な障害・侵害があり、公式復旧と新しいデータの確認まで停止'
        elif gate == 'data_hold':
            reason = 'データ保留: ' + '、'.join(REASON_TEXT.get(c, c) for c in invalid)
        elif gate == 'scheduled_pause':
            names = [e['name'] for e in facts['state'].get('events', []) if any(e['id'] in w['event_ids'] for w in active)]
            reason = 'イベント停止: ' + '、'.join(names) + 'の停止時間内'
        elif gate == 'expired':
            reason = '期限切れ: 判断の有効期限を過ぎて完成した'
        elif status == 'withheld':
            reason = f'方向付与保留（{reason_label}）: ' + '・'.join(
                f'{GROUP_LABELS[k]}={STATE_TEXT[g["state"]]}' for k, g in groups.items())
        else:
            reason = f'方向付与保留（方向の根拠が弱い）: 確度{strength}・整合度{total}点・{band}'
    risk = risk_events(facts, as_of)
    local = as_of.astimezone(NY)
    market_closed_note = mode == 'weekly' and (local.weekday() == 5 or (local.weekday() == 4 and local.hour >= 17)
                                              or (local.weekday() == 6 and local.hour < 18))
    return {
        'policy_version': POLICY_VERSION, 'policy_hash': POLICY_HASH, 'as_of': as_of, 'finalize_at': finalize_at,
        'groups': groups, 'bundles': present, 'coverage_present': count, 'coverage_required': 8,
        'hard_invalid': invalid, 'direction_status': status, 'direction_strength': strength,
        'direction_reason_label': reason_label, 'direction_reason_codes': reason_codes,
        'candidate_direction': 'up' if candidate > 0 else 'down' if candidate < 0 else 'none',
        'items': items, 'total': total, 'band': band, 'confidence': round(confidence, 3), 'caps': caps,
        'bias': bias, 'valid_until': valid_until, 'trade_gate': {
            'status': gate, 'reason_codes': gate_codes, 'active_windows': active, 'upcoming_windows': upcoming,
            'recheck_at': recheck},
        'no_trade': no_trade, 'no_trade_reason': reason, 'risk_events_next_24h': risk,
        'coverage_ratio': round(count / 8, 3), 'incident': incident, 'market_closed_note': market_closed_note,
        'incidents_open': incidents['open'], 'incidents_new': incidents['new'],
        'incidents_released': incidents['released'], 'incidents_rejected': incidents['rejected'],
    }


REASON_TEXT = {'reference_price_unavailable':'参照価格が成立しない','calendar_coverage_unknown':'カレンダーの次24時間が未確認',
               'all_groups_unknown':'方向3群がすべて未確認','future_observation':'未来時刻の観測がある',
               'invalid_fact':'不正な値がある','carry_state_invalid':'採用済みニュース・未解決障害の記録が読めない'}
STATE_TEXT = {'supportive':'上向き','adverse':'下向き','neutral':'閾値未満','mixed':'材料混在','unknown':'データ不足'}
SOURCE_LABELS = {'forexfactory_week':'ForexFactory','fed_calendar':'Fed公式','bea_calendar':'BEA公式',
                 'bls_schedule_cache':'BLS予定キャッシュ','manual_cache':'慣例時刻（未確認）'}
STATUS_LABELS = {'confirmed':'確認済み','tentative':'時刻未確認・慣例'}


def risk_events(facts,as_of):
    out=[]
    for e in facts['state'].get('events',[]):
        t=parse_time(e['start_at'])
        if (e['importance'] in ('high','critical') or e.get('category') in ('eia_release','cot_release','opec_meeting')) and as_of<t<=as_of+timedelta(hours=24):
            out.append(f'{t.astimezone(JST):%Y-%m-%d %H:%M} JST {e["name"]}（{SOURCE_LABELS.get(e["source_id"],e["source_id"])}、{STATUS_LABELS.get(e["status"],e["status"])}）')
    if facts['state'].get('calendar',{}).get('next_24h')!='ok':
        out.append(f'{as_of.astimezone(JST):%Y-%m-%d %H:%M} JST 次24時間の予定が未確認')
    return out
