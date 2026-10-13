"""Collectors added 2026-10-13: XAU bars and named levels, CNBC yields, Cleveland Fed nowcast, US event tiers.

All offline: HTTP is replaced by injected getters and bars are synthetic.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from scrapers import inflation_nowcast as nc
from scrapers import us_events as ue
from scrapers import us_yields as uy
from scrapers import xau_bars as xb
from scrapers import xau_levels as xl

UTC = timezone.utc


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# ------------------------------------------------------------------ xau_bars

@pytest.mark.parametrize('when,expected', [
    ('2026-10-10T12:00', False),   # Saturday
    ('2026-10-09T20:00', True),    # Friday 16:00 New York
    ('2026-10-09T21:00', False),   # Friday 17:00 New York: weekly close
    ('2026-10-11T21:00', False),   # Sunday 17:00 New York: before the open
    ('2026-10-11T22:00', True),    # Sunday 18:00 New York: open
    ('2026-10-13T21:00', False)])  # daily 17:00-18:00 New York break
def test_market_hours_follow_new_york_time(when, expected):
    assert xb.market_open(utc(when)) is expected


def test_parse_values_keeps_closed_well_formed_bars_in_order():
    now = utc('2026-10-13T13:15')
    values = [
        {'datetime': '2026-10-13 13:00:00', 'open': '1', 'high': '2', 'low': '1', 'close': '2'},          # not closed yet
        {'datetime': '2026-10-13 12:00:00', 'open': '4000', 'high': '4008', 'low': '3996', 'close': '4001'},
        {'datetime': '2026-10-13 11:00:00', 'open': '4000', 'high': '3999', 'low': '3990', 'close': '3995'},  # high below open
        {'datetime': '2026-10-13 10:00:00', 'open': 'x', 'high': '1', 'low': '1', 'close': '1'},
        {'datetime': '2026-10-13 09:00:00', 'open': '4000', 'high': '4002', 'low': '3998', 'close': '4000'},
        {'datetime': '2026-10-13 09:00:00', 'open': '4000', 'high': '4003', 'low': '3998', 'close': '4001'}]  # same hour, last wins
    bars = xb.parse_values(values, 60, now)
    assert [b['ts'].hour for b in bars] == [9, 12] and bars[0]['high'] == 4003.0


def test_fetch_bars_reports_provider_errors_and_empty_results():
    now = utc('2026-10-13T13:15')
    assert xb.fetch_bars('1h', 10, now, lambda path, params: {'status': 'error', 'message': 'limit'}) == \
        ([], 'Twelve Data XAU/USD 1h 取得不可（limit）')
    assert xb.fetch_bars('15min', 10, now, lambda path, params: {'values': []}) == \
        ([], 'Twelve Data XAU/USD 15min の確定足なし')


def test_cache_merges_fresh_bars_and_collect_falls_back_to_it(tmp_path):
    cache = tmp_path / 'xau_h1.csv'
    bar = lambda hour, close: {'ts': utc(f'2026-10-13T{hour:02d}:00'), 'open': 4000.0, 'high': 4010.0, 'low': 3990.0,
                               'close': close}
    xb.update_cache([bar(8, 4000.0), bar(9, 4001.0)], cache)
    merged = xb.update_cache([bar(9, 4005.0), bar(10, 4002.0)], cache, keep=2)
    assert [(b['ts'].hour, b['close']) for b in merged] == [(9, 4005.0), (10, 4002.0)]
    assert xb.read_cache(cache) == merged

    def offline(path, params):
        raise OSError('offline')
    meta, h1, m15 = xb.collect(utc('2026-10-13T13:15'), offline, cache)
    assert (meta['h1_count'], meta['h1_last_bar_utc'], meta['m15_count'], m15) == (2, '2026-10-13 10:00', 0, [])
    assert meta['error'] == 'Twelve Data XAU/USD 1h: OSError／Twelve Data XAU/USD 15min: OSError'
    assert xb.read_cache(tmp_path / 'missing.csv') == []


# ------------------------------------------------------------------ us_yields

def quote(symbol, last, previous, at):
    return {'symbol': symbol, 'last': last, 'previous_day_closing': previous, 'last_time': at, 'curmktstatus': 'REG_MKT'}


def test_cnbc_yields_recompute_the_change_and_flag_old_quotes():
    payload = {'FormattedQuoteResult': {'FormattedQuote': [
        quote('US2Y', '4.802%', '4.791%', '2026-10-13T09:11:40.000-0400'),
        quote('US10Y', '5.211%', '5.244%', '2026-10-13T09:14:22.000-0400'),
        quote('US30Y', '5.5%', '5.4%', '2026-10-13T09:14:22.000-0400')]}}
    out = uy.parse(payload, utc('2026-10-13T13:20'))
    assert set(out) == {'US2Y', 'US10Y'}
    assert (out['US2Y']['value'], out['US2Y']['change'], out['US2Y']['as_of_jst'], out['US2Y']['stale']) == \
        (4.802, 0.011, '2026-10-13 22:11', False)
    assert out['US10Y']['change'] == -0.033 and out['US2Y']['trading_date'] == '2026-10-13'
    # Today's only within the same New York trading date (17:00 boundary).
    assert uy.parse(payload, utc('2026-10-13T20:59'))['US2Y']['stale'] is False   # 16:59 New York
    assert uy.parse(payload, utc('2026-10-13T21:00'))['US2Y']['stale'] is True    # 17:00 New York: next trading date
    assert uy.parse(payload, utc('2026-10-14T09:00'))['US2Y']['stale'] is True    # next day 18:00 JST, no new quote
    assert uy.parse(payload, utc('2026-10-13T13:00'))['US2Y']['stale'] is True    # a quote from the future is not used


def test_cnbc_failures_are_errors_not_values():
    def broken(url, params):
        raise RuntimeError('down')
    assert uy.fetch_us_yields(utc('2026-10-13T13:20'), broken)['error'] == 'CNBC 取得不可（RuntimeError）'
    partial = {'FormattedQuoteResult': {'FormattedQuote': [quote('US2Y', '4.802%', 'N/A', '2026-10-13T09:11:40.000-0400'),
                                                           quote('US10Y', '5.211%', '5.244%', 'bad time')]}}
    result = uy.fetch_us_yields(utc('2026-10-13T13:20'), lambda url, params: partial)
    assert result['error'] == 'CNBC US2Y・US10Y を解析できず' and 'US2Y' not in result


def test_cnbc_dollar_index_is_the_fallback_before_the_eur_usd_estimate():
    from scrapers import dxy
    payload = {'FormattedQuoteResult': {'FormattedQuote': [quote('.DXY', '102.215', '102.589', '2026-10-14T05:04:58.000-0400')]}}
    out = dxy._scrape_cnbc(lambda url, params: payload, utc('2026-10-14T09:10'))
    assert (out['source'], out['current_price'], out['prev_close'], out['change_pct'], out['estimated'], out['stale']) == \
        ('CNBC', 102.215, 102.589, -0.3646, False, False)
    assert dxy._scrape_cnbc(lambda url, params: payload, utc('2026-10-14T21:30'))['stale'] is True  # next trading date
    assert dxy._scrape_cnbc(lambda url, params: {'FormattedQuoteResult': {'FormattedQuote': [
        quote('.DXY', '102.215', 'N/A', '2026-10-14T05:04:58.000-0400')]}}, utc('2026-10-14T09:00')) is None

    def broken(url, params):
        raise RuntimeError('down')
    assert dxy._scrape_cnbc(broken, utc('2026-10-14T09:00')) is None


# ------------------------------------------------------------------ inflation_nowcast

def chart(month, labels, series):
    return {'chart': {'subcaption': month, '_comment': 'Updated 10/09'},
            'categories': [{'category': [{'label': x} if x != '|' else {'label': 'release', 'vline': '1'} for x in labels]}],
            'dataset': [{'seriesname': name, 'data': [{'value': v} for v in values]} for name, values in series.items()]}


def test_nowcast_takes_the_latest_point_of_each_series_and_skips_release_markers():
    parsed = nc.parse([chart('2026-9', ['09/01', '|', '10/09'], {'CPI Inflation': ['0.4', '0.531'],
                                                                   'Core CPI Inflation': ['', '0.202'],
                                                                   'Quarterly GDP': ['1.0', '2.0']}),
                       {'chart': {'subcaption': 'Q3'}}])
    assert parsed['2026-9'] == {'cpi': {'value': 0.531, 'as_of': '10/09'}, 'core_cpi': {'value': 0.202, 'as_of': '10/09'}}
    assert parsed['_updated'] == 'Updated 10/09'


@pytest.mark.parametrize('indicator,released,month,key', [
    ('CPI (MoM) (Sep)', date(2026, 10, 14), '2026-9', 'cpi'),
    ('Core CPI (MoM) (Sep)', date(2026, 10, 14), '2026-9', 'core_cpi'),
    ('Core PCE Price Index (MoM) (Dec)', date(2027, 1, 30), '2026-12', 'core_pce'),
    ('PCE Price Index (MoM) (Aug)', date(2026, 9, 26), '2026-8', 'pce'),
    ('CPI (YoY) (Sep)', date(2026, 10, 14), '2026-9', None),
    ('Retail Sales (MoM)', date(2026, 10, 15), None, None)])
def test_nowcast_maps_release_month_and_series(indicator, released, month, key):
    assert nc.target_month(indicator, released) == month and nc.series_key(indicator) == key


def test_fetch_nowcast_keeps_three_latest_months_or_reports_an_error():
    months = ['2026-7', '2026-10', '2026-8', '2026-9']
    payload = [chart(m, ['10/09'], {'CPI Inflation': ['0.3']}) for m in months]
    result = nc.fetch_nowcast(lambda url: payload)
    assert list(result['months']) == ['2026-8', '2026-9', '2026-10'] and result['error'] is None
    assert result['updated'] == 'Updated 10/09'
    assert nc.fetch_nowcast(lambda url: []) ['error'] == 'nowcast系列を解析できず'

    def broken(url):
        raise ValueError('bad json')
    assert nc.fetch_nowcast(broken)['error'] == '取得不可（ValueError）'


# ------------------------------------------------------------------ us_events

@pytest.mark.parametrize('indicator,expected', [
    ('Fed Interest Rate Decision', 'A'), ('FOMC Press Conference', 'A'), ('Fed Chair Powell Speaks', 'A'),
    ('Nonfarm Payrolls (Sep)', 'A'), ('Unemployment Rate (Sep)', 'A'), ('Average Hourly Earnings (MoM) (Sep)', 'A'),
    ('Core CPI (MoM) (Sep)', 'A'), ('CPI (YoY) (Sep)', 'A'),
    ('Retail Sales (MoM) (Sep)', 'B'), ('Core Retail Sales (MoM) (Sep)', 'B'), ('GDP (QoQ) (Q3)', 'B'),
    ('Core PCE Price Index (MoM) (Aug)', 'B'), ('PPI (MoM) (Sep)', 'B'), ('ISM Manufacturing PMI (Sep)', 'B'),
    ('ISM Non-Manufacturing PMI (Sep)', 'B'), ('Durable Goods Orders (MoM) (Aug)', 'B'), ('FOMC Meeting Minutes', 'B'),
    ('Michigan 1-Year Inflation Expectations (Oct)', 'B'), ('Initial Jobless Claims', 'B'),
    ('ADP Nonfarm Employment Change (Sep)', 'B'), ('JOLTS Job Openings (Aug)', 'B'),
    ('Continuing Jobless Claims', None), ('Existing Home Sales (Sep)', None), ('Crude Oil Inventories', None),
    ('10-Year Note Auction', None), ('S&P Global Manufacturing PMI (Sep)', None),
    ('Philadelphia Fed Manufacturing Index (Oct)', None), ('CB Consumer Confidence (Sep)', None)])
def test_tiers_are_a_fixed_us_allowlist(indicator, expected):
    assert ue.tier({'country': 'United States', 'indicator': indicator}) == expected
    assert ue.tier({'country': 'Germany', 'indicator': indicator}) is None


@pytest.mark.parametrize('indicator,name', [
    ('CPI (MoM) (Sep)', 'CPI（前月比・9月）'), ('Core Retail Sales (MoM) (Sep)', '小売売上高 除自動車（前月比・9月）'),
    ('GDP (QoQ) (Q3)', 'GDP（前期比年率・Q3）'), ('Michigan 5-Year Inflation Expectations (Oct)', 'ミシガン大 5年先インフレ期待（10月）'),
    ('Fed Chair Powell Speaks', 'FRB議長 発言'), ('Unlisted Survey', 'Unlisted Survey')])
def test_display_names_are_short_japanese(indicator, name):
    assert ue.display_name(indicator) == name


def test_nowcast_gap_needs_a_percent_consensus_and_a_mapped_series():
    nowcast = {'months': {'2026-9': {'cpi': {'value': 0.531, 'as_of': '10/09'}}}}
    event = {'date': 'Wednesday, October 14, 2026', 'time_jst': '21:30', 'country': 'United States',
             'indicator': 'CPI (MoM) (Sep)', 'forecast': '0.6%'}
    assert ue.nowcast_gap(event, nowcast) == {'nowcast': 0.531, 'as_of': '10/09', 'forecast': 0.6, 'gap': -0.069,
                                              'month': '2026-9', 'series': 'cpi'}  # exact, not rounded to -0.07
    near = {'months': {'2026-9': {'cpi': {'value': 0.249, 'as_of': '10/09'}}}}
    assert ue.nowcast_gap({**event, 'forecast': '0.2%'}, near)['gap'] == 0.049      # stays below the 0.05 cut
    assert ue.gap_text(0.049) == '+0.049pp' and ue.gap_text(0.05) == '+0.05pp' and ue.gap_text(-0.0499) == '-0.0499pp'
    assert ue.nowcast_gap({**event, 'forecast': 'N/A'}, nowcast) is None
    assert ue.nowcast_gap({**event, 'indicator': 'Core CPI (MoM) (Sep)'}, nowcast) is None  # no core point
    assert ue.nowcast_gap(event, {'error': 'timeout'}) is None


def test_us_event_lines_list_tiers_in_time_order_with_the_nowcast_gap():
    nowcast = {'months': {'2026-9': {'cpi': {'value': 0.531, 'as_of': '10/09'}}}}
    events = [{'date': 'Wednesday, October 14, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'CPI (MoM) (Sep)', 'previous': '0.4%', 'forecast': '0.6%', 'actual': 'N/A'},
              {'date': 'Monday, October 12, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'Initial Jobless Claims', 'previous': '220K', 'forecast': '225K', 'actual': '231K'},
              {'date': 'Tuesday, October 13, 2026', 'time_jst': '23:00', 'country': 'United States',
               'indicator': 'Existing Home Sales (Sep)', 'previous': '3.98M', 'forecast': '3.95M'}]
    lines = ue.format_us_event_lines(events, nowcast, '2026-10-13T18:00:00+09:00')
    assert lines[1] == '- Tier B｜10/12 21:30 JST｜新規失業保険申請｜前回 220K｜予想 225K｜結果 231K'
    assert lines[2] == ('- Tier A｜10/14 21:30 JST｜CPI（前月比・9月）｜前回 0.4%｜予想 0.6%'
                        '｜Cleveland連銀nowcast 0.53%（10/09時点）− 市場予想 0.6% = -0.07pp')
    assert len(lines) == 5 and lines[3].startswith('- 指標の採否:') and 'Existing' not in '\n'.join(lines)
    assert ue.format_us_event_lines([], None, None)[1] == '- 該当なし（収集範囲内に米国のTier A/B指標なし）'
    # The same adoption rule as the board: no gap once released, with an old nowcast, or without a time.
    after = ue.format_us_event_lines(events, nowcast, '2026-10-14T22:00:00+09:00')
    assert 'nowcast' not in after[2]
    old = ue.format_us_event_lines(events, nowcast, '2026-10-12T18:00:00+09:00')
    assert 'nowcast' in old[2]                                                         # 3 days old: used
    stale = {'months': {'2026-9': {'cpi': {'value': 0.531, 'as_of': '10/01'}}}}
    assert 'nowcast' not in ue.format_us_event_lines(events, stale, '2026-10-13T18:00:00+09:00')[2]  # 12 days old
    assert 'nowcast' not in ue.format_us_event_lines(events, nowcast, None)[2]
    # With a time, the parent sees the HTML table's period and releases: Tier B two days ahead only.
    later = {**events[1], 'date': 'Friday, October 16, 2026', 'actual': 'N/A'}  # Tier B claims
    lines = ue.format_us_event_lines(events + [later], nowcast, '2026-10-13T18:00:00+09:00')
    assert lines[0].startswith('### 米国の重要指標（10/12からの結果と、Tier Aは10/20・Tier Bは10/15までの予定。')
    assert not any('10/16' in line for line in lines)


def test_windows_are_whole_jst_days_from_the_previous_weekday():
    jst = lambda text: datetime.fromisoformat(text + '+09:00')
    assert ue.results_start(jst('2026-10-19T18:00'), 'daily') == jst('2026-10-16T00:00')   # Monday: from Friday
    assert ue.results_start(jst('2026-10-14T18:00'), 'daily') == jst('2026-10-13T00:00')
    assert ue.results_start(jst('2026-10-17T07:00'), 'weekly') == jst('2026-10-10T00:00')
    assert ue.event_window(jst('2026-10-14T18:00'), 'daily', 'B') == (jst('2026-10-13T00:00'), jst('2026-10-17T00:00'))
    assert ue.window_label(jst('2026-10-14T18:00'), 'daily') == '10/13からの結果と、Tier Aは10/21・Tier Bは10/16までの予定'
    assert ue.window_label(jst('2026-10-17T07:00'), 'weekly') == '10/10〜10/17の結果と10/25までの予定'


# ------------------------------------------------------------------ xau_levels

def flat_bars(start, end, base=4000.0):
    out, t = [], utc(start)
    while t <= utc(end):
        if xb.market_open(t):
            out.append({'ts': t, 'open': base, 'high': base + 1, 'low': base - 1, 'close': base})
        t += timedelta(hours=1)
    return out


def set_bar(bars, when, **values):
    bar = next(b for b in bars if b['ts'] == utc(when))
    bar.update(values)


def scenario():
    """Synthetic H1 bars from trading date 09/01 to 10/13 12:00 UTC with known extremes (comments in JST)."""
    bars = flat_bars('2026-08-31T21:00', '2026-10-13T12:00')  # from the start of the previous month
    set_bar(bars, '2026-09-29T10:00', low=3880.0)                   # last month's low
    set_bar(bars, '2026-09-29T15:00', high=4100.0)                  # last month's high
    set_bar(bars, '2026-10-07T14:00', high=4070.0)                  # equal highs, 4 bars apart
    set_bar(bars, '2026-10-07T18:00', high=4070.5)
    set_bar(bars, '2026-10-08T10:00', low=3900.0)                   # last week's low
    set_bar(bars, '2026-10-12T03:00', low=3950.0)                   # previous day's low
    set_bar(bars, '2026-10-12T14:00', high=4050.0)                  # previous day's high (10/12 23:00 JST)
    set_bar(bars, '2026-10-13T02:00', high=4020.0)                  # Asia high (11:00 JST)
    set_bar(bars, '2026-10-13T05:00', low=3990.0)                   # Asia low (14:00 JST)
    set_bar(bars, '2026-10-13T08:00', high=4030.0)                  # London high (17:00 JST), trades through the Asia high
    set_bar(bars, '2026-10-13T11:00', low=3945.0, close=3960.0)     # London low (20:00 JST), sweeps the previous day's low
    set_bar(bars, '2026-10-13T12:00', high=4008.0, low=3996.0)      # NY, still forming
    m15 = [{'ts': utc('2026-10-12T12:30'), 'open': 4000.0, 'high': 4012.0, 'low': 3992.0, 'close': 4004.0},
           {'ts': utc('2026-10-12T12:45'), 'open': 4004.0, 'high': 4005.0, 'low': 3999.0, 'close': 4000.0}]  # rest of the hour
    events = [{'date': 'Monday, October 12, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'Initial Jobless Claims'},
              {'date': 'Tuesday, October 13, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'Retail Sales (MoM) (Sep)'},
              {'date': 'Tuesday, October 13, 2026', 'time_jst': '23:00', 'country': 'United States',
               'indicator': 'Existing Home Sales (Sep)'},
              {'date': 'Friday, October 9, 2026', 'time_jst': '21:30', 'country': 'United States',
               'indicator': 'Nonfarm Payrolls (Sep)'}]
    return bars, m15, events


def test_trading_day_uses_the_new_york_close_and_sessions_use_each_market_clock():
    assert xl.trading_date(utc('2026-10-12T20:00')) == date(2026, 10, 12)   # 16:00 New York
    assert xl.trading_date(utc('2026-10-12T21:00')) == date(2026, 10, 13)   # 17:00 New York starts the next day
    assert xl.current_trading_date(utc('2026-10-10T12:00')) == date(2026, 10, 12)  # Saturday -> Monday
    assert xl.previous_trading_date(date(2026, 10, 12)) == date(2026, 10, 9)   # Monday -> Friday
    # Both on summer time: London 16-21 JST, NY 21-02 JST.
    assert [xl.session_of(utc(t)) for t in ('2026-10-13T00:00', '2026-10-13T07:00', '2026-10-13T12:00',
                                            '2026-10-13T16:00', '2026-10-13T17:00', '2026-10-12T23:00')] == \
        ['アジア', 'London', 'NY', 'NY', '時間外', '時間外']
    # 2026-10-27: the UK is on winter time (from 10/25), the US still on summer time (until 11/01).
    assert [xl.session_of(utc(t)) for t in ('2026-10-27T07:00', '2026-10-27T08:00', '2026-10-27T12:00',
                                            '2026-10-27T13:00', '2026-10-27T16:00')] == \
        ['時間外', 'London', 'NY', 'NY', 'NY']  # London 17-22 JST and NY 21-02 JST overlap for an hour
    # 2026-11-10: both on winter time: London 17-22 JST, NY 22-03 JST.
    assert [xl.session_of(utc(t)) for t in ('2026-11-10T07:00', '2026-11-10T08:00', '2026-11-10T12:00',
                                            '2026-11-10T13:00', '2026-11-10T17:00', '2026-11-10T18:00')] == \
        ['時間外', 'London', 'London', 'NY', 'NY', '時間外']
    windows = {key: (start.strftime('%H:%M'), end.strftime('%H:%M')) for key, _, start, end in xl.session_windows(date(2026, 11, 10))}
    assert windows == {'asia': ('00:00', '07:00'), 'london': ('08:00', '13:00'), 'ny': ('13:00', '18:00')}  # UTC


def test_sweep_states_follow_the_closes_after_the_first_trade_through():
    bar = lambda hour, high, close: {'ts': utc(f'2026-10-13T{hour:02d}:00'), 'high': high, 'low': close - 1, 'close': close}
    assert xl.sweep_state(4010, 'high', [bar(1, 4005, 4000)]) == ('resting', None)
    assert xl.sweep_state(4010, 'high', [bar(1, 4012, 4011), bar(2, 4011, 4009)]) == ('swept', utc('2026-10-13T01:00'))
    outside = [bar(h, 4020, 4015) for h in range(1, 6)]
    assert xl.sweep_state(4010, 'high', outside)[0] == 'beyond'          # five closes outside so far
    assert xl.sweep_state(4010, 'high', outside + [bar(6, 4020, 4015)])[0] == 'broken'  # six closes outside
    assert xl.sweep_state(4010, 'high', outside + [bar(6, 4020, 4005)])[0] == 'swept'   # back inside on the sixth


def test_sweep_state_is_the_latest_close_and_touched_at_is_the_first_cross():
    bar = lambda hour, high, close: {'ts': utc(f'2026-10-13T{hour:02d}:00'), 'high': high, 'low': close - 1, 'close': close}
    held_out = [bar(h, 4020, 4015) for h in range(1, 9)]                       # eight hours outside: was 定着
    assert xl.sweep_state(4010, 'high', held_out) == ('broken', utc('2026-10-13T01:00'))
    back = held_out + [bar(9, 4016, 4004)]                                     # now back inside
    assert xl.sweep_state(4010, 'high', back) == ('swept', utc('2026-10-13T01:00'))
    again = back + [bar(10, 4018, 4013), bar(11, 4019, 4014)]                  # out again for two hours
    assert xl.sweep_state(4010, 'high', again) == ('beyond', utc('2026-10-13T01:00'))
    low = [{'ts': utc('2026-10-13T01:00'), 'high': 4001, 'low': 3989, 'close': 3995}]
    assert xl.sweep_state(3990, 'low', low) == ('swept', utc('2026-10-13T01:00'))
    quarter = [{'ts': utc('2026-10-13T00:45'), 'high': 4020, 'low': 4010, 'close': 4015, 'hours': 0.25}]
    assert xl.sweep_state(4010, 'high', quarter + held_out[:5]) == ('beyond', utc('2026-10-13T00:45'))  # 5.25 hours
    assert xl.sweep_state(4010, 'high', quarter + held_out[:6]) == ('broken', utc('2026-10-13T00:45'))  # 6.25 hours


def test_scan_after_a_15_minute_release_candle_uses_m15_until_the_hour():
    h1 = [{'ts': utc('2026-10-13T12:00'), 'high': 4030, 'low': 3990, 'close': 4000},   # holds the release itself
          {'ts': utc('2026-10-13T13:00'), 'high': 4004, 'low': 3998, 'close': 4001}]
    m15 = [{'ts': utc('2026-10-13T12:30'), 'high': 4010, 'low': 3995, 'close': 4005},  # the release candle
           {'ts': utc('2026-10-13T12:45'), 'high': 4012, 'low': 4003, 'close': 4004}]  # trades through 4010 right after
    bars = xl.scan_bars(h1, m15, utc('2026-10-13T12:45'))
    assert [(b['ts'].strftime('%H:%M'), b.get('hours', 1.0)) for b in bars] == [('12:45', 0.25), ('13:00', 1.0)]
    assert xl.sweep_state(4010, 'high', bars) == ('swept', utc('2026-10-13T12:45'))
    assert xl.scan_bars(h1, m15, utc('2026-10-13T13:00')) == h1[1:]


def test_build_levels_names_each_level_with_its_state():
    bars, m15, events = scenario()
    result = xl.build_levels(bars, m15, utc('2026-10-13T13:15'), price=4000.0, events=events)
    assert (result['error'], result['trading_day'], result['last_bar_utc'], result['midnight_open']) == \
        (None, '2026-10-13', '2026-10-13 12:00', 4000.0)
    got = [(lv['name'], lv['price'], lv['state_word'], lv['touched_at'], lv['touched_session']) for lv in result['levels']]
    assert got == [
        ('前月高値（9月）', 4100.0, '未到達', None, None),
        ('前週高値（10/05週）・等高値（2回・10/08 03:00まで）', 4070.5, '未到達', None, None),
        ('前日高値（10/12）', 4050.0, '未到達', None, None),
        ('London高値（当日）', 4030.0, '未到達', None, None),
        ('アジア高値（当日）', 4020.0, '掃引済み', '10/13 17:00', 'London'),
        ('新規失業保険申請 発表15分足高値（10/12 21:30）', 4012.0, '掃引済み', '10/12 23:00', 'NY'),
        ('NY高値（当日・形成中）', 4008.0, '形成中', None, None),
        ('小売売上高 発表時刻を含む1時間足高値（10/13 21:30）', 4008.0, '未到達', None, None),
        ('NY安値（当日・形成中）', 3996.0, '形成中', None, None),
        ('小売売上高 発表時刻を含む1時間足安値（10/13 21:30）', 3996.0, '未到達', None, None),
        ('新規失業保険申請 発表15分足安値（10/12 21:30）', 3992.0, '掃引済み', '10/13 14:00', 'アジア'),
        ('アジア安値（当日）', 3990.0, '掃引済み', '10/13 20:00', 'London'),
        ('前日安値（10/12）', 3950.0, '掃引済み', '10/13 20:00', 'London'),
        ('London安値（当日）', 3945.0, '未到達', None, None),
        ('前週安値（10/05週）', 3900.0, '未到達', None, None),
        ('前月安値（9月）', 3880.0, '未到達', None, None)]
    merged = result['levels'][1]
    assert merged['key'] == 'PWH+EQH' and merged['side'] == 'high'
    news = next(lv for lv in result['levels'] if lv['name'].startswith('新規失業保険申請 発表15分足高値'))
    assert news['note'] == 'Tier B' and news['distance'] == 12.0 and news['distance_pct'] == 0.3
    assert not any('_scan_from' in lv for lv in result['levels'])


def test_weekly_levels_add_this_month_and_only_tier_a_news():
    bars, m15, events = scenario()
    result = xl.build_levels(bars, m15, utc('2026-10-13T13:15'), price=4000.0, events=events, weekly=True)
    names = [lv['name'] for lv in result['levels']]
    assert any(name.startswith('今月高値（10月）') or '今月高値（10月）' in name for name in names)
    assert not any('新規失業保険申請' in name or '小売売上高' in name for name in names)  # Tier B is daily-only
    assert '雇用統計 非農業部門雇用者数 発表時刻を含む1時間足高値（10/09 21:30）' in names  # no 15-minute bar: the 1-hour candle
    assert not any(lv['group'] == 'session' for lv in result['levels']) and result['midnight_open'] is None


def test_no_bars_is_an_error_and_lines_say_so():
    result = xl.build_levels([], [], utc('2026-10-13T13:15'))
    assert result['error'].startswith('1時間足なし') and result['levels'] == []
    assert xl.format_level_lines(result) == ['### 流動性の位置（名前付き水準と掃引状態、コード計算）',
                                             '- 取得不可（1時間足なし（水準と掃引状態は計算できません））']


def test_level_lines_give_price_distance_pool_and_state():
    bars, m15, events = scenario()
    lines = xl.format_level_lines(xl.build_levels(bars, m15, utc('2026-10-13T13:15'), price=4000.0, events=events))
    assert lines[1].startswith('- 基準: Twelve Data XAU/USD 1時間足（UTC、確定足）、日足の区切り NY 17:00、最終確定足 2026-10-13 12:00 UTC')
    assert '- 前日安値（10/12）: 3,950.00（現在値比 -50.00／-1.25%）｜SSL｜掃引済み・10/13 20:00 JST（London）' in lines
    assert '- 前日高値（10/12）: 4,050.00（現在値比 +50.00／+1.25%）｜BSL｜未到達' in lines
    assert '- 新規失業保険申請 発表15分足高値（10/12 21:30）: 4,012.00（現在値比 +12.00／+0.30%）｜BSL｜掃引済み・10/12 23:00 JST（NY）｜Tier B' in lines
    assert not any('キリ' in line or 'が残る' in line for line in lines)  # stops are candidates, not observed orders
    assert lines[-1] == ('- 直近の未到達: 上 小売売上高 発表時刻を含む1時間足高値（10/13 21:30） 4,008.00'
                         '／下 小売売上高 発表時刻を含む1時間足安値（10/13 21:30） 3,996.00')


def test_merge_levels_joins_only_the_same_price_and_state():
    row = lambda key, price, state='resting', touched=None, note='': {
        'key': key, 'name': key.lower(), 'side': 'high', 'price': price, 'state': state, 'touched_at': touched, 'note': note}
    merged = xl.merge_levels([row('A', 4008.0, 'forming'), row('B', 4008.2, note='x'), row('C', 4008.4, note='y'),
                              row('D', 4008.4, note='z'), row('E', 4008.4, 'swept', '10/13 01:00'), row('F', 4008.0)])
    assert [(m['key'], m['name'], m['note']) for m in merged] == [
        ('C+D', 'c・d', 'y・z'), ('E', 'e', ''), ('B', 'b', 'x'), ('A', 'a', ''), ('F', 'f', '')]
    # A later high a few cents above an older one is its own row: its state is not taken from the older one.


def test_news_candles_stay_through_the_next_trading_day():
    bars, m15, events = scenario()
    until = lambda t: [b for b in bars if b['ts'] < utc(t)]
    monday = xl.build_levels(until('2026-10-12T08:00'), m15, utc('2026-10-12T09:00'), price=4000.0, events=events)
    assert any('雇用統計 非農業部門雇用者数 発表時刻を含む1時間足高値（10/09 21:30）' in lv['name'] for lv in monday['levels'])
    tuesday = xl.build_levels(until('2026-10-13T08:00'), m15, utc('2026-10-13T09:00'), price=4000.0, events=events)
    assert not any('雇用統計' in lv['name'] for lv in tuesday['levels'])  # gone after the next trading day


def test_coverage_flags_stale_and_missing_bars():
    bars, m15, events = scenario()
    fresh = xl.build_levels(bars, m15, utc('2026-10-13T13:15'), price=4000.0, events=events)
    assert fresh['cautions'] == [] and fresh['coverage']['stale_hours'] == 0
    assert fresh['coverage']['previous_day'] == {'date': '2026-10-12', 'bars': 23, 'expected': 23}
    stale = xl.build_levels(bars, m15, utc('2026-10-13T18:30'), price=4000.0, events=events)
    assert stale['coverage']['stale_hours'] == 5
    assert stale['cautions'] == ['最終確定足が10/13 21:00 JSTで、その後の5時間分の足がない。以降の値動きは状態に入っていない']
    weekend = xl.build_levels(flat_bars('2026-08-31T21:00', '2026-10-09T20:00'), [], utc('2026-10-10T00:00'), price=4000.0)
    assert weekend['cautions'] == []  # the market is closed after Friday 17:00 New York
    gappy = [b for b in bars if not (utc('2026-10-12T03:00') <= b['ts'] <= utc('2026-10-12T06:00'))
             and not (utc('2026-10-13T03:00') <= b['ts'] <= utc('2026-10-13T04:00'))]
    result = xl.build_levels(gappy, m15, utc('2026-10-13T13:15'), price=4000.0, events=events)
    assert result['cautions'] == ['前日（10/12）の1時間足が19/23本。欠けた時間の高安は入っていない',
                                  '当日の1時間足に2本の欠落。その時間の値動きは状態に入っていない']
    no_monday = [b for b in bars if xl.trading_date(b['ts']) != date(2026, 10, 12)]
    result = xl.build_levels(no_monday, m15, utc('2026-10-13T13:15'), price=4000.0, events=events)
    assert result['cautions'][0] == '10/12の1時間足がない（休場か取得欠落）。前日高安は10/09の値'
    assert any(lv['name'].startswith('前日高値（10/09）') for lv in result['levels'])  # named with its real date
    lines = xl.format_level_lines(stale)
    assert '- 注意: 最終確定足が10/13 21:00 JSTで、その後の5時間分の足がない。以降の値動きは状態に入っていない' in lines
