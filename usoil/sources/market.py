"""Yahoo Finance chart bars for CL=F / BZ=F / DX-Y.NYB (the OHLC replacement for the retired Kraken bars).

Parts: ``cl_d1`` (1 d, 3 mo), ``cl_h1`` (1 h, 5 d), ``bz_d1``, ``bz_h1`` and
``dxy_d1`` (DX-Y.NYB auxiliary). Only closed bars are kept: Yahoo returns the
still-forming bar with a null close, and a bar whose end is after the retrieval
is dropped as well. A bar that fails ``0 < low <= min(open, close) <=
max(open, close) <= high`` is dropped and counted, never repaired.

Requests are serialised with a short spacing sleep (the quote API rate-limits
bursts); ``cl_d1`` and ``cl_h1`` are required, the other parts only make the
result partial when they fail. ``roll_note`` flags that CL=F follows the
exchange contract month (e.g. Dec 2026) while CNBC ``@CL.1`` is the front
month; the cross-source roll comparison belongs to the facts layer.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from usoil.fetch import FetchError, Fetcher, SourceResult
from usoil.sources.base import json_of, num, ok, z

UTC = timezone.utc

YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/'
YAHOO_PARTS = (
    # part: (symbol, range, interval, bar minutes)
    ('cl_d1', 'CL=F', '3mo', '1d', 1440),
    ('cl_h1', 'CL=F', '5d', '60m', 60),
    ('bz_d1', 'BZ=F', '3mo', '1d', 1440),
    ('bz_h1', 'BZ=F', '5d', '60m', 60),
    ('dxy_d1', 'DX-Y.NYB', '1mo', '1d', 1440),
)
YAHOO_REQUIRED = ('cl_d1', 'cl_h1')
YAHOO_SPACING = 1.5  # seconds between chart requests: 429/backoff protection (design 4.1)
YAHOO_UNITS = {'CL=F': 'USD/bbl', 'BZ=F': 'USD/bbl', 'DX-Y.NYB': 'index'}
YAHOO_META_KEYS = ('symbol', 'currency', 'exchangeName', 'instrumentType', 'regularMarketPrice',
                   'previousClose', 'chartPreviousClose', 'shortName', 'timezone')
YAHOO_ROLL_NOTE = ('Yahoo CL=F は取引所の限月（例: Dec 2026）で、CNBC @CL.1（フロント限月）と限月が違う場合がある。'
                   '限月跨ぎの検出はクロス源比較（facts層）で行う。')

def _cell(values, index: int):
    """Numeric cell (None for missing / null / junk); never raises."""
    if not isinstance(values, list) or index >= len(values):
        return None
    try:
        return num(values[index], allow_none=True)
    except FetchError:
        return None


def _chart_bars(r, minutes: int):
    data = json_of(r)
    results = data.get('chart', {}).get('result') if isinstance(data, dict) else None
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise FetchError('missing_field')
    result = results[0]
    stamps = result.get('timestamp') or []
    quote_rows = (result.get('indicators') or {}).get('quote') or []
    cols = quote_rows[0] if quote_rows and isinstance(quote_rows[0], dict) else {}
    count = min(len(stamps), *(len(cols.get(key) or []) for key in ('open', 'high', 'low', 'close')))
    received = datetime.fromisoformat(r.retrieved_at).timestamp()
    bars, invalid = [], 0
    for i in range(count):
        try:
            start = int(num(stamps[i]))
        except FetchError:
            invalid += 1
            continue
        if start + minutes * 60 > received:
            continue  # bar still forming at retrieval (Yahoo also sends close=null for it)
        o, h, l, c = (_cell(cols.get(key), i) for key in ('open', 'high', 'low', 'close'))
        if c is None:
            continue  # still-forming bar
        if o is None or h is None or l is None or not 0 < l <= min(o, c) <= max(o, c) <= h:
            invalid += 1  # dropped, never repaired: the hole shows up as a gap caution in usoil/levels.py
            continue
        bars.append({'t': z(datetime.fromtimestamp(start, UTC)), 'o': o, 'h': h, 'l': l, 'c': c,
                     'v': _cell(cols.get('volume'), i)})
    if not bars:
        raise FetchError('empty_payload')
    return r, bars, invalid, result.get('meta')


def yahoo_ohlc(f: Fetcher, sleep=time.sleep, spacing: float = YAHOO_SPACING) -> SourceResult:
    responses, symbols, failures, invalid = {}, {}, [], {}
    first = True
    for part, symbol, range_, interval, minutes in YAHOO_PARTS:
        if not first and spacing:
            sleep(spacing)
        first = False
        response = None
        try:
            response = f.get(f'{YAHOO_URL}{symbol}', params={'range': range_, 'interval': interval})
            r, bars, bad, meta = _chart_bars(response, minutes)
        except (FetchError, KeyError, IndexError, TypeError) as error:
            failures.append({'part': part, 'error_kind': error.kind if isinstance(error, FetchError) else 'missing_field'})
            continue
        responses[part] = r
        bucket = symbols.setdefault(symbol, {'d1': [], 'h1': [], 'invalid_bars': {}})
        kind = 'd1' if minutes == 1440 else 'h1'
        bucket[kind] = bars
        bucket['invalid_bars'][kind] = bad
        invalid[part] = bad
        if meta and 'meta' not in bucket:
            bucket['meta'] = {key: meta.get(key) for key in YAHOO_META_KEYS if key in meta}
    cl = symbols.get('CL=F') or {}
    if not cl.get('d1') and not cl.get('h1'):
        kinds = [item['error_kind'] for item in failures if item['part'] in YAHOO_REQUIRED]
        raise FetchError(kinds[0] if kinds else 'empty_payload')
    last = cl.get('h1') or cl.get('d1')
    observed = z(datetime.fromisoformat(last[-1]['t'].replace('Z', '+00:00'))
                 + timedelta(minutes=60 if cl.get('h1') else 1440))
    return ok('yahoo_ohlc', responses, {'provider': 'Yahoo Finance chart API', 'symbols': symbols,
                                        'invalid_bars': invalid, 'failures': failures,
                                        'roll_note': YAHOO_ROLL_NOTE, 'spacing_seconds': spacing,
                                        'units': dict(YAHOO_UNITS)},
              observed_at=observed, status='ok' if not failures else 'partial')
