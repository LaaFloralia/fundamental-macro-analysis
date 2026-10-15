"""Regressions for the r16 independent review: each test fails on the code before its fix."""
from datetime import datetime, timedelta, timezone

import pytest

from btc import dashboard, levels
from btc.acceptance import FIGURE_TEXT
from btc.report import Doc
from scripts.offchart_dashboard import DashboardError, validate

UTC = timezone.utc


def fact(fact_id, value, display, *, status='ok', stale=False, observed_at='2026-10-15T08:00:00Z', metric=''):
    return {'fact_id': fact_id, 'value': value, 'display_value': value, 'display': display, 'status': status,
            'stale': stale, 'observed_at': observed_at, 'observation_date': None, 'metric': metric}


def page(*facts):
    return dashboard.Page(Doc(), {f['fact_id']: f for f in facts})


def test_conclusion_label_and_news_tally_are_checked():
    source = '- 結論｜理由: 金利が下がった\n- ニュース｜a: 見出し'
    row = {'label': '上昇確率99%', 'text': '金利が下がった', 'source_quote': '結論｜理由: 金利が下がった'}
    with pytest.raises(DashboardError, match='label'):
        validate({'conclusion': {'rows': [row]}}, [], source)
    news = {'id': 'news', 'type': 'news_list', 'items': [
        {'group': 'クリプト', 'assessed': True, 'title': '見出し', 'source_quote': 'ニュース｜a: 見出し'}]}
    validate({}, [dict(news, tallies={'クリプト': '評価 1件・見出し 1件'})], source)
    with pytest.raises(DashboardError, match='tally'):
        validate({}, [dict(news, tallies={'クリプト': '評価 9件・見出し 1件'})], source)


def test_a_gap_stops_the_run_of_closes_outside():
    start = datetime(2026, 10, 15, 0, tzinfo=UTC)
    rows = [{'t': start + timedelta(hours=h), 'h': 101, 'l': 99, 'c': 101} for h in (0, 1, 2, 7, 8, 9)]
    h1 = levels._bars(rows, timedelta(hours=1))
    state = levels.level_state(100.0, 'high', start, h1, [])
    assert state['hours_outside'] == 3 and state['state'] == 'beyond'
    full = levels._bars([{'t': start + timedelta(hours=h), 'h': 101, 'l': 99, 'c': 101} for h in range(6)],
                        timedelta(hours=1))
    assert levels.level_state(100.0, 'high', start, full, [])['state'] == 'broken'


def test_stale_funding_has_no_direction_and_shows_its_time():
    f = fact('fw', 0.0005, '0.0500%', status='stale', stale=True, metric='funding_weighted_8h')
    rows = dashboard.crowd_rows(page(f), {}, [f])
    row = next(r for r in rows if r['label'].startswith('Funding'))
    assert row['direction'] == dashboard.NODATA[1] and row['note'] == '古い値（判定外）' and row['asof']
    fresh = fact('fw', 0.0005, '0.0500%', metric='funding_weighted_8h')
    row = next(r for r in dashboard.crowd_rows(page(fresh), {}, [fresh]) if r['label'].startswith('Funding'))
    assert row['note'] == 'ロングが過密' and row['direction'] != dashboard.NODATA[1]


def test_basis_row_uses_the_reference_copy():
    annual = fact('ann', 5.5, '年率 +5.5%（BTC-25DEC26・71日）')
    item = {'instrument': 'BTC-25DEC26', 'annual_fact_id': 'ann', 'basis_fact_id': 'b', 'days': 70.8}
    rows = dashboard.crowd_rows(page(annual), {'basis': {'items': [item], 'reference': dict(item)}}, [])
    assert any(r['label'] == '先物ベーシス' and r['display'] == annual['display'] for r in rows)


def test_manual_release_shows_without_news():
    st = {'incidents': {'recently_released': [{'incident_id': 'inc-1', 'title': 'Exchange halts', 'release_reason': '復旧確認',
                                                'released_by': 'owner_instruction'}]}}
    notes = dashboard.incident_notes(st, {'incidents_open': []})
    assert notes and 'inc-1' in notes[0] and '障害の手動解除（解除から14日間表示）' in notes[0]
    assert dashboard.news_figure(page(), {'state': {'news': {'status': 'missing', 'items': []}}}, {}, {}) is None


def test_event_dates_are_checked_in_the_browser():
    assert 'date' in FIGURE_TEXT['event_table']
