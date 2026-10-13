"""Publish the latest independently reviewed completed XAUUSD edition to HP.

The document keeps its original as-of date. This is not a fresh-market release:
freshness is checked at original generation, while review/publication use real time.
Only one atomic JSON object per kind is stored remotely. Markdown remains in editions.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import html as html_escape
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from scripts.report_acceptance import CHECKS
from scripts.report_bundle import check_bindings, check_regenerated, timestamp as collection_timestamp

ROOT = Path('/Users/laa/.codex/jobs/chart-intel')
PUBLIC_BASE = 'https://reports-data.laa-inc.com'
BUCKET = 'laa-hp-media'
SITE_BASE = 'https://www.laa-inc.com/reports'


class PublicationError(ValueError):
    pass


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError):
        raise PublicationError('Invalid timestamp') from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def kind_check(kind):
    if kind not in ('daily', 'weekly'):
        raise PublicationError('Only XAUUSD daily/weekly are supported')


def local_file(path, directory, limit=4_000_000):
    path, directory = Path(path), Path(directory).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(directory):
        raise PublicationError('Edition path escapes its directory')
    if not path.is_file() or path.stat().st_size > limit:
        raise PublicationError('Edition file missing or too large')
    return path


def completed(record, kind):
    return (record.get('mode') == kind and record.get('symbol', 'XAUUSD') == 'XAUUSD'
            and record.get('status') == 'succeeded_local' and record.get('ok') is True
            and record.get('generation_status') == 'succeeded'
            and record.get('review_status') == 'parent_passed'
            and record.get('historical_revision') is False)


def latest_completed(kind, root=ROOT):
    kind_check(kind)
    base = Path(root) / 'reports' / kind / 'editions'
    candidates = []
    for path in base.glob('*/*.edition.json'):
        local_file(path, base)
        record = read(path)
        if completed(record, kind):
            candidates.append((stamp(record.get('generated_at')), str(path), path))
    if not candidates:
        raise PublicationError('No completed edition')
    # Validate the newest selected edition; a broken newest edition must not hide
    # behind an apparently successful upload of an older one.
    return max(candidates)[2]


def validate_edition(edition_path, root=ROOT, now=None):
    current = now or datetime.now(timezone.utc)
    path = Path(edition_path)
    record = read(path)
    kind = record.get('mode')
    kind_check(kind)
    base = Path(root) / 'reports' / kind / 'editions'
    local_file(path, base)
    if not completed(record, kind):
        raise PublicationError('Edition is not completed and parent accepted')
    folder = path.parent
    outputs = record['outputs']
    keys = ('html_path', 'md_path', 'bundle_path', 'parent_review_path',
            'render_evidence_path', 'json_path', 'numeric_path')
    files = {key: local_file(outputs[key], folder) for key in keys}
    if any(file.parent.resolve() != folder.resolve() for file in files.values()):
        raise PublicationError('Mixed edition outputs')
    frozen = {key: file.read_bytes() for key, file in files.items()}
    hashes = {label: sha(frozen[key]) for key, label in (
        ('html_path', 'htmlSha256'), ('md_path', 'sourceSha256'),
        ('bundle_path', 'bundleSha256'), ('render_evidence_path', 'renderSha256'),
        ('json_path', 'machineSha256'), ('numeric_path', 'numericSha256'))}
    parent = json.loads(frozen['parent_review_path'])
    if (parent.get('status') != 'parent_passed' or parent.get('independentProcess') is not False
            or parent.get('issues') != []):
        raise PublicationError('Parent acceptance missing')
    for key, value in hashes.items():
        if key != 'sourceSha256' and parent.get(key) != value:
            raise PublicationError('Parent acceptance hash mismatch')
    if any(parent.get('checks', {}).get(key, {}).get('passed') is not True for key in CHECKS):
        raise PublicationError('Parent checks incomplete')
    bundle = json.loads(frozen['bundle_path'])
    collected, generated = stamp(record.get('collected_at')), stamp(record.get('generated_at'))
    if (collected > generated or generated > current or generated - collected > timedelta(hours=6)):
        raise PublicationError('Collection was stale at generation or future-dated')
    if (bundle.get('kind') != kind or bundle.get('historicalRevision') is True
            or bundle.get('synthetic') is True or stamp(bundle.get('asOf')) != collected
            or bundle.get('reportDate') != collected.date().isoformat()):
        raise PublicationError('Bundle edition mismatch')
    sidecars = {}
    for key in ('data', 'source', 'summary', 'figures'):
        item = local_file(bundle[f'{key}Path'], folder)
        value = item.read_bytes()
        if sha(value) != bundle[f'{key}Sha256']:
            raise PublicationError('Bundle source hash mismatch')
        sidecars[key] = value
    if bundle['sourceSha256'] != hashes['sourceSha256']:
        raise PublicationError('Markdown source mismatch')
    data, figures = json.loads(sidecars['data']), json.loads(sidecars['figures'])
    # The existing collector stores naive timestamps in JST; retain that exact
    # established data contract. Publication/review metadata remain timezone-aware.
    if collection_timestamp(data.get('timestamp')) != collected:
        raise PublicationError('Collection timestamp mismatch')
    if bundle.get('missing') or set(bundle['figureIds']) != set(bundle['requiredFigures']):
        raise PublicationError('Required figures missing')
    check_bindings(data, bundle, figures)
    check_regenerated(data, bundle, figures, sidecars['source'].decode('utf-8'), json.loads(sidecars['summary']))
    html_file = files['html_path']
    audit = read(local_file(html_file.with_suffix('.presentation.audit.json'), folder))
    if (audit.get('source_sha256') != hashes['sourceSha256']
            or audit.get('html_sha256') != hashes['htmlSha256']
            or audit.get('summary_sha256') != bundle['summarySha256']
            or audit.get('visuals_sha256') != bundle['figuresSha256']
            or audit.get('figure_count') != len(figures) or audit.get('numeric_check') != 'pass'):
        raise PublicationError('Presentation bindings mismatch')
    if bundle.get('semanticAuditRequired') is True:
        audit_file = local_file(html_file.with_suffix('.semantic-audit.json'), folder)
        semantic_bytes = audit_file.read_bytes()
        semantic_hash = sha(semantic_bytes)
        if (semantic_hash != bundle.get('semanticAuditSha256')
                or parent.get('semanticAudit', {}).get('sha256') != semantic_hash
                or parent.get('semanticAudit', {}).get('reviewed') is not True
                or any(item.get('result') == 'deterministic_error' for item in json.loads(semantic_bytes).get('pairs', []))):
            raise PublicationError('Semantic evidence mismatch')
        sidecars['semanticAudit'] = semantic_bytes
    html = frozen['html_path'].decode('utf-8')
    if re.search(r'<\s*(script|iframe|object|embed|form)\b|\son\w+\s*=|(?:href|src)\s*=\s*[\"\x27]?\s*javascript:', html, re.I):
        raise PublicationError('Active HTML prohibited')
    for name, value in [('report-kind', kind), ('report-as-of', bundle['asOf']), ('report-date', bundle['reportDate'])]:
        if f'<meta name="{name}" content="{value}">' not in html:
            raise PublicationError('HTML edition metadata missing')
    if 'href="/reports"' not in html:
        raise PublicationError('Report return link missing')
    render = json.loads(frozen['render_evidence_path'])
    if (render.get('status') != 'passed' or render.get('htmlSha256') != hashes['htmlSha256']
            or render.get('bundleSha256') != hashes['bundleSha256']
            or {v['width'] for v in render.get('viewports', [])} != {1365, 390}):
        raise PublicationError('Browser evidence mismatch')
    images = render.get('images', [])
    if not images or len({item['path'] for item in images}) != len(images):
        raise PublicationError('Browser images missing or repeated')
    for item in images:
        if sha(local_file(item['path'], folder, 30_000_000).read_bytes()) != item['sha256']:
            raise PublicationError('Browser image hash mismatch')
    review_documents = {key: value.decode('utf-8') for key, value in sidecars.items()}
    review_documents.update({key: value.decode('utf-8') for key, value in frozen.items()
                             if key != 'html_path'})
    return {'editionPath': str(path), 'edition': folder.name, 'record': record,
            'bundle': bundle, 'hashes': hashes, 'html': html, 'images': images,
            'reviewPath': str(html_file.with_suffix('.hp-review.json')),
            'reviewDocuments': review_documents}


def validate_review(candidate, review_path=None, now=None):
    current = now or datetime.now(timezone.utc)
    folder = Path(candidate['editionPath']).parent
    path = local_file(review_path or candidate['reviewPath'], folder)
    review = read(path)
    if (review.get('status') not in ('passed', 'independent_passed')
            or review.get('independentProcess') is not True
            or not review.get('reviewer') or review.get('reviewer') == 'parent-ciel'
            or review.get('issues') != []):
        raise PublicationError('Independent acceptance required')
    reviewed_at = stamp(review.get('reviewedAt'))
    if not stamp(candidate['record']['generated_at']) <= reviewed_at <= current:
        raise PublicationError('Invalid independent review time')
    if any(review.get(key) != value for key, value in candidate['hashes'].items()):
        raise PublicationError('Independent review hash mismatch')
    checks = review.get('checks', {})
    if set(checks) != set(CHECKS) or any(not isinstance(item, dict) or item.get('passed') is not True
            or not isinstance(item.get('evidence'), str) or not item['evidence'].strip() for item in checks.values()):
        raise PublicationError('Independent checks incomplete')
    expected = {item['path']: item['sha256'] for item in candidate['images']}
    inspected = review.get('imagesReviewed', [])
    if not inspected or len({item['path'] for item in inspected}) != len(inspected):
        raise PublicationError('Inspected image evidence missing or repeated')
    if any(expected.get(item['path']) != item.get('sha256') for item in inspected):
        raise PublicationError('Inspected images not bound to browser evidence')
    names = {Path(item['path']).name for item in inspected}
    # Every capture is required, the explanations-open tiles included; the single tall '-full.png'
    # of editions before 2026-10-14 stays optional.
    required = {Path(path).name for path in expected if not Path(path).name.endswith('-full.png')}
    # Page tiles of the one-page dashboard; source tiles in editions before 2026-10-13.
    tiles = lambda view: any(name.startswith((f'{view}-page-', f'{view}-source-')) for name in names)
    if (not required <= names or not {'desktop-overview.png', 'mobile-overview.png'} <= names
            or not tiles('desktop') or not tiles('mobile')):
        raise PublicationError('Independent visual inspection incomplete')
    return review


def display_html(source, as_of):
    """A reversible static dating notice; original reviewed HTML stays intact."""
    date = stamp(as_of).astimezone(ZoneInfo('Asia/Tokyo')).strftime('%Y年%m月%d日 %H:%M JST')
    banner = ('<!-- hp-latest-completed:start -->'
              '<aside aria-label="資料の基準日時" style="box-sizing:border-box;margin:0;'
              'padding:14px 20px;border-bottom:1px solid #ccc;background:#f5f2eb;'
              'color:#222;font:14px/1.7 sans-serif;text-align:center;overflow-wrap:anywhere">'
              '<strong>最新の完成版 · 資料基準 ' + html_escape.escape(date) + '</strong><br>'
              'この資料は記載日時の分析です。次版の確認が完了すると更新します。'
              '</aside><!-- hp-latest-completed:end -->')
    if '<!-- hp-latest-completed:start -->' in source:
        raise PublicationError('Source already includes publication banner')
    match = re.search(r'<body\b[^>]*>', source, re.I)
    if not match:
        raise PublicationError('HTML body missing')
    return source[:match.end()] + banner + source[match.end():]


class R2Storage:
    def __init__(self, public_base=PUBLIC_BASE):
        if not re.fullmatch(r'https://[a-z0-9.-]+(?::[0-9]+)?', public_base):
            raise PublicationError('Invalid public origin')
        self.public_base = public_base

    def request(self, url):
        request = urllib.request.Request(url + '?verify=' + uuid.uuid4().hex,
                                         headers={'Cache-Control': 'no-cache',
                                                  'User-Agent': 'LAA-Report-Publisher/1.0'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read(4_000_001)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise PublicationError(f'Readback HTTP {error.code}') from None
        except Exception:
            raise PublicationError('Readback network failure') from None

    def get(self, kind):
        kind_check(kind)
        return self.request(f'{self.public_base}/hp-reports/{kind}/latest.json')

    def reader(self, kind):
        kind_check(kind)
        return self.request(f'{SITE_BASE}/{kind}')

    def put(self, kind, body):
        kind_check(kind)
        with tempfile.TemporaryDirectory(prefix='hp-report-') as folder:
            path = Path(folder) / 'latest.json'
            path.write_bytes(body)
            path.chmod(0o600)
            result = subprocess.run(['/opt/homebrew/bin/cf', 'r2', 'objects', 'put',
                f'hp-reports/{kind}/latest.json', '--bucket-name', BUCKET,
                '--content-type', 'application/json; charset=utf-8', '--file', str(path)],
                cwd='/Users/laa', capture_output=True, timeout=120)
            if result.returncode:
                raise PublicationError('R2 upload failed; response suppressed')


@contextmanager
def publication_lock(root):
    directory = Path(root) / 'publication' / 'hp-latest'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'publish.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PublicationError('Another HP publication is active') from None
        try:
            yield directory
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def verify_reader(store, kind, expected, sleep=time.sleep):
    """Allow the existing 60-second HP caches to converge, with a fixed limit."""
    for delay in (0, 10, 20, 35, 35, 30):
        if delay:
            sleep(delay)
        try:
            if store.reader(kind) == expected:
                return
        except PublicationError:
            pass
    raise PublicationError('HP readback hash mismatch after cache convergence window')


def publish(kind, root=ROOT, *, review_path=None, publish=False, storage=None,
            public_base=PUBLIC_BASE, verify_site=True, now=None):
    current = now or datetime.now(timezone.utc)
    with publication_lock(root) as directory:
        selected = latest_completed(kind, root)
        candidate = validate_edition(selected, root, current)
        review = validate_review(candidate, review_path, current)
        bundle = candidate['bundle']
        displayed = display_html(candidate['html'], bundle['asOf'])
        if len(displayed.encode()) > 2_000_000:
            raise PublicationError('Displayed report too large')
        envelope = {'schemaVersion': 1, 'kind': kind, 'reportDate': bundle['reportDate'],
                    'asOf': bundle['asOf'], 'publishedAt': current.isoformat(),
                    'url': f'{SITE_BASE}/{kind}', 'sha256': sha(displayed.encode()),
                    'sourceHtmlSha256': candidate['hashes']['htmlSha256'],
                    'html': displayed, 'reviewStatus': 'independent_passed',
                    'reviewedAt': review['reviewedAt']}
        result = {'status': 'dry_run_validated', 'kind': kind, 'edition': candidate['edition'],
                  'editionPath': str(selected), 'asOf': bundle['asOf'], 'datePolicy': 'latest_completed',
                  'sha256': envelope['sha256'], 'storageVerified': False, 'hpVerified': False}
        result['sourceHtmlSha256'] = envelope['sourceHtmlSha256']
        if not publish:
            return result
        store = storage or R2Storage(public_base)
        previous_bytes = store.get(kind)
        active = directory / f'active-{kind}.json'
        if active.exists():
            pending = read(active)
            actual = sha(previous_bytes) if previous_bytes is not None else None
            if actual == pending.get('envelopeSha256'):
                # A timeout or local receipt failure may follow a committed PUT.
                # Reconcile exact intended bytes; never silently overwrite them.
                recovered = json.loads(previous_bytes)
                if verify_site:
                    verify_reader(store, kind, recovered['html'].encode())
                recovered_result = {**pending['result'], 'status': 'published',
                                    'storageVerified': True, 'hpVerified': verify_site,
                                    'publishedAt': recovered['publishedAt'], 'recovered': True}
                write_json(directory / f'latest-{kind}.json', recovered_result)
                active.unlink()
            elif actual == pending.get('previousEnvelopeSha256'):
                # The previous atomic object is still intact: no write committed.
                active.unlink()
            else:
                raise PublicationError('Unresolved publication differs from known atomic objects')
        body = (json.dumps(envelope, ensure_ascii=False) + '\n').encode()
        if len(body) > 3_000_000:
            raise PublicationError('Publication envelope too large')
        unchanged = False
        if previous_bytes is not None:
            previous = json.loads(previous_bytes)
            if (previous.get('kind') != kind or previous.get('schemaVersion') != 1
                    or sha(previous.get('html', '').encode()) != previous.get('sha256')):
                raise PublicationError('Existing public object is invalid')
            if stamp(previous['asOf']) > stamp(envelope['asOf']):
                raise PublicationError('Older report cannot replace newer public report')
            if (previous['asOf'] == envelope['asOf'] and previous['sha256'] == envelope['sha256']
                    and previous.get('sourceHtmlSha256') == envelope['sourceHtmlSha256']
                    and previous.get('reviewStatus') == 'independent_passed'
                    and previous.get('reviewedAt') == envelope['reviewedAt']):
                body, unchanged = previous_bytes, True
        # Recheck after network reads to prevent a concurrent change of accepted local inputs.
        repeated = validate_edition(selected, root, current)
        validate_review(repeated, review_path, current)
        if repeated['hashes'] != candidate['hashes'] or latest_completed(kind, root) != selected:
            raise PublicationError('Edition changed during publication')
        # Persist the intent before PUT, including its exact hash. It contains no
        # HTML copy; interrupted runs reconcile against the one remote object.
        intent = {'status': 'publishing', 'envelopeSha256': sha(body),
                  'previousEnvelopeSha256': sha(previous_bytes) if previous_bytes is not None else None,
                  'result': result, 'startedAt': current.isoformat()}
        write_json(active, intent)
        try:
            if not unchanged:
                store.put(kind, body)
            if store.get(kind) != body:
                raise PublicationError('R2 readback hash mismatch')
            result['storageVerified'] = True
            if verify_site:
                verify_reader(store, kind, displayed.encode())
                result['hpVerified'] = True
            result.update(status='unchanged' if unchanged else 'published', publishedAt=json.loads(body)['publishedAt'])
            write_json(directory / f'latest-{kind}.json', result)
            active.unlink()
        except BaseException:
            # Keep the already-persisted intent even if the filesystem itself
            # fails. Its presence means pending verification, never success.
            raise
        return result


def cleanup_old_html(kind, root=ROOT, *, apply=False):
    """Keep Markdown and all evidence. Only remove old successful edition HTML."""
    kind_check(kind)
    latest = latest_completed(kind, root)
    protected = {latest.parent.resolve()}
    manifest = Path(root) / f'latest-{kind}.json'
    if manifest.exists():
        html = read(manifest).get('outputs', {}).get('html_path')
        if html:
            protected.add(Path(html).parent.resolve())
    receipt = Path(root) / 'publication' / 'hp-latest' / f'latest-{kind}.json'
    if receipt.exists():
        protected.add(Path(read(receipt)['editionPath']).parent.resolve())
    active = Path(root) / 'publication' / 'hp-latest' / f'active-{kind}.json'
    if active.exists():
        protected.add(Path(read(active)['result']['editionPath']).parent.resolve())
    removed, preserved = [], []
    for edition in (Path(root) / 'reports' / kind / 'editions').glob('*/*.edition.json'):
        record = read(edition)
        if not completed(record, kind) or edition.parent.resolve() in protected:
            continue
        outputs = record['outputs']
        html = Path(outputs['html_path'])
        if not html.exists():
            continue
        local_file(html, edition.parent)
        md = local_file(outputs['md_path'], edition.parent)
        bundle = read(local_file(outputs['bundle_path'], edition.parent))
        parent = read(local_file(outputs['parent_review_path'], edition.parent))
        if sha(md.read_bytes()) != bundle['sourceSha256'] or sha(html.read_bytes()) != parent.get('htmlSha256'):
            raise PublicationError('Cleanup refused: source or HTML hash mismatch')
        preserved.append({'edition': edition.parent.name, 'mdPath': str(md), 'sha256': bundle['sourceSha256']})
        removed.append(str(html))
    # Validate every candidate before deleting any byte.
    if apply:
        for filename in removed:
            Path(filename).unlink()
        write_json(Path(root) / 'publication' / 'hp-latest' / f'markdown-history-{kind}.json',
                   {'kind': kind, 'markdown': preserved, 'removedHtml': removed})
    return {'kind': kind, 'applied': apply, 'html': removed, 'markdown': preserved}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('daily', 'weekly'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--public-base', default=PUBLIC_BASE)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--skip-site-verification', action='store_true', help='Initial rollout only; result does not claim HP verified')
    parser.add_argument('--cleanup-old-html', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = publish(args.kind, args.root, review_path=args.review, publish=args.publish,
                         public_base=args.public_base, verify_site=not args.skip_site_verification)
        if args.cleanup_old_html:
            result['retention'] = cleanup_old_html(args.kind, args.root, apply=args.publish)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'needs_attention', 'error': type(error).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
