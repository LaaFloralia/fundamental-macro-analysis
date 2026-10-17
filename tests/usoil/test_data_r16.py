"""Synthetic oil source, macro, curve and continuous-contract level regressions. No network."""
import copy
from datetime import date, datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from usoil import collect, levels, scoring
from usoil.facts import Facts, curve_facts, levels_facts, macro_facts, ny_trading_date, price_facts
from usoil.fetch import FetchError, Response
from usoil.jsonschema_lite import validate as schema_validate
from usoil.pipeline import FACTS_SCHEMA
from usoil.sources import calendar, cot, eia, macro, market, vol
from tests.usoil.test_pipeline import load_collection

UTC = timezone.utc
MANIFEST = 'a' * 64
H = 3600
CHART_ROOT = 'https://query1.finance.yahoo.com/v8/finance/chart/'

class FakeFetcher:
    """Serves JSON payloads per URL (callable(url, params) or fixed), stamped with a fixed retrieval time."""

    def __init__(self, route, retrieved_at: datetime):
        self.route = route
        self.retrieved_at = retrieved_at.isoformat()
        self.calls = []

    def get(self, url, *, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        body = self.route(url, params or {}) if callable(self.route) else self.route
        return Response(200, json.dumps(body).encode(), url, self.retrieved_at, 1, 1.0, 'application/json',
                        self.retrieved_at)


class FakeRawFetcher:
    """Serves one raw body (CSV/text, not JSON), stamped with a fixed retrieval time."""

    def __init__(self, body: bytes, retrieved_at: datetime, content_type: str = 'text/csv'):
        self.body = body
        self.retrieved_at = retrieved_at.isoformat()
        self.content_type = content_type
        self.calls = []

    def get(self, url, *, params=None, **kwargs):
        self.calls.append((url, dict(params or {})))
        return Response(200, self.body, url, self.retrieved_at, 1, 1.0, self.content_type, self.retrieved_at)


def record(result) -> dict:
    return result.to_dict()


def cnbc_payload(us2y=('4.734%', '4.802%', '2026-10-15T05:37:13.000-0400'),
                 dxy=('101.957', '102.55', '2026-10-15T05:37:00.000-0400'),
                 cl1=('60.34', '60.10', '2026-10-15T05:37:00.000-0400'), drop=()):
    # The extended fields come only on the futures rows (CNBC omits them for index/yield symbols).
    extras = {'@CL.1': {'open': '59.90', 'high': '60.55', 'low': '59.80', 'volume': '61,835',
                        'open_interest': '84922', 'expiration_date': '2026-10-20'}}
    rows = []
    for symbol, q in (('US2Y', us2y), ('.DXY', dxy), ('@CL.1', cl1), ('@CL.2', cl1), ('@CL.3', cl1),
                      ('@BZ.1', cl1), ('@BZ.2', cl1), ('@NG.1', cl1), ('US10Y', cl1)):
        if symbol in drop:
            continue
        rows.append({'symbol': symbol, 'last': q[0], 'previous_day_closing': q[1], 'last_time': q[2],
                     'change_pct': '9.99%',  # provider's own change is never used
                     **extras.get(symbol, {})})
    return {'FormattedQuoteResult': {'FormattedQuote': rows}}


def cnbc_record(payload, retrieved=datetime(2026, 10, 15, 9, 40, tzinfo=UTC)):
    return record(macro.cnbc_quotes(FakeFetcher(payload, retrieved)))


def test_cnbc_parsing_values_params_and_exact_change():
    fetcher = FakeFetcher(cnbc_payload(), datetime(2026, 10, 15, 9, 40, tzinfo=UTC))
    rec = record(macro.cnbc_quotes(fetcher))
    url, params = fetcher.calls[0]
    assert url == 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'
    assert params == {'symbols': '@CL.1|@CL.2|@CL.3|@BZ.1|@BZ.2|@NG.1|.DXY|US2Y|US10Y', 'requestMethod': 'itv',
                      'noform': '0', 'partnerId': '2', 'fund': '1', 'exthrs': '1', 'output': 'json', 'events': '1'}
    q = rec['values']['quotes']
    assert rec['status'] == 'ok' and rec['values']['missing'] == []
    assert q['US2Y'] == {'last': 4.734, 'previous_close': 4.802, 'change': -0.068, 'change_pct': pytest.approx(-1.41607663),
                         'last_time': '2026-10-15T09:37:13Z', 'time_kind': 'quote_time', 'open': None, 'high': None,
                         'low': None, 'volume': None, 'open_interest': None, 'expiration_date': None,
                         'unit': 'percent'}
    # The futures rows keep their extended fields: comma volume, open interest and expiry included as-is.
    assert q['@CL.1'] == {'last': 60.34, 'previous_close': 60.10, 'change': 0.24, 'change_pct': pytest.approx(0.3993344),
                          'last_time': '2026-10-15T09:37:00Z', 'time_kind': 'quote_time', 'open': 59.9, 'high': 60.55,
                          'low': 59.8, 'volume': 61835.0, 'open_interest': 84922.0, 'expiration_date': '2026-10-20',
                          'unit': 'USD/bbl'}
    assert q['.DXY']['last'] == 101.957 and q['.DXY']['previous_close'] == 102.55
    assert '.IXIC' not in q  # retired crypto-era macro population is not collected
    assert rec['observed_at'] == '2026-10-15T09:37:00Z'  # required rows only (@CL.1, .DXY)


def test_cnbc_missing_symbol_is_partial_and_nothing_parsed_fails():
    rec = cnbc_record(cnbc_payload(drop=('.DXY',)))
    assert rec['status'] == 'partial' and rec['values']['missing'] == ['.DXY']
    rec = cnbc_record(cnbc_payload(dxy=('N/A', '102.55', '2026-10-15T05:37:00.000-0400')))
    assert rec['status'] == 'partial' and rec['values']['failed'] == [{'symbol': '.DXY', 'error_kind': 'missing_field'}]
    with pytest.raises(FetchError) as caught:
        macro.cnbc_quotes(FakeFetcher({'FormattedQuoteResult': {'FormattedQuote': []}}, datetime.now(UTC)))
    assert caught.value.kind == 'missing_field'


def test_cnbc_future_quote_time_is_rejected():
    rec = cnbc_record(cnbc_payload(us2y=('4.734%', '4.802%', '2026-10-15T09:37:13.000-0400')))
    assert 'US2Y' not in rec['values']['quotes'] and rec['status'] == 'partial'
    assert rec['values']['failed'] == [{'symbol': 'US2Y', 'error_kind': 'future_observation'}]


def test_ny_trading_date_17h_boundary():
    assert ny_trading_date('2026-10-14T20:59:00Z') == date(2026, 10, 14)   # 16:59 New York
    assert ny_trading_date('2026-10-14T21:00:00Z') == date(2026, 10, 15)   # 17:00 New York
    assert ny_trading_date('2026-10-15T00:30:00Z') == date(2026, 10, 15)   # 09:30 JST


def chart_bars(start: int, count: int, step: int):
    stamps, o, h, l, c, v = [], [], [], [], [], []
    for i in range(count):
        stamps.append(start + i * step)
        o.append('100.0')   # noqa: E741 - column lists
        h.append('101.0')
        l.append('99.0')    # noqa: E741
        c.append('100.5')
        v.append('1.5')
    return {'timestamp': stamps,
            'meta': {'symbol': 'CL=F', 'currency': 'USD', 'exchangeName': 'NYM', 'instrumentType': 'FUTURE',
                     'regularMarketPrice': 100.5, 'previousClose': 100.0, 'chartPreviousClose': 99.5,
                     'shortName': 'Crude Oil', 'timezone': 'EDT'},
            'indicators': {'quote': [{'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}]}}


def test_yahoo_ohlc_drops_the_open_bar_and_reads_all_parts():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    h_start = int(datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp())
    d_start = int(datetime(2026, 10, 13, tzinfo=UTC).timestamp())

    def route(url, params):
        assert url.startswith(CHART_ROOT) and url.rsplit('/', 1)[-1] in ('CL=F', 'BZ=F', 'DX-Y.NYB')
        return {'chart': {'result': [chart_bars(h_start if params['interval'] == '60m' else d_start, 3,
                                                H if params['interval'] == '60m' else 86400)], 'error': None}}
    fetcher = FakeFetcher(route, now)
    rec = record(market.yahoo_ohlc(fetcher, sleep=lambda _s: None))
    v = rec['values']
    assert rec['status'] == 'ok'
    assert [b['t'] for b in v['symbols']['CL=F']['h1']] == ['2026-10-15T08:00:00Z', '2026-10-15T09:00:00Z']
    assert [b['t'] for b in v['symbols']['CL=F']['d1']] == ['2026-10-13T00:00:00Z', '2026-10-14T00:00:00Z']
    assert v['symbols']['CL=F']['h1'][0] == {'t': '2026-10-15T08:00:00Z', 'o': 100.0, 'h': 101.0, 'l': 99.0,
                                             'c': 100.5, 'v': 1.5}
    assert v['symbols']['BZ=F']['h1'] and v['symbols']['DX-Y.NYB']['d1']
    assert v['symbols']['CL=F']['invalid_bars'] == {'d1': 0, 'h1': 0} and v['units']['CL=F'] == 'USD/bbl'
    assert rec['observed_at'] == '2026-10-15T10:00:00Z'
    assert set(v['raw_parts']) == {'cl_d1', 'cl_h1', 'bz_d1', 'bz_h1', 'dxy_d1'}
    assert fetcher.calls[0] == (f'{CHART_ROOT}CL=F', {'range': '3mo', 'interval': '1d'})
    assert fetcher.calls[1] == (f'{CHART_ROOT}CL=F', {'range': '5d', 'interval': '60m'})


def test_yahoo_ohlc_inconsistent_bar_is_dropped_not_repaired():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    h_start = int(datetime(2026, 10, 15, 7, tzinfo=UTC).timestamp())

    def route(url, params):
        payload = chart_bars(h_start, 3, H if params['interval'] == '60m' else 86400)
        if url.endswith('CL=F') and params['interval'] == '60m':
            payload['indicators']['quote'][0]['high'][1] = '98.0'  # high below the low
        return {'chart': {'result': [payload], 'error': None}}
    rec = record(market.yahoo_ohlc(FakeFetcher(route, now), sleep=lambda _s: None))
    assert [b['t'] for b in rec['values']['symbols']['CL=F']['h1']] == ['2026-10-15T07:00:00Z', '2026-10-15T09:00:00Z']
    assert rec['values']['symbols']['CL=F']['invalid_bars']['h1'] == 1


def test_yahoo_ohlc_failed_part_is_partial_and_required_failure_raises():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    h_start = int(datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp())
    d_start = int(datetime(2026, 10, 13, tzinfo=UTC).timestamp())

    def route(url, params):
        if url.endswith('CL=F') and params['interval'] == '1d':
            return {'chart': {'result': None, 'error': {'code': 'Not Found'}}}
        return {'chart': {'result': [chart_bars(h_start if params['interval'] == '60m' else d_start, 3,
                                                H if params['interval'] == '60m' else 86400)], 'error': None}}
    rec = record(market.yahoo_ohlc(FakeFetcher(route, now), sleep=lambda _s: None))
    assert rec['status'] == 'partial' and rec['values']['symbols']['CL=F']['d1'] == []
    assert rec['values']['failures'] == [{'part': 'cl_d1', 'error_kind': 'missing_field'}]
    with pytest.raises(FetchError) as caught:
        market.yahoo_ohlc(FakeFetcher({'chart': {'result': None}}, now), sleep=lambda _s: None)
    assert caught.value.kind == 'missing_field'


def test_collector_tasks_and_catalog():
    now = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    ids = [t.source_id for t in collect.first_phase_tasks('daily', now, 36)]
    assert {'yahoo_ohlc', 'cftc_cot', 'eia_wpsr', 'fred', 'forexfactory_week', 'fed_calendar', 'bea_calendar',
            'news'} <= set(ids)
    assert [t.source_id for t in collect.quote_phase_tasks()] == ['cnbc_quotes', 'cboe_ovx']
    catalog = dict(collect.SOURCE_CATALOG)
    assert catalog['cnbc_quotes'] == macro.CNBC_URL and catalog['yahoo_ohlc'] == market.YAHOO_URL
    assert catalog['cftc_cot'] == cot.COT_URL and catalog['eia_wpsr'] == eia.EIA_URL
    assert catalog['cboe_ovx'] == vol.OVX_URL and catalog['forexfactory_week'] == calendar.FF_URL
    assert collect.MAX_WORKERS == 6


def ff_payload():
    row = lambda title, when, impact='High', forecast='0.3%', previous='0.2%', country='USD': {  # noqa: E731
        'title': title, 'country': country, 'date': when, 'impact': impact, 'forecast': forecast, 'previous': previous}
    return [row('Core CPI m/m', '2026-10-14T08:30:00-04:00'), row('CPI m/m', '2026-10-14T08:30:00-04:00', forecast='0.6%', previous='0.4%'),
            row('PPI m/m', '2026-10-15T08:30:00-04:00'), row('Core PPI m/m', '2026-10-15T08:30:00-04:00'),
            row('Retail Sales m/m', '2026-10-15T08:30:00-04:00'), row('Core Retail Sales m/m', '2026-10-15T08:30:00-04:00'),
            row('Unemployment Claims', '2026-10-15T08:30:00-04:00', impact='Medium', forecast='199K', previous='197K'),
            row('Fed Chairman Warsh Speaks', '2026-10-15T23:30:00-04:00', forecast='', previous=''),
            row('Crude Oil Inventories', '2026-10-15T12:00:00-04:00', impact='Low', forecast='0.2M', previous='-3.2M'),
            row('German ZEW', '2026-10-14T05:00:00-04:00', country='EUR'),
            row('Bad <b>title</b>', '2026-10-15T10:00:00-04:00'),
            row('Odd value', '2026-10-15T10:00:00-04:00', forecast='<script>', previous='1.0%'),
            row('Low thing', '2026-10-15T10:00:00-04:00', impact='Low')]


def test_forexfactory_source_keeps_us_high_medium_and_numeric_values():
    fetcher = FakeFetcher(ff_payload(), datetime(2026, 10, 15, 9, 0, tzinfo=UTC))
    rec = record(calendar.forexfactory_week(fetcher))
    events = rec['values']['events']
    titles = [e['title'] for e in events]
    assert 'German ZEW' not in titles and 'Low thing' not in titles and 'Bad <b>title</b>' not in titles
    crude = next(e for e in events if e['title'] == 'Crude Oil Inventories')
    assert crude['importance'] == 'low' and crude['start_at'] == '2026-10-15T16:00:00Z'
    assert crude['forecast'] == '0.2M' and crude['previous'] == '-3.2M'  # the impact-Low oil line is kept
    odd = next(e for e in events if e['title'] == 'Odd value')
    assert odd['forecast'] == '' and odd['previous'] == '1.0%'
    cpi = next(e for e in events if e['title'] == 'CPI m/m')
    assert cpi['start_at'] == '2026-10-14T12:30:00Z' and cpi['importance'] == 'high'


def cot_payload(dates=('2026-09-29', '2026-10-06')):
    rows = []
    for i, day in enumerate(dates):
        rows.append({'report_date_as_yyyy_mm_dd': f'{day}T00:00:00.000', 'cftc_contract_market_code': '067651',
                     'contract_market_name': 'CRUDE OIL, LIGHT SWEET-WTI',
                     'market_and_exchange_names': 'CRUDE OIL, LIGHT SWEET-WTI - NEW YORK MERCANTILE EXCHANGE',
                     'contract_units': 'CONTRACTS OF 1,000 BARRELS', 'futonly_or_combined': 'FutOnly',
                     'm_money_positions_long_all': str(170000 + i), 'm_money_positions_short_all': '124755',
                     'm_money_positions_spread': '15000',
                     'prod_merc_positions_long': '90000', 'prod_merc_positions_short': '88000',
                     'swap_positions_long_all': '60000', 'swap__positions_short_all': '70000',
                     'open_interest_all': '1889422', 'change_in_m_money_long_all': '1000',
                     'change_in_m_money_short_all': '-2000', 'change_in_open_interest_all': '3000',
                     'traders_m_money_long_all': '120', 'traders_m_money_short_all': '80'})
    return sorted(rows, key=lambda row: row['report_date_as_yyyy_mm_dd'], reverse=True)


def test_cot_parses_series_ascending_with_mm_net():
    fetcher = FakeFetcher(cot_payload(), datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    rec = record(cot.cot(fetcher))
    assert fetcher.calls[0] == (cot.COT_URL, {'$where': "cftc_contract_market_code='067651'",
                                              '$order': 'report_date_as_yyyy_mm_dd DESC', '$limit': '156'})
    v = rec['values']
    assert rec['status'] == 'ok' and v['count'] == 2
    assert [row['report_date'] for row in v['series']] == ['2026-09-29', '2026-10-06']
    assert v['latest']['mm_long'] == 170001.0 and v['latest']['mm_short'] == 124755.0
    assert v['latest']['mm_net'] == 170001.0 - 124755.0 and v['latest']['swap_short'] == 70000.0
    assert v['latest']['mm_spread'] == 15000.0 and v['latest']['traders_mm_short'] == 80.0
    assert v['latest']['change_mm_long'] == 1000.0 and v['latest']['traders_mm_short'] == 80.0
    assert v['contract'] == {'code': '067651', 'name': 'CRUDE OIL, LIGHT SWEET-WTI',
                             'market': 'CRUDE OIL, LIGHT SWEET-WTI - NEW YORK MERCANTILE EXCHANGE',
                             'units': 'CONTRACTS OF 1,000 BARRELS', 'report_type': 'FutOnly'}


def test_cot_tolerates_missing_numbers_and_empty_payloads():
    payload = cot_payload(dates=('2026-10-06',))
    payload[0]['m_money_positions_short_all'] = '-'
    payload[0]['open_interest_all'] = 'n/a'
    rec = record(cot.cot(FakeFetcher(payload, datetime(2026, 10, 7, 12, 0, tzinfo=UTC))))
    v = rec['values']['latest']
    assert v['mm_long'] == 170000.0 and v['mm_short'] is None and v['mm_net'] is None and v['open_interest'] is None
    with pytest.raises(FetchError) as caught:
        cot.cot(FakeFetcher([], datetime(2026, 10, 7, 12, 0, tzinfo=UTC)))
    assert caught.value.kind == 'empty_payload'


EIA_ROWS = [
    ['STUB_1', '10/9/26', '10/2/26', 'Difference', 'Percent Change', '10/10/25', 'Difference', 'Percent Change'],
    ['Crude Oil', '698.560', '707.117', '-8.557', '-1.200', '831.530', '-132.969', '-16.000'],
    ['Commercial (Excluding SPR)', '415.577', '424.134', '-8.557', '-2.000', '423.785', '-8.207', '-1.900'],
    ['Strategic Petroleum Reserve (SPR)', '282.983', '282.983', '0.000', '0.000', '407.745', '-124.762', '-30.600'],
    ['Total Motor Gasoline', '208.262', '204.744', '3.518', '1.700', '218.826', '-10.564', '-4.800'],
    ['Kerosene-Type Jet Fuel', '42.795', '42.504', '0.291', '0.700', '44.414', '-1.619', '-3.600'],
    ['Distillate Fuel Oil', '102.611', '105.138', '-2.527', '-2.400', '117.030', '-14.419', '-12.300'],
    ['Propane/Propylene', '106.375', '107.846', '-1.471', '-1.400', '102.376', '3.999', '3.900'],
    ['Total Stocks (Excluding SPR)', '1,224.016', '1,237.400', '-13.384', '-1.100', '1,288.820', '-64.804', '-5.000'],
    ['STUB_1', 'STUB_2', '10/9/26', '10/2/26', 'Difference', '10/10/25', 'Difference', '10/9/26', '10/10/25',
     'Percent Change', '10/9/26', '10/10/25', 'Percent Change'],
    ['Crude Oil Supply ', '(1)     Domestic Production', '13,679', '13,979', '-300', '13,636', '43', '13,888',
     '13,568', '2.4', '13,745', '13,450', '2.2'],
    ['Crude Oil Supply ', '(8)        Imports', '6,049', '6,840', '-791', '5,525', '523', '6,116', '6,064', '0.9',
     '6,169', '6,132', '0.6'],
    # Not on the whitelist, but it carries the raw 0x96 bytes ("– –") that must decode to an unparsable cell.
    ['Crude Oil Supply ', '(10)       Commercial Stock Change', '-1,222', '-455', '-767', '503', '-1,726', '-280',
     '301', '\x96 \x96', '-14', '31', '\x96 \x96'],
    ['Crude Oil Supply ', '(12)        Exports', '4,570', '4,765', '-195', '4,466', '104', '4,047', '4,073', '-0.6',
     '4,259', '3,871', '10.0'],
    ['Crude Oil Supply ', '(17)   Crude Oil Input to Refineries', '16,615', '16,480', '135', '15,130', '1,485',
     '16,541', '16,018', '3.3', '16,661', '16,286', '2.3'],
    ['Products Supplied ', '(30)     Total', '21,361', '20,396', '965', '19,726', '1,635', '21,077', '20,669', '2.0',
     '20,641', '20,344', '1.5'],
    ['Net Imports of Crude and Petroleum Products ', '(33)   Total', '-4,373', '-4,535', '162', '-4,249', '-124',
     '-4,140', '-3,268', '26.7', '-4,093', '-2,727', '50.1'],
]


def eia_payload():
    text = '\n'.join(','.join(f'"{cell}"' if ',' in cell else cell for cell in row) for row in EIA_ROWS)
    return (text + '\n\x1a').encode('latin-1')  # the live file carries 0x96 bytes; decode uses replacement


def test_eia_wpsr_parses_stocks_and_flows_across_the_bad_bytes():
    fetcher = FakeRawFetcher(eia_payload(), datetime(2026, 10, 16, 15, 0, tzinfo=UTC))
    rec = record(eia.wpsr(fetcher))
    assert fetcher.calls[0] == (eia.EIA_URL, {})
    v = rec['values']
    assert rec['status'] == 'ok' and v['missing'] == []
    assert (v['week_ending'], v['week_ago'], v['year_ago']) == ('2026-10-09', '2026-10-02', '2025-10-10')
    assert v['stocks']['commercial_crude_ex_spr'] == {'current': 415.577, 'week_ago': 424.134, 'change_wow': -8.557,
                                                      'change_pct_wow': -2.0, 'year_ago': 423.785, 'change_yoy': -8.207,
                                                      'change_pct_yoy': -1.9}
    assert v['stocks']['crude_oil_incl_spr']['current'] == 698.56 and v['stocks']['gasoline']['current'] == 208.262
    assert v['flows']['production'] == {'current': 13679.0, 'week_ago': 13979.0, 'change_wow': -300.0,
                                        'year_ago': 13636.0, 'change_yoy': 43.0,
                                        'extra': [13888.0, 13568.0, 2.4, 13745.0, 13450.0, 2.2]}
    assert v['flows']['imports']['current'] == 6049.0 and v['flows']['exports']['change_wow'] == -195.0
    assert v['flows']['refinery_input']['current'] == 16615.0 and v['flows']['products_supplied']['change_yoy'] == 1635.0
    assert v['units'] == {'stocks': 'million barrels', 'flows': 'thousand barrels per day'}


def test_eia_wpsr_missing_target_is_partial_and_broken_payloads_raise():
    now = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)
    body = eia_payload().replace(b'Distillate Fuel Oil,', b'Distillate Fuel Oils,')
    rec = record(eia.wpsr(FakeRawFetcher(body, now)))
    assert rec['status'] == 'partial' and rec['values']['missing'] == ['distillate']
    with pytest.raises(FetchError) as caught:
        eia.wpsr(FakeRawFetcher(b'BAD_1,10/9/26\n', now))
    assert caught.value.kind == 'missing_field'
    with pytest.raises(FetchError) as caught:
        eia.wpsr(FakeRawFetcher(b'', now))
    assert caught.value.kind == 'empty_payload'


def ovx_payload():
    return {'timestamp': '2026-10-16 17:15:15',
            'data': {'symbol': '^OVX', 'index': 'OVX', 'current_price': 50.51, 'prev_day_close': 49.13,
                     'open': 49.25, 'high': 51.2, 'low': 48.9, 'price_change': 1.38, 'price_change_percent': 2.81,
                     'last_trade_time': '2026-10-16T12:15:02.475000-05:00', 'volume': 0}}


def test_ovx_quote_uses_last_trade_time_as_the_observation():
    rec = record(vol.ovx(FakeFetcher(ovx_payload(), datetime(2026, 10, 16, 18, 0, tzinfo=UTC))))
    assert rec['status'] == 'ok' and rec['observed_at'] == '2026-10-16T17:15:02.475000Z'
    assert rec['values']['last'] == 50.51 and rec['values']['previous_close'] == 49.13
    assert rec['values']['change'] == pytest.approx(1.38)
    assert rec['values']['change_pct'] == pytest.approx(50.51 / 49.13 * 100 - 100)
    assert rec['values']['high'] == 51.2 and rec['values']['low'] == 48.9


def test_ovx_missing_previous_close_raises():
    payload = ovx_payload()
    payload['data'].pop('prev_day_close')
    with pytest.raises(FetchError) as caught:
        vol.ovx(FakeFetcher(payload, datetime(2026, 10, 16, 18, 0, tzinfo=UTC)))
    assert caught.value.kind == 'missing_field'



def _sources(collection):
    return {r['source_id']: r for r in collection['sources']}


def _macro(c=None, *, as_of=None, drop_dxy=False):
    c = copy.deepcopy(c or load_collection())
    S = _sources(c)
    if drop_dxy:
        S['cnbc_quotes']['values']['quotes'].pop('.DXY', None)
    at = datetime.fromisoformat((as_of or c['collection_completed_at']).replace('Z', '+00:00'))
    F, st = Facts(at, MANIFEST), {}
    macro_facts(F, S, st, at)
    return F, st['macro']


def test_macro_uses_current_dxy_and_real10_not_nominal_us2y():
    c = load_collection()
    S = _sources(c)
    S['cnbc_quotes']['values']['quotes']['US2Y']['change'] = 9.0
    F, m = _macro(c)
    assert m['status'] == 'supportive' and m['window'] == 'same_day_dxy_latest_real10'
    assert m['change_fact_ids'] == [m['same_day']['dxy_change'], m['DFII10_change']]
    assert F.index[m['DFII10_change']]['value'] == -10.0
    assert all(scoring.scorable(F.index[i]) for i in m['change_fact_ids'])
    assert scoring.macro_group({'facts': F.items, 'state': {'macro': m}})['direction'] == 1


@pytest.mark.parametrize('usd,real,status', [(0.30, 0.05, 'adverse'), (-0.30, -0.05, 'supportive'),
                                             (0.40, -0.05, 'mixed'), (0.40, 0.0496, 'neutral')])
def test_macro_thresholds_use_unrounded_real10_changes(usd, real, status):
    c = load_collection()
    S = _sources(c)
    S['cnbc_quotes']['values']['quotes']['.DXY']['change_pct'] = usd
    rows = S['fred']['values']['series']['DFII10']['rows']
    rows[-2]['value'], rows[-1]['value'] = 2.0, 2.0 + real
    F, m = _macro(c)
    assert m['status'] == status
    if real == 0.0496:
        assert F.index[m['DFII10_change']]['display'].startswith('+5.0bp')


def test_macro_fallback_uses_matching_fred_dates_and_both_endpoints():
    F, m = _macro(drop_dxy=True)
    assert m['window'] == 'daily_common_observation' and m['status'] == 'supportive'
    assert m['common_date'] == '2026-10-14' and m['previous_date'] == '2026-10-13'
    for fid in m['change_fact_ids']:
        f = F.index[fid]
        assert len(f['source_fact_ids']) == 2
        assert {F.index[i]['observation_date'] for i in f['source_fact_ids']} == {'2026-10-13','2026-10-14'}


def test_macro_stale_quotes_display_but_do_not_supply_the_current_direction():
    c = load_collection()
    _sources(c)['cnbc_quotes']['values']['quotes']['.DXY']['last_time'] = '2026-10-14T14:59:00Z'
    F, m = _macro(c)
    dxy = F.index[m['same_day']['dxy']]
    assert dxy['value'] is not None and not scoring.scorable(dxy)
    assert m['window'] == 'daily_common_observation'
    _, missing = _macro(c, as_of='2026-10-20T15:00:00Z', drop_dxy=True)
    assert missing['status'] == 'unknown' and missing['reason'] == 'common_observation_stale'


def test_macro_no_common_dates_is_unknown():
    c = load_collection()
    _sources(c)['fred']['values']['series']['DTWEXBGS']['rows'] = [{'date':'2026-10-01','value':100}]
    _, m = _macro(c, drop_dxy=True)
    assert m['status'] == 'unknown' and m['reason'] == 'no_common_dates'


def test_curve_has_same_venue_different_contract_provenance_and_no_retired_annualised_basis():
    c = load_collection(); S = _sources(c)
    F, st = Facts(datetime(2026,10,15,15,tzinfo=UTC), MANIFEST), {}
    curve_facts(F, S, st)
    f = F.index[st['curve']['spread_pct']]
    assert f['value'] == pytest.approx(100 * (100-98)/98)
    assert f['method_id'] == 'front_minus_second_over_second'
    assert len(f['source_fact_ids']) == 2
    assert {F.index[i]['venue'] for i in f['source_fact_ids']} == {'cnbc'}
    assert {F.index[i]['instrument'] for i in f['source_fact_ids']} == {'cl.1','cl.2'}
    assert 'basis' not in st
    assert not any('annual' in x['metric'] for x in F.items)


def test_curve_missing_second_contract_never_uses_brent_or_fred_spot():
    c = load_collection(); S = _sources(c)
    S['cnbc_quotes']['values']['quotes'].pop('@CL.2')
    F, st = Facts(datetime(2026,10,15,15,tzinfo=UTC), MANIFEST), {}
    curve_facts(F, S, st)
    f = F.index[st['curve']['spread_pct']]
    assert f['value'] is None and not scoring.scorable(f)
    assert st['curve']['status'] == 'missing'


def _level_inputs():
    c=load_collection(); S=_sources(c)
    return c, S, datetime.fromisoformat(c['collection_completed_at'].replace('Z','+00:00'))


def test_oil_levels_use_et_closed_bars_and_keep_roll_comparison_unverified():
    c,S,at=_level_inputs()
    F,st=Facts(at,MANIFEST),{}
    price_facts(F,S,st); levels_facts(F,S,st,at)
    lv=st['levels']; k={i['key']:i for i in lv['items']}
    assert (F.index[k['prev_day_high']['fact_id']]['value'], F.index[k['prev_day_low']['fact_id']]['value']) == (102,98)
    f=F.index[k['prev_day_high']['fact_id']]
    assert f['source_id']=='yahoo_ohlc' and '.yahoo.' in f['fact_id'] and f['unit']=='USD/bbl'
    assert f['raw_sha256']=='c'*64 and f['value']<1000
    assert lv['nearest']=={'high':None,'low':None}
    assert lv['weekly_range']['position_pct'] is None
    assert '未確認' in lv['comparison_note']
    assert not any(i['key'].startswith(('asia','london','new_york')) for i in lv['items'])


def test_missing_weekday_bar_prevents_a_fabricated_period_high():
    _,S,at=_level_inputs()
    rows=S['yahoo_ohlc']['values']['symbols']['CL=F']['d1']
    rows[:]=[r for r in rows if not r['t'].startswith('2026-10-14')]
    F,st=Facts(at,MANIFEST),{}
    levels_facts(F,S,st,at)
    assert not any(i['key']=='prev_day_high' for i in st['levels']['items'])
    assert any(x['code']=='window_incomplete' and x.get('key')=='prev_day_range' for x in st['levels']['cautions'])


def test_maintenance_hour_and_weekends_are_not_fabricated_missing_bars():
    ET=ZoneInfo('America/New_York')
    def b(day,hour):
        t=datetime(2026,10,day,hour,tzinfo=ET)
        return {'t':t,'h':101,'l':99,'c':100}
    bars=levels._bars([b(15,16),b(15,18)],timedelta(hours=1))
    assert levels._gaps(bars,timedelta(hours=1),'h1')==[]
    bars=levels._bars([b(15,15),b(15,18)],timedelta(hours=1))
    assert levels._gaps(bars,timedelta(hours=1),'h1')[0]['missing_bars']==1


def test_oil_facts_schema_and_report_with_adopted_sources(tmp_path):
    from tests.usoil.test_review_fixes import _edition
    f,ev,md,html,machine=_edition(load_collection(),tmp_path,'oil')
    assert schema_validate(f,json.loads(FACTS_SCHEMA.read_text()))==[]
    assert not {'etf','derivatives','options','stable','basis'} & f['state'].keys()
    assert f['state']['curve']['status']=='ok'
    assert 'EIA' in md and 'EIA' in html and machine['symbol']=='USOIL'
    assert 'future_observation' not in ev['hard_invalid']


def test_forexfactory_consensus_is_display_only_and_does_not_create_eia_actual():
    fetcher=FakeFetcher(ff_payload(),datetime(2026,10,15,9,tzinfo=UTC))
    rec=record(calendar.forexfactory_week(fetcher))
    from usoil.facts import calendar_events
    events,st=calendar_events({'forexfactory_week':rec},datetime(2026,10,15,10,tzinfo=UTC),{})
    crude=next(e for e in events if e['id'].startswith('eia.'))
    assert crude['consensus']['forecast']=='0.2M' and crude['consensus']['previous']=='-3.2M'
    assert 'actual' not in crude['consensus']
    assert st['nextweek']=='unavailable_404'
