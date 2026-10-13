from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from scripts import report_bundle as bundle
from scripts import report_acceptance as acceptance
from scripts import report_release as release
from scripts import upload_report

NOW = datetime.fromisoformat('2026-09-09T18:00:00+09:00')


def sample_data():
    return {'timestamp': NOW.isoformat(), 'report_quote': {'close': 3400.5, 'datetime': '2026-09-09'},
            'retail_sentiment': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 40, 'short_pct': 60, 'avg_long_entry': 3500,
                                          'avg_short_entry': 3300, 'timestamp': '2026-09-09T08:30:00Z'}},
            'gold_etf': {'tonnes': 980.0, 'as_of_date': '2026-09-08', 'change_5d_t': -3.5, 'change_20d_t': 17.8},
            'xau_levels': {'price': 3400.5, 'last_bar_utc': '2026-09-09 08:00', 'levels': [
                {'key': 'PDH', 'name': '前日高値（09/08）', 'side': 'high', 'price': 3420.0, 'state': 'resting',
                 'state_word': '未到達', 'touched_at': None, 'touched_session': None, 'note': ''},
                {'key': 'PDL', 'name': '前日安値（09/08）', 'side': 'low', 'price': 3380.0, 'state': 'swept',
                 'state_word': '掃引済み', 'touched_at': '09/09 10:00', 'touched_session': 'アジア', 'note': ''}]},
            'liquidity_levels': {'price': 3400.5, 'gvz': 23.0, 'gvz_as_of': '2026-09-08', 'expected_move_1sd': 49.3},
            'economic_calendar': {'events': [{'date': 'Thursday, September 10, 2026', 'time_jst': '21:30',
                                             'country': 'United States', 'indicator': 'PPI (MoM) (Aug)',
                                             'previous': '0.2%', 'forecast': '0.3%', 'actual': 'N/A'}]}}


def figure(result, ident):
    return next(f for f in result['figures'] if f['id'] == ident)


@pytest.mark.parametrize('stamp', ['2026-09-08T18:00:00+09:00', '2026-09-09T11:59:00+09:00', '2026-09-09T18:01:00+09:00', None])
def test_stale_future_missing_timestamp(stamp):
    data = sample_data(); data['timestamp'] = stamp
    with pytest.raises(bundle.BundleError):
        bundle.observations(data, NOW)


def test_missing_price_stops_generation():
    data = sample_data(); data['report_quote']['close'] = None
    with pytest.raises(bundle.BundleError, match='Numeric observation'):
        bundle.observations(data, NOW)


def test_all_figures_derive_from_named_fields():
    data = sample_data(); result = bundle.observations(data, NOW)
    assert result['missing'] == []
    assert [f['id'] for f in result['figures']] == list(bundle.REQUIRED_FIGURES) == ['bias', 'liquidity', 'calendar']
    bundle.check_bindings(data, result, result['figures'])
    next(i for i in figure(result, 'liquidity')['items'] if i['label'] == '前日高値（09/08）')['value'] = 3500  # another real price
    with pytest.raises(bundle.BundleError, match='does not match'):
        bundle.check_bindings(data, result, result['figures'])


def test_missing_figures_not_synthesized():
    data = sample_data(); del data['xau_levels']; data['economic_calendar'] = {'error': 'timeout'}
    result = bundle.observations(data, NOW)
    assert [entry.split(':')[0] for entry in result['missing']] == ['liquidity', 'calendar']
    assert [f['id'] for f in result['figures']] == ['bias']
    assert '欠測・除外: liquidity' in result['appendix']


def test_missing_retail_values_are_left_out_not_zeroed():
    data = sample_data(); data['retail_sentiment']['XAUUSD'].update(avg_long_entry=None, avg_short_entry=None, long_pct=None)
    result = bundle.observations(data, NOW)
    labels = [i['label'] for f in result['figures'] for i in f['items']]
    assert not any('平均建値' in label or '個人のロング比率' in label for label in labels)
    assert result['missing'] == []
    assert '欠測: 個人の比率（MyFXBook・ロング・ショート比率か百分位の値なし）。短期（当日〜1、2日）の集計に入れていない' in result['limitations']  # named, not zeroed
    bundle.check_bindings(data, result, result['figures'])


class MemoryStorage:
    def __init__(self):
        self.values = {'daily/latest.html': (b'old edition', 'text/plain'), 'daily/latest.json': (b'{"old":true}', 'application/json')}
        self.calls = []
        self.fail = None
        self.mismatch = None
        self.latest_writes = 0
    def get(self, path):
        self.calls.append(('get', path))
        if self.mismatch == 'version' and '/versions/' in path:
            return b'wrong', 'text/plain'
        if self.mismatch == 'metadata' and path == 'daily/latest.json' and self.latest_writes == 1:
            self.mismatch = None
            return b'old', 'application/json'
        return self.values.get(path)
    def put(self, path, body, mime, upsert=True):
        self.calls.append(('put', path))
        if self.fail == 'version' and '/versions/' in path:
            raise release.ReleaseError('synthetic upload failure')
        if path == 'daily/latest.html': self.latest_writes += 1
        self.values[path] = (body, mime.split(';')[0])
        if self.fail == 'latest-timeout' and self.latest_writes == 1:
            raise release.ReleaseError('synthetic timeout after commit')
    def reader(self, kind):
        self.calls.append(('reader', kind))
        body, _ = self.values[f'{kind}/latest.html']
        if self.mismatch == 'hp' and self.latest_writes == 1:
            return b'old cached edition', 'text/html'
        return body, 'text/html'
    def delete(self, path):
        self.values.pop(path, None)


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    b = {'kind': 'daily', 'reportDate': '2026-09-09', 'asOf': NOW.isoformat()}
    # Transaction tests isolate acceptance; acceptance itself has adversarial tests below.
    monkeypatch.setattr(release, 'publication_inputs', lambda *args: (b, b'new edition', {'acceptanceSha256': 'a'*64}))
    return (tmp_path/'report.html', tmp_path/'report.bundle.json', tmp_path/'report.acceptance.json', tmp_path/'publication')


def test_dry_run_has_zero_network(inputs):
    store = MemoryStorage()
    result = release.release(*inputs, transport=store, now=NOW)
    assert result['publication'] == 'dry_run_validated'
    assert result['generation'] == 'succeeded'
    assert store.calls == []
    assert 'publishedAt' in result['metadata']
    assert not (inputs[3]/'last-published-daily.json').exists()


@pytest.fixture
def reviewed_inputs(tmp_path, monkeypatch):
    # Exercise the real acceptance/release boundary. Semantic bundle validation
    # is covered separately; these fixtures isolate mutations after it returns.
    monkeypatch.setattr(acceptance, 'validate_bundle', lambda *args, **kwargs: {})
    html = tmp_path/'report.html'; html.write_bytes(b'reviewed edition')
    manifest = tmp_path/'report.bundle.json'
    manifest.write_text(json.dumps({'kind':'daily', 'reportDate':'2026-09-09', 'asOf':NOW.isoformat()}))
    image = tmp_path/'image.png'; image.write_bytes(b'reviewed image')
    render = html.with_suffix('.render.json')
    render.write_text(json.dumps({'status':'passed', 'htmlSha256':bundle.digest(html),
        'bundleSha256':bundle.digest(manifest), 'images':[{'path':str(image),'sha256':bundle.digest(image)}],
        'viewports':[{'width':1365},{'width':390}]}))
    review = html.with_suffix('.acceptance.json')
    review.write_text(json.dumps({'status':'passed', 'reviewer':'independent-test', 'reviewedAt':NOW.isoformat(),
        'checks':{k:True for k in acceptance.CHECKS}, 'htmlSha256':bundle.digest(html),
        'bundleSha256':bundle.digest(manifest), 'renderSha256':bundle.digest(render)}))
    return html, manifest, review, tmp_path/'publication'


@pytest.mark.parametrize('target', ['html', 'bundle', 'review'])
def test_mutation_after_acceptance_is_rejected_before_storage_creation(reviewed_inputs, monkeypatch, target):
    validate = acceptance.validate_acceptance
    html, manifest, review, directory = reviewed_inputs
    def replace_after_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        if target == 'html':
            html.write_bytes(b'unreviewed replacement')
        elif target == 'bundle':
            value = json.loads(manifest.read_text())
            value.update(asOf='1999-01-01T00:00:00+09:00', reportDate='1999-01-01')
            manifest.write_text(json.dumps(value))
        else:
            review.write_text('{"status":"changes_requested"}')
        return result
    monkeypatch.setattr(acceptance, 'validate_acceptance', replace_after_validation)
    store = MemoryStorage(); created = []
    def storage():
        created.append(True)
        return store
    monkeypatch.setattr(release, 'Storage', storage)
    with pytest.raises(release.ReleaseError, match='changed after acceptance'):
        release.release(*reviewed_inputs, publish=True, now=NOW)
    assert created == [] and store.calls == []
    assert not (directory/'last-published-daily.json').exists()


def test_publication_uses_frozen_accepted_bytes_after_input_capture(reviewed_inputs, monkeypatch):
    html, manifest, _, _ = reviewed_inputs
    accepted_body = html.read_bytes()
    accepted_bundle = json.loads(manifest.read_bytes())
    store = MemoryStorage()
    def storage():
        html.write_bytes(b'later unreviewed replacement')
        manifest.write_text(json.dumps({'kind':'weekly','reportDate':'1999-01-01','asOf':'1999-01-01T00:00:00+09:00'}))
        return store
    monkeypatch.setattr(release, 'Storage', storage)
    result = release.release(*reviewed_inputs, publish=True, now=NOW)
    assert result['publication'] == 'succeeded'
    assert store.values['daily/latest.html'][0] == accepted_body
    metadata = json.loads(store.values['daily/latest.json'][0])
    assert metadata['asOf'] == accepted_bundle['asOf']
    assert metadata['reportDate'] == accepted_bundle['reportDate']


def test_publish_version_before_latest_and_verified_receipt(inputs):
    store = MemoryStorage()
    result = release.release(*inputs, publish=True, transport=store, now=NOW)
    assert result['publication'] == 'succeeded'
    assert store.calls[2][0] == 'put' and '/versions/' in store.calls[2][1]
    assert store.calls[3][0] == 'get' and '/versions/' in store.calls[3][1]
    assert store.values['daily/latest.html'][0] == b'new edition'
    m = json.loads(store.values['daily/latest.json'][0])
    assert m['sha256'] == release.sha(b'new edition')
    assert m['asOf'] == NOW.isoformat() and m['url'].endswith('/daily')
    assert (inputs[3]/'last-published-daily.json').exists()


@pytest.mark.parametrize('failure,mismatch', [('version', None), (None, 'version'), ('latest-timeout', None), (None, 'hp'), (None, 'metadata')])
def test_failures_preserve_or_restore_old_edition(inputs, failure, mismatch):
    store = MemoryStorage(); before = deepcopy(store.values)
    store.fail, store.mismatch = failure, mismatch
    with pytest.raises(release.ReleaseError, match='Publication failed'):
        release.release(*inputs, publish=True, transport=store, now=NOW)
    for key, value in before.items():
        assert store.values[key][0] == value[0]
    assert not (inputs[3]/'last-published-daily.json').exists()
    receipt = json.loads((inputs[3]/'active-publication.json').read_text())
    assert receipt['publication'] == 'failed'


def test_duplicate_publication_rejected(inputs):
    with release.release_lock(inputs[3]):
        with pytest.raises(release.ReleaseError, match='already|active'):
            release.release(*inputs, now=NOW)


def test_unresolved_crash_blocks_new_publish(inputs):
    inputs[3].mkdir()
    release.write_json(inputs[3]/'active-publication.json', {'publication': 'publishing'})
    with pytest.raises(release.ReleaseError, match='Unresolved'):
        release.release(*inputs, publish=True, transport=MemoryStorage(), now=NOW)


def test_wrong_mime_and_hash_rejected():
    for response in [(b'ok','application/octet-stream'), (b'wrong','text/html')]:
        with pytest.raises(release.ReleaseError):
            release.verify(response, b'ok', 'HP', html=True, site=True)


def test_cli_returns_failure_on_missing_or_unreviewed_html(tmp_path):
    assert upload_report.main([str(tmp_path/'no.html'), '--mode', 'daily']) == 1


def test_crash_recovery_restores_both_objects(inputs):
    store = MemoryStorage()
    directory = inputs[3]; backup = directory/'interrupted'; backup.mkdir(parents=True)
    (backup/'previous.html').write_bytes(b'previous edition')
    (backup/'previous.json').write_bytes(b'{"old":1}')
    release.write_json(directory/'active-publication.json', {
        'publication': 'publishing', 'kind': 'daily', 'backupDirectory': str(backup),
        'hadPreviousHtml': True, 'hadPreviousMetadata': True})
    result = release.recover(directory, transport=store)
    assert result['rollback'] == 'verified'
    assert store.values['daily/latest.html'][0] == b'previous edition'
    assert store.values['daily/latest.json'][0] == b'{"old":1}'


@pytest.mark.parametrize('had_previous_record', [False, True])
def test_success_marker_crash_recovery_allows_the_next_publish(inputs, monkeypatch, had_previous_record):
    class SimulatedProcessExit(BaseException):
        pass
    store = MemoryStorage()
    before = deepcopy(store.values)
    known_path = inputs[3] / 'last-published-daily.json'
    if had_previous_record:
        release.write_json(known_path, {'publication': 'succeeded', 'attemptId': 'previous-attempt',
                                      'metadata': {'sha256': release.sha(before['daily/latest.html'][0])}})
    previous_record = known_path.read_bytes() if known_path.exists() else None
    original_write = release.write_json
    interrupted = False

    def crash_after_success_marker(path, value):
        nonlocal interrupted
        original_write(path, value)
        if Path(path) == known_path and value.get('publication') == 'succeeded' and not interrupted:
            interrupted = True
            raise SimulatedProcessExit()

    monkeypatch.setattr(release, 'write_json', crash_after_success_marker)
    with pytest.raises(SimulatedProcessExit):
        release.release(*inputs, publish=True, transport=store, now=NOW)
    assert json.loads(known_path.read_text())['metadata']['sha256'] == release.sha(b'new edition')
    assert json.loads((inputs[3]/'active-publication.json').read_text())['publication'] == 'publishing'
    monkeypatch.setattr(release, 'write_json', original_write)
    assert release.recover(inputs[3], transport=store)['rollback'] == 'verified'
    for key, value in before.items():
        assert store.values[key][0] == value[0]
    if previous_record is None:
        assert not known_path.exists()
    else:
        assert known_path.read_bytes() == previous_record
    assert release.release(*inputs, publish=True, transport=store, now=NOW)['publication'] == 'succeeded'


def test_success_marker_write_error_rolls_back_all_three_records(inputs, monkeypatch):
    store = MemoryStorage()
    before = deepcopy(store.values)
    known_path = inputs[3] / 'last-published-daily.json'
    release.write_json(known_path, {'publication': 'succeeded',
                                  'metadata': {'sha256': release.sha(before['daily/latest.html'][0])}})
    previous_record = known_path.read_bytes()
    original_write = release.write_json
    interrupted = False

    def fail_after_success_marker(path, value):
        nonlocal interrupted
        original_write(path, value)
        if Path(path) == known_path and value.get('publication') == 'succeeded' and not interrupted:
            interrupted = True
            raise OSError('synthetic marker write failure')

    monkeypatch.setattr(release, 'write_json', fail_after_success_marker)
    with pytest.raises(release.ReleaseError, match='rollback=verified'):
        release.release(*inputs, publish=True, transport=store, now=NOW)
    assert known_path.read_bytes() == previous_record
    for key, value in before.items():
        assert store.values[key][0] == value[0]
    monkeypatch.setattr(release, 'write_json', original_write)
    assert release.release(*inputs, publish=True, transport=store, now=NOW)['publication'] == 'succeeded'


@pytest.mark.parametrize('final_record', ['receipt.json', 'active-publication.json'])
def test_final_receipt_save_error_rolls_back_and_allows_next_publish(inputs, monkeypatch, final_record):
    store = MemoryStorage(); before = deepcopy(store.values)
    known_path = inputs[3]/'last-published-daily.json'
    release.write_json(known_path, {'publication':'succeeded',
                                  'metadata':{'sha256':release.sha(before['daily/latest.html'][0])}})
    previous_marker = known_path.read_bytes()
    write = release.write_json
    failed_once = False
    def fail_final_record(path, value):
        nonlocal failed_once
        if Path(path).name == final_record and value.get('publication') == 'succeeded' and not failed_once:
            failed_once = True
            raise OSError('synthetic final receipt save failure')
        write(path, value)
    monkeypatch.setattr(release, 'write_json', fail_final_record)
    with pytest.raises(release.ReleaseError, match='rollback=verified'):
        release.release(*inputs, publish=True, transport=store, now=NOW)
    for key, value in before.items():
        assert store.values[key][0] == value[0]
    assert known_path.read_bytes() == previous_marker
    active = json.loads((inputs[3]/'active-publication.json').read_bytes())
    assert active['publication'] == 'failed' and active['rollback'] == 'verified'
    assert json.loads((Path(active['backupDirectory'])/'receipt.json').read_bytes())['publication'] == 'failed'
    monkeypatch.setattr(release, 'write_json', write)
    assert release.release(*inputs, publish=True, transport=store, now=NOW)['publication'] == 'succeeded'


@pytest.mark.parametrize('after_save', [False, True])
def test_interrupt_around_final_receipt_save_remains_recoverable(inputs, monkeypatch, after_save):
    class SimulatedProcessExit(BaseException):
        pass
    store = MemoryStorage(); before = deepcopy(store.values)
    known_path = inputs[3]/'last-published-daily.json'
    release.write_json(known_path, {'publication':'succeeded',
                                  'metadata':{'sha256':release.sha(before['daily/latest.html'][0])}})
    previous_marker = known_path.read_bytes()
    write = release.write_json
    def interrupt_final_receipt(path, value):
        if Path(path).name == 'receipt.json' and value.get('publication') == 'succeeded':
            if after_save:
                write(path, value)
            raise SimulatedProcessExit()
        write(path, value)
    monkeypatch.setattr(release, 'write_json', interrupt_final_receipt)
    with pytest.raises(SimulatedProcessExit):
        release.release(*inputs, publish=True, transport=store, now=NOW)
    active = json.loads((inputs[3]/'active-publication.json').read_bytes())
    assert active['publication'] == 'publishing'
    assert store.values['daily/latest.html'][0] == b'new edition'
    monkeypatch.setattr(release, 'write_json', write)
    assert release.recover(inputs[3], transport=store)['rollback'] == 'verified'
    for key, value in before.items():
        assert store.values[key][0] == value[0]
    assert known_path.read_bytes() == previous_marker
    assert release.release(*inputs, publish=True, transport=store, now=NOW)['publication'] == 'succeeded'


def test_wrong_mode_stops_before_network(inputs, tmp_path, monkeypatch):
    p=tmp_path/'report.html';p.write_text('html')
    p.with_suffix('.bundle.json').write_text(json.dumps({'kind':'weekly'}))
    monkeypatch.setattr(upload_report, 'release', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not release')))
    assert upload_report.upload(p,'daily',publish=True) is None


def test_liquidity_map_uses_named_levels_and_ignores_saved_round_numbers():
    data = sample_data()
    data['liquidity_levels']['levels'] = [{'level': 3400.0, 'major': True, 'distance': -0.5, 'distance_pct': -0.01}]
    result = bundle.observations(data, NOW)
    liquidity = figure(result, 'liquidity')
    assert [i['label'] for i in liquidity['items']] == ['前日高値（09/08）', '現在値', '前日安値（09/08）']
    assert [i['label'] for i in liquidity['aux']] == ['個人ロングの平均建値', '個人ショートの平均建値']
    assert 'キリ' not in json.dumps(result, ensure_ascii=False) and '3,400.00' not in str(liquidity['band'])
    assert liquidity['band'] == {'lower': 3351.2, 'upper': 3449.8, 'label': '想定変動 ±1σ（1日・±49.3ドル）'}
    paths = {b['label']: b['path'] for b in result['bindings'] if b['figure'] == 'liquidity'}
    assert paths['前日安値（09/08）'] == 'xau_levels.levels.1.price' and paths['現在値'] == 'report_quote.close'
    bundle.check_bindings(data, result, result['figures'])
    data['xau_levels']['levels'][1]['price'] = 3381.0  # a changed source value must no longer match the figure
    with pytest.raises(bundle.BundleError, match='does not match'):
        bundle.check_bindings(data, result, result['figures'])


def test_average_entries_carry_their_own_provider_and_time():
    result = bundle.observations(sample_data(), NOW)
    liquidity = figure(result, 'liquidity')
    aux = {i['label']: i for i in liquidity['aux']}
    assert aux['個人ロングの平均建値']['state_word'] == aux['個人ショートの平均建値']['state_word'] == 'MyFXBook・09/09 17:30 JST'
    assert next(i for i in liquidity['items'] if i['label'] == '現在値')['state_word'] == '取得時刻の記録なし'  # not the collection time


def test_in_progress_edition_stays_fresh_across_midnight_without_relabeling():
    data=sample_data();data['timestamp']='2026-09-09T23:45:00+09:00'
    stamp=bundle.require_fresh(data,datetime.fromisoformat('2026-09-10T00:05:00+09:00'))
    assert stamp.date().isoformat()=='2026-09-09'


def test_next_24h_calendar_coverage_keeps_ecb_and_jobless_claims():
    data=sample_data();start='2026-09-09T23:39:00+09:00'
    data['economic_calendar']['events'] += [
        {'date':'Thursday, September 10, 2026','time_jst':'21:15','country':'Euro Zone','indicator':'ECB Interest Rate Decision'},
        {'date':'Thursday, September 10, 2026','time_jst':'21:30','country':'United States','indicator':'Initial Jobless Claims'},
        {'date':'Friday, September 11, 2026','time_jst':'21:30','country':'United States','indicator':'CPI'}]
    result=bundle.calendar_events_24h(data,start)
    assert len(result)==3 and any('ECB' in e for e in result) and any('Initial Jobless' in e for e in result)
    assert not any('CPI' in e for e in result)


def test_etf_row_keeps_the_20_day_context_next_to_the_5_day_vote():
    result = bundle.observations(sample_data(), NOW)
    etf = next(i for i in figure(result, 'bias')['items'] if i['label'] == 'GLD保有量（5営業日）')
    assert etf['symbol'] == '▼' and etf['note'] == '棒は左から5営業日・20営業日。20営業日 +17.80 t'
    assert [b['value'] for b in etf['viz']['items']] == [-3.5, 17.8]


SUMMARY_SOURCE = '''# Report
## セクション0: エグゼクティブサマリー

信頼度: Low
時間軸別の向き: 長期 下向き・弱｜短期 中立｜総合 下向き・弱
XAUUSD：長期は下向き・弱。短期は中立で、9/10 21:30 JSTの米PPIが分岐。
反証・無効化条件：米2年債が−10bp以上低下すれば見直す。
判断から除外する材料：注文板の集計。

## セクション1: 今夜の執行プラン
### 1-1. プランA（本命）

D1・H1構造が取得不可のため、方向は未設定。
'''


def test_summary_quotes_the_xauusd_line_and_leaves_plans_and_conditions_in_the_markdown():
    summary = bundle.make_summary(SUMMARY_SOURCE, 'daily', NOW.isoformat(), [{'text': 'chip', 'source_quote': 'chip'}])
    assert summary['conclusion']['text'] == 'XAUUSD：長期は下向き・弱。短期は中立で、9/10 21:30 JSTの米PPIが分岐。'
    assert summary['conclusion']['source_quote'] == summary['conclusion']['text']
    assert summary['as_of_label'] == '09/09（水）18:00 JST 収集' and summary['chips'][0]['text'] == 'chip'
    assert 'conditions' not in summary and 'metrics' not in summary
    assert '反証' not in json.dumps(summary, ensure_ascii=False) and 'プランA' not in json.dumps(summary, ensure_ascii=False)
    line = 'XAUUSD：長期は下向き・弱。短期は中立で、9/10 21:30 JSTの米PPIが分岐。'
    exact = 'XAUUSD：' + 'あ' * (bundle.CONCLUSION_MAX - len('XAUUSD：'))
    assert bundle.make_summary(SUMMARY_SOURCE.replace(line, exact), 'daily', NOW.isoformat())['conclusion']['text'] == exact
    with pytest.raises(bundle.BundleError, match='over 220 characters'):
        bundle.make_summary(SUMMARY_SOURCE.replace(line, exact + 'あ'), 'daily', NOW.isoformat())
    with pytest.raises(bundle.BundleError, match='no source paragraph'):
        bundle.make_summary('# Report\n## セクション0: エグゼクティブサマリー\n\n| 表 |\n', 'daily', NOW.isoformat())


def test_weekly_cover_quotes_the_complete_xau_decision_line():
    lead = 'XAUUSDはマクロ参考Bullish・確度低、執行保留。方向・価格水準は未設定。最大リスクはFOMC前後の再評価。'
    source = ('# Weekly\n## セクション0: エグゼクティブサマリー\n\n'
              '信頼度: Low ｜ スコア -2\n'
              '臨時検証版。今週は未終了。\n'
              'DXYはNeutral。\n'
              '来週最重要はFOMC。\n' + lead + '\n'
              '前回Weeklyの比較は照合不能。\n')
    result = bundle.make_summary(source, 'weekly', NOW.isoformat())
    assert result['conclusion']['text'] == lead
    assert result['conclusion']['source_quote'] == lead and lead in source


def test_plain_cover_labels_hide_markdown_link_syntax_and_preserve_source_url():
    source = ('# Weekly\n## セクション0: エグゼクティブサマリー\n\n'
              '来週のFOMC結果は9/17 03:00 JST。[Fed日程](https://www.federalreserve.gov/newsevents/2026-september.htm)で確認。\n')
    result = bundle.make_summary(source, 'weekly', NOW.isoformat())['conclusion']
    assert result['text'] == '来週のFOMC結果は9/17 03:00 JST。Fed日程で確認。'
    assert 'https://www.federalreserve.gov/newsevents/2026-september.htm' in result['source_quote']
    assert result['source_quote'] in source
