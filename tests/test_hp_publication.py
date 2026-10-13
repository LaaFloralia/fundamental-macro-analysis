from datetime import datetime, timedelta
import json
from pathlib import Path
import re

import pytest

from scripts import hp_publication as hp

ASOF = datetime.fromisoformat('2026-10-03T07:00:00+09:00')
NOW = datetime.fromisoformat('2026-10-13T12:00:00+09:00')


def fixture_edition(root, *, name='edition-a', as_of=ASOF, tiles='page'):
    folder = root / 'reports' / 'daily' / 'editions' / name
    folder.mkdir(parents=True)
    base = folder / 'Daily_Bias_Report'
    paths = {key: base.with_suffix(suffix) for key, suffix in {
        'html_path': '.html', 'md_path': '.md', 'bundle_path': '.bundle.json',
        'parent_review_path': '.parent-review.json', 'render_evidence_path': '.render.json',
        'json_path': '.machine.json', 'numeric_path': '.numeric.json',
        'summary_path': '.summary.json', 'visuals_path': '.figures.json'}.items()}
    paths['md_path'].write_text('# Original dated analysis\nLimitations retained.\n')
    html = ('<!doctype html><html><head><meta name="report-kind" content="daily">'
            f'<meta name="report-as-of" content="{as_of.isoformat()}">'
            f'<meta name="report-date" content="{as_of.date()}"></head>'
            '<body class="report"><a href="/reports">Reports</a><p>Original analysis</p></body></html>')
    paths['html_path'].write_text(html)
    for key in ('json_path', 'numeric_path', 'summary_path'):
        hp.write_json(paths[key], {})
    figures = [{'id': 'price', 'items': [{'label': 'quote', 'value': 3450}]}]
    hp.write_json(paths['visuals_path'], figures)
    data = base.with_suffix('.data.json')
    hp.write_json(data, {'timestamp': as_of.isoformat(), 'price': 3450})
    bundle = {'kind': 'daily', 'historicalRevision': False, 'asOf': as_of.isoformat(),
              'reportDate': as_of.date().isoformat(), 'missing': [], 'figureIds': ['price'],
              'requiredFigures': ['price'], 'bindings': [{'figure': 'price', 'label': 'quote',
                  'path': 'price', 'digits': 2, 'value': 3450}]}
    for label, path in [('source', paths['md_path']), ('data', data), ('summary', paths['summary_path']),
                         ('figures', paths['visuals_path'])]:
        bundle[label + 'Path'] = str(path)
        bundle[label + 'Sha256'] = hp.sha(path.read_bytes())
    hp.write_json(paths['bundle_path'], bundle)
    hp.write_json(base.with_suffix('.presentation.audit.json'), {
        'source_sha256': bundle['sourceSha256'], 'html_sha256': hp.sha(paths['html_path'].read_bytes()),
        'summary_sha256': bundle['summarySha256'], 'visuals_sha256': bundle['figuresSha256'],
        'figure_count': 1, 'numeric_check': 'pass'})
    images = []
    for name in ('desktop-overview.png', 'mobile-overview.png', 'desktop-figure-1.png',
                 'mobile-figure-1.png', f'desktop-{tiles}-01.png', f'mobile-{tiles}-01.png',
                 'mobile-table-right-1.png'):
        image = folder / 'render-evidence' / name
        image.parent.mkdir(exist_ok=True)
        image.write_bytes(('fixture ' + name).encode())
        images.append({'path': str(image), 'sha256': hp.sha(image.read_bytes())})
    hp.write_json(paths['render_evidence_path'], {'status': 'passed',
        'htmlSha256': hp.sha(paths['html_path'].read_bytes()),
        'bundleSha256': hp.sha(paths['bundle_path'].read_bytes()), 'images': images,
        'viewports': [{'width': 1365}, {'width': 390}]})
    hashes = {label: hp.sha(paths[key].read_bytes()) for key, label in (
        ('html_path', 'htmlSha256'), ('md_path', 'sourceSha256'), ('bundle_path', 'bundleSha256'),
        ('render_evidence_path', 'renderSha256'), ('json_path', 'machineSha256'), ('numeric_path', 'numericSha256'))}
    checks = {key: {'passed': True, 'evidence': 'fixture independently checked'} for key in hp.CHECKS}
    hp.write_json(paths['parent_review_path'], {**hashes, 'status': 'parent_passed',
        'independentProcess': False, 'issues': [], 'checks': checks})
    hp.write_json(base.with_suffix('.hp-review.json'), {**hashes, 'status': 'independent_passed',
        'independentProcess': True, 'reviewer': 'test-independent', 'reviewedAt': NOW.isoformat(),
        'issues': [], 'checks': checks, 'imagesReviewed': images})
    record = {'mode': 'daily', 'status': 'succeeded_local', 'ok': True,
              'generation_status': 'succeeded', 'review_status': 'parent_passed', 'historical_revision': False,
              'collected_at': as_of.isoformat(), 'generated_at': (as_of + timedelta(minutes=10)).isoformat(),
              'outputs': {key: str(path) for key, path in paths.items()}}
    edition = base.with_suffix('.edition.json')
    hp.write_json(edition, record)
    return edition


class MemoryStorage:
    def __init__(self):
        self.body = None
        self.writes = 0
        self.timeout_after_commit = False

    def get(self, kind):
        return self.body

    def put(self, kind, body):
        self.writes += 1
        self.body = body
        if self.timeout_after_commit:
            self.timeout_after_commit = False
            raise hp.PublicationError('ambiguous timeout')

    def reader(self, kind):
        return json.loads(self.body)['html'].encode()


def test_dated_latest_completed_passes_without_global_freshness_bypass(tmp_path):
    fixture_edition(tmp_path)
    result = hp.publish('daily', tmp_path, now=NOW)
    assert result['status'] == 'dry_run_validated'
    assert result['asOf'] == ASOF.isoformat()
    assert result['datePolicy'] == 'latest_completed'
    from scripts.report_bundle import require_fresh, BundleError
    with pytest.raises(BundleError):
        require_fresh({'timestamp': ASOF.isoformat()}, NOW)


def test_review_documents_use_only_hash_validated_bundle_paths(tmp_path):
    edition = fixture_edition(tmp_path)
    secret = tmp_path / 'outside-summary.json'
    secret.write_text('NEVER SEND OUTSIDE DATA')
    record = hp.read(edition)
    record['outputs']['summary_path'] = str(secret)
    hp.write_json(edition, record)
    candidate = hp.validate_edition(edition, tmp_path, NOW)
    assert candidate['reviewDocuments']['summary'] == '{}\n'
    assert 'NEVER SEND OUTSIDE DATA' not in json.dumps(candidate['reviewDocuments'])


def test_publish_one_object_with_reversible_banner_and_idempotence(tmp_path):
    edition = fixture_edition(tmp_path)
    store = MemoryStorage()
    result = hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    data = json.loads(store.body)
    original = Path(hp.read(edition)['outputs']['html_path']).read_text()
    assert result['storageVerified'] and result['hpVerified']
    assert data['sha256'] == hp.sha(data['html'].encode())
    assert data['sourceHtmlSha256'] == hp.sha(original.encode())
    assert re.sub(r'<!-- hp-latest-completed:start -->.*?<!-- hp-latest-completed:end -->', '', data['html']) == original
    assert '2026年10月03日 07:00 JST' in data['html']
    published_at = data['publishedAt']
    again = hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW + timedelta(minutes=1))
    assert again['status'] == 'unchanged' and store.writes == 1
    assert json.loads(store.body)['publishedAt'] == published_at


@pytest.mark.parametrize('target', ['html_path', 'md_path', 'bundle_path', 'json_path', 'numeric_path', 'render_evidence_path'])
def test_corrupt_newest_never_falls_back_or_writes(tmp_path, target):
    fixture_edition(tmp_path, name='old', as_of=ASOF - timedelta(days=1))
    newest = fixture_edition(tmp_path)
    Path(hp.read(newest)['outputs'][target]).write_text('changed bytes')
    store = MemoryStorage()
    with pytest.raises((hp.PublicationError, ValueError)):
        hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    assert store.writes == 0


@pytest.mark.parametrize('change', ['independentProcess', 'checks', 'imagesReviewed', 'reviewedAt'])
def test_incomplete_review_blocks_publish(tmp_path, change):
    edition = fixture_edition(tmp_path)
    review = edition.with_name('Daily_Bias_Report.hp-review.json')
    data = hp.read(review)
    data[change] = {'independentProcess': False, 'checks': {}, 'imagesReviewed': [],
                    'reviewedAt': (NOW + timedelta(days=1)).isoformat()}[change]
    hp.write_json(review, data)
    with pytest.raises(hp.PublicationError):
        hp.publish('daily', tmp_path, now=NOW)


def test_missing_right_edge_image_review_blocks_publish(tmp_path):
    edition = fixture_edition(tmp_path)
    review_path = edition.with_name('Daily_Bias_Report.hp-review.json')
    review = hp.read(review_path)
    review['imagesReviewed'] = [image for image in review['imagesReviewed'] if 'table-right' not in image['path']]
    hp.write_json(review_path, review)
    with pytest.raises(hp.PublicationError, match='visual inspection incomplete'):
        hp.publish('daily', tmp_path, now=NOW)


def test_page_tiles_or_legacy_source_tiles_are_required(tmp_path):
    fixture_edition(tmp_path, name='legacy', as_of=ASOF - timedelta(days=1), tiles='source')
    edition = fixture_edition(tmp_path)
    assert hp.publish('daily', tmp_path, now=NOW)['status'] == 'dry_run_validated'
    review_path = edition.with_name('Daily_Bias_Report.hp-review.json')
    review = hp.read(review_path)
    review['imagesReviewed'] = [image for image in review['imagesReviewed'] if 'mobile-page-' not in image['path']]
    hp.write_json(review_path, review)
    with pytest.raises(hp.PublicationError, match='visual inspection incomplete'):
        hp.publish('daily', tmp_path, now=NOW)


@pytest.mark.parametrize('offset', [-1, 7 * 60, 20 * 24 * 60])
def test_generation_must_have_been_fresh_and_not_future(tmp_path, offset):
    edition = fixture_edition(tmp_path)
    record = hp.read(edition)
    record['generated_at'] = (ASOF + timedelta(minutes=offset)).isoformat()
    hp.write_json(edition, record)
    with pytest.raises(hp.PublicationError, match='stale at generation'):
        hp.publish('daily', tmp_path, now=NOW)


def test_old_candidate_cannot_replace_newer_publication(tmp_path):
    fixture_edition(tmp_path)
    store = MemoryStorage()
    hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    value = json.loads(store.body)
    value['asOf'] = (ASOF + timedelta(days=1)).isoformat()
    store.body = json.dumps(value).encode()
    with pytest.raises(hp.PublicationError, match='Older report'):
        hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    assert store.writes == 1


def test_mutation_after_remote_read_rejected_before_put(tmp_path):
    edition = fixture_edition(tmp_path)
    store = MemoryStorage()
    def mutate(kind):
        Path(hp.read(edition)['outputs']['md_path']).write_text('mutated')
        return None
    store.get = mutate
    with pytest.raises(hp.PublicationError):
        hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    assert store.writes == 0


def test_ambiguous_timeout_reconciles_committed_bytes_without_reupload(tmp_path):
    fixture_edition(tmp_path)
    store = MemoryStorage()
    store.timeout_after_commit = True
    with pytest.raises(hp.PublicationError, match='ambiguous timeout'):
        hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    active = tmp_path / 'publication' / 'hp-latest' / 'active-daily.json'
    assert active.exists()
    assert 'html' not in hp.read(active)
    result = hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW + timedelta(minutes=1))
    assert result['status'] == 'unchanged' and store.writes == 1
    assert not active.exists()


def test_receipt_failure_leaves_reconcilable_intent(tmp_path, monkeypatch):
    fixture_edition(tmp_path)
    store = MemoryStorage()
    write = hp.write_json
    def fail(path, value):
        if Path(path).name == 'latest-daily.json':
            raise OSError('synthetic disk failure')
        write(path, value)
    monkeypatch.setattr(hp, 'write_json', fail)
    with pytest.raises(OSError):
        hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)
    assert (tmp_path / 'publication/hp-latest/active-daily.json').exists()
    monkeypatch.setattr(hp, 'write_json', write)
    assert hp.publish('daily', tmp_path, publish=True, storage=store, now=NOW)['status'] == 'unchanged'
    assert store.writes == 1


def test_cleanup_only_verified_old_success_html_preserves_md_and_evidence(tmp_path):
    old = fixture_edition(tmp_path, name='old', as_of=ASOF - timedelta(days=1))
    current = fixture_edition(tmp_path)
    failed = fixture_edition(tmp_path, name='failed', as_of=ASOF + timedelta(days=1))
    record = hp.read(failed)
    record['status'] = 'needs_attention'
    hp.write_json(failed, record)
    old_files = hp.read(old)['outputs']
    result = hp.cleanup_old_html('daily', tmp_path, apply=True)
    assert result['html'] == [old_files['html_path']]
    assert not Path(old_files['html_path']).exists()
    assert all(Path(path).exists() for key, path in old_files.items() if key != 'html_path')
    assert Path(hp.read(current)['outputs']['html_path']).exists()
    assert Path(record['outputs']['html_path']).exists()


def test_cleanup_protects_latest_attempt_reference_and_refuses_changed_source(tmp_path):
    old = fixture_edition(tmp_path, name='old', as_of=ASOF - timedelta(days=1))
    fixture_edition(tmp_path)
    record = hp.read(old)
    hp.write_json(tmp_path / 'latest-daily.json', record)
    assert hp.cleanup_old_html('daily', tmp_path, apply=True)['html'] == []
    (tmp_path / 'latest-daily.json').unlink()
    Path(record['outputs']['md_path']).write_text('unverified edited Markdown')
    with pytest.raises(hp.PublicationError, match='Cleanup refused'):
        hp.cleanup_old_html('daily', tmp_path, apply=True)
    assert Path(record['outputs']['html_path']).exists()


def test_cleanup_protects_pending_publication_edition(tmp_path):
    pending = fixture_edition(tmp_path, name='pending', as_of=ASOF - timedelta(days=1))
    fixture_edition(tmp_path)
    hp.write_json(tmp_path / 'publication/hp-latest/active-daily.json',
                  {'status': 'publishing', 'result': {'editionPath': str(pending)}})
    assert hp.cleanup_old_html('daily', tmp_path, apply=True)['html'] == []
    assert Path(hp.read(pending)['outputs']['html_path']).exists()


def test_hp_cache_convergence_is_bounded_without_real_sleep():
    store = MemoryStorage()
    responses = iter([b'previous', b'previous', b'expected'])
    store.reader = lambda kind: next(responses)
    delays = []
    hp.verify_reader(store, 'daily', b'expected', sleep=delays.append)
    assert delays == [10, 20]
    store.reader = lambda kind: b'previous'
    delays = []
    with pytest.raises(hp.PublicationError, match='cache convergence'):
        hp.verify_reader(store, 'daily', b'expected', sleep=delays.append)
    assert sum(delays) == 130 and max(delays) < 60


def test_public_readback_identifies_publisher_without_credentials(monkeypatch):
    from io import BytesIO
    observed = []
    def open_request(request, timeout):
        observed.append(request)
        return BytesIO(b'public response')
    monkeypatch.setattr(hp.urllib.request, 'urlopen', open_request)
    assert hp.R2Storage().get('daily') == b'public response'
    headers = dict(observed[0].header_items())
    assert headers['User-agent'] == 'LAA-Report-Publisher/1.0'
    assert headers['Cache-control'] == 'no-cache'
    assert 'Authorization' not in headers


def test_publication_rebuilds_the_figures_from_the_collection_before_upload(tmp_path, monkeypatch):
    from scripts.report_bundle import BundleError
    fixture_edition(tmp_path)
    seen = []
    monkeypatch.setattr(hp, 'check_regenerated', lambda data, bundle, figures, source, summary: seen.append((source, summary)))
    assert hp.publish('daily', tmp_path, now=NOW)['status'] == 'dry_run_validated'
    assert seen == [('# Original dated analysis\nLimitations retained.\n', {})]

    def differs(*args):
        raise BundleError('Figures differ from a rebuild of the collection')
    monkeypatch.setattr(hp, 'check_regenerated', differs)
    with pytest.raises(BundleError, match='Figures differ'):
        hp.publish('daily', tmp_path, now=NOW)
