"""Oil-source failure contracts and inherited secret isolation. Synthetic inputs only."""
import csv
from datetime import datetime, timezone
import io
import json

import pytest

from usoil import runner
from usoil.fetch import FetchError, Response
from usoil.sources import calendar, cot, eia, macro, vol


class Fetch:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def get(self, url, **kwargs):
        at = '2026-10-17T04:00:00+00:00'
        return Response(200, self.payload, url, at, 1, 1.0, 'application/json', at)


def cot_row(**changes):
    return dict(report_date_as_yyyy_mm_dd='2026-10-13T00:00:00.000',
                cftc_contract_market_code='067651', futonly_or_combined='FutOnly',
                m_money_positions_long_all='170000', m_money_positions_short_all='130000',
                open_interest_all='1900000', **changes)


@pytest.mark.parametrize('field,value', [
    ('cftc_contract_market_code', '067411'), ('futonly_or_combined', 'Combined'),
    ('report_date_as_yyyy_mm_dd', 'not-a-date'), ('open_interest_all', '0'),
    ('m_money_positions_long_all', '-1')])
def test_cot_refuses_wrong_population_and_impossible_core_values(field, value):
    row = cot_row()
    row[field] = value
    with pytest.raises(FetchError, match='invalid_payload'):
        cot.cot(Fetch([row]))


def test_cot_refuses_duplicate_reports():
    with pytest.raises(FetchError, match='invalid_payload'):
        cot.cot(Fetch([cot_row(), cot_row()]))


@pytest.mark.parametrize('key', ['cftc_contract_market_code', 'futonly_or_combined'])
def test_cot_identity_must_be_observed_not_filled(key):
    row = cot_row()
    del row[key]
    with pytest.raises(FetchError, match='invalid_payload'):
        cot.cot(Fetch([row]))


@pytest.mark.parametrize('value', ['1e1000', '1e-1000', 'NaN', 'Infinity'])
def test_cnbc_values_must_be_representable_as_finite_floats(value):
    assert macro._quote_decimal(value) is None


@pytest.mark.parametrize('at', ['', '2026-10-17T03:59:00', 'not-a-time', '2099-01-01T00:00:00Z'])
def test_ovx_requires_a_real_aware_nonfuture_observation(at):
    with pytest.raises(FetchError):
        vol.ovx(Fetch({'data': {'current_price': 50, 'prev_day_close': 49, 'last_trade_time': at}}))


def test_ovx_overflowing_derived_change_is_rejected():
    with pytest.raises(FetchError, match='invalid_payload'):
        vol.ovx(Fetch({'data': {'current_price': 100, 'prev_day_close': 1e-308,
                               'last_trade_time': '2026-10-17T03:59:00Z'}}))


def eia_csv(current='415.577', week_ago='424.134', change='-8.557', date='10/9/26'):
    stream = io.StringIO()
    csv.writer(stream).writerows([
        ['STUB_1', date, '10/2/26', 'Difference', 'Percent Change', '10/10/25', 'Difference', 'Percent Change'],
        ['Commercial (Excluding SPR)', current, week_ago, change, '-2', '423.785', '-8.207', '-1.9']])
    return stream.getvalue().encode()


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', 'N/A', ''])
def test_eia_unavailable_numbers_are_partial_and_never_nonfinite(value):
    rec = eia.wpsr(Fetch(eia_csv(current=value)))
    assert rec.status == 'partial'
    assert rec.values['stocks']['commercial_crude_ex_spr']['current'] is None
    assert 'commercial_crude_ex_spr.numeric' in rec.values['missing']
    json.dumps(rec.to_dict(), allow_nan=False)


@pytest.mark.parametrize('kwargs', [{'current': '-1'}, {'change': '8.557'}, {'date': '9/1/26'}])
def test_eia_rejects_reversed_dates_or_inconsistent_stock_changes(kwargs):
    with pytest.raises(FetchError, match='invalid_payload'):
        eia.wpsr(Fetch(eia_csv(**kwargs)))


def test_unrelated_credentials_cannot_reach_the_child(monkeypatch, tmp_path):
    keys = ('FRED_API_KEY', 'TWELVEDATA_API_KEY', 'MYFXBOOK_USERNAME', 'MYFXBOOK_EMAIL',
            'MYFXBOOK_PASSWORD', 'SUPABASE_SERVICE_ROLE_KEY', 'SUPABASE_URL',
            'OP_SERVICE_ACCOUNT_TOKEN', 'OP_SYNTHETIC_TEST', 'PYTHONPATH', 'PYTHONHOME')
    for key in keys:
        monkeypatch.setenv(key, 'synthetic-canary')
    env = runner.isolated_environment(runner.Paths(root=tmp_path))
    assert not set(keys).intersection(env)
    assert env['BRAIN_PATH'] == str(tmp_path / 'disabled-brain')


def test_ai_auxiliaries_are_disabled_without_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    monkeypatch.setattr(runner.importlib.util, 'spec_from_file_location',
                        lambda *a, **k: pytest.fail('auxiliary module must not be loaded'))
    paths = runner.Paths(root=tmp_path)
    assert runner.entry_d1(paths, 'daily') == 'skipped'
    assert runner.entry_triage(paths, tmp_path) == {'mode': 'fallback', 'reason': 'auxiliary_disabled'}
    from usoil.headline_triage import load_triage
    assert load_triage(tmp_path, set()) == (None, {'mode':'fallback','reason':'auxiliary_disabled'})


@pytest.mark.parametrize('when', ['not-a-time', '2026-10-17T12:30:00'])
def test_malformed_relevant_calendar_row_prevents_complete_coverage(when):
    from usoil.facts import calendar_events
    rows=[{'country':'USD','title':'Crude Oil Inventories','impact':'Low',
           'date':'2026-10-14T10:30:00-04:00'},
          {'country':'USD','title':'CPI m/m','impact':'High','date':when}]
    rec=calendar.forexfactory_week(Fetch(rows))
    assert rec.status=='partial' and rec.values['parse']['rejected']==1
    _,coverage=calendar_events({'forexfactory_week':rec.to_dict()},datetime(2026,10,17,4,tzinfo=timezone.utc),{})
    assert coverage['next_24h']=='unknown'


@pytest.mark.parametrize('bad', [None, {}, {'country':'USD','title':'CPI m/m','date':'2026-10-17T12:00:00Z'}])
def test_unclassifiable_calendar_row_is_not_silently_ignored(bad):
    good={'country':'USD','title':'Crude Oil Inventories','impact':'Low','date':'2026-10-14T14:30:00Z'}
    rec=calendar.forexfactory_week(Fetch([good,bad]))
    assert rec.status=='partial' and rec.values['parse']['rejected']==1


def test_disabled_triage_replaces_a_symlink_without_writing_its_target(monkeypatch, tmp_path):
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    victim=tmp_path/'victim'
    victim.write_text('preserve')
    (tmp_path/'news-triage.json').symlink_to(victim)
    result=runner.entry_triage(runner.Paths(root=tmp_path),tmp_path)
    assert result=={'mode':'fallback','reason':'auxiliary_disabled'}
    assert victim.read_text()=='preserve' and not (tmp_path/'news-triage.json').is_symlink()


def test_disabled_triage_write_failure_stays_fixed_and_cleans_its_temp(monkeypatch, tmp_path):
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    (tmp_path/'news-triage.json').mkdir()
    assert runner.entry_triage(runner.Paths(root=tmp_path),tmp_path)=={'mode':'fallback','reason':'triage_write_failed'}
    assert not list(tmp_path.glob('.news-triage-*.tmp'))


def test_disabled_triage_refuses_a_symlinked_staging_directory(monkeypatch, tmp_path):
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    outside=tmp_path/'outside'
    outside.mkdir()
    target=outside/'news-triage.json'
    target.write_text('preserve')
    link=tmp_path/'stage-link'
    link.symlink_to(outside, target_is_directory=True)
    result=runner.entry_triage(runner.Paths(root=tmp_path),link)
    assert result=={'mode':'fallback','reason':'triage_write_failed'}
    assert target.read_text()=='preserve' and not list(outside.glob('.news-triage-*'))


def test_disabled_triage_refuses_a_symlink_in_the_work_ancestor(monkeypatch, tmp_path):
    monkeypatch.delenv('USOIL_ENABLE_AI_AUX', raising=False)
    root=tmp_path/'job'
    root.mkdir()
    outside=tmp_path/'outside'
    stage=outside/'stage'
    stage.mkdir(parents=True)
    target=stage/'news-triage.json'
    target.write_text('preserve')
    (root/'work').symlink_to(outside,target_is_directory=True)
    result=runner.entry_triage(runner.Paths(root=root),root/'work'/'stage')
    assert result=={'mode':'fallback','reason':'triage_write_failed'}
    assert target.read_text()=='preserve' and not list(stage.glob('.news-triage-*'))
