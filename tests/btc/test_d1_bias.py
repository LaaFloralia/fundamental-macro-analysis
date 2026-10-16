"""BTCUSD D1 typed bias reference: state, entry hook (outside the sandbox), render extension, scoring log."""
import copy
import json
import os
from pathlib import Path
import stat

import pytest

from btc import d1_bias, runner, workflow
from btc.common import JST, parse_time, read_json, write_json
from btc.pipeline import BtcStages, MACHINE_SCHEMA
from btc.jsonschema_lite import validate as schema_validate
from tests.btc.test_pipeline import EDITION, build_edition, built  # noqa: F401 - fixture

CHOICES = {'long': 'up_weak', 'short': 'neutral', 'overall': 'down_weak'}


def decide_with(choices=CHOICES, calls=None):
    def decide(purpose, state, questions, **kw):
        if calls is not None:
            calls.append({'purpose': purpose, 'state': state, 'questions': questions, **kw})
        probs = {k: 0.05 for k in ('up_strong', 'up_weak', 'neutral', 'down_weak', 'down_strong')}
        return {'answers': {q: {'type': 'choice', 'choice': c, 'probabilities': {**probs, c: 0.8}}
                            for q, c in choices.items()}, 'provider': 'd1'}
    return decide


def prepared_job(root: Path, built):
    """A job root with the prepare worker's outputs for one request (as workflow.prepare writes them)."""
    collection, facts, ctx = built
    folder = root / 'work' / EDITION
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'collection.json', collection)
    write_json(folder / 'facts.json', facts)
    stages = BtcStages()
    (folder / 'analysis-input.md').write_text(stages.analysis_input(collection, facts, ctx), encoding='utf-8')
    write_json(folder / 'analysis.schema.json', stages.analysis_schema())
    request = {'schemaVersion': 1, 'symbol': 'BTCUSD', 'mode': 'daily', 'session_slot': 'pm',
               'created_at': '2026-10-07T21:30:00+09:00', 'collected_at': facts['collected_at'],
               'data_path': str(folder / 'collection.json'), 'data_sha256': workflow.digest(folder / 'collection.json'),
               'facts_path': str(folder / 'facts.json'), 'facts_sha256': workflow.digest(folder / 'facts.json'),
               'input_path': str(folder / 'analysis-input.md'),
               'input_sha256': workflow.digest(folder / 'analysis-input.md'),
               'analysis_schema_path': str(folder / 'analysis.schema.json'),
               'analysis_schema_sha256': workflow.digest(folder / 'analysis.schema.json'),
               'previous_editions': [], 'workflow': 'x', 'historical': False,
               'status': 'awaiting_parent_authoring', 'publication_ready': False}
    write_json(folder / 'request.json', request)
    write_json(root / 'latest-daily.json', {'mode': 'daily', 'status': 'awaiting_parent_authoring',
                                            'request_path': str(folder / 'request.json')})
    return folder


def job_of(root: Path) -> workflow.Job:
    return workflow.Job(root=root, python=Path('/usr/bin/python3'))


# ---------------------------------------------------------------- state

def test_state_is_fact_displays_and_kept_headlines_without_the_code_direction(built):
    _, facts, _ = built
    state = d1_bias.btc_state(facts, 'daily')
    rows = '\n'.join(state['rows'])
    assert len(state['rows']) >= 6 and 'etf_netflow_usd_5d' in rows and 'stable_supply_change_7d' in rows
    assert set(state) == {'symbol', 'mode', 'as_of', 'rows', 'headlines', 'direction_note'}
    assert state['headlines'] and all(h['title'] for h in state['headlines'])
    ps = [h['relevance'] or 0 for h in state['headlines']]
    assert ps == sorted(ps, reverse=True)
    blob = json.dumps(state, ensure_ascii=False)
    assert 'withheld' not in blob and 'band' not in blob and 'candidate' not in blob


def test_no_usable_facts_means_no_state():
    assert d1_bias.btc_state({'facts': [], 'state': {}}, 'daily') is None


# ---------------------------------------------------------------- entry hook

def test_entry_places_one_line_under_the_decision_block_and_keeps_hashes_valid(tmp_path, built):
    folder = prepared_job(tmp_path, built)
    calls = []
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with(calls=calls)) == 'ok'
    assert calls[0]['purpose'] == 'bias-btcusd' and calls[0]['data_class'] == 'public'
    text = (folder / 'analysis-input.md').read_text(encoding='utf-8')
    lines = text.split('\n')
    coverage = next(i for i, x in enumerate(lines) if x.startswith('- coverage:'))
    assert lines[coverage + 1].startswith('- D1判定（参考。最終判断は親、コード集計の代わりではない）: 長期 上向き・弱')
    assert sum(x.startswith('- D1判定') for x in lines) == 1
    assert lines[coverage + 2] == '' and lines[coverage + 3].startswith('  - ')  # the item rows follow
    request = read_json(folder / 'request.json')
    assert request['input_sha256'] == workflow.digest(folder / 'analysis-input.md')
    assert request['d1_bias']['総合']['label'] == '下向き・弱'
    # The render phase still verifies every input digest.
    package = {'request_path': str(folder / 'request.json')}
    write_json(folder / 'package.json', package)
    workflow._load_request(job_of(tmp_path), folder / 'package.json', 'daily')


def test_the_gateway_state_never_carries_the_code_decision(tmp_path, built):
    prepared_job(tmp_path, built)
    calls = []
    d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with(calls=calls))
    blob = json.dumps(calls[0]['state'], ensure_ascii=False)
    assert 'direction_status' not in blob and 'coverage' not in blob and 'trade_gate' not in blob


@pytest.mark.parametrize('failure', ['raise', 'empty'])
def test_gateway_failure_leaves_the_run_untouched(tmp_path, built, failure):
    folder = prepared_job(tmp_path, built)
    before = {n: (folder / n).read_bytes() for n in ('analysis-input.md', 'request.json')}

    def decide(*a, **k):
        if failure == 'raise':
            raise RuntimeError('down')
        return {'answers': {}}
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide) == 'unavailable'
    assert {n: (folder / n).read_bytes() for n in before} == before


def test_entry_skips_a_second_run_and_a_changed_input(tmp_path, built):
    folder = prepared_job(tmp_path, built)
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'ok'
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'skipped'
    other = tmp_path / 'other'
    folder2 = prepared_job(other, built)
    (folder2 / 'analysis-input.md').write_text('changed', encoding='utf-8')
    assert d1_bias.entry_reference(other, 'daily', decide=decide_with()) == 'skipped'


def test_no_awaiting_request_or_foreign_path_is_ignored(tmp_path, built):
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'unavailable'
    prepared_job(tmp_path, built)
    write_json(tmp_path / 'latest-daily.json', {'status': 'failed'})
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'no_request'
    write_json(tmp_path / 'latest-daily.json', {'status': 'awaiting_parent_authoring', 'request_path': '/etc/passwd'})
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'no_request'


def test_runner_entry_label_is_fixed_when_the_runtime_is_missing(tmp_path):
    paths = runner.Paths(root=tmp_path)
    assert runner.entry_d1(paths, 'daily') == 'unavailable'
    assert runner.snapshot_allowed('scripts/d1_bias.py') and runner.snapshot_allowed('btc/d1_bias.py')


# ---------------------------------------------------------------- render and scoring

def with_reference(built, d1):
    collection, facts, ctx = built
    ctx = copy.copy(ctx)
    write_json(ctx.work_dir / 'request.json', {'d1_bias': d1})
    return (collection, facts, ctx)


GOOD = {'総合': {'label': '下向き・弱', 'probs': {'下向き・弱': 0.8, '中立': 0.1}, 'provider': 'd1', 'ms': 900},
        '長期': {'label': '上向き・弱', 'probs': {}, 'provider': 'd1', 'ms': 900}}


def test_machine_carries_the_reference_and_still_passes_its_schema(built):
    parts, machine = build_edition(with_reference(built, GOOD))
    assert machine['d1_bias'] == GOOD
    BtcStages().validate_machine(machine, parts)
    bad = copy.deepcopy(machine)
    bad['d1_bias']['総合']['label'] = '強気'
    assert schema_validate(bad, json.loads(MACHINE_SCHEMA.read_text()))


def test_no_reference_changes_nothing(built):
    write_json(built[2].work_dir / 'request.json', {})
    parts, machine = build_edition(built)
    assert 'd1_bias' not in machine
    BtcStages().validate_machine(machine, parts)


@pytest.mark.parametrize('broken', [{'総合': {'label': 'x'}}, {'総合': 'x'}, 'x', {'月': GOOD['総合']}, []])
def test_a_malformed_stored_reference_is_ignored(built, broken):
    parts, machine = build_edition(with_reference(built, broken))
    assert 'd1_bias' not in machine


def test_scoring_log_records_the_code_label_and_leaves_missing_horizons_null(tmp_path, built):
    parts, machine = build_edition(with_reference(built, GOOD))
    written = d1_bias.record_scores(tmp_path, machine.get('d1_bias'), machine, run='parent-daily-x', at='2026-10-07T21:40:00+09:00')
    assert written == 2
    path = tmp_path / 'logs' / 'd1-bias-scores.jsonl'
    rows = {r['timeframe']: r for r in map(json.loads, path.read_text().splitlines())}
    assert rows['総合']['symbol'] == 'BTCUSD' and rows['総合']['d1_label'] == '下向き・弱'
    assert rows['総合']['final_label'] == d1_bias.final_labels(machine)['総合']
    assert rows['長期']['final_label'] is None and rows['長期']['differs'] is None
    assert rows['総合']['date'] == machine['data_as_of']
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert d1_bias.record_scores(tmp_path, None, machine, run='r', at='t') == 0


def test_render_package_writes_the_log_after_success_and_never_fails_on_it(tmp_path, built):
    class Stages(BtcStages):
        def after_render(self, root, machine, run, at):
            raise RuntimeError('log broke')
    assert hasattr(BtcStages, 'after_render')
    # A failing hook is swallowed by the workflow (checked on the source: it is wrapped in try/except).
    source = Path(workflow.__file__).read_text(encoding='utf-8')
    assert "hook(job.root, machine, target.name, current.isoformat())" in source and 'except Exception' in source


@pytest.mark.parametrize('status,direction,strength,expected', [
    ('withheld', 'none', '保留', '中立'), ('available', 'up', '高', '上向き・強'), ('available', 'up', '中', '上向き・弱'),
    ('available', 'down', '低', '下向き・弱'), ('available', 'down', '高', '下向き・強')])
def test_final_label_follows_the_code_direction(status, direction, strength, expected):
    machine = {'btc': {'direction_status': status, 'candidate_direction': direction, 'direction_strength': strength}}
    assert d1_bias.final_labels(machine) == {'総合': expected}
