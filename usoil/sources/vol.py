"""Cboe OVX (crude oil volatility index) delayed quote: last, previous close and the day's OHLC.

One request only (the CDN JSON endpoint, no redirect). The observation time is
the provider's ``last_trade_time`` (ISO with offset); the daily close series
used by the percentile rule comes from FRED ``OVXCLS`` (usoil.sources.macro),
not from here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math

from usoil.fetch import FetchError, Fetcher, SourceResult
from usoil.sources.base import num, ok, z

OVX_URL = 'https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_OVX.json'


def _number(row: dict, key: str):
    try:
        return num(row.get(key), allow_none=True)
    except FetchError:
        return None


def ovx(f: Fetcher) -> SourceResult:
    r = f.get(OVX_URL)
    data = r.json()
    row = data.get('data') if isinstance(data, dict) else None
    if not isinstance(row, dict):
        raise FetchError('invalid_payload')
    last, prev = _number(row, 'current_price'), _number(row, 'prev_day_close')
    if not last or not prev or prev <= 0 or last <= 0:
        raise FetchError('missing_field')
    change_pct = (last / prev - 1) * 100
    if not math.isfinite(change_pct):
        raise FetchError('invalid_payload')
    trade = str(row.get('last_trade_time') or '').strip()
    try:
        at = datetime.fromisoformat(trade)
        observed = z(at)
    except ValueError:
        raise FetchError('missing_field') from None
    if at > datetime.fromisoformat(r.retrieved_at) + timedelta(seconds=60):
        raise FetchError('future_observation')
    return ok('cboe_ovx', [r], {'provider': 'Cboe (delayed quotes)', 'symbol': 'OVX',
                                'last': last, 'previous_close': prev, 'change': last - prev,
                                'change_pct': change_pct,
                                'open': _number(row, 'open'), 'high': _number(row, 'high'),
                                'low': _number(row, 'low'),
                                'note': '日中値は遅延表示。終値系列はFRED OVXCLS（sources.macro）を使う'},
              observed_at=observed)
