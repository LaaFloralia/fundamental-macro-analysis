"""CFTC Commitments of Traders (disaggregated, futures-only) for NYMEX light sweet crude (067651).

The Socrata dataset is queried with the market code, newest first, and the
series is stored oldest -> newest with a computed ``mm_net`` (managed-money
long minus short). ``report_date`` is the CFTC's own as-of date, so the record
is ``source_date`` quality; no observation time is invented.
"""
from __future__ import annotations

from datetime import date

from usoil.fetch import FetchError, Fetcher, SourceResult
from usoil.sources.base import json_of, num, ok

COT_URL = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
COT_CONTRACT_CODE = '067651'
COT_ROWS = 156  # 3 years of weekly reports
NUMBER_FIELDS = {
    'mm_long': 'm_money_positions_long_all',
    'mm_short': 'm_money_positions_short_all',
    'mm_spread': 'm_money_positions_spread',
    'prod_long': 'prod_merc_positions_long',
    'prod_short': 'prod_merc_positions_short',
    'swap_long': 'swap_positions_long_all',
    'swap_short': 'swap__positions_short_all',  # the Socrata field really has two underscores
    'open_interest': 'open_interest_all',
    'change_mm_long': 'change_in_m_money_long_all',
    'change_mm_short': 'change_in_m_money_short_all',
    'change_open_interest': 'change_in_open_interest_all',
    'traders_mm_long': 'traders_m_money_long_all',
    'traders_mm_short': 'traders_m_money_short_all',
}


def _number(row: dict, key: str):
    try:
        return num(row.get(key), allow_none=True)
    except FetchError:
        return None


def _row(row: dict) -> dict:
    item = {'report_date': str(row['report_date_as_yyyy_mm_dd'])[:10]}
    try:
        date.fromisoformat(item['report_date'])
    except ValueError:
        raise FetchError('invalid_payload') from None
    if row.get('cftc_contract_market_code') != COT_CONTRACT_CODE:
        raise FetchError('invalid_payload')
    if row.get('futonly_or_combined') != 'FutOnly':
        raise FetchError('invalid_payload')
    item.update({name: _number(row, key) for name, key in NUMBER_FIELDS.items()})
    if any(item[k] is not None and item[k] < 0 for k in ('mm_long', 'mm_short', 'open_interest')):
        raise FetchError('invalid_payload')
    if item['open_interest'] == 0:
        raise FetchError('invalid_payload')
    item['mm_net'] = (None if item['mm_long'] is None or item['mm_short'] is None
                      else item['mm_long'] - item['mm_short'])
    return item


def cot(f: Fetcher) -> SourceResult:
    r = f.get(COT_URL, params={'$where': f"cftc_contract_market_code='{COT_CONTRACT_CODE}'",
                               '$order': 'report_date_as_yyyy_mm_dd DESC', '$limit': str(COT_ROWS)})
    rows = json_of(r)
    if not isinstance(rows, list) or not rows:
        raise FetchError('empty_payload')
    series = [_row(row) for row in rows
              if isinstance(row, dict) and row.get('report_date_as_yyyy_mm_dd')]
    if not series:
        raise FetchError('missing_field')
    series.sort(key=lambda item: item['report_date'])
    if len({item['report_date'] for item in series}) != len(series):
        raise FetchError('invalid_payload')
    latest = dict(series[-1])
    latest_raw = next((row for row in rows if str(row.get('report_date_as_yyyy_mm_dd', ''))[:10]
                       == latest['report_date']), rows[0])
    contract = {'code': COT_CONTRACT_CODE,
                'name': str(latest_raw.get('contract_market_name') or '').strip() or None,
                'market': str(latest_raw.get('market_and_exchange_names') or '').strip() or None,
                'units': str(latest_raw.get('contract_units') or '').strip() or None,
                'report_type': str(latest_raw.get('futonly_or_combined') or '').strip() or None}
    return ok('cftc_cot', [r], {'provider': 'CFTC Commitments of Traders (disaggregated, futures-only)',
                                'contract': contract, 'series': series, 'latest': latest,
                                'count': len(series)},
              timestamp_quality='source_date')
