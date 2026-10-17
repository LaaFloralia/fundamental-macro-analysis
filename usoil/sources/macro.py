"""FRED series (the only credentialed source) and same-day CNBC quotes for macro alignment.

FRED: ``FRED_API_KEY`` is passed as a query parameter and removed from every
recorded URL (P2/P8). ``DTWEXBGS`` is the FRED broad US dollar index, never
"DXY". ``DCOILWTICO``/``DCOILBRENTEU`` are spot assessments that diverge from
the futures complex: they stay reference-only and are never scored (design
4.2). ``OVXCLS`` keeps 320 daily rows because the volatility rule uses a
252-business-day percentile (factor model, vol_tail_risk).

``cnbc_quotes`` is the same-day complement: WTI/Brent front- and second-month
futures (``@CL.1`` … ``@BZ.2``), Henry Hub (``@NG.1``), the ICE US Dollar Index
(``.DXY``) and the 2/10-year Treasury yields with their previous closes and the
extended quote fields (open/high/low/volume/open interest/expiry). Every
returned row is parsed generically (the facts layer also reads ``.IXIC``);
``missing`` lists what CNBC_SYMBOLS did not return. ``@CL.1`` and ``.DXY`` are
required for an ``ok`` status.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import os
import re

from usoil.fetch import FetchError, Fetcher, SourceResult
from usoil.sources.base import json_of, num, ok, z

UTC = timezone.utc
FRED_SERIES = {
    # series: (label, unit, frequency)
    'DTWEXBGS': ('米ドル広義指数（FRED）', 'index', 'daily'),
    'VIXCLS': ('VIX（現物指数・終値）', 'index', 'daily'),
    'SP500': ('S&P 500（現物指数・終値）', 'index', 'daily'),
    'DFII10': ('米10年実質金利（TIPS・FRED）', 'percent', 'daily'),
    'OVXCLS': ('OVX（原油ボラ指数・終値）', 'index', 'daily'),
    'DCOILWTICO': ('WTI現物（EIA・参照に使わない）', 'USD/bbl', 'daily'),
    'DCOILBRENTEU': ('Brent現物（EIA・参照に使わない）', 'USD/bbl', 'daily'),
    'DGS2': ('米2年債利回り（FRED）', 'percent', 'daily'),
    'T10YIE': ('10年期待インフレ（FRED）', 'percent', 'daily'),
    'BAMLH0A0HYM2': ('米ハイイールドOAS（FRED）', 'percent', 'daily'),
}
FRED_LIMITS = {'OVXCLS': 320}  # 252営業日百分位に必要な履歴（他は頻度別の既定値）
CNBC_URL = 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'
CNBC_SYMBOLS = ('@CL.1', '@CL.2', '@CL.3', '@BZ.1', '@BZ.2', '@NG.1', '.DXY', 'US2Y', 'US10Y')
CNBC_REQUIRED = ('@CL.1', '.DXY')
CNBC_FUTURE_TOLERANCE = timedelta(seconds=60)
CNBC_UNITS = {'@CL.1': 'USD/bbl', '@CL.2': 'USD/bbl', '@CL.3': 'USD/bbl',
              '@BZ.1': 'USD/bbl', '@BZ.2': 'USD/bbl', '@NG.1': 'USD/MMBtu',
              '.DXY': 'index', 'US2Y': 'percent', 'US10Y': 'percent'}
CNBC_SYMBOL_RE = re.compile(r'^[.@A-Za-z0-9_-]{1,20}$')


def fred(f: Fetcher, env=os.environ) -> SourceResult:
    key = env.get('FRED_API_KEY')
    if not key:
        raise FetchError('credentials_missing')
    responses, series = {}, {}
    failed = []
    for sid, (label, unit, freq) in FRED_SERIES.items():
        limit = FRED_LIMITS.get(sid) or {'daily': 40, 'weekly': 12, 'monthly': 6}[freq]
        try:
            r = f.get('https://api.stlouisfed.org/fred/series/observations', params={
                'series_id': sid, 'api_key': key, 'file_type': 'json', 'sort_order': 'desc', 'limit': limit},
                secret_params=['api_key'])
        except FetchError as error:
            failed.append({'series_id': sid, 'error_kind': error.kind})
            continue
        responses[sid] = r
        rows = []
        for obs in json_of(r).get('observations', []):
            value = num(obs.get('value'), allow_none=True)
            if value is not None:
                rows.append({'date': obs['date'], 'value': value})
        rows.sort(key=lambda x: x['date'])
        series[sid] = {'label': label, 'unit': unit, 'frequency': freq, 'rows': rows, 'raw': r.raw_sha256}
    if not series:
        raise FetchError(failed[0]['error_kind'] if failed else 'empty_payload')
    return ok('fred', responses, {'series': series, 'failed': failed},
              status='ok' if not failed else 'partial', timestamp_quality='source_date')


def _quote_decimal(text) -> Decimal | None:
    """'4.734%' / '26,692.868' as an exact decimal; empty, 'N/A' or non-finite text is None."""
    cleaned = str(text if text is not None else '').replace('%', '').replace(',', '').strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    converted = float(value)
    return value if math.isfinite(converted) and (not value or converted != 0) else None


def _quote_float(text):
    value = _quote_decimal(text)
    return float(value) if value is not None else None


def _quote_expiry(text):
    text = str(text if text is not None else '').strip()
    return text if len(text) == 10 and text[4] == '-' and text[7] == '-' else None


def _quote_time(text):
    """Quote time: '2026-10-15T05:37:13.000-0400' -> aware datetime; '2026-10-14' (cash close) -> date."""
    text = str(text or '').strip()
    try:
        return datetime.strptime(text, '%Y-%m-%dT%H:%M:%S.%f%z')
    except ValueError:
        pass
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def cnbc_quotes(f: Fetcher) -> SourceResult:
    r = f.get(CNBC_URL, params={'symbols': '|'.join(CNBC_SYMBOLS), 'requestMethod': 'itv', 'noform': '0',
                                'partnerId': '2', 'fund': '1', 'exthrs': '1', 'output': 'json', 'events': '1'})
    data = json_of(r)
    rows = ((data.get('FormattedQuoteResult') or {}).get('FormattedQuote') or []) if isinstance(data, dict) else []
    received = datetime.fromisoformat(r.retrieved_at)
    quotes, failed = {}, []
    for row in rows:
        symbol = row.get('symbol') if isinstance(row, dict) else None
        if not symbol or not CNBC_SYMBOL_RE.fullmatch(symbol) or symbol in quotes:
            continue
        last, prev = _quote_decimal(row.get('last')), _quote_decimal(row.get('previous_day_closing'))
        at = _quote_time(row.get('last_time'))
        if last is None or prev is None or prev == 0 or at is None:
            if symbol in CNBC_SYMBOLS:
                failed.append({'symbol': symbol, 'error_kind': 'missing_field'})
            continue
        if isinstance(at, datetime) and at > received + CNBC_FUTURE_TOLERANCE:
            if symbol in CNBC_SYMBOLS:
                failed.append({'symbol': symbol, 'error_kind': 'future_observation'})
            continue
        change, change_pct = float(last - prev), float((last / prev - 1) * 100)
        if not math.isfinite(change) or not math.isfinite(change_pct):
            if symbol in CNBC_SYMBOLS:
                failed.append({'symbol': symbol, 'error_kind': 'invalid_payload'})
            continue
        # The change is last minus previous close, exact in decimal and not rounded (as scrapers/us_yields.py):
        # thresholds judge it unrounded and only the display rounds.
        quotes[symbol] = {'last': float(last), 'previous_close': float(prev), 'change': change,
                          'change_pct': change_pct,
                          'last_time': z(at) if isinstance(at, datetime) else at.isoformat(),
                          'time_kind': 'quote_time' if isinstance(at, datetime) else 'close_date',
                          'open': _quote_float(row.get('open')), 'high': _quote_float(row.get('high')),
                          'low': _quote_float(row.get('low')), 'volume': _quote_float(row.get('volume')),
                          'open_interest': _quote_float(row.get('open_interest')),
                          'expiration_date': _quote_expiry(row.get('expiration_date')),
                          'unit': CNBC_UNITS.get(symbol)}
    if not quotes:
        raise FetchError('missing_field')
    missing = [s for s in CNBC_SYMBOLS if s not in quotes]
    stamps = [q['last_time'] for s, q in quotes.items() if s in CNBC_REQUIRED and q['time_kind'] == 'quote_time']
    return ok('cnbc_quotes', [r], {'provider': 'CNBC', 'quotes': quotes, 'missing': missing, 'failed': failed},
              observed_at=max(stamps) if stamps else None,
              status='ok' if not missing and not failed else 'partial')
