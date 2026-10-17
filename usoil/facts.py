"""Deterministic USOIL facts from one frozen collection (design 5; P5, P8).

Every number the report shows is a fact here. Observed facts cite one source
response (``raw_sha256``); derived facts use ``source_id='computed'``,
``raw_sha256=None``, ``input_manifest_sha256`` and ``source_fact_ids``.
Missing values stay ``None`` with a reason; nothing is filled with zero.

``state`` carries code-owned intermediate results (reference price, groups'
inputs, walls, option clusters, events, news) that scoring and the report
read; it never contains parent text.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import math
import re
import statistics
from zoneinfo import ZoneInfo

from usoil import SYMBOL
from usoil.common import JST, digest, parse_time
from usoil import levels as levels_mod, us_calendar
from usoil.history import History, comparison_stats, describe, describe_window
from usoil.text import md_safe

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
SCHEMA_VERSION = 'usoil-facts-1.0'
METHOD_VERSION = '1.0'
SAFE_ID = re.compile(r'[^a-z0-9_.:-]+')
QUOTE_STALE = 6 * 3600
SAME_DAY_STALE = 6 * 3600
OPTIONS_STALE = 900
COT_MIN_WEEKS = 104
LEVEL_VALID_AFTER_BAR = timedelta(hours=2)
ROLL_NOTE = '参照価格はCNBC @CL.1。OHLCはYahoo CL=Fの連続限月でロール混在・限月比較は未確認。FRED現物を参照価格・採点に使わない。'


# ----------------------------------------------------------------- helpers

def z(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = parse_time(value)
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def stamp_key(value) -> str:
    if value is None:
        return 'na'
    if isinstance(value, (date,)) and not isinstance(value, datetime):
        return value.strftime('%Y%m%d')
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return value.replace('-', '')
    t = parse_time(value) if isinstance(value, str) else value
    return t.astimezone(UTC).strftime('%Y%m%dt%H%M%Sz')


def slug(text: str) -> str:
    return SAFE_ID.sub('_', str(text).lower()).strip('_') or 'na'


def fmt_num(value: float, decimals: int = 0, sign: bool = False) -> str:
    text = f'{value:{"+" if sign else ""},.{decimals}f}'
    return text


def pct(value: float | None, decimals: int = 2, sign: bool = True) -> str:
    return f'{value:{"+" if sign else ""}.{decimals}f}%'


def jst(value) -> str:
    return parse_time(value).astimezone(JST).strftime('%Y-%m-%d %H:%M JST')


def jdate(value: str) -> str:
    return value


def _usable(record: dict | None) -> bool:
    return bool(record) and record.get('status') in ('ok', 'partial', 'stale')


def _fresh(fact: dict | None) -> bool:
    """Scorable at as_of: ok/provisional, not stale, with a value (R-02)."""
    return bool(fact) and fact['status'] in ('ok', 'provisional') and not fact.get('stale') and fact['value'] is not None


class Facts:
    """Builds the fact list for one edition with the shared provenance fields."""

    def __init__(self, as_of: datetime, manifest_sha: str):
        self.as_of = as_of
        self.manifest = manifest_sha
        self.items: list[dict] = []
        self.index: dict[str, dict] = {}

    def _add(self, fact: dict) -> dict:
        base = fact['fact_id']
        n = 1
        while fact['fact_id'] in self.index:
            n += 1
            fact['fact_id'] = f'{base}.r{n}'
        self.items.append(fact)
        self.index[fact['fact_id']] = fact
        return fact

    def _common(self, *, domain, provider, instrument, metric, window, key, category, value, unit, status,
                missing_reason, display, display_value, observed_at, observation_date, period_start, period_end,
                published_at, retrieved_at, retrieval_started_at, available_at, timestamp_quality,
                scheduled_release_at, stale_after, freshness_rule_id, valid_until, venue, population, coverage,
                method_id, source_id, evidence_kind, source_fact_ids, raw_sha256, first_seen_at=None):
        logical = '.'.join(['usoil', slug(domain), slug(provider), slug(instrument), slug(metric), slug(window)])
        fact_id = f'{logical}.{slug(key)}'
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                value, status, missing_reason = None, 'invalid', 'non_finite_value'
        if value is None:
            display_value = None
            if status in ('ok', 'provisional', 'partial', 'stale'):
                status = 'missing'
            missing_reason = missing_reason or 'value_unavailable'
            display = display if display and display != '—' else missing_display(missing_reason)
        display = md_safe(display)  # provider labels (e.g. FGI classification) are external text
        if value is not None and display_value is not None:
            display_value = _display_number(display, display_value)
        reference = observed_at or period_end or retrieved_at
        if valid_until is None and stale_after is not None and reference is not None:
            valid_until = z(parse_time(reference) + timedelta(seconds=stale_after))
        stale = bool(valid_until is not None and parse_time(valid_until) < self.as_of)
        if stale and status in ('ok', 'provisional', 'partial'):
            status = 'stale'
        fact = {
            'fact_id': fact_id, 'logical_id': logical, 'revision': 0, 'asset': SYMBOL, 'category': category,
            'metric': metric, 'source_id': source_id, 'value': value, 'unit': unit, 'status': status,
            'missing_reason': missing_reason if value is None or status not in ('ok', 'provisional') else missing_reason,
            'observed_at': z(observed_at), 'observation_date': observation_date, 'period_start': z(period_start),
            'period_end': z(period_end), 'published_at': z(published_at),
            'first_seen_at': z(first_seen_at or retrieved_at or self.as_of), 'retrieved_at': z(retrieved_at or self.as_of),
            'retrieval_started_at': z(retrieval_started_at or retrieved_at or self.as_of),
            'available_at': z(available_at or retrieved_at or self.as_of), 'timestamp_quality': timestamp_quality,
            'scheduled_release_at': z(scheduled_release_at), 'stale_after_seconds': stale_after,
            'freshness_rule_id': freshness_rule_id, 'stale': stale, 'valid_until': valid_until, 'venue': venue,
            'instrument': instrument, 'population': population, 'coverage': coverage or _coverage(),
            'evidence_kind': evidence_kind, 'method_id': method_id, 'method_version': METHOD_VERSION,
            'source_fact_ids': list(dict.fromkeys(source_fact_ids or [])), 'raw_sha256': raw_sha256,
            'input_manifest_sha256': self.manifest if source_id == 'computed' else None,
            'display': display, 'display_value': display_value if value is not None else None,
        }
        return self._add(fact)

    def observed(self, *, record: dict | None, raw: str | None, domain, provider, instrument, metric, value, unit,
                 display, display_value=None, window='snapshot', key=None, category=None, observed_at=None,
                 observation_date=None, period_start=None, period_end=None, published_at=None, status='ok',
                 missing_reason=None, stale_after=None, freshness_rule_id=None, valid_until=None, venue=None,
                 population='', coverage=None, method_id='observed_value', timestamp_quality=None,
                 scheduled_release_at=None, first_seen_at=None, source_id=None) -> dict:
        record = record or {}
        values = record.get('values') or {}
        retrieved = record.get('retrieved_at')
        quality = timestamp_quality or values.get('timestamp_quality') or 'unknown'
        if quality == 'receipt_interval' and period_start is None and period_end is None:
            period_start, period_end = values.get('retrieval_started_at') or retrieved, retrieved
        if raw is None and status not in ('missing', 'terms_restricted'):
            status, value, missing_reason = 'missing', None, missing_reason or record.get('error_kind') or 'source_unavailable'
        if key is None:
            key = stamp_key(observed_at or observation_date or period_end or retrieved)
        return self._common(
            domain=domain, provider=provider, instrument=instrument, metric=metric, window=window, key=key,
            category=category or domain, value=value, unit=unit, status=status, missing_reason=missing_reason,
            display=display, display_value=value if display_value is None else display_value,
            observed_at=observed_at, observation_date=observation_date, period_start=period_start,
            period_end=period_end, published_at=published_at, retrieved_at=retrieved,
            retrieval_started_at=values.get('retrieval_started_at'), available_at=retrieved,
            timestamp_quality=quality, scheduled_release_at=scheduled_release_at, stale_after=stale_after,
            freshness_rule_id=freshness_rule_id, valid_until=valid_until, venue=venue, population=population,
            coverage=coverage, method_id=method_id, source_id=source_id or record.get('source_id') or 'unknown',
            evidence_kind='observed', source_fact_ids=[], raw_sha256=raw if status not in ('missing', 'terms_restricted') else raw,
            first_seen_at=first_seen_at)

    def derived(self, *, sources: list[dict], domain, provider, instrument, metric, value, unit, display,
                display_value=None, window='snapshot', key=None, category=None, status='ok', missing_reason=None,
                method_id, observed_at=None, observation_date=None, stale_after=None, valid_until=None,
                freshness_rule_id=None, venue=None, population='', coverage=None, evidence_kind='derived',
                timestamp_quality=None) -> dict:
        sources = [s for s in sources if s]
        ids = [s['fact_id'] for s in sources]
        if any(s['value'] is None for s in sources) and value is not None:
            value, missing_reason = None, 'input_missing'
        times = [parse_time(s['available_at']) for s in sources] + [self.as_of]
        upstream = [parse_time(s['valid_until']) for s in sources if s.get('valid_until')]
        if valid_until is None and stale_after is None and upstream:
            valid_until = z(min(upstream))
        if status == 'ok' and any(s['status'] in ('stale', 'partial', 'provisional', 'conflict') for s in sources):
            worst = next(x for x in ('conflict', 'stale', 'partial', 'provisional')
                         if any(s['status'] == x for s in sources))
            status = worst
        quality = timestamp_quality or (sources[0]['timestamp_quality'] if sources else 'unknown')
        if key is None:
            key = stamp_key(observed_at or observation_date or (sources[0]['observed_at'] if sources else None)
                            or (sources[0]['observation_date'] if sources else None)
                            or (sources[0]['retrieved_at'] if sources else self.as_of))
        return self._common(
            domain=domain, provider=provider, instrument=instrument, metric=metric, window=window, key=key,
            category=category or domain, value=value, unit=unit, status=status, missing_reason=missing_reason,
            display=display, display_value=value if display_value is None else display_value,
            observed_at=observed_at, observation_date=observation_date, period_start=None, period_end=None,
            published_at=None, retrieved_at=max(s['retrieved_at'] for s in sources) if sources else None,
            retrieval_started_at=min(s['retrieval_started_at'] for s in sources) if sources else None,
            available_at=max(times), timestamp_quality=quality, scheduled_release_at=None, stale_after=stale_after,
            freshness_rule_id=freshness_rule_id, valid_until=valid_until, venue=venue, population=population,
            coverage=coverage, method_id=method_id, source_id='computed', evidence_kind=evidence_kind,
            source_fact_ids=ids, raw_sha256=None)

    def missing(self, *, domain, provider, instrument, metric, unit, reason, source_id, window='snapshot',
                category=None, status='missing', display=None) -> dict:
        return self._common(
            domain=domain, provider=provider, instrument=instrument, metric=metric, window=window, key='na',
            category=category or domain, value=None, unit=unit, status=status, missing_reason=reason,
            display=display or missing_display(reason), display_value=None, observed_at=None, observation_date=None,
            period_start=None, period_end=None, published_at=None, retrieved_at=None, retrieval_started_at=None,
            available_at=None, timestamp_quality='unknown', scheduled_release_at=None, stale_after=None,
            freshness_rule_id=None, valid_until=None, venue=None, population='', coverage=None,
            method_id='not_observed' if source_id != 'computed' else 'not_computed', source_id=source_id,
            evidence_kind='derived' if source_id == 'computed' else 'observed', source_fact_ids=[], raw_sha256=None)


_NUMBER = re.compile(r'[-+−]?\d+(?:,\d{3})*(?:\.\d+)?')


MISSING_LABELS = {
    'four_week_history_incomplete': '保存済み4週履歴がそろわない',
    'history_insufficient': '履歴不足', 'base_day_missing': '比較日の値がない', 'common_observation_stale': '共通日が古い',
    'no_common_dates': '共通日がない', 'dollar_proxy_missing': 'ドル指数がない', 'treasury_missing': '米国債利回りがない',
    'input_missing': '入力がない', 'inputs_missing': '入力がない', 'bar_missing': '分足がない',
    'source_unavailable': '取得できず', 'credentials_missing': '認証情報なし', 'not_used_by_terms': '規約により未使用',
    'budget_exhausted': '時間切れ', 'timeout': '取得できず（時間切れ）', 'connection': '取得できず（接続）',
    'stale_observation': '観測が古い', 'future_observation': '未来時刻の観測', 'quote_gap': '気配がない',
}


def missing_display(reason) -> str:
    """Japanese label for the report; the reason code stays in missing_reason and the fact table (section 10)."""
    label = MISSING_LABELS.get(str(reason or ''))
    if label is None and str(reason or '').startswith(('http_', 'parse_', 'invalid_', 'empty_', 'missing_', 'tls')):
        label = '取得できず'
    return f'欠測（{label}）' if label else '欠測'


def _display_number(display: str, approx: float) -> float:
    """The number exactly as printed in ``display`` that ``approx`` was formatted into.

    Figures compare item values with the quoted Markdown text digit by digit,
    so the bound value is taken from the printed string, never re-rounded.
    """
    best = None
    for token in _NUMBER.findall(display or ''):
        number = float(token.replace(',', '').replace('−', '-'))
        decimals = len(token.split('.', 1)[1]) if '.' in token else 0
        tolerance = 0.5 * 10 ** -decimals + 1e-9 * max(1.0, abs(approx))
        if abs(number - approx) <= tolerance and (best is None or abs(number - approx) < abs(best - approx)):
            best = number
    return best if best is not None else approx


def fact_text(fact: dict) -> str:
    """The fact as written in prose: display plus the qualifier that must stay beside it.

    Percentiles carry their comparison window and count (P5), walls their 25 bp band and strikes their OI.
    """
    note = (fact.get('coverage') or {}).get('note') or ''
    if fact['value'] is not None and note and fact['method_id'] in (
            'midrank_percentile_excluding_current',):
        if fact['metric'].endswith('_share'):
            return fact['display']
        return f'{fact["display"]}（{note}）'
    return fact['display']


def _coverage(included=(), expected=(), observed=None, expected_count=None, note='') -> dict:
    return {'included_components': list(included), 'expected_components': list(expected),
            'observed_count': observed, 'expected_count': expected_count, 'note': note}


def _percentile_fact(F: Facts, base: dict, series: list[dict], *, min_count: int, window_days=None,
                     window_count=None, label: str, as_of=None) -> tuple[dict, dict]:
    stats = comparison_stats(series, base['value'], as_of=as_of or base['observed_at'] or base['retrieved_at'],
                             window_days=window_days, window_count=window_count, min_count=min_count,
                             metric=base['logical_id'])
    if stats['status'] in ('ok', 'zero_variance') and stats['percentile'] is not None:
        # Short display (figures draw it next to a label); the window and count stay in coverage.note and are
        # printed beside the value wherever the fact is written out (fact_text).
        value, status, reason = stats['percentile'], 'ok', None
        display = f'百分位 {stats["percentile"]:.1f}'
    else:
        value, status, reason = None, 'warming_up', 'insufficient_history'
        display = describe(stats)
    fact = F.derived(sources=[base], domain=base['category'], provider=base['logical_id'].split('.')[2],
                     instrument=base['instrument'] or 'na', metric=f'{base["metric"]}_percentile',
                     value=value, unit='percentile', display=display, status=status, missing_reason=reason,
                     method_id='midrank_percentile_excluding_current', venue=base['venue'],
                     population=f'{label}（{base["population"]}）', observed_at=base['observed_at'],
                     observation_date=base['observation_date'],
                     coverage=_coverage(observed=stats['count'], expected_count=min_count,
                                        note=describe_window(stats)))
    if value is None:
        fact['status'] = 'warming_up'
    return fact, stats


# ---------------------------------------------------------------- builders

def carry_state(state: dict, history_dir, as_of: datetime):
    """Known events and unresolved incidents from adopted editions (R-04, R-05); scoring reads them.

    An invalid carried file never raises here and never reads as "nothing known, nothing open": it sets
    ``state.carry`` invalid with a fixed code, which scoring turns into the hard invalid
    ``carry_state_invalid`` (R2-04)."""
    from usoil import carry
    try:
        known = carry.known_news(history_dir, as_of)
        records = carry.incidents(history_dir)
    except carry.CarryStateError as error:
        state['carry'] = {'status': 'invalid', 'error': error.code}
        state['news']['carried'] = []
        state['incidents'] = {'open': [], 'released_ids': [], 'recently_released': []}
        return
    fields = ('incident_id', 'news_id', 'event_cluster_id', 'url', 'title', 'published_at', 'affected_source_ids',
              'recorded_edition_id', 'recorded_as_of')
    # Every record is evaluated as of this edition (R4-01): a closure made after as_of (owner release between
    # collection and facts build, or an old collection rebuilt) does not exist yet; the incident was still open.
    open_, released_ids, recent = [], [], []
    for r in records:
        closed_at = carry.closed_at(r)
        if closed_at is not None and closed_at <= as_of:
            released_ids.append(r['incident_id'])
            if r['status'] == 'released_manually' and \
                    closed_at <= as_of <= closed_at + timedelta(days=carry.RELEASE_VISIBLE_DAYS):
                # Visible in every edition for 14 days (operator assertion, not authentication).
                recent.append({k: r.get(k) for k in ('incident_id', 'title', 'released_at', 'release_reason',
                                                      'released_by')})
        elif parse_time(r['recorded_as_of']) <= as_of:
            open_.append({k: r.get(k) for k in fields})
    state['carry'] = {'status': 'ok'}
    state['news']['carried'] = [
        {k: r.get(k) for k in ('known_id', 'ids', 'first_known_at', 'adopted_as_of', 'edition_id', 'mode', 'title',
                               'body_hashes', 'follow_up_of')} for r in known]
    # Closed ids never re-open from the identical publication (R3-02, R3-02b).
    state['incidents'] = {'open': open_, 'released_ids': sorted(released_ids), 'recently_released': recent}


def _safe_body(body: dict) -> dict:
    out = dict(body)
    if 'excerpt' in out:
        out['excerpt'] = md_safe(out['excerpt'])
    return out



def _raw(record: dict | None, part='r0'):
    return ((record or {}).get('values') or {}).get('raw_parts', {}).get(part)


def _date_expiry(day: str, days: int) -> str:
    return z(datetime.fromisoformat(day + 'T23:59:59+00:00') + timedelta(days=days))


def ny_trading_date(value) -> date:
    return (parse_time(value).astimezone(NY) + timedelta(hours=7)).date()


def _quote_expiry(at, as_of):
    local = as_of.astimezone(NY)
    closed = local.weekday() == 5 or (local.weekday() == 4 and local.hour >= 17) or (
        local.weekday() == 6 and local.hour < 18)
    # CME holidays are unverified. This is the adopted conservative four-calendar-day ceiling,
    # used only during the usual weekend closure; intraday observations still expire after six hours.
    return z(parse_time(at) + timedelta(days=4)) if closed else z(parse_time(at) + timedelta(seconds=QUOTE_STALE))


def price_facts(F: Facts, S: dict, state: dict):
    rec = S.get('cnbc_quotes')
    q = ((rec or {}).get('values') or {}).get('quotes', {}).get('@CL.1') if _usable(rec) else None
    primary = None
    if q and q.get('time_kind') == 'quote_time':
        valid = _quote_expiry(q['last_time'], F.as_of)
        primary = F.observed(record=rec, raw=_raw(rec), domain='price', provider='cnbc', instrument='cl_front',
                             metric='last', value=q.get('last'), unit='USD/bbl', display=f'{q["last"]:,.2f} USD/bbl',
                             observed_at=q['last_time'], valid_until=valid, venue='cnbc',
                             timestamp_quality='source_observed', freshness_rule_id='oil_quote_6h_weekend_max_4d',
                             population='CNBC @CL.1 フロント限月・単一venue')
        for key in ('previous_close', 'open', 'high', 'low', 'volume', 'open_interest'):
            value = q.get(key)
            if value is not None:
                F.observed(record=rec, raw=_raw(rec), domain='price', provider='cnbc', instrument='cl_front',
                           metric=key, value=value, unit='contracts' if key in ('volume', 'open_interest') else 'USD/bbl',
                           display=f'{value:,.2f}', observed_at=q['last_time'], valid_until=valid,
                           timestamp_quality='source_observed', venue='cnbc', population='CNBC @CL.1（表示用）')
    else:
        primary = F.missing(domain='price', provider='cnbc', instrument='cl_front', metric='last', unit='USD/bbl',
                            reason=(rec or {}).get('error_kind') or 'source_unavailable', source_id='cnbc_quotes')
    ref, fallback = primary, False
    if not _fresh(primary):
        yahoo = S.get('yahoo_ohlc')
        rows = ((yahoo or {}).get('values') or {}).get('symbols', {}).get('CL=F', {}).get('d1', [])
        rows = [r for r in rows if parse_time(r['t']) + timedelta(days=1) <= F.as_of]
        if _usable(yahoo) and rows:
            row = max(rows, key=lambda r: r['t'])
            day, today = parse_time(row['t']).astimezone(NY).date(), F.as_of.astimezone(NY).date()
            lag = sum((day + timedelta(days=i)).weekday() < 5 for i in range(1, (today - day).days + 1))
            if day <= today and lag <= 1 and (today - day).days <= 4:
                ref = F.observed(record=yahoo, raw=_raw(yahoo, 'cl_d1'), domain='price', provider='yahoo',
                                 instrument='cl_continuous', metric='close', value=row['c'], unit='USD/bbl',
                                 display=f'{row["c"]:,.2f} USD/bbl（Yahoo終値・限月混在）', observed_at=None,
                                 observation_date=day.isoformat(), period_end=z(parse_time(row['t']) + timedelta(days=1)),
                                 status='provisional', timestamp_quality='source_date', venue='yahoo',
                                 freshness_rule_id='latest_closed_daily_max_1_business_day',
                                 valid_until=_date_expiry(day.isoformat(), 4), population=ROLL_NOTE)
                fallback = True
    state['reference_price'] = {'status': 'ok' if _fresh(ref) else ref['status'], 'fact_id': ref['fact_id'],
                                'provider': 'yahoo' if fallback else 'cnbc', 'fallback': fallback,
                                'venue_count': 1 if _fresh(ref) else 0, 'venue_fact_ids': [ref['fact_id']], 'note': ROLL_NOTE,
                                'expiration_date': (q or {}).get('expiration_date')}


def curve_facts(F: Facts, S: dict, state: dict):
    rec = S.get('cnbc_quotes')
    quotes = ((rec or {}).get('values') or {}).get('quotes') or {} if _usable(rec) else {}
    out = {'status': 'missing', 'items': [], 'roll_note': ROLL_NOTE}
    for front, back, key in (('@CL.1', '@CL.2', 'front'), ('@CL.2', '@CL.3', 'second_third')):
        a, b = quotes.get(front), quotes.get(back)
        if not a or not b or any(q.get('time_kind') != 'quote_time' for q in (a, b)):
            fact = F.missing(domain='curve', provider='cnbc', instrument=f'cl_{key}_spread', metric='pct',
                             unit='percent', source_id='computed', reason='contract_quote_missing')
            out['spread_pct' if key == 'front' else 'second_third_pct'] = fact['fact_id']
            continue
        src = [F.observed(record=rec, raw=_raw(rec), domain='curve', provider='cnbc', instrument=slug(symbol),
                          metric='last', value=q['last'], unit='USD/bbl', display=f'{q["last"]:,.2f}',
                          observed_at=q['last_time'], stale_after=SAME_DAY_STALE,
                          freshness_rule_id='quote_6h', timestamp_quality='source_observed', venue='cnbc')
               for symbol, q in ((front, a), (back, b))]
        spread = float(Decimal(str(a['last']))-Decimal(str(b['last'])))
        value = float(100*(Decimal(str(a['last']))-Decimal(str(b['last'])))/Decimal(str(b['last']))) if b['last'] > 0 else None
        for metric, number, unit in (('absolute', spread, 'USD/bbl'), ('pct', value, 'percent')):
            fact = F.derived(sources=src, domain='curve', provider='cnbc', instrument=f'cl_{key}_spread',
                             metric=metric, value=number, unit=unit,
                             display=f'{number:+.2f}{"%" if unit == "percent" else " USD/bbl"}' if number is not None else '—',
                             observed_at=min(a['last_time'], b['last_time']),
                             method_id='front_minus_second_over_second' if metric == 'pct' else 'front_minus_second',
                             population=f'{front} − {back}（同一venue・異限月）')
            target = ('spread_pct' if metric == 'pct' else 'spread') if key == 'front' else f'second_third_{metric}'
            out[target] = fact['fact_id']
        out['items'].append({'front': front, 'back': back, 'spread_pct_fact_id': fact['fact_id'],
                             'front_expiry': a.get('expiration_date'), 'back_expiry': b.get('expiration_date')})
    out['status'] = 'ok' if _fresh(F.index.get(out.get('spread_pct'))) else F.index[out['spread_pct']]['status']
    state['curve'] = out


def eia_facts(F: Facts, S: dict, state: dict, history: History, as_of: datetime):
    rec = S.get('eia_wpsr')
    out = {'status': 'missing', 'mixed': False, 'week_ending': None, 'window': '1w', 'fact_ids': []}
    if not _usable(rec):
        for key, metric in (('change_crude', 'crude_change_1w'), ('change_products', 'products_change_1w'),
                            ('change_4w', 'crude_change_4w')):
            out[key] = F.missing(domain='inventory', provider='eia', instrument='wpsr', metric=metric,
                                 unit='million barrels', window='4w' if key == 'change_4w' else '1w',
                                 reason=(rec or {}).get('error_kind') or 'source_unavailable', source_id='eia_wpsr')['fact_id']
        state['inventory'] = out
        return
    v, raw = rec['values'], _raw(rec)
    day = v['week_ending']
    out['week_ending'] = day
    expiry = _date_expiry(day, 8)
    stock_facts = {}
    for key, row in v.get('stocks', {}).items():
        for field, metric in (('current', key), ('change_wow', f'{key}_change_1w'), ('change_yoy', f'{key}_change_yoy')):
            value = row.get(field)
            fact = F.observed(record=rec, raw=raw, domain='inventory', provider='eia', instrument='wpsr',
                              metric='crude_change_1w' if key == 'commercial_crude_ex_spr' and field == 'change_wow' else metric,
                              value=value, unit='million barrels', window='1w',
                              display=f'{value:+.3f}M bbl' if value is not None else '—', observation_date=day,
                              valid_until=expiry, freshness_rule_id='eia_wpsr_8_days', timestamp_quality='source_date',
                              population=f'EIA WPSR {key} / {field}（週末日 {day}、公表時刻は推測しない）')
            out['fact_ids'].append(fact['fact_id'])
            if field == 'current':
                out[{'commercial_crude_ex_spr': 'crude'}.get(key, key)] = fact['fact_id']
            if field == 'change_wow':
                stock_facts[key] = fact
    crude = stock_facts.get('commercial_crude_ex_spr')
    if crude is None:
        crude = F.missing(domain='inventory', provider='eia', instrument='wpsr', metric='crude_change_1w',
                          unit='million barrels', window='1w', reason='commercial_crude_missing', source_id='eia_wpsr')
    out['change_crude'] = crude['fact_id']
    product_sources = [stock_facts[k] for k in ('gasoline', 'distillate') if k in stock_facts]
    products = F.derived(sources=product_sources, domain='inventory', provider='eia', instrument='wpsr',
                         metric='products_change_1w', value=sum(f['value'] for f in product_sources)
                         if len(product_sources) == 2 and all(f['value'] is not None for f in product_sources) else None,
                         unit='million barrels', display=f'{sum(f["value"] or 0 for f in product_sources):+.3f}M bbl',
                         window='1w', observation_date=day, valid_until=expiry,
                         missing_reason='products_missing' if len(product_sources) < 2 else None,
                         method_id='gasoline_plus_distillate_change', population='ガソリン＋留出油の週次変化')
    out['change_products'] = products['fact_id']
    for key, row in v.get('flows', {}).items():
        value = row.get('current')
        fact = F.observed(record=rec, raw=raw, domain='inventory', provider='eia', instrument='wpsr', metric=key,
                          value=value, unit='thousand barrels per day', window='1w', observation_date=day,
                          display=f'{value:,.0f} kb/d' if value is not None else '—', valid_until=expiry,
                          timestamp_quality='source_date', freshness_rule_id='eia_wpsr_8_days', population='EIA WPSR 週間実績')
        out[key] = fact['fact_id']
    # Persist only actual source observations. A replay cannot use arrivals after its own as_of.
    stored = history.read('eia', 'wpsr', 'crude_change_1w')
    eligible = [r for r in stored if parse_time(r.get('available_at') or r['recorded_at']) <= as_of]
    by_day = {r['observation_date']: r for r in eligible if r.get('observation_date') and r.get('value') is not None}
    if crude['value'] is not None and date.fromisoformat(day) <= as_of.date():
        entry = {'source': 'eia', 'instrument': 'wpsr', 'metric': 'crude_change_1w', 'observed_at': day+'T00:00:00Z',
                 'observation_date': day, 'value': crude['value'], 'available_at': rec['retrieved_at'],
                 'retrieved_at': rec['retrieved_at'], 'raw_sha256': raw}
        result = history.append([entry], recorded_at=z(as_of))
        out['history_conflicts'] = result['conflicts']
        if result['conflicts']:
            out['history_warning'] = '同一週の既存保存値と不一致。4週集計は保留。'
        by_day[day] = entry
    wanted = [(date.fromisoformat(day)-timedelta(weeks=i)).isoformat() for i in range(4)]
    four_sources = [crude]
    if all(d in by_day for d in wanted) and not out.get('history_conflicts'):
        for d in wanted[1:]:
            r = by_day[d]
            four_sources.append(F.observed(record={'source_id': 'eia_wpsr', 'retrieved_at': r.get('available_at'), 'values': {}},
                                            raw=r.get('raw_sha256'), domain='inventory', provider='eia', instrument='wpsr',
                                            metric='historical_crude_change_1w', value=r['value'], unit='million barrels',
                                            display=f'{r["value"]:+.3f}M bbl', window='1w', observation_date=d,
                                            timestamp_quality='source_date', population='保存済みEIA週次実測（4週集計用）'))
        total = sum(by_day[d]['value'] for d in wanted)
        four = F.derived(sources=four_sources, domain='inventory', provider='eia', instrument='wpsr',
                         metric='crude_change_4w', value=total, unit='million barrels', display=f'{total:+.3f}M bbl',
                         window='4w', observation_date=day, valid_until=expiry, method_id='four_consecutive_week_changes',
                         coverage=_coverage(included=sorted(wanted), observed=4, expected_count=4, note='保存済み4週連続の合計'))
    else:
        four = F.missing(domain='inventory', provider='eia', instrument='wpsr', metric='crude_change_4w',
                         unit='million barrels', window='4w', reason='four_week_history_incomplete', source_id='computed')
    out['change_4w'] = four['fact_id']
    out['status'] = 'ok' if _fresh(crude) else crude['status']
    out['mixed'] = bool(_fresh(crude) and _fresh(products) and abs(crude['value']) >= 4 and abs(products['value']) >= 4
                        and crude['value'] * products['value'] < 0)
    state['inventory'] = out


def cot_facts(F: Facts, S: dict, state: dict, as_of: datetime):
    rec = S.get('cftc_cot')
    rows = ((rec or {}).get('values') or {}).get('series') or [] if _usable(rec) else []
    out = {'status': 'missing'}
    if not rows:
        state['cot'] = out
        return
    rows = sorted({r['report_date']: r for r in rows}.values(), key=lambda r: r['report_date'])[-156:]
    last = rows[-1]
    day = last['report_date']
    expiry = _date_expiry(day, 10)
    made = {}
    for key in ('mm_long', 'mm_short', 'mm_net', 'open_interest', 'change_open_interest', 'change_mm_long', 'change_mm_short'):
        value = last.get(key)
        made[key] = F.observed(record=rec, raw=_raw(rec), domain='cot', provider='cftc', instrument='mm', metric=key,
                               value=value, unit='contracts', display=f'{value:+,.0f}枚' if value is not None else '—',
                               window='1w', observation_date=day, valid_until=expiry, freshness_rule_id='cot_10_days',
                               timestamp_quality='source_date', population='CFTC 72hh-3qpy / 067651 NYMEX WTI futures-only')
        out['oi' if key == 'open_interest' else key] = made[key]['fact_id']
    oi, net = last.get('open_interest'), last.get('mm_net')
    ratio = F.derived(sources=[made['mm_net'], made['open_interest']], domain='cot', provider='cftc', instrument='mm',
                      metric='net_over_open_interest', value=net/oi if net is not None and oi and oi > 0 else None,
                      unit='ratio', display=f'{net/oi:.5f}' if net is not None and oi and oi > 0 else '—', window='1w',
                      observation_date=day, method_id='net_over_open_interest')
    history_rows = [{'observed_at': r['report_date']+'T00:00:00Z', 'value': r['mm_net']/r['open_interest']}
                    for r in rows[:-1] if r.get('mm_net') is not None and r.get('open_interest', 0) and r['open_interest'] > 0]
    per, stats = _percentile_fact(F, ratio, history_rows, min_count=COT_MIN_WEEKS, window_count=156,
                                 label='MM net/OI・156週', as_of=day+'T00:00:00Z')
    change = last.get('change_open_interest')
    old_oi = oi-change if oi is not None and change is not None else None
    growth = 100*change/old_oi if old_oi and old_oi > 0 else None
    oi_change = F.derived(sources=[made['open_interest'], made['change_open_interest']], domain='cot', provider='cftc',
                          instrument='mm', metric='oi_change_pct', value=growth, unit='percent',
                          display=f'{growth:+.2f}%' if growth is not None else '—', window='1w', observation_date=day,
                          method_id='oi_change_over_prior_oi')
    dl, ds = last.get('change_mm_long'), last.get('change_mm_short')
    delta = dl-ds if dl is not None and ds is not None else None
    net_change = F.derived(sources=[made['change_mm_long'], made['change_mm_short']], domain='cot', provider='cftc',
                           instrument='mm', metric='mm_net_change', value=delta, unit='contracts',
                           display=f'{delta:+,.0f}枚' if delta is not None else '—', window='1w', observation_date=day,
                           method_id='change_long_minus_change_short', population='週次±20,000枚は表示・解釈のみ（方向加点なし）')
    out.update(status='ok' if _fresh(ratio) else ratio['status'], report_date=day, mm_net_ratio=ratio['fact_id'],
               mm_net_ratio_percentile=per['fact_id'], oi_change_pct=oi_change['fact_id'], mm_net_change=net_change['fact_id'],
               percentile_count=stats['count'], publication_stalled=(as_of.date()-date.fromisoformat(day)).days > 30)
    state['cot'] = out


def vol_facts(F: Facts, S: dict, state: dict):
    rec = S.get('cboe_ovx')
    v = ((rec or {}).get('values') or {}) if _usable(rec) and (rec or {}).get('observed_at') else {}
    level = F.observed(record=rec, raw=_raw(rec), domain='vol', provider='cboe', instrument='ovx', metric='level',
                       value=v.get('last'), unit='index', display=f'{v["last"]:.2f}' if v.get('last') is not None else '—',
                       observed_at=(rec or {}).get('observed_at'), stale_after=OPTIONS_STALE,
                       freshness_rule_id='ovx_quote_15m', timestamp_quality='source_observed', population='Cboe OVX（方向なしテールリスク）')
    out = {'status': 'ok' if _fresh(level) else level['status'], 'level': level['fact_id']}
    change = F.observed(record=rec, raw=_raw(rec), domain='vol', provider='cboe', instrument='ovx', metric='provider_change_1d',
                        value=v.get('change_pct'), unit='percent',
                        display=f'{v["change_pct"]:+.2f}%' if v.get('change_pct') is not None else '—',
                        observed_at=(rec or {}).get('observed_at'), stale_after=OPTIONS_STALE,
                        freshness_rule_id='ovx_quote_15m', timestamp_quality='source_observed', method_id='last_over_previous_close_minus_one')
    fred = S.get('fred')
    rows = (((fred or {}).get('values') or {}).get('series') or {}).get('OVXCLS', {}).get('rows') or [] if _usable(fred) else []
    current_day = parse_time(level['observed_at']).date() if level['observed_at'] else F.as_of.date()
    rows = sorted({r['date']: r for r in rows if date.fromisoformat(r['date']).weekday() < 5
                   and date.fromisoformat(r['date']) < current_day}.values(), key=lambda r: r['date'])
    series = [{'observed_at': r['date']+'T00:00:00Z', 'value': r['value']} for r in rows]
    # Materialise the historical source for provenance, not merely the current quote.
    historical = None
    info = (((fred or {}).get('values') or {}).get('series') or {}).get('OVXCLS')
    if rows and info:
        r = rows[-1]
        historical = F.observed(record=fred, raw=info.get('raw'), domain='vol', provider='fred', instrument='ovxcls',
                                metric='history_latest', value=r['value'], unit='index', display=f'{r["value"]:.2f}',
                                observation_date=r['date'], timestamp_quality='source_date', population='FRED OVXCLS履歴')
    prior = rows[-1] if rows else None
    daily_change = (float(100*(Decimal(str(level['value']))/Decimal(str(prior['value']))-1))
                    if prior and prior['value'] > 0 and level['value'] is not None
                    and (current_day-date.fromisoformat(prior['date'])).days <= 4 else None)
    scored_change = F.derived(sources=[level]+([historical] if historical else []), domain='vol', provider='cboe',
                              instrument='ovx', metric='change_1d', value=daily_change, unit='percent',
                              display=f'{daily_change:+.2f}%' if daily_change is not None else '—', window='1d',
                              observed_at=level['observed_at'], method_id='cboe_level_over_prior_fred_close_minus_one',
                              missing_reason='prior_fred_close_missing_or_stale' if daily_change is None else None,
                              population='Cboe現在値 / 観測日より前のFRED OVXCLS最新終値 − 1（Cboe previous closeは採点不使用）')
    per, stats = _percentile_fact(F, level, series, min_count=60, window_count=252, label='OVX・252営業日',
                                 as_of=current_day.isoformat()+'T00:00:00Z')
    if historical:
        per['source_fact_ids'].append(historical['fact_id'])
    past = [r for r in rows if date.fromisoformat(r['date']) <= current_day-timedelta(days=7)]
    base = past[-1] if past and (current_day-date.fromisoformat(past[-1]['date'])).days <= 10 else None
    weekly_base = None
    if base and info:
        weekly_base = F.observed(record=fred, raw=info.get('raw'), domain='vol', provider='fred', instrument='ovxcls',
                                 metric='weekly_baseline', value=base['value'], unit='index', display=f'{base["value"]:.2f}',
                                 observation_date=base['date'], timestamp_quality='source_date', population='FRED OVXCLS週次比較終値')
    weekly = 100*(level['value']/base['value']-1) if base and base['value'] > 0 and level['value'] is not None else None
    wf = F.derived(sources=[level]+([weekly_base] if weekly_base else []), domain='vol', provider='cboe', instrument='ovx',
                   metric='change_1w', value=weekly, unit='percent', display=f'{weekly:+.2f}%' if weekly is not None else '—',
                   window='1w', observed_at=level['observed_at'], method_id='last_over_previous_week_close_minus_one')
    out.update(percentile=per['fact_id'], change_1d=scored_change['fact_id'], provider_change_1d=change['fact_id'], change_1w=wf['fact_id'],
               percentile_count=stats['count'], weekly_regime_change=bool(weekly is not None and abs(weekly) >= 20))
    state['vol'] = out

def _macro_direction(dollar, real):
    if dollar <= -0.30 and real <= -5:
        return 'supportive'
    if dollar >= 0.30 and real >= 5:
        return 'adverse'
    if dollar * real < 0:
        return 'mixed'
    return 'neutral'


def macro_facts(F: Facts, S: dict, state: dict, as_of: datetime):
    out = {'status': 'unknown', 'reason': 'inputs_missing', 'fact_ids': [], 'change_fact_ids': [],
           'proxy_labels': ['FRED DTWEXBGSはDXYと別指数'], 'same_day': {'status': 'missing'}}
    rec = S.get('fred')
    series = ((rec or {}).get('values') or {}).get('series') or {} if _usable(rec) else {}
    source_by_date = {}
    for sid, info in series.items():
        # Spot oil assessments are retained as explicit non-scoring notes; they can never supply reference/curve.
        rows = sorted({r['date']: r for r in info.get('rows', [])}.values(), key=lambda r: r['date'])
        if not rows:
            continue
        r = rows[-1]
        limit = {'daily': 4, 'weekly': 10, 'monthly': 75}.get(info.get('frequency'), 4)
        fact = F.observed(record=rec, raw=info.get('raw'), domain='macro', provider='fred', instrument=sid.lower(),
                          metric='level', value=r['value'], unit=info.get('unit', 'index'), window=info.get('frequency', 'daily'),
                          display=f'{r["value"]:,.2f}{"%" if info.get("unit") == "percent" else ""}（{r["date"]}）', observation_date=r['date'],
                          valid_until=_date_expiry(r['date'], limit), timestamp_quality='source_date',
                          freshness_rule_id=f'daily_{limit}_calendar_days', method_id='fred_observation',
                          population=info.get('label', sid)+('（現物・参照と採点に不使用）' if sid.startswith('DCOIL') else '（表示用）'))
        out[sid] = fact['fact_id']
        out['fact_ids'].append(fact['fact_id'])
        source_by_date[sid] = {r['date']: r['value'] for r in rows}
        if sid == 'DFII10' and len(rows) > 1:
            prev = rows[-2]
            bp = float((Decimal(str(r['value']))-Decimal(str(prev['value'])))*100)
            change = F.derived(sources=[fact], domain='macro', provider='fred', instrument='dfii10',
                               metric='change_bp_common_window', value=bp, unit='bp', window='1d',
                               display=f'{bp:+.1f}bp（{prev["date"]}→{r["date"]}）', observation_date=r['date'],
                               method_id='yield_change_bp_previous_observation')
            out['DFII10_change'] = change['fact_id']
    cnbc = S.get('cnbc_quotes')
    q = ((cnbc or {}).get('values') or {}).get('quotes', {}).get('.DXY') if _usable(cnbc) else None
    same = out['same_day']
    if q and q.get('time_kind') == 'quote_time':
        current = ny_trading_date(q['last_time']) == ny_trading_date(as_of)
        level = F.observed(record=cnbc, raw=_raw(cnbc), domain='macro', provider='cnbc', instrument='ice_dxy', metric='level',
                           value=q['last'], unit='index', display=f'{q["last"]:.2f}', observed_at=q['last_time'],
                           stale_after=SAME_DAY_STALE, status='ok' if current else 'stale',
                           missing_reason=None if current else 'previous_trading_date', timestamp_quality='source_observed',
                           freshness_rule_id='ny_trading_date_and_6h', population='ICE DXY（CNBC気配・FRED広義ドルとは別）')
        change = F.derived(sources=[level], domain='macro', provider='cnbc', instrument='ice_dxy',
                           metric='change_pct_previous_close', value=q['change_pct'], unit='percent', window='same_day',
                           observed_at=q['last_time'], display=f'{q["change_pct"]:+.2f}%', method_id='pct_change_previous_close')
        same.update(status='ok' if _fresh(change) else change['status'], dxy=level['fact_id'], dxy_change=change['fact_id'])
    real = F.index.get(out.get('DFII10_change'))
    dollar = F.index.get(same.get('dxy_change'))
    if _fresh(real) and _fresh(dollar):
        status = _macro_direction(dollar['value'], real['value'])
        out.update(status=status, reason=f'macro_{status}', window='same_day_dxy_latest_real10',
                   common_date=real['observation_date'], change_fact_ids=[dollar['fact_id'], real['fact_id']])
    else:
        real_rows, usd_rows = source_by_date.get('DFII10', {}), source_by_date.get('DTWEXBGS', {})
        dates = sorted(real_rows.keys() & usd_rows.keys())
        if len(dates) < 2:
            out['reason'] = 'no_common_dates'
        else:
            d0, d1 = dates[-2:]
            expiry = _date_expiry(d1, 4)
            if parse_time(expiry) < as_of:
                out['reason'] = 'common_observation_stale'
            else:
                changes = []
                y = float((Decimal(str(real_rows[d1]))-Decimal(str(real_rows[d0])))*100)
                usd = float(100*(Decimal(str(usd_rows[d1]))/Decimal(str(usd_rows[d0]))-1)) if usd_rows[d0] else None
                for sid, value, metric, unit in (('DFII10', y, 'change_bp_common_window', 'bp'),
                                                  ('DTWEXBGS', usd, 'change_pct_common_window', 'percent')):
                    # Both endpoints are represented: a newer available series row cannot silently stand for d1.
                    inputs = [F.observed(record=rec, raw=series[sid].get('raw'), domain='macro', provider='fred',
                                         instrument=sid.lower(), metric='common_date_level', value=source_by_date[sid][d],
                                         unit=series[sid].get('unit', 'index'), window='1d', observation_date=d,
                                         display=f'{source_by_date[sid][d]:.4f}', timestamp_quality='source_date') for d in (d0,d1)]
                    f = F.derived(sources=inputs, domain='macro', provider='fred', instrument=sid.lower(), metric=metric,
                                  value=value, unit=unit, window='1d', observation_date=d1, valid_until=expiry,
                                  display=f'{value:+.3f}{"bp" if unit == "bp" else "%"}' if value is not None else '—',
                                  method_id='common_dates_change', population=f'{d0}→{d1}（FRED共通日）')
                    changes.append(f)
                if all(_fresh(f) for f in changes):
                    status = _macro_direction(usd, y)
                    out.update(status=status, reason=f'macro_{status}', window='daily_common_observation',
                               common_date=d1, previous_date=d0, change_fact_ids=[f['fact_id'] for f in changes])
    state['macro'] = out


def levels_facts(F: Facts, S: dict, state: dict, as_of: datetime):
    rec = S.get('yahoo_ohlc')
    v = ((rec or {}).get('values') or {}).get('symbols', {}).get('CL=F', {}) if _usable(rec) else {}
    ref = F.index.get(state.get('reference_price', {}).get('fact_id'))
    price = ref['value'] if _fresh(ref) else None
    built = levels_mod.build_levels(v.get('h1', []), v.get('d1', []), as_of, price)
    valid_until = z(parse_time(built['last_bar_end'])+LEVEL_VALID_AFTER_BAR) if built.get('last_bar_end') else None
    items = []
    for item in built['items']:
        if item['bars'] == 'reference':
            fact = F.derived(sources=[ref] if ref else [], domain='liquidity', provider='computed', instrument='cl_front',
                             metric='named_level_price', value=item['price'], unit='USD/bbl', display=f'{item["price"]:.2f}',
                             key=item['key'], window='reference', method_id='reference_price_integer_round_number',
                             population=item['name']+'（参照価格から計算した参考水準・到達保証なし）')
        else:
            fact = F.observed(record=rec, raw=_raw(rec, 'cl_'+item['bars']), domain='liquidity', provider='yahoo',
                              instrument='cl_continuous', metric='named_level_price', key=item['key'], value=item['price'],
                              unit='USD/bbl', display=f'{item["price"]:.2f}', window=item['bars'],
                              period_start=item.get('window_start'), period_end=item['formed_at'], valid_until=valid_until,
                              timestamp_quality='source_observed', method_id='named_level_bar_extreme',
                              population=item['name']+'（Yahoo連続限月・CNBC限月との比較未確認）')
        items.append({**{k: item.get(k) for k in ('key','name','side','pool','price','state','state_word','touched_at',
                                                 'formed_at','major')}, 'fact_id': fact['fact_id']})
    state['levels'] = {k: built.get(k) for k in ('status','last_bar_start','nearest','cautions','comparison_status',
                                                'comparison_note','weekly_range','reference_position_pct')}
    state['levels'].update(items=items, price_basis='reference_price' if price is not None else None)
    weekly = state['levels'].get('weekly_range')
    bounds = [F.index[x['fact_id']] for x in items if x['key'] in ('prev_week_high','prev_week_low')]
    if weekly and len(bounds) == 2:
        width = F.derived(sources=bounds, domain='liquidity', provider='yahoo', instrument='cl_continuous',
                          metric='previous_week_range_width', value=weekly['width'], unit='USD/bbl', window='1w',
                          display=f'幅{weekly["width"]:.2f} USD/bbl', method_id='previous_week_high_minus_low',
                          population='Yahoo月〜金ET日付バー5本の前週高安差（CNBCとの位置比較は未確認）')
        weekly['fact_id'] = width['fact_id']



CALENDAR_VERIFY_MAX_AGE = timedelta(hours=24)
FF_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'


def stop_window(event: dict) -> dict | None:
    start, ident = parse_time(event['start_at']), event['id']
    if ident.startswith('fomc.statement'):
        before, after, rule = 30, 90, 'fomc_statement_m30_p90'
    elif ident.startswith('fomc.press'):
        before, after, rule = 15, 90, 'fomc_press_m15_to_statement_p120'
    elif ident.startswith('eia.') or event.get('category') == 'eia_release':
        before, after, rule = 15, 30, 'eia_release_m15_p30'
    elif ident.startswith('cot.') or event.get('category') == 'cot_release':
        before, after, rule = 15, 30, 'cot_release_m15_p30'
    elif ident.startswith('opec.') or event.get('category') == 'opec_meeting':
        before, after, rule = 120, 360, 'opec_meeting_m2h_p6h'
    elif any(ident.startswith(p) for p in ('bls.cpi','bls.empsit','bea.pce','bea.gdp')):
        before, after, rule = 15, 30, 'macro_release_m15_p30'
    else:
        return None
    return {'start_at': z(start-timedelta(minutes=before)), 'end_at': z(start+timedelta(minutes=after)),
            'event_ids': [ident], 'rule_id': rule}


def _ff_identity(title):
    lower = title.lower()
    if 'crude oil inventories' in lower:
        return 'eia.inventories', 'eia_release', 'EIA 原油在庫'
    if 'opec' in lower or 'jmmc' in lower:
        return 'opec.meeting', 'opec_meeting', title
    if 'fomc press' in lower:
        return 'fomc.press', 'monetary_policy', title
    if 'federal funds rate' in lower or 'fomc statement' in lower:
        return 'fomc.statement', 'monetary_policy', title
    if 'non-farm' in lower or 'unemployment rate' in lower:
        return 'bls.empsit', 'macro_release', title
    if 'cpi' in lower:
        return 'bls.cpi', 'macro_release', title
    if 'pce' in lower:
        return 'bea.pce', 'macro_release', title
    if 'gdp' in lower:
        return 'bea.gdp', 'macro_release', title
    return 'ff.'+slug(title), 'economic_release', title


def calendar_events(S: dict, as_of: datetime, cache: dict) -> tuple[list, dict]:
    """FF is the adopted oil calendar. Its thisweek coverage ends Saturday NY; nextweek is unavailable.

    Official events remain visible, but cannot fill a missing oil-calendar coverage interval. Dates without
    confirmed times and the manual Wednesday/COT convention remain tentative and never enlarge coverage.
    """
    rec = S.get('forexfactory_week')
    values = ((rec or {}).get('values') or {})
    rows = values.get('events') or [] if _usable(rec) else []
    retrieved = (rec or {}).get('retrieved_at')
    horizon = as_of+timedelta(hours=24)
    reasons, events = [], []
    today = as_of.astimezone(NY).date()
    weeks = {parse_time(r['start_at']).astimezone(NY).date()-timedelta(
        days=(parse_time(r['start_at']).astimezone(NY).weekday()+1)%7) for r in rows}
    coverage_end = None
    if len(weeks) == 1:
        begin = next(iter(weeks))
        coverage_end = datetime.combine(begin+timedelta(days=7), datetime.min.time(), NY)
    if not rec or rec.get('status') != 'ok' or not rows:
        reasons.append('forexfactory_week_missing_or_partial')
    if not retrieved or parse_time(retrieved) > as_of or as_of-parse_time(retrieved) > CALENDAR_VERIFY_MAX_AGE:
        reasons.append('forexfactory_verification_time_out_of_range')
    if coverage_end is None or horizon >= coverage_end or today < next(iter(weeks), today):
        reasons.append('forexfactory_next_24h_beyond_coverage')
    status = {'next_24h': 'unknown' if reasons else 'ok', 'forexfactory': 'unknown' if reasons else 'ok',
              'coverage_end': z(coverage_end), 'verified_at': retrieved, 'horizon_date': horizon.astimezone(NY).date().isoformat(),
              'reasons': reasons, 'sources': {}, 'manual_cache': False, 'bls_cache': 'unused',
              'nextweek': 'unavailable_404'}
    for row in rows:
        prefix, category, name = _ff_identity(row['title'])
        stamp = parse_time(row['start_at']).astimezone(NY).date().isoformat()
        item = {'id': f'{prefix}.{stamp}', 'name': name, 'category': category, 'start_at': row['start_at'],
                'end_at': None, 'timezone': 'America/New_York', 'importance': row['importance'],
                'status': 'confirmed', 'source_id': 'forexfactory_week', 'source_url': FF_URL,
                'verified_at': retrieved, 'published_at': None,
                'consensus': {'label': md_safe(row['title']), 'previous': row.get('previous',''),
                              'forecast': row.get('forecast',''), 'source_id': 'forexfactory_week'}}
        events.append(item)
    for source, key in (('fed_calendar','fed'),('bea_calendar','bea')):
        record = S.get(source)
        status[key] = (record or {}).get('status','missing')
        if _usable(record):
            for event in record.get('values',{}).get('events',[]):
                if not any(e['id'] == event['id'] for e in events):
                    events.append(dict(event, verified_at=record.get('retrieved_at')))
    if cache and parse_time(cache['verified_at']) <= as_of:
        for event in cache.get('events',[]):
            if not any(e['id'] == event['id'] for e in events):
                events.append(dict(event))
    # Standing practice is only a tentative fallback, not a claim that the calendar confirms a release.
    first = today-timedelta(days=1)
    for offset in range(10):
        day = first+timedelta(days=offset)
        if day.weekday() not in (2,4):
            continue
        if day.weekday() == 2:
            sunday = day-timedelta(days=(day.weekday()+1)%7)
            if any(e['id'].startswith('eia.') and sunday <= parse_time(e['start_at']).astimezone(NY).date()
                   < sunday+timedelta(days=7) for e in events):
                continue
            prefix, name, category, hour, minute = 'eia.inventories', 'EIA 原油在庫（慣例時刻・未確認）', 'eia_release', 10, 30
        else:
            prefix, name, category, hour, minute = 'cot.release', 'CFTC COT公表（慣例時刻・未確認）', 'cot_release', 15, 30
        events.append({'id':f'{prefix}.{day.isoformat()}', 'name':name, 'category':category,
                       'start_at':z(datetime(day.year,day.month,day.day,hour,minute,tzinfo=NY)), 'end_at':None,
                       'timezone':'America/New_York', 'importance':'high' if day.weekday()==2 else 'medium',
                       'status':'tentative', 'source_id':'manual_cache', 'source_url':
                       'https://www.eia.gov/petroleum/supply/weekly/' if day.weekday()==2 else
                       'https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm',
                       'verified_at':None, 'published_at':None})
        if day.weekday()==2:
            status['manual_cache'] = status['manual_cache'] or as_of <= parse_time(events[-1]['start_at']) <= horizon
    return events, status


OPEC_SUBJECT = re.compile(r'\bOPEC(?:\s*\+)?\b|\bJMMC\b', re.I)
OPEC_PLAN = re.compile(r'\b(?:will\s+(?:meet|hold|convene)|(?:is|are)\s+(?:scheduled|set)\s+to|'
                       r'to\s+(?:meet|convene)|(?:meeting|meetings|conference)\b.{0,80}\b(?:scheduled|on|at))', re.I)
# A bounded textual extractor, not a timezone/year guesser. Every accepted timestamp contains its own
# four-digit year, clock and UTC/GMT/numeric offset in the same schedule sentence.
OPEC_ISO_WHEN = re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?'
                           r'(?:Z|[+-]\d{2}:?\d{2}|\s+(?:UTC|GMT))\b', re.I)
OPEC_DATE_CLOCK = re.compile(r'\b(\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|'
                             r'September|October|November|December)\s+\d{1,2},?\s+\d{4})'
                             r'\s+(?:at\s+)?(\d{1,2}:\d{2}(?::\d{2})?)\s*(UTC|GMT|[+-]\d{2}:?\d{2})\b', re.I)


def _opec_explicit_times(sentence: str) -> list[tuple[datetime, str]]:
    found = {}
    for match in OPEC_ISO_WHEN.finditer(sentence):
        text = re.sub(r'\s+(UTC|GMT)$', '+00:00', match.group(), flags=re.I)
        try:
            at = datetime.fromisoformat(text.replace('Z', '+00:00').replace('z', '+00:00'))
        except ValueError:
            continue
        if at.tzinfo is not None:
            found[z(at)] = (at, match.group())
    for match in OPEC_DATE_CLOCK.finditer(sentence):
        day, clock, zone = match.groups()
        try:
            if '-' not in day:
                day = datetime.strptime(day.replace(',', '').title(), '%B %d %Y').date().isoformat()
            if len(clock.split(':')[0]) == 1:
                clock = '0'+clock
            zone = '+00:00' if zone.upper() in ('UTC', 'GMT') else zone
            at = datetime.fromisoformat(day+'T'+clock+zone)
        except ValueError:
            continue
        if at.tzinfo is not None:
            found[z(at)] = (at, match.group())
    return list(found.values())


def opec_news_events(news: dict, as_of: datetime) -> tuple[list[dict], list[dict]]:
    """Only explicit timed schedules in a retrieved body may open an OPEC tentative window.

    Publication/retrieval clocks are provenance only. A date alone, an unsupported/ambiguous clock or a
    headline is a lead for a structured or parent-verified schedule, never an invented meeting time.
    """
    events, unresolved = [], []
    for item in news.get('items', []):
        body = item.get('body') or {}
        text = ' '.join((item.get('title', ''), item.get('excerpt', ''), body.get('excerpt', '')))
        if not OPEC_SUBJECT.search(text) or not OPEC_PLAN.search(text):
            continue
        note = {'news_id':item['id'], 'title':item.get('title',''), 'source_url':item.get('url',''),
                'reason':'meeting_date_time_unconfirmed', 'requirement':'年付き日付・時刻・タイムゾーンを明示した本文、または構造化/親確認済みの予定が必要'}
        if body.get('status') != 'retrieved':
            unresolved.append({**note,'reason':'meeting_body_not_retrieved'})
            continue
        if parse_time(item['published_at']) > as_of:
            unresolved.append({**note,'reason':'future_news_publication'})
            continue
        # Search only the retrieved body: a headline's date or the publication header cannot supply the clock.
        candidates = []
        for sentence in re.split(r'[!?。\n]|\.(?:\s|$)', body.get('excerpt', '')):
            if not OPEC_SUBJECT.search(sentence) or not OPEC_PLAN.search(sentence):
                continue
            if re.search(r'\b(?:published|publication|updated|posted|cancelled|canceled|not|no)\b', sentence, re.I):
                continue  # Publication headers and negated/cancelled schedules cannot anchor a meeting.
            times = _opec_explicit_times(sentence)
            if len(times) == 1:
                candidates.append((times[0][0], sentence.strip()))
        unique = {z(t):(t, quote) for t,quote in candidates}
        if len(unique) != 1:
            unresolved.append({**note, 'reason':'meeting_time_ambiguous' if len(unique)>1 else note['reason']})
            continue
        at, quote = next(iter(unique.values()))
        ident = 'opec.news.'+stamp_key(at)
        events.append({'id':ident,'name':'OPEC/OPEC+会合（ニュース本文の明示予定・未確認）',
                       'category':'opec_meeting','start_at':z(at),'end_at':None,'timezone':'UTC',
                       'importance':'high','status':'tentative','source_id':'news','source_url':item['url'],
                       'published_at':item['published_at'],'verified_at':None,'news_id':item['id'],
                       'schedule_evidence_quote':quote[:600], 'timestamp_rule_id':'explicit_schedule_in_retrieved_body'})
    return events, unresolved


def events_state(F: Facts, S: dict, state: dict, as_of: datetime):
    from usoil.sources.calendar import load_cache
    events, status = calendar_events(S, as_of, load_cache())
    news_events, unresolved = opec_news_events(state.get('news', {}), as_of)
    for event in news_events:
        if not any(e.get('category') == 'opec_meeting' and e.get('start_at') == event['start_at'] for e in events):
            events.append(event)
    state.setdefault('news', {})['opec_schedule_unconfirmed'] = unresolved
    state['news']['opec_schedule_detected'] = news_events
    status['opec_schedule_unconfirmed'] = unresolved
    out = []
    for event in events:
        start = event.get('start_at')
        if not start or not as_of-timedelta(days=1) <= parse_time(start) <= as_of+timedelta(days=8):
            continue
        item = {'end_at':None,'published_at':None,'verified_at':None,'source_url':'','timezone':'America/New_York',
                **event, 'name':md_safe(event['name']), 'start_at':z(start)}
        item['stop_window'] = stop_window(item)
        out.append(item)
    # Shared ids (CPI core/headline) represent one release, not multiple score inputs.
    state['events'] = sorted({e['id']:e for e in out}.values(), key=lambda e:e['start_at'])
    state['calendar'] = status
    state['calendar_extra'] = []


def news_state(F: Facts, S: dict, state: dict):
    rec = S.get('news')
    out = {'status':'missing', 'items':[], 'feeds':[], 'selection':None, 'feeds_ok':{}}
    if not _usable(rec):
        state['news'] = out
        return
    v = rec['values']
    for item in v.get('kept',[]):
        if not re.match(r'^https?://[^\s]+$', item.get('url') or ''):
            continue
        out['items'].append({'id':item['id'], 'event_cluster_id':item['event_cluster_id'],
                             'title':md_safe(item['title']), 'url':item['url'], 'publisher':item['publisher'],
                             'official':item['official'], 'published_at':z(item['published_at']),
                             'first_seen_at':z(item['first_seen_at']) if item.get('first_seen_at') else None,
                             'code_verification':item['code_verification'], 'body':_safe_body(item.get('body') or {}),
                             'excerpt':md_safe(item.get('excerpt','')), 'p':item.get('p'), 'reaction':None,
                             'fact_ids':[], 'feed_raw_sha256':item.get('feed_raw_sha256')})
    out.update({k:v.get(k) for k in ('feeds','selection','selection_rule','candidate_count','window_start','window_end',
                                    'lookback_hours','feeds_ok')})
    out['status'] = 'missing' if not out['items'] and not sum((out.get('feeds_ok') or {}).values()) else rec['status']
    state['news'] = out


def source_health(S: dict, as_of: datetime) -> list[dict]:
    return [{'source_id':key, 'status':{'unavailable':'missing'}.get(rec['status'],rec['status']),
             'checked_at':z(rec.get('retrieved_at') or as_of), 'last_observed_at':z(rec.get('observed_at')),
             'reason':rec.get('error_kind') or ''} for key,rec in S.items()]


def build(collection: dict, *, mode: str, session_slot: str, collection_path, history_dir) -> dict:
    as_of = parse_time(collection['collection_completed_at']).astimezone(UTC)
    F = Facts(as_of, digest(collection_path))
    S = {r['source_id']:r for r in collection['sources']}
    state = {}
    price_facts(F,S,state)
    levels_facts(F,S,state,as_of)
    curve_facts(F,S,state)
    eia_facts(F,S,state,History(history_dir),as_of)
    cot_facts(F,S,state,as_of)
    vol_facts(F,S,state)
    macro_facts(F,S,state,as_of)
    news_state(F,S,state)
    events_state(F,S,state,as_of)
    carry_state(state,history_dir,as_of)
    state['source_health'] = source_health(S,as_of)
    state['terms_restricted'] = [{'source_id':t['source_id'],'reason':t['reason']} for t in collection.get('terms_restricted',[])
                                 if t.get('status')=='terms_restricted']
    state['not_used'] = [{'source_id':t['source_id'],'reason':t['reason']} for t in collection.get('terms_restricted',[])
                         if t.get('status')!='terms_restricted']
    state['collection'] = {'started_at':z(collection['collection_started_at']),'completed_at':z(as_of),
                           **{k:collection.get(k) for k in ('elapsed_ms','budget_exhausted','collector_version','news_lookback_hours')}}
    return {'schema_version':SCHEMA_VERSION,'symbol':SYMBOL,'asset':SYMBOL,'mode':mode,'session_slot':session_slot,
            'as_of':z(as_of),'collected_at':z(collection['collection_started_at']), 'input_manifest_sha256':F.manifest,
            'facts':F.items,'state':state}


def index(facts: dict) -> dict:
    return {f['fact_id']:f for f in facts['facts']}


def dumps_state(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True)
