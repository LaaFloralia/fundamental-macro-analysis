"""Hash-bound factual and visual acceptance for one report edition.

A browser measurement is evidence, not semantic approval. A separate reviewer
must inspect the generated images and meaning and supply a hash-bound review.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re

from scripts.report_bundle import BundleError, check_bindings, check_regenerated, digest, require_fresh

CHECKS = ('facts_match_sources', 'numbers_and_units', 'summary_preserves_conditions',
          'no_unverified_trading_claims', 'desktop_readable', 'mobile_readable',
          'figures_meaning_and_labels', 'no_content_loss')


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def validate_bundle(html_path, bundle_path, now=None):
    html_path, bundle_path = Path(html_path), Path(bundle_path)
    b = json.loads(bundle_path.read_text())
    for key in ('data', 'source', 'summary', 'figures'):
        if digest(b[f'{key}Path']) != b[f'{key}Sha256']:
            raise BundleError(f'{key} input changed after bundle creation')
    data = json.loads(Path(b['dataPath']).read_text())
    if require_fresh(data, now).isoformat() != b['asOf']:
        raise BundleError('Bundle timestamp does not match source data')
    if b['missing'] or set(b['figureIds']) != set(b['requiredFigures']):
        raise BundleError('Required figures are missing')
    figures = json.loads(Path(b['figuresPath']).read_text())
    check_bindings(data, b, figures)
    check_regenerated(data, b, figures, Path(b['sourcePath']).read_text(encoding='utf-8'),
                      json.loads(Path(b['summaryPath']).read_text()))
    audit = json.loads(html_path.with_suffix('.presentation.audit.json').read_text())
    if audit.get('source_sha256') != b['sourceSha256'] or audit.get('html_sha256') != digest(html_path):
        raise BundleError('HTML or source digest mismatch')
    if audit.get('summary_sha256') != b['summarySha256'] or audit.get('visuals_sha256') != b['figuresSha256']:
        raise BundleError('Presentation sidecars do not match bundle')
    if audit.get('figure_count') != len(figures) or audit.get('numeric_check') != 'pass':
        raise BundleError('Rendered figures failed numeric checks')
    html = html_path.read_text()
    if re.search(r'<\s*(script|iframe|object|embed|form)\b|\son\w+\s*=', html, re.I):
        raise BundleError('Active content is prohibited')
    for name, value in [('report-kind', b['kind']), ('report-as-of', b['asOf']), ('report-date', b['reportDate'])]:
        if f'<meta name="{name}" content="{value}">' not in html:
            raise BundleError('HTML edition metadata is missing')
    if 'href="/reports"' not in html:
        raise BundleError('Reports return link is missing')
    return b


def compact(text):
    return re.sub(r'\s+', '', str(text or '')).replace('−', '-')


# Every cell the page shows (explanations open), so a dropped cell fails the browser check.
FIGURE_TEXT = {'bias_board': ('label', 'display', 'baseline', 'change', 'direction', 'asof', 'note'),
               'liquidity_map': ('label', 'price_text', 'pool', 'state_word', 'touched', 'distance_text', 'target'),
               'event_table': ('label', 'time', 'previous', 'forecast', 'actual', 'diff', 'direction', 'memo')}


def shown_texts(summary, figures):
    """Strings the page must show: conclusion, lead chips, figure titles, every item and verdict, limitations."""
    texts = [(summary.get('conclusion') or {}).get('text')]
    texts += [x.get('text') for x in summary.get('chips') or []]
    texts += [x.get('text') for x in summary.get('limitations') or []]
    for figure in figures:
        texts.append(figure.get('title'))
        for item in figure.get('items') or []:
            keys = FIGURE_TEXT.get(figure.get('type'), ())
            if item.get('kind') == 'text':  # a judgement in words: one wide cell, no baseline or change
                keys = tuple(k for k in keys if k not in ('baseline', 'change'))
            texts += [item.get(key) for key in keys]
        # aux: retail average entries of a v5 edition (newer editions show them as items)
        texts += [item.get(key) for item in figure.get('aux') or [] for key in ('label', 'price_text', 'state_word')]
        texts += [c.get('text') for key in ('cautions', 'missing') for c in figure.get(key) or []]
        texts += [tile.get(key) for tile in figure.get('verdicts') or [] for key in ('label', 'direction', 'short')]
        if figure.get('reason'):
            texts.append(figure['reason'].get('text'))
    return [t for t in dict.fromkeys(texts) if t not in (None, '') and compact(t)]


def browser_check(html_path, bundle_path, *, now=None):
    """Real-browser evidence for one edition: content present, no overflow, readable captures.

    Captures the page as the reader first sees it (explanations collapsed): the first
    screen, every figure, consecutive page tiles and, on mobile, the right side of any
    table wider than the screen; then every <details> open, as page tiles again
    (`-open-NN`). Text presence is checked with every <details> open.
    """
    from playwright.sync_api import sync_playwright
    b = validate_bundle(html_path, bundle_path, now)
    html_path = Path(html_path).absolute()
    out = html_path.with_suffix('.render-evidence')
    out.mkdir(exist_ok=True)
    figures = json.loads(Path(b['figuresPath']).read_text())
    summary = json.loads(Path(b['summaryPath']).read_text())
    expected = shown_texts(summary, figures)
    record = {'schemaVersion': 2, 'htmlSha256': digest(html_path), 'bundleSha256': digest(bundle_path),
              'status': 'failed', 'viewports': [], 'images': [], 'expectedTexts': len(expected)}

    def capture(path, **kwargs):
        record['images'].append({'path': str(path), 'sha256': digest(path), **kwargs})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for width, label in [(1365, 'desktop'), (390, 'mobile')]:
                page = browser.new_page(viewport={'width': width, 'height': 950}, device_scale_factor=1)
                page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(('file:', 'data:')) else route.abort())
                page.goto(html_path.as_uri(), wait_until='load')
                page.evaluate('document.fonts.ready')
                if page.locator('script,iframe,object,embed,form').count():
                    raise BundleError('Active document nodes are prohibited')
                if page.locator('details[open]').count():
                    raise BundleError('Explanations must start collapsed')
                encoded = page.locator('#source-markdown').get_attribute('href').split(',', 1)[1]
                if base64.b64decode(encoded) != Path(b['sourcePath']).read_bytes():
                    raise BundleError('Embedded original source bytes differ')
                if page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'):
                    raise BundleError(f'{label} body overflows viewport')
                small = page.locator('details > summary').evaluate_all(
                    '(nodes) => nodes.filter(n => n.getBoundingClientRect().height < 44).length')
                if small:
                    raise BundleError(f'{label} has {small} expandable labels under 44px')
                top = out / f'{label}-overview.png'
                page.screenshot(path=str(top), full_page=False)
                capture(top, viewport=label)
                for i, figure in enumerate(page.locator('.data-figure').all()):
                    path = out / f'{label}-figure-{i + 1}.png'
                    figure.screenshot(path=str(path))
                    capture(path, viewport=label)
                if label == 'mobile':
                    for index, table in enumerate(page.locator('.board-wrap').all()):
                        if table.evaluate('(e) => e.scrollWidth > e.clientWidth + 1'):
                            table.evaluate('(e) => e.scrollLeft = e.scrollWidth')
                            path = out / f'mobile-table-right-{index + 1}.png'
                            table.screenshot(path=str(path))
                            capture(path, viewport=label)
                            table.evaluate('(e) => e.scrollLeft = 0')
                # Consecutive tiles of the whole page at readable size (what the reader scrolls through).
                page.evaluate('window.scrollTo(0, 0)')
                height = page.evaluate('document.documentElement.scrollHeight')
                y, tile = 0, 0
                while y < height:
                    tile += 1
                    clip = {'x': 0, 'y': y, 'width': width, 'height': min(950, height - y)}
                    path = out / f'{label}-page-{tile:02}.png'
                    page.screenshot(path=str(path), full_page=True, clip=clip)
                    capture(path, viewport=label, section='page', clip=clip)
                    y += 850
                page.locator('details').evaluate_all('(nodes) => nodes.forEach(n => n.open = true)')
                body = compact(page.locator('main').inner_text())
                missing = [t for t in expected if compact(t) not in body]
                if missing:
                    raise BundleError(f'{label} page is missing {len(missing)} figure or summary texts')
                if page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'):
                    raise BundleError(f'{label} body overflows viewport with explanations open')
                page.evaluate('window.scrollTo(0, 0)')
                height = page.evaluate('document.documentElement.scrollHeight')
                y, tile = 0, 0
                while y < height:
                    tile += 1
                    clip = {'x': 0, 'y': y, 'width': width, 'height': min(950, height - y)}
                    path = out / f'{label}-open-{tile:02}.png'
                    page.screenshot(path=str(path), full_page=True, clip=clip)
                    capture(path, viewport=label, section='explanations-open', clip=clip)
                    y += 850
                record['viewports'].append({'width': width, 'height': 950, 'bodyOverflow': False,
                                            'figureCount': page.locator('.data-figure').count(), 'textsMissing': 0})
                page.close()
        finally:
            browser.close()
    record['status'] = 'passed'
    result = html_path.with_suffix('.render.json')
    write(result, record)
    return str(result)


def validate_acceptance(html_path, bundle_path, acceptance_path, now=None):
    validate_bundle(html_path, bundle_path, now)
    review_bytes = Path(acceptance_path).read_bytes()
    review = json.loads(review_bytes)
    if review.get('status') != 'passed' or not review.get('reviewer') or not review.get('reviewedAt'):
        raise BundleError('Independent semantic and visual review is pending')
    if review.get('htmlSha256') != digest(html_path) or review.get('bundleSha256') != digest(bundle_path):
        raise BundleError('Review applies to different report bytes')
    if any(review.get('checks', {}).get(check) is not True for check in CHECKS):
        raise BundleError('Semantic or visual review is incomplete')
    render_path = Path(html_path).with_suffix('.render.json')
    if review.get('renderSha256') != digest(render_path):
        raise BundleError('Review does not reference current browser evidence')
    render = json.loads(render_path.read_text())
    if render.get('status') != 'passed' or render.get('htmlSha256') != digest(html_path) or render.get('bundleSha256') != digest(bundle_path):
        raise BundleError('Real browser render is not verified')
    for image in render.get('images', []):
        if digest(image['path']) != image['sha256']:
            raise BundleError('Reviewed render evidence changed')
    if not render.get('images') or {v['width'] for v in render.get('viewports', [])} != {1365, 390}:
        raise BundleError('Desktop/mobile render evidence is missing')
    return {'acceptanceSha256': hashlib.sha256(review_bytes).hexdigest(), 'publicationReady': True,
            'htmlSha256': review['htmlSha256'], 'bundleSha256': review['bundleSha256']}
