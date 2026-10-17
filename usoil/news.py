"""WTI public RSS with exact-host body checks and the existing SSRF guard.

No valid minute-bar oil source is available; price reactions remain missing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import re
import time
from urllib.parse import urlsplit, urlunsplit

from usoil import headline_triage as triage
from usoil.fetch import FetchError, Fetcher, SourceResult, checked_url
from usoil.sources.base import z

UTC = timezone.utc
MACRO_FEEDS = (
    ('investing_commodities_rss', 'Investing.com', 'https://www.investing.com/rss/news_11.rss'),
    ('investing_economy_rss', 'Investing.com', 'https://www.investing.com/rss/news_14.rss'),
)
GENERAL_FEEDS = (('oilprice_rss', 'OilPrice.com', 'https://oilprice.com/rss/main'),) + MACRO_FEEDS
OFFICIAL_FEEDS = (
    ('eia_todayinenergy_rss', 'EIA', 'https://www.eia.gov/rss/todayinenergy.xml'),
    ('cftc_rss', 'CFTC', 'https://www.cftc.gov/RSS/RSSGP/rssgp.xml'),
)
FALLBACK = re.compile(r'\bWTI\b|\bBrent\b|\bcrude\b|\boil\b|petroleum|\bOPEC\b|refiner|'
                      r'gasoline|distillate|inventor|\bEIA\b|\bCFTC\b|\bFed\b|FOMC|Powell|inflation|'
                      r'CPI|payroll|jobs report|Treasur|yield|dollar|geopolit|\bwars?\b|missile|airstrike|'
                      r'sanction|ceasefire|tariff|Middle East|\bIran\b|\bIsrael\b|Ukraine|Russia|Hormuz|pipeline', re.I)
MAX_BODIES = 8
BODY_BUDGET_SECONDS = 90
MAX_REACTIONS = 8
OFFICIAL_IDS = {fid for fid, _, _ in OFFICIAL_FEEDS}
MACRO_IDS = {fid for fid, _, _ in MACRO_FEEDS}
MACRO_KEEP = 5
BODY_HOSTS = {
    'oilprice_rss': ('oilprice.com',),
    'eia_todayinenergy_rss': ('www.eia.gov',),
    'cftc_rss': ('www.cftc.gov',),
    'investing_commodities_rss': (),
    'investing_economy_rss': (),
}


def _feed_parser():
    from scrapers import news_triage as xau  # read-only reuse of the XAU feed parser
    return xau


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip('/'), '', ''))


def title_key(title: str) -> str:
    return hashlib.sha256(re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip().encode()).hexdigest()[:16]


def collect_headlines(f: Fetcher, start: datetime, now: datetime) -> tuple[list[dict], list[dict]]:
    xau = _feed_parser()
    seen_url, seen_title, candidates, feeds = set(), set(), [], []
    for official, group in ((False, GENERAL_FEEDS), (True, OFFICIAL_FEEDS)):
        for source_id, publisher, url in group:
            record = {'source_id': source_id, 'publisher': publisher, 'official': official, 'status': 'unavailable',
                      'items': 0, 'error_kind': None, 'retrieved_at': None}
            try:
                r = f.get(url, timeout=15)
                items = xau.parse_feed(r.body, publisher, start, now, UTC if source_id in MACRO_IDS else None)
                record.update(status='ok', retrieved_at=z(datetime.fromisoformat(r.retrieved_at)), raw_sha256=r.raw_sha256)
            except FetchError as error:
                record['error_kind'] = error.kind
                feeds.append(record)
                continue
            except Exception:  # noqa: BLE001 - parser/XML failures become a fixed label
                record['error_kind'] = 'parse_error'
                feeds.append(record)
                continue
            fresh = 0
            for item in items:
                key_url, key_title = canonical_url(item['url']), title_key(item['title'])
                if key_url in seen_url or key_title in seen_title:
                    continue
                seen_url.add(key_url)
                seen_title.add(key_title)
                fresh += 1
                candidates.append({'source_id': source_id, 'publisher': publisher, 'official': official,
                                   'title': item['title'], 'url': canonical_url(item['url']),
                                   'published_at': z(datetime.fromisoformat(item['published'])),
                                   'first_seen_at': record['retrieved_at'], 'excerpt': item.get('text', '')[:300],
                                   'feed_raw_sha256': record.get('raw_sha256')})
            record['items'] = fresh
            feeds.append(record)
    candidates.sort(key=lambda c: c['published_at'], reverse=True)
    for index, item in enumerate(candidates[:200]):
        item['id'] = f'n{index + 1:03d}'
    return candidates[:200], feeds


def select(candidates: list[dict], scores: dict | None) -> list[dict]:
    if scores is None:
        pool = [dict(c, p=None) for c in candidates if FALLBACK.search(c['title'] + ' ' + c.get('excerpt', ''))]
        return _share(sorted(pool, key=lambda c: c['published_at'], reverse=True))
    ranked = sorted((dict(c, p=scores.get(c['id'])) for c in candidates),
                    key=lambda c: (c['p'] or 0, c['published_at']), reverse=True)
    kept = [c for c in ranked if (c['p'] or 0) >= triage.THRESHOLD]
    if len(kept) < triage.KEEP_MIN:
        kept = ranked[:triage.KEEP_MIN]
    return _share(kept)


def _share(ranked: list[dict]) -> list[dict]:
    """Reserve energy/official places while limiting broad macro headlines."""
    macro = [c for c in ranked if c['source_id'] in MACRO_IDS][:MACRO_KEEP]
    rest = [c for c in ranked if c['source_id'] not in MACRO_IDS][:triage.KEEP_MAX - len(macro)]
    chosen = {c['id'] for c in macro + rest}
    return [c for c in ranked if c['id'] in chosen]


class _ArticleText(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'form'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.parts = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(' '.join(data.split()))


def body_url_allowed(url, source_id: str) -> bool:
    """https, default port, no userinfo, DNS name equal to one of the feed's fixed hosts."""
    try:
        host = checked_url(str(url))
    except FetchError:
        return False
    return host in BODY_HOSTS.get(source_id, ())


def body_check(f: Fetcher, item: dict) -> dict:
    """Fetch the article on the feed's own host; keep hash/length/excerpt only."""
    if not body_url_allowed(item.get('url'), item.get('source_id', '')):
        return {'status': 'not_attempted', 'error_kind': 'url_not_allowed'}
    host = urlsplit(item['url']).hostname or ''
    try:
        r = f.get(item['url'], timeout=15, allow_redirects=False)
        parser = _ArticleText()
        parser.feed(r.text())
        text = ' '.join(parser.parts)
    except FetchError as error:
        return {'status': 'unavailable', 'error_kind': error.kind}
    except Exception:  # noqa: BLE001
        return {'status': 'unavailable', 'error_kind': 'parse_error'}
    words = [w for w in re.findall(r'[a-z0-9]{4,}', item['title'].lower())]
    found = sum(1 for w in words if w in text.lower())
    overlap = round(found / len(words), 2) if words else 0.0
    if len(text) < 400 or overlap < 0.5:
        return {'status': 'unavailable', 'error_kind': 'invalid_payload', 'host': host, 'text_chars': len(text),
                'title_overlap': overlap}
    start = max(0, text.lower().find(words[0]) if words else 0)
    return {'status': 'retrieved', 'host': host, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'text_chars': len(text), 'title_overlap': overlap, 'excerpt': text[start:start + 280],
            'retrieved_at': z(datetime.fromisoformat(r.retrieved_at))}


def reaction(f: Fetcher, published_at: str, now: datetime, others: list[str]) -> dict:
    """No valid minute-bar oil source in v0.1; never substitute a synthetic ticker."""
    event = datetime.fromisoformat(published_at)
    return {'status': 'missing', 'market_id': None, 'reaction_basis': 'unknown',
            'event_at': z(event), 'baseline_at': None, 'return_before_5m_bps': None,
            'return_after_1m_bps': None, 'return_after_5m_bps': None,
            'timing_error_bound_seconds': 60, 'confounded': False, 'confound_event_ids': [], 'fact_ids': [],
            'note': '公表前後の原油1分足は未取得。Yahoo1時間足とCNBCスナップショットでは分単位反応を計算しない。'}


def collect_headline_source(f: Fetcher, *, now: datetime, lookback_hours: float) -> SourceResult:
    """Context phase (collect worker): feeds and de-duplicated candidates only; no triage, bodies or reactions."""
    started = time.monotonic()
    start = now - timedelta(hours=lookback_hours)
    candidates, feeds = collect_headlines(f, start, now)
    ok_general = sum(1 for x in feeds if x['status'] == 'ok' and not x['official'])
    ok_official = sum(1 for x in feeds if x['status'] == 'ok' and x['official'])
    status = 'ok' if ok_general >= 2 and ok_official >= 2 else ('partial' if ok_general + ok_official else 'unavailable')
    if status == 'unavailable':
        raise FetchError('connection')
    values = {'window_start': z(start), 'window_end': z(now), 'lookback_hours': lookback_hours, 'feeds': feeds,
              'candidate_count': len(candidates), 'candidates': candidates,
              'feeds_ok': {'general': ok_general, 'official': ok_official},
              'headline_elapsed_ms': round((time.monotonic() - started) * 1000)}
    retrieved = [x['retrieved_at'] for x in feeds if x.get('retrieved_at')]
    return SourceResult('news', status=status, retrieved_at=max(retrieved), source_url=GENERAL_FEEDS[0][2],
                        values=values, raw_sha256=[x['raw_sha256'] for x in feeds if x.get('raw_sha256')])


def headline_candidates(record: dict | None) -> list[dict]:
    """Candidates of a context-phase news record (empty when the headline source failed)."""
    values = (record or {}).get('values') or {}
    rows = values.get('candidates')
    return rows if record and record.get('status') in ('ok', 'partial') and isinstance(rows, list) else []


def finish_news(f: Fetcher, record: dict, scores: dict | None, selection: dict, *, now: datetime) -> SourceResult:
    """News-detail phase (sandboxed, no secrets): select -> bodies -> reactions -> clusters -> code verification."""
    started = time.monotonic()
    values = dict(record['values'])
    candidates = values.pop('candidates')
    headline_ms = values.pop('headline_elapsed_ms', 0)
    kept = select(candidates, scores)
    # Body checks: official sources first, then most recent; bounded count and time.
    order = sorted(sorted(kept, key=lambda c: c['published_at'], reverse=True), key=lambda c: not c['official'])
    checked = 0
    body_started = time.monotonic()
    for item in order:
        if item['source_id'] in MACRO_IDS:  # headlines only; uses no body place
            item['body'] = {'status': 'not_attempted', 'error_kind': 'not_used_by_terms'}
            continue
        if checked >= MAX_BODIES or time.monotonic() - body_started > BODY_BUDGET_SECONDS or f.deadline.remaining() < 20:
            item['body'] = {'status': 'not_attempted', 'error_kind': 'budget_exhausted'}
            continue
        checked += 1
        item['body'] = body_check(f, item)
    times = [c['published_at'] for c in kept]
    for index, item in enumerate(sorted(kept, key=lambda c: c['published_at'], reverse=True)):
        if index < MAX_REACTIONS and f.deadline.remaining() > 15:
            item['reaction'] = reaction(f, item['published_at'], now, [t for t in times if t != item['published_at']])
        else:
            item['reaction'] = None
    for item in kept:
        item['event_cluster_id'] = 'c-' + title_key(item['title'])
        item['code_verification'] = ('primary_body_retrieved' if item['official'] and item['body'].get('status') == 'retrieved'
                                     else 'secondary_body_retrieved' if item['body'].get('status') == 'retrieved'
                                     else 'headline_only')
    values.update(selection=selection, kept=kept,
                  selection_rule={'threshold': triage.THRESHOLD, 'keep_min': triage.KEEP_MIN,
                                  'keep_max': triage.KEEP_MAX, 'macro_keep_max': MACRO_KEEP},
                  elapsed_ms=headline_ms + round((time.monotonic() - started) * 1000))
    return SourceResult('news', status=record['status'], retrieved_at=record['retrieved_at'],
                        source_url=record.get('source_url'), values=values, raw_sha256=list(record.get('raw_sha256') or []))
