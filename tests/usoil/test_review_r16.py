"""Regressions for the r16 independent review: each test fails on the code before its fix."""
from datetime import datetime, timedelta, timezone

import pytest

from usoil import dashboard, levels
from usoil.acceptance import FIGURE_TEXT
from usoil.report import Doc
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
        {'group': '原油', 'assessed': True, 'title': '見出し', 'source_quote': 'ニュース｜a: 見出し'}]}
    validate({}, [dict(news, tallies={'原油': '評価 1件・見出し 1件'})], source)
    with pytest.raises(DashboardError, match='tally'):
        validate({}, [dict(news, tallies={'原油': '評価 9件・見出し 1件'})], source)


def test_a_gap_stops_the_run_of_closes_outside():
    start = datetime(2026, 10, 15, 0, tzinfo=UTC)
    rows = [{'t': start + timedelta(hours=h), 'h': 101, 'l': 99, 'c': 101} for h in (0, 1, 2, 7, 8, 9)]
    h1 = levels._bars(rows, timedelta(hours=1))
    state = levels.level_state(100.0, 'high', start, h1, [])
    assert state['hours_outside'] == 3 and state['state'] == 'beyond'
    full = levels._bars([{'t': start + timedelta(hours=h), 'h': 101, 'l': 99, 'c': 101} for h in range(6)],
                        timedelta(hours=1))
    assert levels.level_state(100.0, 'high', start, full, [])['state'] == 'broken'


@pytest.mark.parametrize('block,key,label', [('cot','mm_net_ratio','WTI MMネット/建玉'),
                                                ('vol','level','OVX')])
def test_stale_oil_positioning_has_no_direction_and_shows_its_time(block,key,label):
    f = fact('oil', 50, '50.00', status='stale', stale=True)
    st = {block: {key:'oil'}}
    row = next(r for r in dashboard.crowd_rows(page(f), st, [f]) if r['label']==label)
    assert row['direction']==dashboard.NODATA[1] and row['note']=='古い値・採点外' and row['asof']
    fresh = fact('oil',50,'50.00')
    row = next(r for r in dashboard.crowd_rows(page(fresh), st, [fresh]) if r['label']==label)
    assert row['direction']!=dashboard.NODATA[1] and row['note']==''


def test_curve_row_uses_the_referenced_fact_and_keeps_the_display():
    spread = fact('curve', 2.1, '+2.10%')
    st = {'inventory':{}, 'curve':{'spread_pct':'curve'}}
    rows = dashboard.spot_rows(page(spread), st, {'groups':{'inventory_supply':{'state':'supportive'}}})
    assert any(r['display']==spread['display'] and '限月' in r['label'] for r in rows)


def test_manual_release_shows_without_news():
    st = {'incidents': {'recently_released': [{'incident_id': 'inc-1', 'title': 'Oil terminal halts', 'release_reason': '復旧確認',
                                                'released_by': 'owner_instruction'}]}}
    notes = dashboard.incident_notes(st, {'incidents_open': []})
    assert notes and 'inc-1' in notes[0] and '障害の手動解除（解除から14日間表示）' in notes[0]
    assert dashboard.news_figure(page(), {'state': {'news': {'status': 'missing', 'items': []}}}, {}, {}) is None


def test_event_dates_are_checked_in_the_browser():
    assert 'date' in FIGURE_TEXT['event_table']
