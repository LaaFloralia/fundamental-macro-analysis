"""Stablecoin supply, mempool fees, BTC dominance, US Treasury yields, FRED series and same-day quotes.

FRED is the only credentialed source (FRED_API_KEY, P2/P8). The key is passed
as a query parameter and removed from every recorded URL. ``DTWEXBGS`` is the
FRED broad US dollar index, never "DXY"; equity series are cash index closes.
``cnbc_quotes`` is the same-day complement: the US 2-year yield, the ICE US
Dollar Index (DXY) and the Nasdaq Composite with their previous closes.
``forexfactory_week`` is this week's US calendar with previous and forecast values
(no actuals in the feed); it is display only.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
import re
import xml.etree.ElementTree as ET

from btc.fetch import FetchError, Fetcher, SourceResult
from btc.sources.base import from_s, json_of, num, ok, z

UTC = timezone.utc
STABLES = ('USDT', 'USDC')
FRED_SERIES = {
    # series: (label, unit, frequency)
    'DTWEXBGS': ('米ドル広義指数（FRED）', 'index', 'daily'),
    'VIXCLS': ('VIX（現物指数・終値）', 'index', 'daily'),
    'NASDAQCOM': ('Nasdaq総合（現物指数・終値）', 'index', 'daily'),
    'SP500': ('S&P 500（現物指数・終値）', 'index', 'daily'),
    'WALCL': ('Fed総資産（水曜時点）', 'USD million', 'weekly'),
    'WDTGAL': ('財務省一般口座TGA（水曜時点）', 'USD million', 'weekly'),
    'RRPONTSYD': ('翌日物リバースレポ', 'USD billion', 'daily'),
    'M2SL': ('M2（月次）', 'USD billion', 'monthly'),
}
CNBC_URL = 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'
CNBC_SYMBOLS = ('US2Y', '.DXY', '.IXIC')
CNBC_REQUIRED = ('US2Y', '.DXY')
CNBC_FUTURE_TOLERANCE = timedelta(seconds=60)
FF_URL = 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'
FF_IMPACT = {'High': 'high', 'Medium': 'medium'}
FF_VALUE = re.compile(r'^[-+]?\d[\d,]*(?:\.\d+)?[%KMBT]?$')
FF_TITLE = re.compile(r"^[A-Za-z0-9 /.,'&()\-]{3,80}$")


def defillama(f: Fetcher) -> SourceResult:
    current = f.get('https://stablecoins.llama.fi/stablecoins', params={'includePrices': 'true'})
    history = f.get('https://stablecoins.llama.fi/stablecoincharts/all')
    assets = {}
    for row in json_of(current).get('peggedAssets', []):
        if row.get('symbol') in STABLES and row.get('pegType') == 'peggedUSD':
            assets[row['symbol']] = {'supply_usd': num((row.get('circulating') or {}).get('peggedUSD')),
                                     'price': num(row.get('price'), allow_none=True)}
    series = []
    for row in json_of(history)[-60:]:
        value = num(((row.get('totalCirculatingUSD') or {}).get('peggedUSD')), allow_none=True)
        if value is not None:
            series.append({'date': datetime.fromtimestamp(int(row['date']), UTC).date().isoformat(), 'supply_usd': value})
    if len(series) < 31:
        raise FetchError('missing_field')
    return ok('defillama', {'current': current, 'history': history}, {'scope': 'all peggedUSD stablecoins (DefiLlama aggregate)',
                                                'daily': series, 'assets': assets},
              timestamp_quality='source_date')


def mempool(f: Fetcher) -> SourceResult:
    r = f.get('https://mempool.space/api/v1/fees/recommended')
    d = json_of(r)
    values = {k: num(d[k]) for k in ('fastestFee', 'halfHourFee', 'hourFee', 'economyFee', 'minimumFee')}
    return ok('mempool_fees', [r], {'fees_sat_vb': values}, timestamp_quality='receipt_interval')


def coingecko_global(f: Fetcher) -> SourceResult:
    r = f.get('https://api.coingecko.com/api/v3/global')
    d = json_of(r)['data']
    share = num(d['market_cap_percentage']['btc'])
    if not 0 < share < 100:
        raise FetchError('invalid_payload')
    return ok('coingecko_global', [r], {'btc_dominance_pct': share, 'attribution': 'CoinGecko'},
              observed_at=from_s(d['updated_at']))


def _treasury_month(f: Fetcher, data: str, month: str):
    r = f.get('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml',
              params={'data': data, 'field_tdr_date_value_month': month})
    if b'<!DOCTYPE' in r.body[:2000].upper() or b'<!ENTITY' in r.body.upper():
        raise FetchError('invalid_payload')
    try:
        root = ET.fromstring(r.body)
    except ET.ParseError:
        raise FetchError('parse_error') from None
    rows = []
    for props in root.iter('{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties'):
        item = {}
        for child in props:
            item[child.tag.rsplit('}', 1)[-1]] = (child.text or '').strip()
        if item.get('NEW_DATE'):
            rows.append(item)
    return r, rows


def treasury(f: Fetcher, today: date | None = None) -> SourceResult:
    today = today or datetime.now(UTC).date()
    months = [today.strftime('%Y%m'), (today.replace(day=1) - timedelta(days=1)).strftime('%Y%m')]
    responses, nominal, real = {}, {}, {}
    for month in months:
        r, rows = _treasury_month(f, 'daily_treasury_yield_curve', month)
        responses[f'nominal_{month}'] = r
        for row in rows:
            day = row['NEW_DATE'][:10]
            nominal[day] = {'y2': num(row.get('BC_2YEAR'), allow_none=True), 'y10': num(row.get('BC_10YEAR'), allow_none=True),
                            'raw': r.raw_sha256}
        r, rows = _treasury_month(f, 'daily_treasury_real_yield_curve', month)
        responses[f'real_{month}'] = r
        for row in rows:
            real[row['NEW_DATE'][:10]] = (num(row.get('TC_10YEAR'), allow_none=True), r.raw_sha256)
    days = sorted(nominal)
    if not days:
        raise FetchError('empty_payload')
    series = [{'date': d, 'y2_pct': nominal[d]['y2'], 'y10_pct': nominal[d]['y10'], 'nominal_raw': nominal[d]['raw'],
               'real10_pct': (real.get(d) or (None, None))[0], 'real_raw': (real.get(d) or (None, None))[1]} for d in days]
    return ok('treasury_yields', responses, {'series': series[-30:], 'unit': 'percent',
                                             'note': 'Daily Treasury Par Yield Curve / Real Yield Curve (end of day)'},
              timestamp_quality='source_date')


def fred(f: Fetcher, env=os.environ) -> SourceResult:
    key = env.get('FRED_API_KEY')
    if not key:
        raise FetchError('credentials_missing')
    responses, series = {}, {}
    failed = []
    for sid, (label, unit, freq) in FRED_SERIES.items():
        limit = {'daily': 40, 'weekly': 12, 'monthly': 6}[freq]
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
    return value if value.is_finite() else None


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


def forexfactory_week(f: Fetcher) -> SourceResult:
    """US events of impact High/Medium this week. Values are kept only when they look like a number."""
    r = f.get(FF_URL)
    data = json_of(r)
    if not isinstance(data, list):
        raise FetchError('invalid_payload')
    events = []
    for row in data:
        if not isinstance(row, dict) or row.get('country') != 'USD' or row.get('impact') not in FF_IMPACT:
            continue
        title = str(row.get('title') or '').strip()
        try:
            when = datetime.fromisoformat(str(row.get('date')))
        except ValueError:
            continue
        if when.tzinfo is None or not FF_TITLE.match(title):
            continue
        value = lambda key: (lambda v: v if FF_VALUE.match(v) else '')(str(row.get(key) or '').strip())  # noqa: E731
        events.append({'title': title, 'start_at': z(when), 'importance': FF_IMPACT[row['impact']],
                       'previous': value('previous'), 'forecast': value('forecast')})
    if not events:
        raise FetchError('empty_payload')
    events.sort(key=lambda e: (e['start_at'], e['title']))
    return ok('forexfactory_week', [r], {'provider': 'ForexFactory (faireconomy.media)', 'events': events})


def cnbc_quotes(f: Fetcher) -> SourceResult:
    r = f.get(CNBC_URL, params={'symbols': '|'.join(CNBC_SYMBOLS), 'requestMethod': 'itv', 'noform': '0',
                                'partnerId': '2', 'fund': '1', 'exthrs': '1', 'output': 'json', 'events': '1'})
    data = json_of(r)
    rows = ((data.get('FormattedQuoteResult') or {}).get('FormattedQuote') or []) if isinstance(data, dict) else []
    received = datetime.fromisoformat(r.retrieved_at)
    quotes, failed = {}, []
    for row in rows:
        symbol = row.get('symbol') if isinstance(row, dict) else None
        if symbol not in CNBC_SYMBOLS or symbol in quotes:
            continue
        last, prev = _quote_decimal(row.get('last')), _quote_decimal(row.get('previous_day_closing'))
        at = _quote_time(row.get('last_time'))
        if last is None or prev is None or prev == 0 or at is None:
            failed.append({'symbol': symbol, 'error_kind': 'missing_field'})
            continue
        if isinstance(at, datetime) and at > received + CNBC_FUTURE_TOLERANCE:
            failed.append({'symbol': symbol, 'error_kind': 'future_observation'})
            continue
        # The change is last minus previous close, exact in decimal and not rounded (as scrapers/us_yields.py):
        # thresholds judge it unrounded and only the display rounds.
        quotes[symbol] = {'last': float(last), 'previous_close': float(prev), 'change': float(last - prev),
                          'change_pct': float((last / prev - 1) * 100),
                          'last_time': z(at) if isinstance(at, datetime) else at.isoformat(),
                          'time_kind': 'quote_time' if isinstance(at, datetime) else 'close_date'}
    if not quotes:
        raise FetchError('missing_field')
    missing = [s for s in CNBC_SYMBOLS if s not in quotes]
    stamps = [q['last_time'] for s, q in quotes.items() if s in CNBC_REQUIRED and q['time_kind'] == 'quote_time']
    return ok('cnbc_quotes', [r], {'provider': 'CNBC', 'quotes': quotes, 'missing': missing, 'failed': failed,
                                   'unit': {'US2Y': 'percent', '.DXY': 'index', '.IXIC': 'index'}},
              observed_at=max(stamps) if stamps else None,
              status='ok' if all(s in quotes for s in CNBC_REQUIRED) else 'partial')
