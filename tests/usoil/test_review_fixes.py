"""Regression tests for the independent review findings R-01..R-09 (Astra, 2026-10-08). No network.

Each test names the finding and the boundary the review asked for.
"""
import copy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from usoil import analysis as analysis_mod, carry, facts as facts_mod, machine as machine_mod, presentation, scoring
from usoil import workflow
from usoil.common import JST, parse_time, write_json
from usoil.facts import Facts, calendar_events, eia_facts, cot_facts, vol_facts
from usoil.history import History
from usoil.news import reaction, title_key
from usoil.pipeline import UsoilStages
from usoil.sources.calendar import fomc_coverage_end, load_cache
from usoil.stages import AnalysisError
from tests.usoil.test_pipeline import EDITION, claim, context, index, load_collection, valid_analysis
from tests.usoil.test_workflow import full_review

UTC = timezone.utc


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    collection = load_collection()
    ctx = context(tmp_path_factory.mktemp('job'), collection)
    return collection, UsoilStages().build_facts(collection, ctx), ctx


def sources(collection):
    return {s['source_id']: s for s in collection['sources']}


def as_of_of(facts):
    return parse_time(facts['as_of']).astimezone(UTC)


def _edition(collection, tmp_path, name, history_files=None):
    ctx = context(tmp_path / name, collection)
    for file_name, data in (history_files or {}).items():
        ctx.history_dir.mkdir(parents=True, exist_ok=True)
        (ctx.history_dir / file_name).write_bytes(data)
    stages = UsoilStages()
    facts = stages.build_facts(collection, ctx)
    ctx.generated_at = (as_of_of(facts) + timedelta(minutes=10)).astimezone(JST)
    a = valid_analysis(facts)
    ref = facts['state']['reference_price']['fact_id']
    a['thesis'] = claim('TEST 参照価格は{{fact:%s}}。' % ref, [ref], 'fact_interpretation')
    validated = stages.validate_analysis(a, facts, ctx)
    parts = stages.build_report(validated, facts, collection, ctx)
    machine = machine_mod.build(parts.machine_core, parts.machine_extensions, mode='daily', session_slot='pm',
                                collected_at=collection['collection_started_at'],
                                generated_at=ctx.generated_at.isoformat(), as_of=facts['as_of'])
    stages.validate_machine(machine, parts)
    folder = tmp_path / name / 'out'
    folder.mkdir()
    label = parse_time(collection['collection_started_at']).astimezone(JST).strftime('%Y-%m-%d_%H%M')
    md = folder / f'USOIL_Daily_Report_{label}.md'
    md.write_text(parts.markdown)
    summary, figures = write_json(folder / 's.json', parts.summary), write_json(folder / 'f.json', parts.figures)
    html = presentation.render(md, summary_path=summary, visuals_path=figures, as_of=collection['collection_started_at'],
                               kind='daily', session_slot='pm', generated_at=ctx.generated_at.isoformat())['html_path']
    ev = scoring.evaluate(facts, mode='daily', assessments=[])
    return facts, ev, parts.markdown, Path(html).read_text(), machine


def _make_stale(facts, fact_ids):
    f = copy.deepcopy(facts)
    for x in f['facts']:
        if x['fact_id'] in fact_ids:
            x.update(status='stale', stale=True)
    return f


def _calendar(collection, as_of, mutate=None, cache_mutate=None):
    S = copy.deepcopy(sources(collection))
    cache = load_cache()
    if mutate:
        mutate(S)
    if cache_mutate:
        cache_mutate(cache)
    return calendar_events(S, as_of, cache)


def test_r03_fomc_coverage_end_is_what_the_page_lists():
    page = '<h4>2026 FOMC Meetings</h4><p>October</p><p>27-28</p><p>December</p><p>8-9*</p>'
    assert fomc_coverage_end(page, date(2026, 10, 7)) == '2026-12-31'
    page2 = page + '<h4>2027 FOMC Meetings</h4><p>January</p><p>26-27</p>'
    assert fomc_coverage_end(page2, date(2026, 10, 7)) == '2027-02-04'  # capped at the 120-day parse window
    assert fomc_coverage_end('<h4>2025 FOMC Meetings</h4>', date(2026, 10, 7)) == '2026-10-06'  # nothing covered


def _item(news_id, title, url, published, sha, official=True):
    return {'id': news_id, 'event_cluster_id': 'c-' + title_key(title), 'title': title, 'url': url,
            'publisher': 'EIA' if official else 'Media', 'official': official, 'published_at': published.isoformat(),
            'first_seen_at': None,
            'code_verification': 'primary_body_retrieved' if official else 'secondary_body_retrieved',
            'body': {'status': 'retrieved', 'text_sha256': sha, 'excerpt': title}, 'excerpt': title, 'p': None,
            'reaction': None, 'fact_ids': [], 'feed_raw_sha256': None}


def _assess(item, impact='supportive', importance='high', **extra):
    return {'news_id': item['id'], 'event_cluster_id': item['event_cluster_id'], 'group_id': 'oil_specific_event',
            'impact': impact, 'importance': importance, 'verification': 'primary_confirmed',
            'source_quote': item['title'][:20], 'fact_ids': [], 'interpretation': claim('TEST 一次本文で確認。'),
            **extra}


def _edition_facts(facts, history, as_of, items):
    f = copy.deepcopy(facts)
    f['as_of'] = as_of.isoformat()
    f['state']['news']['items'] = items
    facts_mod.carry_state(f['state'], history, as_of)
    return f


def test_r05_known_news_across_editions_and_modes(built, tmp_path):
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    first = _item('n1', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=2), 'a' * 64)
    f1 = _edition_facts(facts, hist, t0, [first])
    a1 = _assess(first)
    ev = scoring.evaluate(f1, mode='daily', assessments=[a1])
    assert ev['groups']['oil_specific_event']['direction'] == 1
    assert carry.record_known(hist, f1, {'news_assessments': [a1]}, edition_id='e1', mode='daily') == 1
    known_id = carry.known_news(hist, t0)[0]['known_id']

    # Next edition (+12 h): a re-report with another URL and an updated headline, linked by the parent.
    rerun = _item('n2', 'Update: EIA emergency petroleum supply release', 'https://www.eia.gov/news/a-update',
                  t0 + timedelta(hours=11), 'b' * 64)
    f2 = _edition_facts(facts, hist, t0 + timedelta(hours=12), [rerun])
    assert [k['known_id'] for k in f2['state']['news']['carried']] == [known_id]
    linked = _assess(rerun, known_event_ids=[known_id])
    g = scoring.evaluate(f2, mode='daily', assessments=[linked])['groups']['oil_specific_event']
    assert g['direction'] == 1 and g['components']['carry_forward'][0]['kind'] == 'carry_forward'

    # +40 h: still inside 36 h of the re-report, but the known event's period has ended -> not counted.
    f3 = _edition_facts(facts, hist, t0 + timedelta(hours=40), [dict(rerun, published_at=(t0 + timedelta(hours=39)).isoformat())])
    assert scoring.evaluate(f3, mode='daily', assessments=[linked])['groups']['oil_specific_event']['direction'] == 0
    # Same primary body under a new URL and headline is matched by the code without a parent link.
    same_body = _item('n3', 'Emergency petroleum release recap', 'https://www.eia.gov/news/b',
                      t0 + timedelta(hours=39), 'a' * 64)
    f3b = _edition_facts(facts, hist, t0 + timedelta(hours=40), [same_body])
    assert scoring.evaluate(f3b, mode='daily', assessments=[_assess(same_body)])['groups']['oil_specific_event']['direction'] == 0
    # A follow-up with new primary facts counts; the same flag on an old body does not.
    follow = _item('n4', 'EIA sets a new date for the petroleum supply release', 'https://www.eia.gov/news/c',
                   t0 + timedelta(hours=39), 'c' * 64)
    f4 = _edition_facts(facts, hist, t0 + timedelta(hours=40), [follow, same_body])
    g = scoring.evaluate(f4, mode='daily', assessments=[
        _assess(follow, known_event_ids=[known_id], follow_up_new_facts=True),
        _assess(same_body, known_event_ids=[known_id], follow_up_new_facts=True)])['groups']['oil_specific_event']
    assert g['direction'] == 1 and g['components']['news_ids'] == ['n4']
    assert g['components']['carry_forward'][0]['kind'] == 'follow_up_new_facts'

    # Across modes: a Daily-adopted event counts in Weekly only inside 7 days of its first appearance.
    fw = _edition_facts(facts, hist, t0 + timedelta(hours=40), [rerun])
    assert scoring.evaluate(fw, mode='weekly', assessments=[linked])['groups']['oil_specific_event']['direction'] == 1
    # +8 days: outside every scoring window, but still retained for matching (R2-02) -> the link holds, no points.
    fw8 = _edition_facts(facts, hist, t0 + timedelta(days=8), [dict(rerun, published_at=(t0 + timedelta(days=7, hours=23)).isoformat())])
    assert [k['known_id'] for k in carry.known_news(hist, t0 + timedelta(days=8))] == [known_id]
    assert [k['known_id'] for k in fw8['state']['news']['carried']] == [known_id]
    assert scoring.evaluate(fw8, mode='weekly', assessments=[linked])['groups']['oil_specific_event']['direction'] == 0
    # Not linked and not matched -> the code cannot tie it; the parent must link (briefing lists known_id).
    assert 'known_event_ids' in analysis_mod.briefing(f2, scoring.evaluate(f2, mode='daily'), edition_id='x',
                                                      mode='daily', session_slot='pm')

    # Legacy unit input: an explicit URL set excludes the item.
    assert scoring.eligible_news(f1, [a1], 'daily', t0, {first['url']}) == []


def test_r2_02_late_re_reports_never_count_again(built, tmp_path, monkeypatch):
    """Astra R2-02: matching retention (180 days) is separate from the scoring window."""
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    first = _item('n1', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=2), 'a' * 64)
    f1 = _edition_facts(facts, hist, t0, [first])
    carry.record_known(hist, f1, {'news_assessments': [_assess(first)]}, edition_id='e1', mode='daily')
    known_id = carry.known_news(hist, t0)[0]['known_id']
    late = t0 + timedelta(days=8)
    for item in (_item('n2', 'Emergency petroleum supply release takes effect', first['url'], late - timedelta(hours=1), 'b' * 64),
                 _item('n3', first['title'], 'https://www.eia.gov/news/z', late - timedelta(hours=1), 'c' * 64),
                 _item('n4', 'Petroleum release now allowed', 'https://www.eia.gov/news/y', late - timedelta(hours=1), 'a' * 64)):
        f = _edition_facts(facts, hist, late, [item])
        for mode in ('daily', 'weekly'):
            g = scoring.evaluate(f, mode=mode, assessments=[_assess(item)])['groups']['oil_specific_event']
            assert g['direction'] == 0, (item['id'], mode)
        assert g['components']['news_ids'] == []
    # An explicit link to an event older than the 14-day briefing table is accepted (and still never counts).
    old = t0 + timedelta(days=20)
    rewrite = _item('n5', 'Agency recap: petroleum release', 'https://example.gov/recap', old - timedelta(hours=1), 'd' * 64)
    f = _edition_facts(facts, hist, old, [rewrite])
    a = valid_analysis(f)
    a['news_assessments'] = [_assess(rewrite, known_event_ids=[known_id])]
    analysis_mod.validate(a, f, edition_id=EDITION)
    assert scoring.evaluate(f, mode='weekly', assessments=a['news_assessments'])['groups']['oil_specific_event']['direction'] == 0
    assert known_id not in analysis_mod.briefing(f, scoring.evaluate(f, mode='daily'), edition_id='x', mode='daily',
                                                 session_slot='pm')
    f8 = _edition_facts(facts, hist, late, [])
    assert known_id in analysis_mod.briefing(f8, scoring.evaluate(f8, mode='daily'), edition_id='x', mode='daily',
                                             session_slot='pm')
    # Retention: 180 days, then at most KNOWN_MAX_RECORDS (oldest dropped).
    monkeypatch.setattr(carry, 'KNOWN_MAX_RECORDS', 3)
    for i in range(4):
        when = t0 + timedelta(days=30, hours=i)
        item = _item(f'm{i}', f'Unrelated official notice {i}', f'https://www.eia.gov/m{i}', when - timedelta(hours=1), str(i) * 64)
        carry.record_known(hist, _edition_facts(facts, hist, when, [item]), {'news_assessments': [_assess(item)]},
                           edition_id=f'e{i}', mode='daily')
    titles = [r['title'] for r in carry.known_news(hist, t0 + timedelta(days=31))]
    assert titles == ['Unrelated official notice 1', 'Unrelated official notice 2', 'Unrelated official notice 3']
    monkeypatch.setattr(carry, 'KNOWN_MAX_RECORDS', 5000)
    far = t0 + timedelta(days=30 + 181)
    item = _item('z', 'Another notice', 'https://www.eia.gov/z', far - timedelta(hours=1), 'e' * 64)
    carry.record_known(hist, _edition_facts(facts, hist, far, [item]), {'news_assessments': [_assess(item)]},
                       edition_id='ez', mode='daily')
    assert [r['title'] for r in carry.known_news(hist, far)] == ['Another notice']


def test_r2_03_adopted_follow_up_counts_from_its_own_publication(built, tmp_path):
    """Astra R2-03: an adopted follow-up is its own known record; it never falls back to the original's time."""
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    first = _item('n1', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=2), 'a' * 64)
    carry.record_known(hist, _edition_facts(facts, hist, t0, [first]), {'news_assessments': [_assess(first)]},
                       edition_id='e1', mode='daily')
    known_id = carry.known_news(hist, t0)[0]['known_id']
    t1 = t0 + timedelta(hours=41)
    follow = _item('n4', 'EIA sets a new date for the petroleum supply release', 'https://www.eia.gov/news/c',
                   t1 - timedelta(hours=1), 'c' * 64)
    a4 = _assess(follow, known_event_ids=[known_id], follow_up_new_facts=True)
    f1 = _edition_facts(facts, hist, t1, [follow])
    g = scoring.evaluate(f1, mode='daily', assessments=[a4])['groups']['oil_specific_event']
    assert g['direction'] == 1 and g['components']['carry_forward'][0]['kind'] == 'follow_up_new_facts'
    assert carry.record_known(hist, f1, {'news_assessments': [a4]}, edition_id='e2', mode='daily') == 1
    records = carry.known_news(hist, t1)
    original, own = records
    assert own['follow_up_of'] == known_id and own['known_id'] != known_id
    assert own['first_known_at'] == (t1 - timedelta(hours=1)).isoformat() and own['body_hashes'] == ['c' * 64]
    assert original['body_hashes'] == ['a' * 64]  # not folded into the original
    # +2 h: the same follow-up (URL match) is carried from its own first_known_at, not the original's (43 h old).
    for assessment in (a4, _assess(follow), _assess(follow, known_event_ids=[known_id])):
        f2 = _edition_facts(facts, hist, t1 + timedelta(hours=2), [follow])
        g = scoring.evaluate(f2, mode='daily', assessments=[assessment])['groups']['oil_specific_event']
        assert g['direction'] == 1, assessment
        assert g['components']['carry_forward'][0] == {'news_id': 'n4', 'known_id': own['known_id'], 'kind': 'carry_forward',
                                                       'first_known_at': own['first_known_at']}
    # ... until its own first_known_at + the mode window.
    f3 = _edition_facts(facts, hist, t1 + timedelta(hours=36), [follow])
    assert scoring.evaluate(f3, mode='daily', assessments=[a4])['groups']['oil_specific_event']['direction'] == 0
    # Re-adopting it merges into the follow-up record, not the original.
    assert carry.record_known(hist, f1, {'news_assessments': [a4]}, edition_id='e3', mode='daily') == 0
    assert len(carry.known_news(hist, t1)) == 2


def test_r05_report_build_uses_carried_state(built, tmp_path):
    collection, facts, ctx = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    first = _item('n1', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=40), 'a' * 64)
    f0 = _edition_facts(facts, hist, t0 - timedelta(hours=39), [first])
    carry.record_known(hist, f0, {'news_assessments': [_assess(first)]}, edition_id='e0', mode='weekly')
    rerun = _item('n2', 'Emergency petroleum supply release approved', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=1), 'b' * 64)  # same URL, new headline
    f = _edition_facts(facts, hist, t0, [rerun])
    a = valid_analysis(f)
    a['news_assessments'] = [_assess(rerun)]
    stages = UsoilStages()
    c = copy.copy(ctx)
    c.generated_at = (t0 + timedelta(minutes=5)).astimezone(JST)
    parts = stages.build_report(stages.validate_analysis(a, f, c), f, collection, c)
    group = parts.machine_extensions['usoil']['groups']['oil_specific_event']
    assert group['direction'] == 0 and group['components']['carry_forward'] == []
    assert group['components']['news_ids'] == []


def test_r05_analysis_validates_known_links(built, tmp_path):
    _, facts, _ = built
    t0 = as_of_of(facts)
    item = _item('n9', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a', t0, 'a' * 64)
    f = _edition_facts(facts, tmp_path / 'h', t0, [item])
    a = valid_analysis(f)
    a['news_assessments'] = [_assess(item, known_event_ids=['k-000000000000'], follow_up_new_facts=True)]
    with pytest.raises(AnalysisError) as caught:
        analysis_mod.validate(a, f, edition_id=EDITION)
    assert any('unknown_known_event:k-000000000000' in p for p in caught.value.problems)
    a['news_assessments'] = [_assess(item, follow_up_new_facts=True)]
    with pytest.raises(AnalysisError) as caught:
        analysis_mod.validate(a, f, edition_id=EDITION)
    assert any('follow_up_requires_known_event_ids' in p for p in caught.value.problems)


def _fresh_fact(facts, source_id):
    """A fresh but pre-notice observation cannot prove recovery."""
    item = copy.deepcopy(next(x for x in facts['facts'] if x['source_id'] == source_id and scoring.scorable(x)))
    item['fact_id'] += '.oldproof'
    item['observed_at'] = (as_of_of(facts) - timedelta(hours=2)).isoformat()
    facts['facts'].append(item)
    return item['fact_id']


def _oi_fact(facts, source_id):
    """Legacy helper name retained for cross-module imports; returns a fresh timed oil observation."""
    return next(x['fact_id'] for x in facts['facts']
                if x['source_id'] == source_id and x.get('observed_at') and scoring.scorable(x))


def _fact_id(facts, part):
    return next(x['fact_id'] for x in facts['facts'] if part in x['fact_id'])


def test_r04_incident_persists_until_official_recovery_and_fresh_data(built, tmp_path):
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    hack = _item('n1', 'Oil terminal halts operations after a safety incident', 'https://status.example.org/i1',
                 t0 - timedelta(hours=1), 'd' * 64)
    f1 = _edition_facts(facts, hist, t0, [hack])
    a1 = _assess(hack, impact='adverse', importance='critical', affected_source_ids=['cnbc_quotes'])
    ev1 = scoring.evaluate(f1, mode='daily', assessments=[a1])
    assert ev1['trade_gate']['status'] == 'incident_hold' and len(ev1['incidents_new']) == 1
    incident_id = ev1['incidents_new'][0]['incident_id']
    assert carry.record_incidents(hist, f1, {'news_assessments': [a1]}, ev1, edition_id='e1')['opened'] == [incident_id]

    # Next edition: the article dropped out of selection -> still on hold (inherited, parent sees it).
    f2 = _edition_facts(facts, hist, t0 + timedelta(hours=6), [])
    assert [i['incident_id'] for i in f2['state']['incidents']['open']] == [incident_id]
    ev2 = scoring.evaluate(f2, mode='daily', assessments=[])
    assert ev2['trade_gate']['status'] == 'incident_hold' and ev2['trade_gate']['reason_codes'] == ['incident_unresolved']
    assert scoring.evaluate(f2, mode='daily')['trade_gate']['status'] == 'incident_hold'  # preview too
    briefing = analysis_mod.briefing(f2, scoring.evaluate(f2, mode='daily'), edition_id='x', mode='daily', session_slot='pm')
    assert incident_id in briefing

    # Restart: a brand-new state read only from the job history still holds.
    state = {'news': {'items': []}}
    facts_mod.carry_state(state, hist, t0 + timedelta(hours=7))
    assert [i['incident_id'] for i in state['incidents']['open']] == [incident_id]

    # Release attempts.
    media = _item('n5', 'Operator says terminal operations resumed', 'https://media.example.com/r', t0 - timedelta(minutes=5),
                  'e' * 64, official=False)
    stale_official = _item('n6', 'Oil terminal resumes operations', 'https://status.example.org/r0',
                           t0 - timedelta(hours=2), 'f' * 64)
    official = _item('n7', 'Oil terminal resumes operations', 'https://status.example.org/r1',
                     t0 - timedelta(minutes=5), '1' * 64)
    f3 = _edition_facts(facts, hist, t0, [media, stale_official, official])
    price_proof = _oi_fact(f3, 'cnbc_quotes')  # source-observed quote after the recovery notice
    other_proof = _oi_fact(f3, 'cboe_ovx')
    stale = copy.deepcopy(f3)
    next(x for x in stale['facts'] if x['fact_id'] == price_proof).update(status='stale', stale=True)
    cases = [
        ([], f3, None),
        ([{'incident_id': incident_id, 'news_id': 'n5', 'fact_ids': [price_proof]}], f3, 'recovery_not_official_body'),
        ([{'incident_id': incident_id, 'news_id': 'n6', 'fact_ids': [price_proof]}], f3, 'recovery_not_after_incident'),
        ([{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [other_proof]}], f3,
         'recovery_affected_source_not_fresh:cnbc_quotes'),
        ([{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [price_proof]}], stale, 'recovery_data_not_fresh'),
    ]
    for recoveries, f, problem in cases:
        ev = scoring.evaluate(f, mode='daily', assessments=[], recoveries=recoveries)
        assert ev['trade_gate']['status'] == 'incident_hold', recoveries
        if problem:
            assert any(problem in p for p in ev['incidents_rejected'][0]['problems']), ev['incidents_rejected']
    good = [{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [price_proof]}]
    ev = scoring.evaluate(f3, mode='daily', assessments=[], recoveries=good)
    assert ev['trade_gate']['status'] != 'incident_hold' and [r['incident_id'] for r in ev['incidents_released']] == [incident_id]
    assert carry.record_incidents(hist, f3, {'news_assessments': []}, ev, edition_id='e3')['released'] == [incident_id]
    f4 = _edition_facts(facts, hist, t0 + timedelta(hours=12), [])
    assert f4['state']['incidents']['open'] == []
    assert scoring.evaluate(f4, mode='daily', assessments=[])['trade_gate']['status'] != 'incident_hold'


def test_r2_01_incident_without_affected_sources_is_not_released_by_the_parent(built, tmp_path, monkeypatch):
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    hack = _item('n1', 'Oil terminal halts operations after a safety incident', 'https://status.example.org/i1',
                 t0 - timedelta(hours=1), 'd' * 64)
    f1 = _edition_facts(facts, hist, t0, [hack])
    ev1 = scoring.evaluate(f1, mode='daily', assessments=[_assess(hack, impact='adverse', importance='critical')])
    incident_id = ev1['incidents_new'][0]['incident_id']
    carry.record_incidents(hist, f1, {}, ev1, edition_id='e1')
    official = _item('n7', 'Oil terminal resumes operations', 'https://status.example.org/r1',
                     t0 - timedelta(minutes=5), '1' * 64)
    f = _edition_facts(facts, hist, t0, [official])
    reference = f['state']['reference_price']['fact_id']
    ev = scoring.evaluate(f, mode='daily', assessments=[], recoveries=[
        {'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [reference, _oi_fact(f, 'cnbc_quotes')]}])
    assert ev['trade_gate']['status'] == 'incident_hold'
    assert 'recovery_affected_sources_unspecified' in ev['incidents_rejected'][0]['problems']
    briefing = analysis_mod.briefing(f, scoring.evaluate(f, mode='daily'), edition_id='x', mode='daily', session_slot='pm')
    assert '親では解除できない' in briefing and '参照価格の新鮮さ' not in briefing
    # Only the owner's manual release clears it.
    _fixed_clock(monkeypatch, t0 + timedelta(minutes=1))
    assert carry.main(['release', '--root', str(tmp_path), '--incident', incident_id, '--reason', '社長指示']) == 0
    f4 = _edition_facts(facts, hist, t0 + timedelta(hours=12), [])
    assert scoring.evaluate(f4, mode='daily', assessments=[])['trade_gate']['status'] != 'incident_hold'


def test_r04_analysis_validates_recovery_references(built, tmp_path):
    _, facts, _ = built
    hist = tmp_path / 'history'
    t0 = as_of_of(facts)
    hack = _item('n1', 'Oil terminal halts operations after a safety incident', 'https://status.example.org/i1',
                 t0 - timedelta(hours=1), 'd' * 64)
    f1 = _edition_facts(facts, hist, t0, [hack])
    a1 = _assess(hack, impact='adverse', importance='critical', affected_source_ids=['cnbc_quotes'])
    carry.record_incidents(hist, f1, {}, scoring.evaluate(f1, mode='daily', assessments=[a1]), edition_id='e1')
    incident_id = carry.open_incidents(hist)[0]['incident_id']
    official = _item('n7', 'Oil terminal resumes operations', 'https://status.example.org/r1', t0 - timedelta(minutes=5),
                     '1' * 64)
    f = _edition_facts(facts, hist, t0, [official])
    a = valid_analysis(f)
    for recoveries, problem in (
            ([{'incident_id': 'inc-unknown', 'news_id': 'n7', 'fact_ids': ['x']}], 'unknown_open_incident:inc-unknown'),
            ([{'incident_id': incident_id, 'news_id': 'n404', 'fact_ids': ['x']}], 'unknown_news:n404'),
            ([{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': ['usoil.nope']}], 'unknown_fact:usoil.nope'),
            ([{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [_oi_fact(f, 'cboe_ovx')]}],
             'recovery_affected_source_not_fresh'),
            ([{'incident_id': incident_id, 'news_id': 'n7', 'fact_ids': [_fresh_fact(f, 'cnbc_quotes')]}],
             'recovery_data_not_observed_after_notice')):
        a['incident_recoveries'] = recoveries
        with pytest.raises(AnalysisError) as caught:
            analysis_mod.validate(a, f, edition_id=EDITION)
        assert any(problem in p for p in caught.value.problems), caught.value.problems
    a['incident_recoveries'] = [{'incident_id': incident_id, 'news_id': 'n7',
                                 'fact_ids': [_oi_fact(f, 'cnbc_quotes')]}]
    analysis_mod.validate(a, f, edition_id=EDITION)
    a['news_assessments'] = [_assess(official, affected_source_ids=['cnbc_quotes'])]
    with pytest.raises(AnalysisError) as caught:
        analysis_mod.validate(a, f, edition_id=EDITION)
    assert any('affected_sources_only_for_critical_incident' in p for p in caught.value.problems)


def test_r04_r05_acceptance_writes_job_history(built, tmp_path):
    """workflow.carry_forward runs on acceptance and writes both files in the job's history."""
    _, facts, _ = built
    job = workflow.Job(root=tmp_path / 'job', python=Path('/usr/bin/python3'))
    t0 = as_of_of(facts)
    hack = _item('n1', 'Oil terminal halts operations after a safety incident', 'https://status.example.org/i1',
                 t0 - timedelta(hours=1), 'd' * 64)
    f = _edition_facts(facts, job.history, t0, [hack])
    folder = job.reports / 'daily' / 'editions' / 'parent-daily-20261007T212342-0000beef'
    folder.mkdir(parents=True)
    a = {'news_assessments': [_assess(hack, impact='adverse', importance='critical')]}
    record = {'outputs': {'facts_path': str(write_json(folder / 'x.facts.json', f)),
                          'analysis_path': str(write_json(folder / 'x.analysis.json', a)),
                          'md_path': str(folder / 'x.md')}}
    out = workflow.carry_forward(job, record, 'daily')
    assert out['known_news_added'] == 1 and len(out['incidents_opened']) == 1
    assert out['incidents_open_after'] == out['incidents_opened']
    assert (job.history / carry.INCIDENT_FILE).is_file() and (job.history / carry.KNOWN_FILE).is_file()
    assert json.loads((job.history / carry.INCIDENT_FILE).read_text())['records'][0]['recorded_edition_id'] == folder.name


# ------------------------------------------------------------------ follow-up A: lagged fallback (design 5, 7.3)


# ------------------------------------------------------------------ follow-up B: owner-only manual release

def _fixed_clock(monkeypatch, when):
    """Pin the CLI's release time (``released_at``) so later editions are evaluated after it (R4-01)."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when
    monkeypatch.setattr(carry, 'datetime', Clock)


def _open_incident(built, hist):
    _, facts, _ = built
    t0 = as_of_of(facts)
    hack = _item('n1', 'Oil terminal halts operations after a safety incident', 'https://status.example.org/i1',
                 t0 - timedelta(hours=1), 'd' * 64)
    f = _edition_facts(facts, hist, t0, [hack])
    ev = scoring.evaluate(f, mode='daily', assessments=[_assess(hack, impact='adverse', importance='critical')])
    carry.record_incidents(hist, f, {}, ev, edition_id='e1')
    return facts, ev['incidents_new'][0]['incident_id']


def test_manual_release_cli(built, tmp_path, capsys, monkeypatch):
    root = tmp_path / 'job'
    hist = root / 'history'
    facts, incident_id = _open_incident(built, hist)
    _fixed_clock(monkeypatch, as_of_of(facts) + timedelta(minutes=1))
    assert carry.main(['release', '--root', str(root), '--incident', 'inc-nope', '--reason', 'x']) == 2
    assert 'incident_unknown' in capsys.readouterr().out
    assert carry.main(['release', '--root', str(root), '--incident', incident_id, '--reason', '  ']) == 2
    assert 'reason_invalid' in capsys.readouterr().out
    assert carry.main(['release', '--root', str(tmp_path / 'nojob'), '--incident', incident_id, '--reason', 'x']) == 2
    assert 'history_missing' in capsys.readouterr().out
    assert carry.main(['release', '--root', str(root), '--incident', incident_id,
                       '--reason', '社長指示: 原油供給設備の公式復旧を電話で確認']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'released_manually' and out['incident_id'] == incident_id
    records = json.loads((hist / carry.INCIDENT_FILE).read_text())['records']
    assert len(records) == 1  # never deleted
    assert records[0]['status'] == 'released_manually' and records[0]['released_by'] == 'owner_instruction'
    assert records[0]['release_reason'] == '社長指示: 原油供給設備の公式復旧を電話で確認' and records[0]['released_at']
    assert carry.open_incidents(hist) == []
    f = _edition_facts(facts, hist, as_of_of(facts) + timedelta(hours=6), [])
    assert scoring.evaluate(f, mode='daily', assessments=[])['trade_gate']['status'] != 'incident_hold'
    # Not open any more: refused, file unchanged.
    before = (hist / carry.INCIDENT_FILE).read_bytes()
    assert carry.main(['release', '--root', str(root), '--incident', incident_id, '--reason', 'again']) == 2
    assert 'incident_not_open:released_manually' in capsys.readouterr().out
    assert (hist / carry.INCIDENT_FILE).read_bytes() == before


def test_manual_release_is_not_in_the_parent_workflow():
    workflow_text = (Path(__file__).parents[2] / 'usoil' / 'job_template' / 'PARENT-WORKFLOW.md').read_text()
    assert 'usoil.carry release' not in workflow_text and 'released_manually' not in workflow_text
    readme = (Path(__file__).parents[2] / 'docs' / 'usoil' / 'README.md').read_text()
    assert 'owner指定の手動解除は履歴・理由を保持' in readme and '親が独断で実行しません' in readme


# ------------------------------------------------------------------ R2-04 strict carried-state validation

def _carry_files(built, tmp_path):
    """A valid known-news file and a valid incidents file written by the code."""
    _, facts, _ = built
    hist = tmp_path / 'seed'
    t0 = as_of_of(facts)
    first = _item('n1', 'EIA confirms an emergency petroleum supply release', 'https://www.eia.gov/news/a',
                  t0 - timedelta(hours=30), 'a' * 64)
    f = _edition_facts(facts, hist, t0 - timedelta(hours=29), [first])
    carry.record_known(hist, f, {'news_assessments': [_assess(first)]}, edition_id='e0', mode='daily')
    _open_incident(built, hist)
    return {name: (hist / name).read_bytes() for name in (carry.KNOWN_FILE, carry.INCIDENT_FILE)}


def _corrupt(files, which, how):
    data = json.loads(files[which])
    if how == 'unknown_status':
        data['records'][0]['status'] = 'closed'
    elif how == 'missing_key':
        del data['records'][0]['first_known_at' if which == carry.KNOWN_FILE else 'published_at']
    elif how == 'schema_version':
        data['schema_version'] = 2
    out = dict(files)
    out[which] = b'{"schema_version": 1, "records": [' if how == 'broken_json' else json.dumps(data).encode()
    return out


@pytest.mark.parametrize('which,how,code', [
    (carry.INCIDENT_FILE, 'unknown_status', 'incidents_record_invalid'),
    (carry.INCIDENT_FILE, 'missing_key', 'incidents_record_invalid'),
    (carry.KNOWN_FILE, 'missing_key', 'known_news_record_invalid'),
    (carry.INCIDENT_FILE, 'schema_version', 'incidents_schema_version_unknown'),
    (carry.KNOWN_FILE, 'schema_version', 'known_news_schema_version_unknown'),
    (carry.INCIDENT_FILE, 'broken_json', 'incidents_json_invalid'),
    (carry.KNOWN_FILE, 'broken_json', 'known_news_json_invalid'),
])
def test_r2_04_invalid_carried_state_is_data_hold_and_never_overwritten(built, tmp_path, capsys, which, how, code):
    collection, _, _ = built
    files = _corrupt(_carry_files(built, tmp_path), which, how)
    facts, ev, md, html, machine = _edition(collection, tmp_path, 'bad', history_files=files)
    hist = tmp_path / 'bad' / 'history'
    assert facts['state']['carry'] == {'status': 'invalid', 'error': code}
    assert facts['state']['news']['carried'] == []
    assert facts['state']['incidents'] == {'open': [], 'released_ids': [], 'recently_released': []}
    assert 'carry_state_invalid' in ev['hard_invalid'] and ev['trade_gate']['status'] == 'data_hold'
    assert machine['no_trade'] is True
    for text in (md, html):
        assert '採用済みニュース・未解決障害の記録が読めない' in text
    # Acceptance skips every carry write; the CLI refuses; the bytes never change.
    job = workflow.Job(root=tmp_path / 'bad', python=Path('/usr/bin/python3'))
    folder = job.reports / 'daily' / 'editions' / 'parent-daily-20261007T212342-0000beef'
    folder.mkdir(parents=True)
    item = _item('n2', 'Oil terminal halts operations', 'https://status.example.org/i2', as_of_of(facts), 'f' * 64)
    f = copy.deepcopy(facts)
    f['state']['news']['items'] = [item]
    a = {'news_assessments': [_assess(item, impact='adverse', importance='critical')]}
    record = {'outputs': {'facts_path': str(write_json(folder / 'x.facts.json', f)),
                          'analysis_path': str(write_json(folder / 'x.analysis.json', a)), 'md_path': str(folder / 'x.md')}}
    assert workflow.carry_forward(job, record, 'daily') == {'skipped': 'carry_state_invalid', 'error': code}
    f['state']['carry'] = {'status': 'ok'}  # even if the facts were built before the file broke
    write_json(folder / 'x.facts.json', f)
    assert workflow.carry_forward(job, record, 'daily') == {'skipped': 'carry_state_invalid', 'error': code}
    assert carry.main(['list', '--root', str(job.root)]) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'invalid', 'error': code}
    assert carry.main(['release', '--root', str(job.root), '--incident', 'inc-x', '--reason', 'owner']) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'invalid', 'error': code}
    for name, data in files.items():
        assert (hist / name).read_bytes() == data
    assert not [x for x in hist.iterdir() if x.name.endswith('.tmp')]


def test_r2_04_failed_replace_leaves_no_temporary_file(tmp_path, monkeypatch):
    hist = tmp_path / 'history'
    hist.mkdir()

    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(carry.os, 'replace', fail)
    with pytest.raises(OSError):
        carry._write(hist / carry.INCIDENT_FILE, [])
    assert list(hist.iterdir()) == []


def test_carry_list_cli_is_read_only(built, tmp_path, capsys):
    root = tmp_path / 'job'
    hist = root / 'history'
    facts, incident_id = _open_incident(built, hist)
    before = (hist / carry.INCIDENT_FILE).read_bytes()
    assert carry.main(['list', '--root', str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'ok' and [i['incident_id'] for i in out['open_incidents']] == [incident_id]
    assert set(out['open_incidents'][0]) == {'incident_id', 'published_at', 'title', 'affected_source_ids',
                                             'recorded_edition_id', 'recorded_as_of'}
    assert (hist / carry.INCIDENT_FILE).read_bytes() == before
    readme = (Path(__file__).parents[2] / 'docs' / 'usoil' / 'README.md').read_text()
    workflow_text = (Path(__file__).parents[2] / 'usoil' / 'job_template' / 'PARENT-WORKFLOW.md').read_text()
    assert '壊れた履歴は空へ置き換えず' in readme and 'usoil.carry list' not in workflow_text


# ------------------------------------------------------------------ R2-05 partial calendar parses

FOMC_MIXED = ('<h4>2026 FOMC Meetings</h4>'
              '<p>March</p><p>17\u201318</p>'      # broken but outside the range: ignored
              '<p>September</p><p>15-16</p>'
              '<p>October</p><p>27-28</p>'
              '<p>December</p><p>8\u20139*</p>')  # en dash: an in-range candidate that does not parse
BEA_MIXED = ('<table>'
             '<tr><td>October 29 8:30 AM</td><td>News</td><td>GDP (Advance Estimate), 3rd Quarter 2026</td></tr>'
             '<tr><td>Oct 30 8:30</td><td>News</td><td>Personal Income and Outlays, September 2026</td></tr>'
             '<tr><td>To Be Announced</td><td>News</td><td>GDP (Second Estimate), 3rd Quarter 2026</td></tr>'
             '<tr><td>sometime</td><td>News</td><td>U.S. International Trade in Goods and Services</td></tr>'
             '<tr><td>November 5 8:30 AM</td><td>News</td><td>U.S. International Trade in Goods and Services</td></tr>'
             '</table>')


def _calendar_fetcher(body):
    from usoil.fetch import Deadline, Fetcher
    from tests.usoil.test_fetch import FakeResponse, FakeSession
    return Fetcher(Deadline(60), session=FakeSession([FakeResponse(200, body.encode())]), sleep=lambda s: None)


def test_r2_05_fomc_partial_parse_is_partial():
    from usoil.sources import calendar as cal
    today = date(2026, 10, 7)
    events, stats = cal.parse_fomc_detail(FOMC_MIXED, today)
    assert stats['candidates_in_range'] == 2 and stats['parsed_in_range'] == 1
    assert stats['unparsed'] == ['2026 December 8\u20139*']
    assert sorted({e['start_at'][:10] for e in events}) == ['2026-10-28']
    result = cal.fed_calendar(_calendar_fetcher(FOMC_MIXED), today)
    assert result.status == 'partial' and result.values['parse']['unparsed'] == stats['unparsed']
    clean = FOMC_MIXED.replace('8\u20139*', '8-9*')
    assert cal.fed_calendar(_calendar_fetcher(clean), today).status == 'ok'
    # Coverage comes only from parsed structure: a year whose meetings all fail never extends coverage.
    _, broken = cal.parse_fomc_detail('<h4>2026 FOMC Meetings</h4><p>October</p><p>27\u201328</p>', today)
    assert broken['coverage_end'] == '2026-10-06' and broken['unparsed']


def test_r2_05_bea_partial_parse_is_partial():
    from usoil.sources import calendar as cal
    today = date(2026, 10, 7)
    events, stats = cal.parse_bea_detail(BEA_MIXED, today)
    assert stats['candidates'] == 3 and stats['parsed'] == 1 and stats['to_be_announced'] == 1
    assert stats['unparsed'] == ['Oct 30 8:30 | Personal Income and Outlays, September 2026']
    assert [e['id'] for e in events] == ['bea.gdp.2026-10-29']
    assert stats['coverage_end'] == '2026-11-05'  # latest parsed row of any title
    result = cal.bea_calendar(_calendar_fetcher(BEA_MIXED), today)
    assert result.status == 'partial'
    clean = BEA_MIXED.replace('Oct 30 8:30', 'October 30 8:30 AM')
    result = cal.bea_calendar(_calendar_fetcher(clean), today)
    assert result.status == 'ok' and result.values['parse']['unparsed'] == []


# ------------------------------------------------------------------ acceptance path with the production schema

def test_acceptance_writes_carry_with_a_production_schema_analysis(tmp_path, real_browser):
    """prepare -> parent analysis (schema-valid, not a stub) -> render -> accept: carry files are written strictly."""
    collection = load_collection()
    news = next(s for s in collection['sources'] if s['source_id'] == 'news')
    official = next(k for k in news['values']['kept'] if k['body'].get('status') == 'retrieved')
    official.update(official=True, publisher='EIA', code_verification='primary_body_retrieved',
                    url='https://www.eia.gov/synthetic/incident-01')
    started = parse_time(collection['collection_started_at']).astimezone(JST)
    job = workflow.Job(root=tmp_path / 'job', python=Path('/usr/bin/python3'))

    def collect(job_, ctx, output):
        write_json(output, collection)
    assert workflow.prepare(job, 'daily', UsoilStages(), collect, clock=lambda: started + timedelta(minutes=5),
                            started=started) == 0
    request_path = Path(json.loads((job.root / 'latest-daily.json').read_text())['request_path'])
    request = json.loads(request_path.read_text())
    facts = json.loads(Path(request['facts_path']).read_text())
    item = next(n for n in facts['state']['news']['items'] if n['id'] == official['id'])
    assert item['code_verification'] == 'primary_body_retrieved' and facts['state']['carry'] == {'status': 'ok'}
    a = valid_analysis(facts, edition_id=request_path.parent.name)
    a['news_assessments'] = [{'news_id': item['id'], 'event_cluster_id': item['event_cluster_id'],
                              'group_id': 'oil_specific_event', 'impact': 'adverse', 'importance': 'critical',
                              'verification': 'primary_confirmed', 'source_quote': item['title'][:20], 'fact_ids': [],
                              'interpretation': claim('TEST 一次本文で確認した障害。'),
                              'affected_source_ids': ['cnbc_quotes']}]
    analysis_mod.validate(a, facts, edition_id=request_path.parent.name)  # the production schema, not a stub
    analysis_path = write_json(request_path.parent / 'analysis.json', a)
    package = write_json(request_path.parent / 'package.json',
                         {'request_path': str(request_path), 'analysis_path': str(analysis_path)})
    now = started + timedelta(minutes=12)
    assert workflow.render_package(job, package, 'daily', UsoilStages(), now=now) == 0
    edition_path = Path(json.loads((job.root / 'latest-daily.json').read_text())['edition_path'])
    machine = json.loads(Path(json.loads(edition_path.read_text())['outputs']['json_path']).read_text())
    assert machine['no_trade'] is True
    review_path = write_json(job.work / 'review.json', full_review(edition_path))
    assert workflow.accept_review(job, review_path, 'daily', now=now) == 0
    latest = json.loads((job.root / 'latest-daily.json').read_text())
    incidents = carry.open_incidents(job.history)
    assert len(incidents) == 1 and incidents[0]['affected_source_ids'] == ['cnbc_quotes']
    assert latest['carry'] == {'known_news_added': 1, 'incidents_opened': [incidents[0]['incident_id']],
                               'incidents_released': [], 'incidents_open_after': [incidents[0]['incident_id']]}
    carry.check(job.history)
    known = carry.known_news(job.history, parse_time(facts['as_of']))
    assert [k['ids'][0] for k in known] == ['url:https://www.eia.gov/synthetic/incident-01']
    assert known[0]['follow_up_of'] is None


# Oil replacements for crypto-only R01/R02/R06/R08/R09 populations.

def test_inventory_complete_missing_and_invalid_facts_preserve_the_safety_boundary(tmp_path):
    base=load_collection()
    full=_edition(base,tmp_path,'inventory-full')
    missing=copy.deepcopy(base)
    rec=sources(missing)['eia_wpsr']
    rec.update(status='unavailable',error_kind='connection',values={})
    partial=_edition(missing,tmp_path,'inventory-missing')
    assert full[1]['bundles']['inventory']
    f,ev,md,html,machine=partial
    assert not ev['bundles']['inventory'] and ev['coverage_present']==full[1]['coverage_present']-1
    assert scoring.inventory_component(f)['state']=='unknown'
    assert machine['usoil']['coverage']['present']==ev['coverage_present']
    assert 'inventory' in machine['usoil']['coverage']['missing']
    assert 'EIA' in md and 'EIA' in html
    invalid=copy.deepcopy(full[0])
    index(invalid)[invalid['state']['inventory']['change_crude']].update(status='invalid')
    bad=scoring.evaluate(invalid,mode='daily')
    assert 'invalid_fact' in bad['hard_invalid'] and bad['trade_gate']['status']=='data_hold'
    assert bad['confidence']==0 and bad['no_trade'] is True


@pytest.mark.parametrize('block,key', [('inventory','change_crude'),('curve','spread_pct'),
                                       ('cot','mm_net_ratio_percentile'),('vol','level'),
                                       ('reference_price','fact_id')])
def test_stale_oil_factors_keep_the_value_but_never_count_as_a_bundle(built,block,key):
    _,facts,_=built
    fid=facts['state'][block][key]
    old=_make_stale(facts,{fid})
    bundle={'reference_price':'price'}.get(block,block)
    assert index(old)[fid]['value']==index(facts)[fid]['value']
    assert scoring.bundles(facts)[bundle] and not scoring.bundles(old)[bundle]
    if block=='inventory':assert scoring.inventory_component(old)['state']=='unknown'
    if block=='curve':assert scoring.curve_component(old)['state']=='unknown'
    ev=scoring.evaluate(old,mode='daily')
    if block in ('cot','vol'):
        item=next(x for x in ev['items'] if x['key']=={'cot':'cot_crowding','vol':'vol_tail_risk'}[block])
        assert item['status']=='unassessed'


def test_ovx_quote_becomes_display_only_after_fifteen_minutes(built):
    collection,facts,_=built
    at=as_of_of(facts)+timedelta(minutes=16)
    F,st=Facts(at,'a'*64),{}
    vol_facts(F,sources(collection),st)
    f=F.index[st['vol']['level']]
    assert f['status']=='stale' and f['value'] is not None and not scoring.scorable(f)
    assert not scoring.bundles({'facts':F.items,'state':st})['vol']


def test_ff_calendar_future_stale_partial_and_range_edges_fail_closed(built):
    collection,facts,_=built
    at=as_of_of(facts)
    _,normal=_calendar(collection,at)
    assert normal['next_24h']=='ok' and normal['nextweek']=='unavailable_404'
    for mutate in (lambda S:S['forexfactory_week'].update(status='partial'),
                   lambda S:S['forexfactory_week'].update(retrieved_at=(at+timedelta(seconds=1)).isoformat()),
                   lambda S:S['forexfactory_week'].update(retrieved_at=(at-timedelta(days=2)).isoformat()),
                   lambda S:S.pop('forexfactory_week')):
        _,cal=_calendar(collection,at,mutate=mutate)
        assert cal['next_24h']=='unknown' and cal['reasons']
    end=parse_time(normal['coverage_end'])
    # The calendar must prove the entire next 24 hours, with its boundary fail-closed.
    S=copy.deepcopy(sources(collection))
    S['forexfactory_week']['retrieved_at']=(end-timedelta(hours=24)).isoformat()
    _,cal=calendar_events(S,end-timedelta(hours=24),{})
    assert cal['next_24h']=='unknown'


def _inventory_history_row(day,value,available='2026-10-10T15:00:00Z'):
    return {'source':'eia','instrument':'wpsr','metric':'crude_change_1w','observed_at':day+'T00:00:00Z',
            'observation_date':day,'value':value,'available_at':available,'retrieved_at':available,'raw_sha256':'f'*64}


@pytest.mark.parametrize('omit,future', [(None,False),('2026-10-02',False),(None,True)])
def test_eia_four_weeks_require_every_week_and_only_history_available_as_of(built,tmp_path,omit,future):
    collection,facts,_=built;at=as_of_of(facts)
    h=History(tmp_path/'history')
    rows=[_inventory_history_row(day,-1,'2026-10-16T15:00:00Z' if future and day=='2026-10-02' else '2026-10-10T15:00:00Z')
          for day in ['2026-09-18','2026-09-25','2026-10-02'] if day!=omit]
    # An older observation cannot stand in for a missing week.
    rows.append(_inventory_history_row('2026-09-11',-99))
    h.append(rows,recorded_at='2026-10-10T15:00:00Z')
    F,st=Facts(at,'a'*64),{}
    eia_facts(F,sources(collection),st,h,at)
    four=F.index[st['inventory']['change_4w']]
    if omit or future:
        assert four['value'] is None and not scoring.scorable(four)
    else:
        assert four['value']==-8 and len(four['source_fact_ids'])==4
        assert four['coverage']['observed_count']==4


def test_eia_append_retains_distinct_weeks_and_conflicts_never_rewrite_history(tmp_path):
    h=History(tmp_path/'history')
    rows=[_inventory_history_row('2026-10-02',-1),_inventory_history_row('2026-10-09',-5)]
    assert h.append(rows)['added']==2
    p=h.path('eia','wpsr','crude_change_1w');before=p.read_bytes()
    assert h.append(rows)['duplicates']==2
    assert h.append([_inventory_history_row('2026-10-09',999)])['conflicts']==1
    assert p.read_bytes()==before
    assert {r['observation_date'] for r in h.read('eia','wpsr','crude_change_1w')}=={'2026-10-02','2026-10-09'}


def test_recovery_requires_every_observed_input_after_the_oil_recovery_notice(built,tmp_path):
    _,facts,_=built;hist=tmp_path/'history';at=as_of_of(facts)
    stop=_item('n1','Oil terminal halts operations','https://status.example.org/i1',at-timedelta(hours=1),'d'*64)
    f0=_edition_facts(facts,hist,at,[stop])
    ev=scoring.evaluate(f0,mode='daily',assessments=[_assess(stop,impact='adverse',importance='critical',affected_source_ids=['cnbc_quotes'])])
    incident=ev['incidents_new'][0]['incident_id'];carry.record_incidents(hist,f0,{},ev,edition_id='e1')
    notice=_item('n7','Oil terminal resumes operations','https://status.example.org/r1',at-timedelta(minutes=5),'e'*64)
    f=_edition_facts(facts,hist,at,[notice]);good=_oi_fact(f,'cnbc_quotes')
    old=_fresh_fact(f,'cnbc_quotes')
    derived=f['state']['curve']['spread_pct']
    mixed=copy.deepcopy(index(f)[derived]);mixed['fact_id']+='.oldinput';mixed['source_fact_ids'].append(old);f['facts'].append(mixed)
    def outcome(ids,ff=f):
        return scoring.evaluate(ff,mode='daily',assessments=[],recoveries=[{'incident_id':incident,'news_id':'n7','fact_ids':ids}])
    for ids in ([old],[mixed['fact_id']],[good,mixed['fact_id']]):
        result=outcome(ids)
        assert result['trade_gate']['status']=='incident_hold'
        assert 'recovery_data_not_observed_after_notice' in result['incidents_rejected'][0]['problems']
    for field,value in [('timestamp_quality','source_date'),('observed_at',None)]:
        broken=copy.deepcopy(f);index(broken)[good][field]=value
        assert outcome([good],broken)['trade_gate']['status']=='incident_hold'
    for ids in ([good],[derived]):
        assert [r['incident_id'] for r in outcome(ids)['incidents_released']]==[incident]


def test_no_verified_oil_minute_market_means_no_invented_news_reaction():
    class NoFetch:
        def get(self,*args,**kwargs):
            raise AssertionError('unadopted minute market was requested')
    event=datetime(2026,10,15,14,30,tzinfo=UTC)
    out=reaction(NoFetch(),event.isoformat(),event+timedelta(minutes=20),[])
    assert out['status']=='missing' and out['market_id'] is None and out['reaction_basis']=='unknown'
    assert out['baseline_at'] is None
    assert all(out[k] is None for k in ('return_before_5m_bps','return_after_1m_bps','return_after_5m_bps'))


def test_cot_contract_counts_and_inventory_barrels_never_use_price_or_usd_conversion(built):
    collection,facts,_=built;at=as_of_of(facts)
    c=copy.deepcopy(collection)
    for q in sources(c)['cnbc_quotes']['values']['quotes'].values():q['last']*=10
    F,st=Facts(at,'a'*64),{}
    cot_facts(F,sources(c),st,at)
    original=index(facts)[facts['state']['cot']['mm_net_ratio']]
    assert F.index[st['cot']['mm_net_ratio']]['value']==original['value']
    assert F.index[st['cot']['oi']]['unit']=='contracts'
    assert index(facts)[facts['state']['inventory']['change_crude']]['unit']=='million barrels'


def test_incident_survives_to_the_next_day_until_official_notice_and_new_source_observation(built, tmp_path):
    collection, facts, _ = built
    hist = tmp_path / 'history'
    at = as_of_of(facts)
    stop = _item('n1', 'Oil terminal halts operations', 'https://status.example.org/i1',
                 at - timedelta(hours=1), 'd' * 64)
    f0 = _edition_facts(facts, hist, at, [stop])
    a = _assess(stop, impact='adverse', importance='critical', affected_source_ids=['cnbc_quotes'])
    ev = scoring.evaluate(f0, mode='daily', assessments=[a])
    incident = ev['incidents_new'][0]['incident_id']
    carry.record_incidents(hist, f0, {}, ev, edition_id='day-one')
    tomorrow = at + timedelta(days=1)
    inherited = _edition_facts(facts, hist, tomorrow, [])
    assert scoring.evaluate(inherited, mode='daily', assessments=[])['trade_gate']['status'] == 'incident_hold'

    later = copy.deepcopy(collection)
    for key in ('collection_started_at', 'collection_completed_at'):
        later[key] = (parse_time(later[key]) + timedelta(days=1)).isoformat()
    for rec in later['sources']:
        rec['retrieved_at'] = (parse_time(rec['retrieved_at']) + timedelta(days=1)).isoformat()
        rec['values']['retrieval_started_at'] = rec['retrieved_at']
        if rec.get('observed_at'):
            rec['observed_at'] = (parse_time(rec['observed_at']) + timedelta(days=1)).isoformat()
    for quote in sources(later)['cnbc_quotes']['values']['quotes'].values():
        quote['last_time'] = (parse_time(quote['last_time']) + timedelta(days=1)).isoformat()
    notice = _item('n7', 'Oil terminal officially resumes operations', 'https://status.example.org/recovered',
                   tomorrow - timedelta(minutes=5), 'e' * 64)
    sources(later)['news']['values']['kept'] = [notice]
    ctx = context(tmp_path / 'day-two', later)
    ctx.history_dir = hist
    recovered = UsoilStages().build_facts(later, ctx)
    proof = _oi_fact(recovered, 'cnbc_quotes')
    recovery = {'incident_id': incident, 'news_id': 'n7', 'fact_ids': [proof]}
    after = scoring.evaluate(recovered, mode='daily', assessments=[], recoveries=[recovery])
    assert after['trade_gate']['status'] != 'incident_hold'
    assert [x['incident_id'] for x in after['incidents_released']] == [incident]
    carry.record_incidents(hist, recovered, {}, after, edition_id='day-two')
    assert carry.open_incidents(hist) == []


def test_available_ovx_number_cannot_be_relabelled_as_an_oil_destination(built):
    _, original, _ = built
    facts = copy.deepcopy(original)
    # Synthetic verified comparison isolates the fact whitelist from the preceding roll gate.
    facts['state']['levels']['comparison_status'] = 'verified'
    choice = valid_analysis(facts)['destination_choice']
    fid = facts['state']['vol']['level']
    choice.update(side='down', kind='named_level_price', level_fact_ids=[fid],
                  invalidation_rule_ids=['named_level_reformed'])
    problems = analysis_mod.destination_problems(choice, facts, index(facts))
    assert f'destination: level_not_allowed_for_kind:{fid}' in problems


def test_retired_option_strike_destination_kind_is_rejected_by_the_oil_analysis_schema(built):
    _, facts, _ = built
    a = valid_analysis(facts)
    a['destination_choice']['kind'] = 'option_strike'
    with pytest.raises(AnalysisError) as caught:
        analysis_mod.validate(a, facts, edition_id=EDITION)
    assert any('destination_choice.kind' in problem for problem in caught.value.problems)
