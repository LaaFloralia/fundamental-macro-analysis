"""HTML presentation: the one-page dashboard shared with the XAU report (scripts/offchart_dashboard.py).

The summary/figure sidecars must quote the Markdown exactly (every shown string and number is
rechecked against its quoted line by the dashboard), the HTML is static (no script, no external
resource), the Markdown source is embedded byte for byte as a download link, and rendering is not
acceptance. USOIL adds ``report-symbol`` and ``report-session-slot`` metadata.
"""
from __future__ import annotations

from html import escape
import hashlib
import json
import os
from pathlib import Path
import tempfile

from usoil import SYMBOL

SUMMARY_FIELDS = ('conclusion', 'chips', 'limitations')
KINDS = ('daily', 'weekly')
SESSION_SLOTS = ('am', 'pm', 'weekly')
RENDERER = 'scripts/offchart_dashboard.py'


class PresentationError(ValueError):
    pass


def _load(path):
    raw = Path(path).read_bytes()
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def _quoted(item, source: str) -> bool:
    quote = item.get('source_quote') if isinstance(item, dict) else None
    return isinstance(quote, str) and bool(quote.strip()) and quote in source


def validate_quotes(summary: dict, figures: list, source: str) -> None:
    """Every visible summary/figure item quotes a nonempty exact source passage."""
    if not isinstance(summary, dict):
        raise PresentationError('Summary must be a JSON object.')
    for field in SUMMARY_FIELDS:
        items = summary.get(field, [])
        if field == 'conclusion':
            items = (items.get('rows') or [items]) if items else []
        if not isinstance(items, list):
            raise PresentationError('Summary collections must be arrays.')
        if any(not _quoted(item, source) for item in items):
            raise PresentationError('Summary source_quote must exactly match a nonempty source passage.')
    if summary.get('confidence') and not _quoted(summary['confidence'], source):
        raise PresentationError('Summary source_quote must exactly match a nonempty source passage.')
    if not isinstance(figures, list):
        raise PresentationError('Figures must be a JSON array.')
    for figure in figures:
        if not isinstance(figure, dict) or not isinstance(figure.get('items'), list) or not figure['items']:
            raise PresentationError('Each figure must contain a nonempty items array.')
        rows = figure['items'] + (figure.get('verdicts') or [])
        if any(not _quoted(item, source) for item in rows):
            raise PresentationError('Figure source_quote must exactly match a nonempty source passage.')


def render(md_path, *, summary_path, visuals_path, as_of: str, kind: str, session_slot: str,
           generated_at: str, renderer=None, timeout: int = 120) -> dict:
    """Render sibling .html and .presentation.audit.json; never overwrite inputs."""
    from scripts.offchart_dashboard import DashboardError, build
    md_path = Path(md_path).absolute()
    if md_path.suffix != '.md':
        raise PresentationError('Input must be a Markdown .md file.')
    if kind not in KINDS or session_slot not in SESSION_SLOTS or not isinstance(as_of, str) or not as_of:
        raise PresentationError('Report edition metadata is required')
    html_path = md_path.with_suffix('.html')
    audit_path = md_path.with_suffix('.presentation.audit.json')
    raw = md_path.read_bytes()
    source = raw.decode('utf-8')
    if not source.strip():
        raise PresentationError('Markdown source is empty.')
    summary, summary_hash = _load(summary_path)
    figures, figures_hash = _load(visuals_path)
    validate_quotes(summary, figures, source)
    title = f'{SYMBOL} チャート外分析 {kind.capitalize()}'
    try:
        document, audit = build(source, summary, figures, title=title, source_name=md_path.name)
    except DashboardError as error:
        # The dashboard's own validation message is local text about our input.
        raise PresentationError(f'Dashboard rejected the input: {error}') from None
    if audit.get('source_sha256') != hashlib.sha256(raw).hexdigest():
        raise PresentationError('Rendered source digest does not match the input bytes.')
    document = document.replace('<body>', '<body><a class="reports-back" href="/reports">レポート一覧へ戻る</a>', 1)
    meta = ''.join(f'<meta name="{key}" content="{escape(value, quote=True)}">' for key, value in (
        ('report-kind', kind), ('report-symbol', SYMBOL), ('report-session-slot', session_slot),
        ('report-as-of', as_of), ('report-date', as_of[:10]), ('report-generated-at', generated_at)))
    document = document.replace('</head>', meta + '</head>', 1)
    data = document.encode('utf-8')
    audit.update({
        'adapter': 'chart-intel-usoil/usoil.presentation', 'renderer': RENDERER,
        'render_status': 'rendered', 'summary_mode': 'supplied',
        'visual_status': 'provided_data_checked' if figures else 'not_provided',
        'presentation_complete': False, 'pdf_status': 'not_generated',
        'publication_status': 'not_attempted', 'symbol': SYMBOL, 'session_slot': session_slot,
        'summary_sha256': summary_hash, 'visuals_sha256': figures_hash, 'as_of': as_of,
        'semantic_review': 'pending', 'html_sha256': hashlib.sha256(data).hexdigest(),
    })
    with tempfile.TemporaryDirectory(prefix='.presentation-', dir=md_path.parent) as temporary:
        stage = Path(temporary)
        staged_html, staged_audit = stage / 'report.html', stage / 'audit.json'
        staged_html.write_bytes(data)
        staged_audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(staged_html, 0o600)
        os.chmod(staged_audit, 0o600)
        os.replace(staged_html, html_path)
        os.replace(staged_audit, audit_path)
    return {'html_path': str(html_path), 'presentation_audit_path': str(audit_path)}
