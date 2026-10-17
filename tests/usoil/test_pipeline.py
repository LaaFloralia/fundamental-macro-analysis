"""Oil pipeline contracts on an explicitly synthetic collection. No network.

Checks provenance, adopted populations, fixed score gates, parent rejection,
MD/machine agreement, expiry and hostile-headline rendering.
"""
import copy
from datetime import timedelta
import json
from pathlib import Path
import re

import pytest

from usoil import analysis as analysis_mod, machine as machine_mod, report, scoring, workflow
from usoil.acceptance import static_html_problems
from usoil.collect import SOURCE_CATALOG
from usoil.common import JST, JobError, parse_time, read_json, write_json
from usoil.jsonschema_lite import SchemaError, validate as schema_validate
from usoil.pipeline import FACTS_SCHEMA, MACHINE_SCHEMA, UsoilStages
from usoil.stages import AnalysisError, RunContext
from tests.usoil.test_workflow import full_review, journal_contract

EDITION = 'parent-daily-20261007T212342-0000beef'
XSS = '<img src=x onerror=alert(1)>'


def load_collection() -> dict:
    from tests.usoil.oil_fixtures import load_collection as synthetic_oil
    return synthetic_oil()


def context(root: Path, collection: dict, name=EDITION) -> RunContext:
    work = root / 'work' / name
    work.mkdir(parents=True, exist_ok=True)
    write_json(work / 'collection.json', collection)
    return RunContext(root=root, mode='daily', session_slot='pm', work_dir=work, history_dir=root / 'history',
                      started_at=parse_time(collection['collection_started_at']))


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    collection = load_collection()
    ctx = context(tmp_path_factory.mktemp('job'), collection)
    facts = UsoilStages().build_facts(collection, ctx)
    return collection, facts, ctx


def claim(text, ids=(), kind='hypothesis'):
    return {'text': text, 'fact_ids': list(ids), 'claim_kind': kind}


def valid_analysis(facts, edition_id=EDITION):
    ref = facts['state']['reference_price']['fact_id']
    return {'schema_version': analysis_mod.SCHEMA_VERSION, 'edition_id': edition_id,
            'input_manifest_sha256': facts['input_manifest_sha256'], 'requested_status': 'ready_for_validation',
            'thesis': claim('TEST 参照価格は{{fact:%s}}。' % ref, [ref], 'fact_interpretation'),
            'three_domains': {'liquidity': claim('TEST 連続限月の高安とラウンド水準は参考値。'),
                              'positioning': claim('TEST COTの偏りを観測する。'),
                              'bias': claim('TEST 在庫とドルの整合を確認する。')},
            'positioning_summary': claim('TEST COTは方向確定を担わず混雑を確認する。'),
            'news_assessments': [],
            'scenarios': [{'id': 'base', 'condition_rule_ids': ['inventory_direction_flip'], 'fact_ids': [ref],
                           'expected_effect': claim('TEST 在庫取り崩しが続けば支えになる。'),
                           'counter_case': claim('TEST 在庫増加へ転じれば支えは消える。'),
                           'invalidation_rule_ids': ['inventory_direction_flip']}],
            'destination_choice': {'side': 'none', 'kind': 'none', 'level_fact_ids': [],
                                   'rationale': claim('TEST 限月整合を確認できないため目的地は置かない。'),
                                   'counter_case': claim('TEST なし。'), 'invalidation_rule_ids': []},
            'counter_cases': [claim('TEST マクロが逆風なら保留する。')],
            'reevaluation_conditions': [{'rule_id': 'source_recovers', 'fact_ids': [],
                                         'explanation': claim('TEST 欠測が回復したら再評価。')}],
            'limitations': [claim('TEST 合成した分析で、相場の判断ではない。', kind='limitation')], 'issues': []}


def index(facts):
    return {f['fact_id']: f for f in facts['facts']}


# ------------------------------------------------------------------ facts

def test_facts_schema_and_provenance(built):
    """B1 retrieval_started_at <= retrieved_at; B2 observed facts carry raw_sha256, derived facts are 'computed'."""
    _, facts, _ = built
    assert schema_validate(facts, json.loads(FACTS_SCHEMA.read_text())) == []
    ids = index(facts)
    observed = [f for f in facts['facts'] if f['evidence_kind'] == 'observed' and f['value'] is not None]
    derived = [f for f in facts['facts'] if f['evidence_kind'] == 'derived' and f['value'] is not None]
    assert observed and derived
    for f in observed:
        assert f['source_id'] != 'computed' and re.fullmatch(r'[0-9a-f]{64}', f['raw_sha256'] or ''), f['fact_id']
    for f in derived:
        assert f['source_id'] == 'computed' and f['raw_sha256'] is None, f['fact_id']
        assert re.fullmatch(r'[0-9a-f]{64}', f['input_manifest_sha256'] or '') and f['source_fact_ids'], f['fact_id']
        assert all(s in ids for s in f['source_fact_ids']), f['fact_id']
    for f in facts['facts']:
        if f['retrieval_started_at'] and f['retrieved_at']:
            assert parse_time(f['retrieval_started_at']) <= parse_time(f['retrieved_at']), f['fact_id']


def test_only_adopted_oil_populations_are_scored(built):
    _, facts, _ = built
    assert not {'etf','derivatives','options','stable','onchain'} & facts['state'].keys()
    assert {'inventory','curve','cot','vol'} <= facts['state'].keys()
    assert not any(f['provider'] in ('binance','bybit','okx') for f in facts['facts'] if 'provider' in f)


def test_inventory_references_observed_eia_week(built):
    _, facts, _ = built
    st=facts['state']['inventory']
    ids=st['fact_ids']
    observed=[index(facts)[i] for i in ids if index(facts)[i]['evidence_kind']=='observed']
    assert any(f['observation_date']=='2026-10-09' for f in observed)
    assert any(f['value']==-5.0 for f in observed)


def test_unused_and_unconfirmed_sources(built):
    _, facts, _ = built
    ids = {sid for sid, _ in SOURCE_CATALOG}
    assert not any(s in sid for sid in ids for s in ('binance','deribit','farside','coinglass'))
    assert 'cme_group' in {x['source_id'] for x in facts['state']['terms_restricted']}
    assert facts['state']['calendar']['nextweek']=='unavailable_404'


def test_confidence_caps_never_increase_a_score(built):
    _, facts, _ = built
    ev=scoring.evaluate(facts,mode='daily')
    assert ev['confidence'] <= 0.75
    if ev['band']=='Low':assert ev['no_trade'] is True
    assert len(ev['items'])==8


def test_preview_has_no_adopted_oil_event_without_parent_verification(built):
    _, facts, _ = built
    ev=scoring.evaluate(facts,mode='daily')
    assert ev['groups']['oil_specific_event']['direction']==0
    assert ev['direction_strength']!='高'


def test_rates_keep_percent_units_and_weekly_vol_keeps_observation_time(tmp_path):
    collection=load_collection()
    fred=next(s for s in collection['sources'] if s['source_id']=='fred')
    fred['values']['series']['DFII10']['unit']='percent'
    ctx=context(tmp_path,collection)
    facts=UsoilStages().build_facts(collection,ctx)
    ids=index(facts)
    assert '%' in ids[facts['state']['macro']['DFII10']]['display']
    vol=facts['state']['vol']
    assert ids[vol['change_1w']]['observed_at']==ids[vol['level']]['observed_at']


def test_daily_close_cannot_prove_hours_of_acceptance():
    from usoil.levels import level_state
    start=parse_time('2026-10-01T04:00:00Z')
    daily={'start':start,'end':start+timedelta(days=1),'h':105,'l':95,'c':101}
    state=level_state(100,'high',start,[],[daily])
    assert state['state']=='beyond' and state['hours_outside']==0
    assert '未確認' in state['state_word']


# ------------------------------------------------------------------ parent analysis

def _news(facts, verification):
    return next(n for n in facts['state']['news']['items'] if n['code_verification'] == verification)


def _wall(facts, below: bool):
    ids=index(facts)
    ref=ids[facts['state']['reference_price']['fact_id']]['value']
    return next(x['fact_id'] for x in facts['state']['levels']['items']
                if ids[x['fact_id']]['value'] is not None and (ids[x['fact_id']]['value']<ref)==below)


def _option_level(facts, below: bool):
    return _wall(facts, below)


def mutate_unsafe(a, facts):
    a['three_domains']['bias'] = claim('TEST ' + XSS)


def mutate_raw_number(a, facts):
    a['three_domains']['bias'] = claim('TEST 83000ドルが節目。')


def mutate_unknown_fact(a, facts):
    a['positioning_summary'] = claim('TEST 根拠あり。', ['usoil.unknown.fact'], 'fact_interpretation')


def mutate_token_not_listed(a, facts):
    a['thesis']['fact_ids']=[]


def mutate_interpretation_without_facts(a, facts):
    a['positioning_summary'] = claim('TEST 偏りがある。', [], 'fact_interpretation')


def mutate_headline_as_fact(a, facts):
    n = _news(facts, 'headline_only')
    a['news_assessments'] = [{'news_id': n['id'], 'event_cluster_id': n['event_cluster_id'], 'group_id': 'context_only',
                              'impact': 'supportive', 'importance': 'medium', 'verification': 'headline_only',
                              'source_quote': n['title'][:20], 'fact_ids': [],
                              'interpretation': claim('TEST 見出しは上向き。', [a['thesis']['fact_ids'][0]],
                                                      'fact_interpretation')}]


def mutate_primary_without_official(a, facts):
    n = _news(facts, 'secondary_body_retrieved')
    a['news_assessments'] = [{'news_id': n['id'], 'event_cluster_id': n['event_cluster_id'], 'group_id': 'context_only',
                              'impact': 'unknown', 'importance': 'medium', 'verification': 'primary_confirmed',
                              'source_quote': n['title'][:20], 'fact_ids': [], 'interpretation': claim('TEST 参考。')}]


def mutate_quote_not_in_text(a, facts):
    mutate_primary_without_official(a, facts)
    a['news_assessments'][0].update(verification='secondary_body_confirmed', source_quote='TEST 存在しない引用')


def mutate_wrong_side(a, facts):
    facts['state']['levels']['comparison_status']='verified'
    a['destination_choice'].update(side='up',kind='named_level_price',level_fact_ids=[_wall(facts,below=True)],
                                   invalidation_rule_ids=['named_level_reformed'])


def mutate_changes_requested(a, facts):
    a['requested_status'] = 'changes_requested'
    a['issues'] = [{'code': 'fact_mismatch', 'description': 'TEST', 'fact_ids': []}]


def mutate_edition(a, facts):
    a['edition_id'] = 'parent-daily-20990101T000000-ffffffff'


@pytest.mark.parametrize('mutate, problem', [
    (mutate_unsafe, 'markup_or_unsafe_characters'), (mutate_raw_number, 'raw_number_outside_tokens'),
    (mutate_unknown_fact, 'unknown_fact:usoil.unknown.fact'), (mutate_token_not_listed, 'token_fact_not_listed'),
    (mutate_interpretation_without_facts, 'interpretation_without_facts'),
    (mutate_headline_as_fact, 'unverified_news_stated_as_fact'),
    (mutate_primary_without_official, 'primary_confirmed_without_official_body'),
    (mutate_quote_not_in_text, 'source_quote_not_in_retrieved_text'),
    (mutate_wrong_side, 'level_on_wrong_side'), (mutate_changes_requested, 'parent_requested_changes'),
    (mutate_edition, 'edition_id_mismatch')])
def test_parent_analysis_rejections(built, mutate, problem):
    _, facts, _ = built
    facts=copy.deepcopy(facts)
    a = valid_analysis(facts)
    mutate(a, facts)
    with pytest.raises(AnalysisError) as caught:
        analysis_mod.validate(a, facts, edition_id=EDITION)
    assert any(problem in p for p in caught.value.problems), caught.value.problems


# ------------------------------------------------------------------ report and machine

def build_edition(built, analysis=None, minutes=12):
    collection, facts, ctx = built
    ctx = copy.copy(ctx)
    ctx.generated_at = (parse_time(facts['as_of']) + timedelta(minutes=minutes)).astimezone(JST)
    stages = UsoilStages()
    validated = stages.validate_analysis(analysis or valid_analysis(facts), facts, ctx)
    parts = stages.build_report(validated, facts, collection, ctx)
    machine = machine_mod.build(parts.machine_core, parts.machine_extensions, mode='daily', session_slot='pm',
                                collected_at=collection['collection_started_at'],
                                generated_at=ctx.generated_at.isoformat(), as_of=facts['as_of'])
    return parts, machine


def test_report_structure_and_machine_schema(built):
    parts, machine = build_edition(built)
    UsoilStages().validate_machine(machine, parts)  # B9 schema (own validator) + MD decision block
    md = parts.markdown
    assert re.match(r'^# USOIL チャート外分析 Daily — \d{4}-\d{2}-\d{2} \d{2}:\d{2} JST\n', md)
    headings = re.findall(r'^## (\d+)\.', md, re.M)
    assert headings == [str(i) for i in range(12)]
    assert report.LIQUIDITY_NOTE in md and 'EIA' in md and 'COT' in md
    assert machine['research_only'] is True and machine['execution_enabled'] is False
    assert machine['usoil']['confidence_kind'] == 'uncalibrated_ordinal'
    assert len(machine['usoil']['analysis']['scenarios']) == 1  # B6: scenarios come from the parent
    figure_ids = {f['id'] for f in parts.figures}
    assert {'bias', 'calendar'} <= figure_ids <= {'bias', 'liquidity', 'news', 'calendar'}


def test_markdown_and_machine_must_agree(built):
    parts, machine = build_edition(built)
    changed = copy.deepcopy(machine)
    changed['confidence'] = 0.75
    with pytest.raises(JobError) as caught:
        UsoilStages().validate_machine(changed, parts)
    assert caught.value.category == 'markdown_machine_mismatch'
    extra = copy.deepcopy(machine)
    extra['unexpected_key'] = 1
    with pytest.raises(JobError) as caught:
        UsoilStages().validate_machine(extra, parts)
    assert caught.value.category == 'machine_invalid'


def test_own_schema_validator_rejects_unknown_keywords():
    with pytest.raises(SchemaError):
        schema_validate({'a': 1}, {'type': 'object', 'properties': {'a': {'type': 'integer', 'exclusiveMaximun': 3}}})
    assert schema_validate(json.loads(MACHINE_SCHEMA.read_text()), {'type': 'object'}) == []


def test_expired_report_is_never_tradeable(built):
    _, machine=build_edition(built,minutes=300)
    assert machine['no_trade'] is True
    assert machine['usoil']['trade_gate']['status']=='expired'


# ------------------------------------------------------------------ end to end

def with_hostile_headline(collection: dict) -> dict:
    c = copy.deepcopy(collection)
    news = next(s for s in c['sources'] if s['source_id'] == 'news')
    base = next(k for k in news['values']['kept'] if k['body'].get('status') != 'retrieved')
    hostile = copy.deepcopy(base)
    hostile.update(id='n999', event_cluster_id='c-0000000000000999', reaction=None,
                   title=XSS + ' Crude oil [click](javascript:alert(2)) | `x`',
                   url='https://oilprice.com/synthetic/test-hostile-headline',
                   excerpt='<script>alert(3)</script>',
                   body={'status': 'not_attempted', 'error_kind': 'budget_exhausted'})
    news['values']['kept'].insert(0, hostile)
    return c


def test_hostile_headline_end_to_end(tmp_path, real_browser):
    """A headline carrying markup is neutralised in facts, input, MD, HTML and machine; the edition passes review."""
    collection = with_hostile_headline(load_collection())
    started = parse_time(collection['collection_started_at']).astimezone(JST)
    clock = lambda: started + timedelta(minutes=5)  # noqa: E731
    job = workflow.Job(root=tmp_path / 'job', python=Path('/usr/bin/python3'))

    def collect(job_, ctx, output):
        write_json(output, collection)
    assert workflow.prepare(job, 'daily', UsoilStages(), collect, clock=clock, started=started) == 0
    state = read_json(job.root / 'latest-daily.json')
    assert state['status'] == 'awaiting_parent_authoring'
    request_path = Path(state['request_path'])
    request = read_json(request_path)
    facts = read_json(request['facts_path'])
    item = next(n for n in facts['state']['news']['items'] if 'test-hostile-headline' in n['url'])
    assert '<' not in item['title'] and '[' not in item['title'] and '|' not in item['title']
    assert '＜img src=x onerror=alert(1)＞' in item['title'] and 'javascript：' in item['title']
    briefing = Path(request['input_path']).read_text()
    assert XSS not in briefing and '<script' not in briefing

    a = valid_analysis(facts, edition_id=request_path.parent.name)
    a['news_assessments'] = [{'news_id': item['id'], 'event_cluster_id': item['event_cluster_id'],
                              'group_id': 'context_only', 'impact': 'unknown', 'importance': 'low',
                              'verification': 'headline_only', 'source_quote': 'Crude oil', 'fact_ids': [],
                              'interpretation': claim('TEST 見出しのみで参考扱い。')}]
    analysis_path = write_json(request_path.parent / 'analysis.json', a)
    package = write_json(request_path.parent / 'package.json',
                         {'request_path': str(request_path), 'analysis_path': str(analysis_path)})
    now = started + timedelta(minutes=12)
    assert workflow.render_package(job, package, 'daily', UsoilStages(), now=now) == 0
    edition_path = Path(read_json(job.root / 'latest-daily.json')['edition_path'])
    out = read_json(edition_path)['outputs']
    md = Path(out['md_path']).read_text()
    html = Path(out['html_path']).read_text()
    machine = read_json(out['json_path'])
    assert '＜img src=x onerror=alert(1)＞' in md and XSS not in md and '](javascript' not in md
    assert static_html_problems(html) == []
    assert not re.search(r'<\s*img\b', html, re.I) and not re.search(r'<\s*script\b', html, re.I)
    assert not re.search(r'<[^>]*\sonerror\s*=', html, re.I)  # only as text, never an attribute
    assert any('＜img' in n['title'] for n in machine['usoil']['news'])
    assert read_json(out['render_evidence_path'])['status'] == 'passed'

    review_path = write_json(job.work / 'review.json', full_review(edition_path))
    assert workflow.accept_review(job, review_path, 'daily', now=now) == 0
    journal_contract(job, 'daily')


# ------------------------------------------------------------------ fixture hygiene (public repository)

OWN_TEXT_PATHS = {'fixture_note', 'terms_restricted/[]/detail', 'sources/[]/values/note', 'sources/[]/values/scope',
                  'sources/[]/values/underlying_scope', 'sources/[]/values/market', 'sources/[]/values/report',
                  'sources/[]/values/unit', 'sources/[]/values/series/[]/label',
                  'sources/[]/values/events/[]/name'}  # our labels + official BEA release names (US government)


def test_fixture_has_no_external_prose():
    """All prose is deliberately synthetic; provider data never leaks into a test fixture."""
    collection=load_collection()
    assert collection['collector_version']=='synthetic-usoil'
    news=next(s for s in collection['sources'] if s['source_id']=='news')
    for item in news['values']['kept']:
        assert item['title'].startswith('Synthetic ')
        assert item['excerpt'].startswith('Synthetic ')
        if item['body'].get('text_excerpt'):
            assert item['body']['text_excerpt'].startswith('Synthetic ')
    assert 'Bitcoin' not in json.dumps(collection)
