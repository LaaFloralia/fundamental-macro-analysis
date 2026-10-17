"""Oil-specific semantics and the unchanged MD/HTML/fact binding contract."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from usoil import dashboard, d1_bias, levels, news, report, scoring
from usoil.bundle import check_bindings
from usoil.presentation import validate_quotes
from scripts.offchart_dashboard import build as render_html

UTC = timezone.utc
ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 10, 16, 22, tzinfo=UTC)


def bar(day, *, hour=0, high=105, low=95, close=100):
    start = datetime.combine(day, datetime.min.time(), ET) + timedelta(hours=hour)
    return {'t': start.astimezone(UTC).isoformat(), 'o': close, 'h': high, 'l': low, 'c': close}


def test_previous_week_needs_all_five_et_dates():
    monday = date(2026, 10, 5)
    rows = [bar(monday + timedelta(days=i), high=101+i, low=90+i) for i in range(5)]
    h1 = [bar(date(2026, 10, 16), hour=15)]
    complete = levels.build_levels(h1, rows, NOW, 100.77)
    weekly = [i for i in complete['items'] if i['key'].startswith('prev_week')]
    assert {i['price'] for i in weekly} == {105, 90}
    assert complete['weekly_range']['width'] == 15
    missing = levels.build_levels(h1, rows[:4], NOW, 100.77)
    assert not any(i['key'].startswith('prev_week') for i in missing['items'])
    assert any(c.get('key') == 'prev_week_range' and c['code'] == 'window_incomplete' for c in missing['cautions'])


def test_et_day_boundary_and_dst_are_not_utc_calendar():
    start, end = levels.period_window('prev_day', date(2026, 11, 2))
    assert start == datetime(2026, 11, 1, 4, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 5, tzinfo=UTC)
    # 00:30 UTC Friday is Thursday in ET, so previous date is Wednesday.
    built = levels.build_levels([], [bar(date(2026, 10, 14), high=104)],
                                datetime(2026, 10, 16, 0, 30, tzinfo=UTC), None)
    assert any(i['key'] == 'prev_day_high' and i['price'] == 104 for i in built['items'])


def test_maintenance_null_and_forming_bars_do_not_create_levels_or_false_gaps():
    day = date(2026, 10, 15)
    rows = [bar(day, hour=16), dict(bar(day, hour=17), h=None, l=None, c=None), bar(day, hour=18)]
    rows += [bar(date(2026, 10, 16), hour=18, high=999)]  # closes after NOW
    built = levels.build_levels(rows, [], NOW, 100)
    assert not any(c['code'] == 'h1_gap' for c in built['cautions'])
    assert built['last_bar_start'] == '2026-10-15T22:00:00Z'
    assert not any('asia' in i['key'] or 'london' in i['key'] or 'ny_' in i['key'] for i in built['items'])


def test_real_trading_hour_gap_remains_visible():
    rows = [bar(date(2026, 10, 15), hour=14), bar(date(2026, 10, 15), hour=16)]
    built = levels.build_levels(rows, [], NOW, 100)
    assert any(c['code'] == 'h1_gap' and c['missing_bars'] == 1 for c in built['cautions'])


def test_rounds_use_one_dollar_and_emphasize_five_without_fake_contract_comparisons():
    built = levels.build_levels([bar(date(2026, 10, 16), hour=15)], [], NOW, 100)
    rounds = [i for i in built['items'] if i['key'].startswith('round_')]
    assert [i['price'] for i in rounds] == list(range(97, 104))
    assert [i['price'] for i in rounds if i['major']] == [100]
    assert all(i['state'] == 'reference' and i['pool'] is None for i in rounds)
    assert built['nearest'] == {'high': None, 'low': None}
    assert built['reference_position_pct'] is None
    assert built['comparison_status'] == 'unverified'


def test_oil_news_sources_and_fallback_do_not_keep_crypto_only_news():
    ids = {i[0] for i in news.GENERAL_FEEDS + news.OFFICIAL_FEEDS}
    assert ids == {'oilprice_rss', 'investing_commodities_rss', 'investing_economy_rss', 'eia_todayinenergy_rss', 'cftc_rss'}
    assert news.FALLBACK.search('OPEC cuts crude oil production')
    assert not news.FALLBACK.search('Bitcoin ETF approved for crypto investors')


@pytest.mark.parametrize('url,source,allowed', [
    ('https://oilprice.com/Energy/a.html', 'oilprice_rss', True),
    ('https://www.eia.gov/todayinenergy/a.php', 'eia_todayinenergy_rss', True),
    ('https://oilprice.com.evil.test/a', 'oilprice_rss', False),
    ('https://oilprice.com@127.0.0.1/a', 'oilprice_rss', False),
    ('http://oilprice.com/a', 'oilprice_rss', False),
    ('https://www.investing.com/news/a', 'investing_economy_rss', False),
])
def test_oil_body_allowlist_keeps_security_boundary(url, source, allowed):
    assert news.body_url_allowed(url, source) is allowed


def test_news_minute_reaction_is_missing_without_network_or_fake_ticker():
    class NoFetch:
        def get(self, *args, **kwargs):
            pytest.fail('No aligned oil minute source must mean no reaction request')
    result = news.reaction(NoFetch(), NOW.isoformat(), NOW, [])
    assert result['status'] == 'missing' and result['market_id'] is None
    assert result['return_after_5m_bps'] is None
    assert '原油1分足は未取得' in result['note']


def test_d1_is_off_by_default_before_any_file_read_or_provider_call(monkeypatch):
    monkeypatch.delenv('USOIL_D1_ENABLED', raising=False)
    monkeypatch.setattr(d1_bias, '_read_regular', lambda *args: pytest.fail('default-off read'))
    assert d1_bias.entry_reference(Path('/missing'), 'daily', decide=lambda *args: pytest.fail('provider call')) == 'skipped'


def material():
    rows, ids = [], {}
    def add(block, key, value, unit='percent'):
        fid = f'usoil.{block}.test.wti.{key}.snapshot.20261016'
        ids.setdefault(block, {'status': 'ok'})[key] = fid
        rows.append({'fact_id': fid, 'logical_id': fid, 'category': block, 'metric': key, 'value': value,
                     'display_value': value, 'display': f'{value:+.3f} {unit}', 'status': 'ok', 'stale': False,
                     'observed_at': NOW.isoformat(), 'observation_date': None, 'period_end': None,
                     'published_at': None, 'retrieved_at': NOW.isoformat(), 'valid_until': (NOW+timedelta(hours=6)).isoformat(),
                     'source_id': 'test', 'unit': unit, 'missing_reason': None, 'method_id': 'synthetic'})
        return fid
    ref = add('reference_price', 'fact_id', 100.77, 'USD/bbl')
    add('inventory', 'change_crude', -8.557, 'million_bbl')
    add('inventory', 'change_products', .991, 'million_bbl')
    add('curve', 'spread_pct', 2.1)
    add('curve', 'second_third_pct', 1.78)
    add('cot', 'mm_net_ratio', .1); add('cot', 'mm_net_ratio_percentile', 50); add('cot', 'oi_change_pct', 1)
    add('vol', 'level', 50.51, 'index'); add('vol', 'percentile', 50); add('vol', 'change_1d', 2)
    dxy = add('macro', 'dxy_change', -.4)
    real = add('macro', 'DFII10_change', -6, 'bp')
    ids['macro'].update(status='supportive', reason='same_day_dxy_real10', change_fact_ids=[dxy, real],
                        same_day={'dxy_change': dxy}, fact_ids=[dxy, real])
    ids['reference_price'].update(fallback=False, provider='cnbc', venue_fact_ids=[ref])
    ids.update(calendar={'next_24h': 'ok'}, events=[], source_health=[],
               news={'status': 'ok', 'items': [], 'feeds_ok': {'general': 2, 'official': 1}, 'feeds': [],
                     'candidate_count': 0, 'window_start': (NOW-timedelta(hours=36)).isoformat(),
                     'window_end': NOW.isoformat(), 'lookback_hours': 36},
               collection={'started_at': NOW.isoformat(), 'completed_at': NOW.isoformat()},
               levels={'status': 'missing', 'items': [], 'cautions': []})
    return {'as_of': NOW.isoformat(), 'collected_at': NOW.isoformat(), 'input_manifest_sha256': 'a'*64,
            'state': ids, 'facts': rows}


def parent_analysis():
    claim = {'text': '原油の需給を観測範囲で解釈する。', 'fact_ids': [], 'claim_kind': 'hypothesis'}
    result = {'thesis': claim, 'positioning_summary': claim, 'three_domains': {k: claim for k in ('liquidity', 'positioning', 'bias')},
              'news_assessments': [], 'destination_choice': {'side': 'none', 'kind': 'none', 'level_fact_ids': [],
                  'rationale': claim, 'counter_case': claim, 'invalidation_rule_ids': []},
              'scenarios': [{'id': 'base', 'condition_rule_ids': ['inventory_direction_flip'], 'fact_ids': [],
                             'expected_effect': claim, 'counter_case': claim, 'invalidation_rule_ids': ['inventory_direction_flip']}],
              'counter_cases': [claim], 'reevaluation_conditions': [{'rule_id': 'source_recovers', 'fact_ids': [], 'explanation': claim}],
              'limitations': [dict(claim, claim_kind='limitation')]}
    result['expanded'] = {k: claim['text'] for k in ('thesis', 'positioning_summary', 'three_domains.liquidity',
        'three_domains.positioning', 'three_domains.bias', 'destination_choice.rationale', 'destination_choice.counter_case',
        'scenarios[0].expected_effect', 'scenarios[0].counter_case', 'counter_cases[0]', 'reevaluation_conditions[0].explanation',
        'limitations[0]')}
    return result


def test_oil_report_keeps_six_fields_and_md_html_numeric_contract(tmp_path):
    facts, analysis = material(), parent_analysis()
    ctx = SimpleNamespace(mode='daily', session_slot='pm', generated_at=NOW, root=tmp_path,
                          work_dir=tmp_path/'oil-test', previous_editions=[])
    parts = report.build(analysis, facts, {'collector_version': 'test'}, ctx)
    assert set(parts.machine_core) == {'bias', 'no_trade', 'no_trade_reason', 'risk_events_next_24h', 'positioning_summary', 'confidence'}
    oil = parts.machine_extensions['usoil']
    assert all(oil[key]['fact_ids'] for key in ('inventory', 'curve', 'cot', 'vol'))
    assert not {'etf', 'derivatives', 'options', 'onchain'} & set(oil)
    validate_quotes(parts.summary, parts.figures, parts.markdown)
    check_bindings({f['fact_id']: f for f in facts['facts']}, parts.bindings, parts.figures)
    html, audit = render_html(parts.markdown, parts.summary, parts.figures, title='USOIL', source_name='oil.md')
    assert audit['numeric_check'] == 'pass'
    assert '-8.557' in html and 'EIA商業原油在庫' in html and 'OVX' in html
    assert 'リテールセンチメント: v0.1未実装' in parts.markdown
    for stale_crypto in ('Farside', 'Deribit', 'Binance', 'ステーブル供給', 'USOILT', 'bitcoin'):
        assert stale_crypto not in parts.markdown


def test_liquidity_page_does_not_assert_cross_contract_distances():
    facts = material()
    fact = facts['facts'][0]
    facts['state']['levels'] = {'items': [{'key': 'prev_day_high', 'name': '前日高値', 'price': 105,
        'fact_id': fact['fact_id'], 'side': 'high', 'state': 'resting', 'state_word': '未到達'}]}
    doc = report.Doc(); page = dashboard.Page(doc, {f['fact_id']: f for f in facts['facts']})
    figure = dashboard.liquidity_figure(page, facts, {'choice': {'kind': 'none'}, 'status': 'none'}, 'daily', NOW)
    assert all(i['distance_text'] == '' and not i['pool'] for i in figure['items'])
    assert '限月整合未確認' in figure['subtitle']


def test_previous_month_requires_every_weekday_without_filling_holiday_holes():
    first = date(2026, 9, 1)
    rows = [bar(first + timedelta(days=i), high=100+i, low=80+i, close=95+i)
            for i in range(30) if (first+timedelta(days=i)).weekday() < 5]
    complete = levels.build_levels([], rows, NOW, None)
    assert {i['price'] for i in complete['items'] if i['key'].startswith('prev_month')} == {129, 80}
    missing = levels.build_levels([], rows[1:], NOW, None)
    assert not any(i['key'].startswith('prev_month') for i in missing['items'])
    assert any(c.get('key') == 'prev_month_range' for c in missing['cautions'])


def test_weekend_note_is_visible_without_changing_the_gate(tmp_path):
    facts, analysis = material(), parent_analysis()
    saturday = datetime(2026, 10, 17, 1, tzinfo=UTC)
    facts['as_of'] = saturday.isoformat()
    ctx = SimpleNamespace(mode='weekly', session_slot='weekly', generated_at=saturday, root=tmp_path,
                          work_dir=tmp_path/'oil-weekly', previous_editions=[])
    parts = report.build(analysis, facts, {'collector_version': 'test'}, ctx)
    assert '週末注記' in parts.markdown
    assert any('週末注記' in item['text'] for item in parts.summary['chips'])
    assert parts.machine_extensions['usoil']['trade_gate']['status'] == 'clear'


def test_d1_material_uses_only_oil_semantics():
    facts = material()
    result = d1_bias.usoil_state(facts, 'daily')
    assert result['symbol'] == 'USOIL'
    assert 'WTI crude oil' in result['direction_note']
    assert any('inventory.' in row and '-8.557' in row for row in result['rows'])
    assert any('curve.' in row for row in result['rows'])
    assert any('cot.' in row for row in result['rows'])
    assert any('vol.' in row for row in result['rows'])
    assert all(not row.startswith('l.') for row in result['rows'])  # prefix length is USOIL-specific


def test_second_third_curve_live_state_key_reaches_all_presentations(tmp_path):
    facts, analysis = material(), parent_analysis()
    fid = facts['state']['curve']['second_third_pct']
    fact = next(f for f in facts['facts'] if f['fact_id'] == fid)
    ctx = SimpleNamespace(mode='daily', session_slot='pm', generated_at=NOW, root=tmp_path,
                          work_dir=tmp_path/'oil-curve', previous_editions=[])
    parts = report.build(analysis, facts, {'collector_version': 'test'}, ctx)
    rows = next(f for f in parts.figures if f['id'] == 'bias')['items']
    row = next(r for r in rows if r['label'] == 'WTI第2−第3限月差率')
    assert row['display'] == fact['display']
    assert row['value'] == 1.78 and row['direction'] == '参考'
    assert 'WTI第2−第3限月差率: ' + fact['display'] in parts.markdown
    assert any('second_third_pct' in r and fact['display'] in r for r in d1_bias.usoil_state(facts, 'daily')['rows'])
    check_bindings({f['fact_id']: f for f in facts['facts']}, parts.bindings, parts.figures)


def test_products_inventory_is_reference_even_when_oil_supply_group_supportive():
    facts = material()
    ev = scoring.evaluate(facts, mode='daily', assessments=[])
    assert ev['groups']['inventory_supply']['state'] == 'supportive'
    page = dashboard.Page(report.Doc(), {f['fact_id']: f for f in facts['facts']})
    rows = {r['label']: r for r in dashboard.spot_rows(page, facts['state'], ev)}
    product = rows['製品在庫・週次変化']
    assert product['value'] == .991
    assert (product['direction'], product['symbol'], product['tone']) == ('参考', '—', 'neutral')
    assert '単独の方向票・加点には使わない' in product['rule']
    assert rows['EIA商業原油在庫・週次変化']['direction'] == '上向き'


def test_weekly_inventory_direction_only_marks_the_selected_four_week_row():
    facts = material()
    single = next(f for f in facts['facts'] if f['fact_id'] == facts['state']['inventory']['change_crude'])
    four = dict(single, fact_id='usoil.inventory.test.wti.change_4w.4w.20261016',
                logical_id='usoil.inventory.test.wti.change_4w.4w.20261016', value=-9, display_value=-9,
                display='-9.000 million_bbl')
    facts['facts'].append(four)
    facts['state']['inventory']['change_4w'] = four['fact_id']
    ev = scoring.evaluate(facts, mode='weekly', assessments=[])
    page = dashboard.Page(report.Doc(), {f['fact_id']: f for f in facts['facts']})
    rows = {r['label']: r for r in dashboard.spot_rows(page, facts['state'], ev)}
    assert rows['原油在庫・4週累積変化']['direction'] == '上向き'
    assert rows['EIA商業原油在庫・週次変化']['direction'] == '参考'
    assert rows['製品在庫・週次変化']['direction'] == '参考'
