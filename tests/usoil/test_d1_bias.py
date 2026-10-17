"""USOIL D1 typed bias reference: state, entry hook (outside the sandbox), render extension, scoring log."""
import copy
import json
import os
from pathlib import Path
import stat

import pytest

from usoil import d1_bias, runner, workflow
from usoil.common import read_json, write_json
from usoil.pipeline import UsoilStages, MACHINE_SCHEMA
from usoil.jsonschema_lite import validate as schema_validate
from tests.usoil.test_pipeline import EDITION, build_edition, built  # noqa: F401 - fixture

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
    stages = UsoilStages()
    (folder / 'analysis-input.md').write_text(stages.analysis_input(collection, facts, ctx), encoding='utf-8')
    write_json(folder / 'analysis.schema.json', stages.analysis_schema())
    request = {'schemaVersion': 1, 'symbol': 'USOIL', 'mode': 'daily', 'session_slot': 'pm',
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



@pytest.fixture
def enable_local_d1(monkeypatch):
    monkeypatch.setenv('USOIL_D1_ENABLED', '1')
    monkeypatch.setenv('USOIL_ENABLE_AI_AUX', '1')


def test_d1_is_disabled_by_default_without_calling_a_provider(tmp_path, built, monkeypatch):
    prepared_job(tmp_path, built)
    monkeypatch.delenv('USOIL_D1_ENABLED', raising=False)
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    calls = []
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with(calls=calls)) == 'skipped'
    assert runner.entry_d1(runner.Paths(root=tmp_path), 'daily') == 'skipped'
    assert calls == []

# ---------------------------------------------------------------- state

def test_state_is_fact_displays_and_kept_headlines_without_the_code_direction(built):
    _, facts, _ = built
    state = d1_bias.usoil_state(facts, 'daily')
    rows = '\n'.join(state['rows'])
    assert len(state['rows']) >= 6 and 'crude_change_1w' in rows and 'cl_front_spread' in rows
    assert set(state) == {'symbol', 'mode', 'as_of', 'rows', 'headlines', 'direction_note'}
    assert state['headlines'] and all(h['title'] for h in state['headlines'])
    ps = [h['relevance'] or 0 for h in state['headlines']]
    assert ps == sorted(ps, reverse=True)
    blob = json.dumps(state, ensure_ascii=False)
    assert 'withheld' not in blob and 'band' not in blob and 'candidate' not in blob


def test_no_usable_facts_means_no_state():
    assert d1_bias.usoil_state({'facts': [], 'state': {}}, 'daily') is None


# ---------------------------------------------------------------- entry hook

def test_optin_unregistered_usoil_purpose_never_mutates_the_request(tmp_path, built, enable_local_d1):
    folder = prepared_job(tmp_path, built)
    before = {n:(folder/n).read_bytes() for n in ('analysis-input.md','request.json')}
    calls = []
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with(calls=calls)) == 'unavailable'
    assert calls == []  # No BTC/XAU purpose substitution, even with a supplied local decision stub.
    assert {n:(folder/n).read_bytes() for n in before} == before
    request = read_json(folder/'request.json')
    assert request['input_sha256']==workflow.digest(folder/'analysis-input.md') and 'd1_bias' not in request
    write_json(folder/'package.json',{'request_path':str(folder/'request.json')})
    workflow._load_request(job_of(tmp_path),folder/'package.json','daily')


def test_reference_material_never_carries_the_code_decision(built):
    state=d1_bias.usoil_state(built[1],'daily')
    blob=json.dumps(state,ensure_ascii=False)
    assert 'direction_status' not in blob and 'coverage' not in blob and 'trade_gate' not in blob


@pytest.mark.parametrize('failure', ['raise', 'empty'])
def test_gateway_failure_leaves_the_run_untouched(tmp_path, built, failure, enable_local_d1):
    folder = prepared_job(tmp_path, built)
    before = {n: (folder / n).read_bytes() for n in ('analysis-input.md', 'request.json')}

    def decide(*a, **k):
        if failure == 'raise':
            raise RuntimeError('down')
        return {'answers': {}}
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide) == 'unavailable'
    assert {n: (folder / n).read_bytes() for n in before} == before


def test_entry_skips_an_existing_reference_and_a_changed_input(tmp_path, built, enable_local_d1):
    folder=prepared_job(tmp_path,built)
    request=read_json(folder/'request.json');request['d1_bias']=GOOD
    write_json(folder/'request.json',request)
    assert d1_bias.entry_reference(tmp_path,'daily',decide=decide_with())=='skipped'
    other=tmp_path/'other';folder2=prepared_job(other,built)
    (folder2/'analysis-input.md').write_text('changed',encoding='utf-8')
    assert d1_bias.entry_reference(other,'daily',decide=decide_with())=='skipped'


def test_no_awaiting_request_or_foreign_path_is_ignored(tmp_path, built, enable_local_d1):
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'unavailable'
    prepared_job(tmp_path, built)
    write_json(tmp_path / 'latest-daily.json', {'status': 'failed'})
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'no_request'
    write_json(tmp_path / 'latest-daily.json', {'status': 'awaiting_parent_authoring', 'request_path': '/etc/passwd'})
    assert d1_bias.entry_reference(tmp_path, 'daily', decide=decide_with()) == 'no_request'


def test_runner_entry_label_is_fixed_when_the_runtime_is_missing(tmp_path, enable_local_d1):
    paths = runner.Paths(root=tmp_path)
    assert runner.entry_d1(paths, 'daily') == 'unavailable'
    assert runner.snapshot_allowed('scripts/d1_bias.py') and runner.snapshot_allowed('usoil/d1_bias.py')


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
    UsoilStages().validate_machine(machine, parts)
    bad = copy.deepcopy(machine)
    bad['d1_bias']['総合']['label'] = '強気'
    assert schema_validate(bad, json.loads(MACHINE_SCHEMA.read_text()))


def test_no_reference_changes_nothing(built):
    write_json(built[2].work_dir / 'request.json', {})
    parts, machine = build_edition(built)
    assert 'd1_bias' not in machine
    UsoilStages().validate_machine(machine, parts)


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
    assert rows['総合']['symbol'] == 'USOIL' and rows['総合']['d1_label'] == '下向き・弱'
    assert rows['総合']['final_label'] == d1_bias.final_labels(machine)['総合']
    assert rows['長期']['final_label'] is None and rows['長期']['differs'] is None
    assert rows['総合']['date'] == machine['data_as_of']
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert d1_bias.record_scores(tmp_path, None, machine, run='r', at='t') == 0


def test_render_package_writes_the_log_after_success_and_never_fails_on_it(tmp_path, built):
    class Stages(UsoilStages):
        def after_render(self, root, machine, run, at):
            raise RuntimeError('log broke')
    assert hasattr(UsoilStages, 'after_render')
    # A failing hook is swallowed by the workflow (checked on the source: it is wrapped in try/except).
    source = Path(workflow.__file__).read_text(encoding='utf-8')
    assert "hook(job.root, machine, target.name, current.isoformat())" in source and 'except Exception' in source


@pytest.mark.parametrize('status,direction,strength,expected', [
    ('withheld', 'none', '保留', '中立'), ('available', 'up', '高', '上向き・強'), ('available', 'up', '中', '上向き・弱'),
    ('available', 'down', '低', '下向き・弱'), ('available', 'down', '高', '下向き・強')])
def test_final_label_follows_the_code_direction(status, direction, strength, expected):
    machine = {'usoil': {'direction_status': status, 'candidate_direction': direction, 'direction_strength': strength}}
    assert d1_bias.final_labels(machine) == {'総合': expected}


def test_entry_file_helpers_refuse_links(tmp_path):
    import os
    import pytest
    from usoil import d1_bias as entry
    real = tmp_path / 'real.json'
    real.write_text('{}', encoding='utf-8')
    link = tmp_path / 'link.json'
    link.symlink_to(real)
    with pytest.raises(OSError):
        entry._read_regular(link)
    hard = tmp_path / 'hard.json'
    os.link(real, hard)
    with pytest.raises(PermissionError):
        entry._read_regular(hard)
    with pytest.raises(PermissionError):
        entry._write_atomic(link, b'x')
    assert real.read_text(encoding='utf-8') == '{}'
    target = tmp_path / 'out.json'
    entry._write_atomic(target, b'ok')
    assert target.read_bytes() == b'ok'
    assert not [p for p in tmp_path.iterdir() if p.name.startswith('.d1-')]
