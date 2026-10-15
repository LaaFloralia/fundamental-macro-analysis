"""R16 data additions: same-day CNBC quotes and the macro rule, Kraken bars and named levels, Deribit basis. No network."""
import copy
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from btc import collect, levels, scoring
from btc.facts import Facts, basis_facts, levels_facts, macro_facts, ny_trading_date
from btc.fetch import FetchError, Response
from btc.jsonschema_lite import validate as schema_validate
from btc.pipeline import FACTS_SCHEMA
from btc.sources import macro, market, options
from tests.btc.test_pipeline import load_collection

UTC = timezone.utc
MANIFEST = 'a' * 64


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


def record(result) -> dict:
    return result.to_dict()


# ------------------------------------------------------------------ CNBC quotes

def cnbc_payload(us2y=('4.734%', '4.802%', '2026-10-15T05:37:13.000-0400'),
                 dxy=('101.957', '102.55', '2026-10-15T05:37:00.000-0400'),
                 ixic=('26,692.868', '27,203.11', '2026-10-14'), drop=()):
    rows = []
    for symbol, q in (('US2Y', us2y), ('.DXY', dxy), ('.IXIC', ixic)):
        if symbol in drop:
            continue
        rows.append({'symbol': symbol, 'last': q[0], 'previous_day_closing': q[1], 'last_time': q[2],
                     'change_pct': '9.99%'})  # provider's own change is never used
    return {'FormattedQuoteResult': {'FormattedQuote': rows}}


def cnbc_record(payload, retrieved=datetime(2026, 10, 15, 9, 40, tzinfo=UTC)):
    return record(macro.cnbc_quotes(FakeFetcher(payload, retrieved)))


def test_cnbc_parsing_values_params_and_exact_change():
    fetcher = FakeFetcher(cnbc_payload(), datetime(2026, 10, 15, 9, 40, tzinfo=UTC))
    rec = record(macro.cnbc_quotes(fetcher))
    url, params = fetcher.calls[0]
    assert url == 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'
    assert params == {'symbols': 'US2Y|.DXY|.IXIC', 'requestMethod': 'itv', 'noform': '0', 'partnerId': '2',
                      'fund': '1', 'exthrs': '1', 'output': 'json', 'events': '1'}
    q = rec['values']['quotes']
    assert rec['status'] == 'ok' and rec['values']['missing'] == []
    assert q['US2Y'] == {'last': 4.734, 'previous_close': 4.802, 'change': -0.068, 'change_pct': pytest.approx(-1.41607663),
                         'last_time': '2026-10-15T09:37:13Z', 'time_kind': 'quote_time'}
    assert q['.DXY']['last'] == 101.957 and q['.DXY']['previous_close'] == 102.55
    assert q['.IXIC']['last'] == 26692.868 and q['.IXIC']['last_time'] == '2026-10-14'
    assert q['.IXIC']['time_kind'] == 'close_date'
    assert rec['observed_at'] == '2026-10-15T09:37:13Z'


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


def macro_state(cnbc=None, as_of=datetime(2026, 10, 15, 9, 50, tzinfo=UTC), treasury=None, dollar=None):
    S = {}
    if cnbc is not None:
        S['cnbc_quotes'] = cnbc
    if treasury:
        S['treasury_yields'] = {'source_id': 'treasury_yields', 'status': 'ok', 'retrieved_at': '2026-10-15T09:40:00Z',
                                'values': {'series': [{'date': d, 'y2_pct': y, 'y10_pct': None, 'nominal_raw': 'c' * 64,
                                                       'real10_pct': None, 'real_raw': None} for d, y in treasury],
                                           'timestamp_quality': 'source_date',
                                           'retrieval_started_at': '2026-10-15T09:39:58Z'}}
    if dollar:
        S['fred'] = {'source_id': 'fred', 'status': 'ok', 'retrieved_at': '2026-10-15T09:40:00Z',
                     'values': {'series': {'DTWEXBGS': {'label': '米ドル広義指数（FRED）', 'unit': 'index',
                                                        'frequency': 'daily', 'raw': 'd' * 64,
                                                        'rows': [{'date': d, 'value': v} for d, v in dollar]}},
                                'timestamp_quality': 'source_date', 'retrieval_started_at': '2026-10-15T09:39:58Z'}}
    F = Facts(as_of, MANIFEST)
    state = {}
    macro_facts(F, S, state, as_of)
    return F, state['macro']


def test_same_day_facts_and_display_formats():
    F, m = macro_state(cnbc_record(cnbc_payload()))
    same = m['same_day']
    assert same['status'] == 'ok' and same['trading_date'] == '2026-10-15'
    y, dy = F.index[same['ust_2y']], F.index[same['ust_2y_change']]
    d, dd = F.index[same['dxy']], F.index[same['dxy_change']]
    n, dn = F.index[same['nasdaq']], F.index[same['nasdaq_change']]
    assert (y['display'], dy['display']) == ('4.73%', '-6.8bp')
    assert (d['display'], dd['display']) == ('101.96', '-0.58%')
    assert (n['display'], dn['display']) == ('26,692.87', '-1.88%')
    assert dy['value'] == -6.8 and y['category'] == 'macro' and y['fact_id'].startswith('btc.macro.cnbc.ust_2y.')
    assert d['fact_id'].startswith('btc.macro.cnbc.dxy.') and 'ICE' in d['population']
    assert n['observation_date'] == '2026-10-14' and n['status'] == 'ok'
    assert all(scoring.scorable(f) for f in (dy, dd, dn))
    assert {same['ust_2y'], same['dxy'], same['nasdaq']} <= set(m['fact_ids'])


def test_nasdaq_intraday_quote_is_labelled_with_its_new_york_time():
    rec = cnbc_record(cnbc_payload(ixic=('26,700.10', '26,692.87', '2026-10-15T11:05:00.000-0400')),
                      retrieved=datetime(2026, 10, 15, 15, 10, tzinfo=UTC))
    F, m = macro_state(rec, as_of=datetime(2026, 10, 15, 15, 15, tzinfo=UTC))
    n, dn = F.index[m['same_day']['nasdaq']], F.index[m['same_day']['nasdaq_change']]
    assert (n['display'], dn['display']) == ('26,700.10', '+0.03%')
    assert '10/15 11:05 NY時点' in n['population'] and '10/15 11:05 NY時点' in dn['population']
    assert n['observed_at'] == '2026-10-15T15:05:00Z' and n['status'] == 'ok'


def test_macro_rule_uses_same_day_quotes():
    F, m = macro_state(cnbc_record(cnbc_payload()))
    assert (m['rule'], m['status'], m['reason'], m['window']) == ('same_day_quotes', 'supportive', 'same_day_quotes',
                                                                   'same_day')
    assert m['change_fact_ids'] == [m['same_day']['ust_2y_change'], m['same_day']['dxy_change']]
    assert scoring.macro_group({'facts': F.items, 'state': {'macro': m}})['direction'] == 1
    # +5.0 bp and +0.30 % exactly (decimal, unrounded) -> adverse
    _, m = macro_state(cnbc_record(cnbc_payload(us2y=('4.85%', '4.80%', '2026-10-15T05:37:13.000-0400'),
                                                dxy=('100.30', '100.00', '2026-10-15T05:37:00.000-0400'))))
    assert m['status'] == 'adverse'
    # 4.96 bp prints as +5.0bp but stays below the threshold
    F, m = macro_state(cnbc_record(cnbc_payload(us2y=('4.8496%', '4.80%', '2026-10-15T05:37:13.000-0400'),
                                                dxy=('100.40', '100.00', '2026-10-15T05:37:00.000-0400'))))
    assert F.index[m['same_day']['ust_2y_change']]['display'].startswith('+5.0bp') and m['status'] == 'neutral'
    # Opposite moves past both thresholds (-6.8bp, +0.40%) are mixed, as in the common-date rule
    _, m = macro_state(cnbc_record(cnbc_payload(dxy=('100.40', '100.00', '2026-10-15T05:37:00.000-0400'))))
    assert m['status'] == 'mixed'


TREASURY = [('2026-10-13', 4.90), ('2026-10-14', 4.80)]
DOLLAR = [('2026-10-13', 120.0), ('2026-10-14', 119.5)]


def test_stale_quote_on_previous_ny_trading_date_falls_back_to_common_date():
    # Quotes from 16:30 New York on 10-14; as_of 17:30 New York belongs to trading date 10-15.
    stale = cnbc_record(cnbc_payload(us2y=('4.734%', '4.802%', '2026-10-14T16:30:00.000-0400'),
                                     dxy=('101.957', '102.55', '2026-10-14T16:30:00.000-0400')),
                        retrieved=datetime(2026, 10, 14, 21, 31, tzinfo=UTC))
    as_of = datetime(2026, 10, 14, 21, 35, tzinfo=UTC)
    F, m = macro_state(stale, as_of=as_of, treasury=TREASURY, dollar=DOLLAR)
    same = m['same_day']
    assert same['status'] == 'stale' and same['trading_date'] == '2026-10-15'
    for key in ('ust_2y', 'ust_2y_change', 'dxy', 'dxy_change'):
        fact = F.index[same[key]]
        assert fact['status'] == 'stale' and fact['value'] is not None and not scoring.scorable(fact)
    assert F.index[same['ust_2y']]['display'] == '4.73%'  # still displayable
    assert m['rule'] == 'common_date' and m['window'] == 'daily_common_observation'
    assert (m['status'], m['reason']) == ('supportive', 'macro_supportive')  # -10 bp, -0.42 %
    assert m['common_date'] == '2026-10-14' and 'cnbc' not in ''.join(m['change_fact_ids'])


def test_same_day_quote_older_than_six_hours_is_not_scored():
    _, m = macro_state(cnbc_record(cnbc_payload()), as_of=datetime(2026, 10, 15, 16, 0, tzinfo=UTC),
                       treasury=TREASURY, dollar=DOLLAR)
    assert m['same_day']['status'] == 'stale' and m['rule'] == 'common_date'


def test_missing_dxy_keeps_common_date_rule_and_its_reasons():
    _, m = macro_state(cnbc_record(cnbc_payload(drop=('.DXY',))))
    assert m['same_day']['status'] == 'missing' and 'dxy' not in m['same_day'] and 'ust_2y' in m['same_day']
    assert (m['rule'], m['status'], m['reason']) == ('common_date', 'unknown', 'treasury_missing')
    _, m = macro_state(None, treasury=TREASURY, dollar=[('2026-10-01', 120.0), ('2026-10-02', 119.0)])
    assert m['same_day'] == {'status': 'missing', 'trading_date': '2026-10-15'}
    assert (m['rule'], m['reason']) == ('common_date', 'no_common_dates')


def test_policy_names_the_macro_source():
    assert scoring.POLICY['macro']['source'] == 'cnbc_same_day_quotes_else_treasury_fred_common_date'


# ------------------------------------------------------------------ Kraken bars

H = 3600


def ohlc_payload(start: int, count: int, step: int, error=None):
    rows = [[start + i * step, '100.0', '101.0', '99.0', '100.5', '100.2', '1.5', 10] for i in range(count)]
    if error:
        return {'error': [error], 'result': {}}
    return {'error': [], 'result': {'XXBTZUSD': rows, 'last': start + (count - 1) * step}}


def test_kraken_ohlc_drops_the_open_bar_and_reads_both_intervals():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    h_start = int(datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp())
    d_start = int(datetime(2026, 10, 13, tzinfo=UTC).timestamp())

    def route(url, params):
        assert url == 'https://api.kraken.com/0/public/OHLC' and params['pair'] == 'XBTUSD'
        return ohlc_payload(h_start, 3, H) if params['interval'] == 60 else ohlc_payload(d_start, 3, 86400)
    rec = record(market.kraken_ohlc(FakeFetcher(route, now)))
    v = rec['values']
    assert rec['status'] == 'ok' and [b['t'] for b in v['h1']] == ['2026-10-15T08:00:00Z', '2026-10-15T09:00:00Z']
    assert [b['t'] for b in v['d1']] == ['2026-10-13T00:00:00Z', '2026-10-14T00:00:00Z']
    assert v['h1'][0] == {'t': '2026-10-15T08:00:00Z', 'o': 100.0, 'h': 101.0, 'l': 99.0, 'c': 100.5, 'v': 1.5}
    assert rec['observed_at'] == '2026-10-15T10:00:00Z' and set(v['raw_parts']) == {'h1', 'd1'}


def test_kraken_ohlc_inconsistent_bar_is_dropped_not_repaired():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    h_start = int(datetime(2026, 10, 15, 7, tzinfo=UTC).timestamp())

    def route(url, params):
        payload = ohlc_payload(h_start, 3, H)
        if params['interval'] == 60:
            payload['result']['XXBTZUSD'][1][2] = '98.0'  # high below the low
        return payload
    rec = record(market.kraken_ohlc(FakeFetcher(route, now)))
    assert [b['t'] for b in rec['values']['h1']] == ['2026-10-15T07:00:00Z', '2026-10-15T09:00:00Z']
    assert rec['values']['invalid_bars']['h1'] == 1


def test_kraken_ohlc_payload_error():
    now = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)
    with pytest.raises(FetchError) as caught:
        market.kraken_ohlc(FakeFetcher(ohlc_payload(0, 0, H, error='EGeneral:Invalid arguments'), now))
    assert caught.value.kind == 'invalid_payload'
    h_start = int(datetime(2026, 10, 15, 8, tzinfo=UTC).timestamp())

    def route(url, params):
        return ohlc_payload(h_start, 3, H) if params['interval'] == 60 else ohlc_payload(0, 0, 0, error='EAPI:Rate limit')
    rec = record(market.kraken_ohlc(FakeFetcher(route, now)))
    assert rec['status'] == 'partial' and rec['values']['d1'] == []
    assert rec['values']['failures'] == [{'part': 'd1', 'error_kind': 'invalid_payload'}]


# ------------------------------------------------------------------ named levels

def bar(t: datetime, h=100.0, l=99.0, c=99.5):  # noqa: E741
    return {'t': t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'o': c, 'h': h, 'l': l, 'c': c}


def hours(start: datetime, end: datetime, override=None):
    """Flat hourly bars [start, end); override maps a bar start to (h, l, c)."""
    out, t = [], start
    while t < end:
        h, l, c = (override or {}).get(t, (100.0, 99.0, 99.5))  # noqa: E741
        out.append(bar(t, h, l, c))
        t += timedelta(hours=1)
    return out


def days(start: date, end: date, **override):
    out, d = [], start
    while d < end:
        h, l = override.get(d.isoformat(), (100.0, 99.0))  # noqa: E741
        out.append(bar(datetime(d.year, d.month, d.day, tzinfo=UTC), h, l, 99.5))
        d += timedelta(days=1)
    return out


def by_key(result):
    return {i['key']: i for i in result['items']}


def test_previous_day_week_month_levels():
    now = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)  # Thursday
    d1 = days(date(2026, 8, 1), date(2026, 10, 15),
              **{'2026-10-14': (110.0, 95.0), '2026-10-07': (120.0, 90.0), '2026-09-18': (130.0, 80.0)})
    h1 = hours(datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 15, 10, tzinfo=UTC))
    result = levels.build_levels(h1, d1, now, 99.7)
    k = by_key(result)
    assert (k['prev_day_high']['price'], k['prev_day_low']['price']) == (110.0, 95.0)
    assert (k['prev_day_high']['name'], k['prev_day_low']['name']) == ('前日高値', '前日安値')
    assert (k['prev_week_high']['price'], k['prev_week_low']['price']) == (120.0, 90.0)   # Mon 10-05 .. Sun 10-11
    assert (k['prev_month_high']['price'], k['prev_month_low']['price']) == (130.0, 80.0)  # September
    assert k['prev_day_high']['formed_at'] == '2026-10-15T00:00:00Z'
    assert k['prev_day_high']['pool'] == 'BSL' and k['prev_day_low']['pool'] == 'SSL'
    assert k['prev_month_high']['state'] == 'resting' and k['prev_month_high']['state_word'] == '未到達'
    assert result['status'] == 'ok' and result['cautions'] == [] and result['last_bar_start'] == '2026-10-15T09:00:00Z'
    # Sessions of the current day appear only once they have ended in closed bars: Asia (00-06 UTC) yes, London no.
    assert 'asia_high_today' in k and 'london_high_today' not in k and 'ny_high_prev_day' in k
    assert k['asia_high_today']['name'] == 'アジア高値（当日）' and k['london_low_prev_day']['name'] == 'London安値（前日）'


@pytest.mark.parametrize('day,london_utc,ny_utc', [
    (date(2026, 3, 6), (8, 13), (13, 18)),    # both on standard time
    (date(2026, 3, 9), (8, 13), (12, 17)),    # US moved to EDT (03-08), UK still GMT
    (date(2026, 3, 30), (7, 12), (12, 17)),   # UK on BST (03-29)
    (date(2026, 10, 26), (8, 13), (12, 17)),  # UK back to GMT (10-25), US still EDT
    (date(2026, 11, 2), (8, 13), (13, 18)),   # US back to EST (11-01)
])
def test_session_windows_follow_local_clocks_across_dst(day, london_utc, ny_utc):
    zones = {key: (zone, a, b) for key, _, zone, a, b in levels.SESSIONS}
    for key, expected in (('london', london_utc), ('ny', ny_utc), ('asia', (0, 6))):
        start, end = levels.session_window(day, *zones[key])
        assert (start.hour, end.hour, start.date(), end.date()) == (*expected, day, day)


def test_session_range_uses_the_local_window_across_dst():
    # A spike in the 07:00 UTC bar belongs to London only when the UK is on BST.
    for day, inside in ((date(2026, 3, 27), False), (date(2026, 3, 30), True)):
        spike = datetime(day.year, day.month, day.day, 7, tzinfo=UTC)
        h1 = hours(datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=1),
                   datetime(day.year, day.month, day.day, 20, tzinfo=UTC), {spike: (150.0, 99.0, 99.5)})
        k = by_key(levels.build_levels(h1, [], datetime(day.year, day.month, day.day, 20, 5, tzinfo=UTC), 99.5))
        assert (k['london_high_today']['price'] == 150.0) is inside
        assert k['london_high_today']['formed_at'] == (f'{day}T12:00:00Z' if inside else f'{day}T13:00:00Z')


FORMED = datetime(2026, 10, 15, tzinfo=UTC)


def scan(*bars_hlc, level=100.0, side='high'):
    h1 = [{'start': FORMED + timedelta(hours=i), 'end': FORMED + timedelta(hours=i + 1), 'h': h, 'l': l, 'c': c}
          for i, (h, l, c) in enumerate(bars_hlc)]
    return levels.level_state(level, side, FORMED, h1, [])


def test_each_sweep_state():
    assert scan((99, 98, 98.5), (99.9, 98, 99))['state'] == 'resting'
    swept = scan((99, 98, 98.5), (101, 98, 99.5), (99, 98, 98.6))
    assert swept['state'] == 'swept' and swept['touched_at'] == '2026-10-15T01:00:00Z'
    beyond = scan((101, 98, 100.5), *[(102, 100.2, 101)] * 4)
    assert beyond['state'] == 'beyond' and beyond['hours_outside'] == 5
    assert scan((101, 98, 100.5), *[(102, 100.2, 101)] * 5)['state'] == 'broken'  # 6 h of closes outside
    # back inside after having settled outside: the latest close decides (swept)
    assert scan(*[(102, 100.2, 101)] * 8, (101, 99, 99.5))['state'] == 'swept'
    low = scan((101, 99.5, 100), (100.5, 98, 98.5), level=99.0, side='low')
    assert low['state'] == 'beyond' and low['touched_at'] == '2026-10-15T01:00:00Z'


def test_formation_older_than_hourly_history_scans_daily_bars_first():
    d1 = [{'start': datetime(2026, 9, 3, tzinfo=UTC), 'end': datetime(2026, 9, 4, tzinfo=UTC), 'h': 120, 'l': 90, 'c': 95}]
    h1 = [{'start': datetime(2026, 9, 20, tzinfo=UTC), 'end': datetime(2026, 9, 20, 1, tzinfo=UTC), 'h': 99, 'l': 98,
           'c': 98.5}]
    st = levels.level_state(110.0, 'high', datetime(2026, 9, 1, tzinfo=UTC), h1, d1)
    assert st['state'] == 'swept' and st['touched_at'] == '2026-09-03T00:00:00Z'


def test_nearest_resting_levels_skip_swept_ones():
    now = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
    d1 = days(date(2026, 8, 1), date(2026, 10, 15),
              **{'2026-10-14': (101.0, 98.0), '2026-10-07': (120.0, 90.0), '2026-09-18': (130.0, 80.0)})
    # The previous-day high (101) is swept at 03:00 today; the low (98) stays untouched.
    h1 = hours(datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 15, 10, tzinfo=UTC),
               {datetime(2026, 10, 15, 3, tzinfo=UTC): (101.5, 99.0, 99.5)})
    result = levels.build_levels(h1, d1, now, 99.7)
    k = by_key(result)
    assert k['prev_day_high']['state'] == 'swept' and k['prev_day_high']['touched_at'] == '2026-10-15T03:00:00Z'
    assert k['asia_high_today']['price'] == 101.5 and k['asia_high_today']['state'] == 'resting'
    assert result['nearest']['high'] == 'asia_high_today'  # 101.5; the swept 101 is skipped
    low = k[result['nearest']['low']]
    assert low['state'] == 'resting' and low['price'] == 99.0  # session lows of the flat bars, above the 98 PDL


def test_missing_hourly_bars_are_cautions_not_invented():
    now = datetime(2026, 10, 15, 20, 30, tzinfo=UTC)
    h1 = [b for b in hours(datetime(2026, 10, 14, tzinfo=UTC), datetime(2026, 10, 15, 20, tzinfo=UTC))
          if b['t'] != '2026-10-15T09:00:00Z']
    result = levels.build_levels(h1, [], now, 99.5)
    codes = [c['code'] for c in result['cautions']]
    assert 'h1_gap' in codes and 'd1_missing' in codes and result['status'] == 'partial'
    gap = next(c for c in result['cautions'] if c['code'] == 'h1_gap')
    assert gap['missing_bars'] == 1 and gap['from'] == '2026-10-15T09:00:00Z'
    k = by_key(result)
    assert 'london_high_today' not in k and 'ny_high_today' in k  # London 07-12 UTC lost its 09:00 bar
    assert any(c['code'] == 'window_incomplete' and c['key'] == 'london_range_today' for c in result['cautions'])


def test_levels_facts_state_and_valid_until():
    now = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
    d1 = days(date(2026, 8, 1), date(2026, 10, 15), **{'2026-10-14': (82345.6, 80000.0)})
    h1 = hours(datetime(2026, 10, 13, tzinfo=UTC), datetime(2026, 10, 15, 10, tzinfo=UTC))
    rec = {'source_id': 'kraken_ohlc', 'status': 'ok', 'retrieved_at': '2026-10-15T10:01:00Z',
           'observed_at': '2026-10-15T10:00:00Z',
           'values': {'h1': h1, 'd1': d1, 'raw_parts': {'h1': 'e' * 64, 'd1': 'f' * 64},
                      'timestamp_quality': 'source_observed', 'retrieval_started_at': '2026-10-15T10:00:59Z'}}
    F = Facts(now, MANIFEST)
    state = {}
    levels_facts(F, {'kraken_ohlc': rec}, state, now)
    lv = state['levels']
    item = next(i for i in lv['items'] if i['key'] == 'prev_day_high')
    fact = F.index[item['fact_id']]
    assert fact['display'] == '82,345.6' and fact['value'] == 82345.6 and fact['raw_sha256'] == 'f' * 64
    assert fact['category'] == 'liquidity' and fact['metric'] == 'named_level_price' and fact['instrument'] == 'btcusd'
    assert fact['fact_id'] == 'btc.liquidity.kraken.btcusd.named_level_price.d1.prev_day_high'
    assert fact['valid_until'] == '2026-10-15T12:00:00Z' and fact['status'] == 'ok'
    assert set(item) == {'key', 'name', 'side', 'pool', 'fact_id', 'price', 'state', 'state_word', 'touched_at',
                         'formed_at'}
    assert lv['price_basis'] == 'kraken_last_h1_close' and lv['last_bar_start'] == '2026-10-15T09:00:00Z'
    assert set(lv) == {'status', 'last_bar_start', 'items', 'nearest', 'cautions', 'price_basis'}  # no bars in state
    late = Facts(now + timedelta(hours=2), MANIFEST)
    levels_facts(late, {'kraken_ohlc': rec}, state, now + timedelta(hours=2))
    assert state['levels']['status'] == 'stale'


# ------------------------------------------------------------------ Deribit basis

def futures_payload(at=datetime(2026, 10, 15, 11, 59, tzinfo=UTC)):
    ts = int(at.timestamp() * 1000)
    # The live futures summary (2026-10-15) carries the index as estimated_delivery_price, no underlying_price.
    row = lambda name, mark, index: {'instrument_name': name, 'mark_price': mark, 'estimated_delivery_price': index,  # noqa: E731
                                     'creation_timestamp': ts}
    return {'result': [row('BTC-PERPETUAL', 100050.0, 100000.0), row('BTC-17OCT26', 100020.0, 100000.0),
                       row('BTC-26MAR27', 102500.0, 100000.0), row('BTC-25DEC26', 101070.0, 100000.0),
                       row('BTC-25DEC26-100000-C', 0.05, 100000.0)]}


def test_future_expiry_from_name():
    assert options.future_expiry('BTC-25DEC26') == datetime(2026, 12, 25, 8, tzinfo=UTC)
    assert options.future_expiry('BTC-3OCT25') == datetime(2025, 10, 3, 8, tzinfo=UTC)
    assert options.future_expiry('BTC-PERPETUAL') is None and options.future_expiry('BTC-31FEB27') is None


def test_deribit_basis_annualised_skips_short_and_perpetual():
    as_of = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    fetcher = FakeFetcher(futures_payload(), as_of - timedelta(seconds=30))
    rec = record(options.deribit_futures(fetcher))
    assert fetcher.calls[0] == ('https://www.deribit.com/api/v2/public/get_book_summary_by_currency',
                                {'currency': 'BTC', 'kind': 'future'})
    assert [r['instrument'] for r in rec['values']['futures']] == ['BTC-17OCT26', 'BTC-25DEC26', 'BTC-26MAR27']
    F = Facts(as_of, MANIFEST)
    state = {}
    basis_facts(F, {'deribit_futures': rec}, state, as_of)
    b = state['basis']
    assert b['status'] == 'ok' and [i['instrument'] for i in b['items']] == ['BTC-25DEC26', 'BTC-26MAR27']
    dec = b['items'][0]
    days_left = (datetime(2026, 12, 25, 8, tzinfo=UTC) - as_of).total_seconds() / 86400
    assert dec['expiry'] == '2026-12-25T08:00:00Z' and dec['days'] == round(days_left, 2)
    basis, annual = F.index[dec['basis_fact_id']], F.index[dec['annual_fact_id']]
    assert basis['display'] == '+1.07%' and basis['value'] == pytest.approx(1.07)
    assert annual['value'] == round(1.07 * 365 / days_left, 3)
    assert annual['display'] == f'年率 +{1.07 * 365 / days_left:.1f}%（BTC-25DEC26・71日）' == '年率 +5.5%（BTC-25DEC26・71日）'
    assert annual['source_fact_ids'] == [basis['fact_id']] and basis['category'] == 'derivatives'
    assert b['reference']['instrument'] == 'BTC-25DEC26'  # 70.8 days is nearer to 90 than 161.8


def test_deribit_basis_without_eligible_future():
    as_of = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    payload = {'result': [p for p in futures_payload()['result'] if p['instrument_name'] in ('BTC-PERPETUAL', 'BTC-17OCT26')]}
    rec = record(options.deribit_futures(FakeFetcher(payload, as_of)))
    state = {}
    basis_facts(Facts(as_of, MANIFEST), {'deribit_futures': rec}, state, as_of)
    assert state['basis'] == {'status': 'missing', 'items': [], 'reference': None, 'reason': 'no_dated_future_7d_or_more'}
    with pytest.raises(FetchError):
        options.deribit_futures(FakeFetcher({'result': [futures_payload()['result'][0]]}, as_of))


# ------------------------------------------------------------------ collector wiring and end to end

def test_collector_tasks_and_catalog():
    now = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    ids = [t.source_id for t in collect.first_phase_tasks('daily', now, 36)]
    assert {'cnbc_quotes', 'kraken_ohlc', 'deribit_futures'} <= set(ids)
    catalog = dict(collect.SOURCE_CATALOG)
    assert catalog['cnbc_quotes'] == macro.CNBC_URL
    assert catalog['kraken_ohlc'] == 'https://api.kraken.com/0/public/OHLC'
    assert catalog['deribit_futures'] == 'https://www.deribit.com/api/v2/public/get_book_summary_by_currency'
    assert collect.MAX_WORKERS == 6


def collection_with_new_sources():
    collection = copy.deepcopy(load_collection())
    started = datetime.fromisoformat(collection['collection_started_at']).astimezone(UTC)  # 2026-10-07 12:23 UTC
    ny = '2026-10-07T08:05:00.000-0400'
    cnbc = macro.cnbc_quotes(FakeFetcher(cnbc_payload(us2y=('4.72%', '4.79%', ny), dxy=('101.80', '102.40', ny),
                                                      ixic=('26,692.868', '27,203.11', '2026-10-06')), started))
    last_hour = started.replace(minute=0, second=0, microsecond=0)
    h1 = hours(last_hour - timedelta(days=30), last_hour)
    d1 = days(date(2025, 10, 1), started.date())

    def route(url, params):
        rows = h1 if params['interval'] == 60 else d1
        return {'error': [], 'result': {'XXBTZUSD': [[int(datetime.fromisoformat(b['t'].replace('Z', '+00:00')).timestamp()),
                                                      str(b['o']), str(b['h']), str(b['l']), str(b['c']), '0', '1', 1]
                                                     for b in rows], 'last': 0}}
    kraken = market.kraken_ohlc(FakeFetcher(route, started))
    futures = options.deribit_futures(FakeFetcher(futures_payload(started - timedelta(seconds=5)), started))
    collection['sources'] += [cnbc.to_dict(), kraken.to_dict(), futures.to_dict()]
    return collection


def test_end_to_end_facts_schema_and_machine_with_new_sources(tmp_path):
    from tests.btc.test_review_fixes import _edition
    collection = collection_with_new_sources()
    facts, ev, md, html, machine = _edition(collection, tmp_path, 'r16')
    st = facts['state']
    assert schema_validate(facts, json.loads(FACTS_SCHEMA.read_text())) == []
    assert 'future_observation' not in ev['hard_invalid']
    assert st['macro']['rule'] == 'same_day_quotes' and st['macro']['status'] == 'supportive'
    assert st['macro']['same_day']['trading_date'] == '2026-10-07'
    assert ev['groups']['macro']['state'] == 'supportive' and scoring.bundles(facts)['macro']
    assert st['levels']['status'] in ('ok', 'partial') and st['levels']['items']
    assert st['basis']['status'] == 'ok'
    assert {h['source_id'] for h in st['source_health']} >= {'cnbc_quotes', 'kraken_ohlc', 'deribit_futures'}
    assert machine['btc']['macro']['window'] == 'same_day' and machine['btc']['macro']['proxy_labels'] == ['ice_dxy']
