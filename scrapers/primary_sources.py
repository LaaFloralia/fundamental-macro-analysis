"""Official pages behind public news leads, found with the Exa search API (shared by XAU and BTC).

Why: the feeds give media headlines. The 2026-10-30 Binance outflow (14 headlines, one event) could not
be counted because no official body was read, and the same original statement re-told by several outlets
looks like several items. This module asks Exa for the actor's own pages (``includeDomains`` limited to
the actor's official site or path) and keeps only what code can check.

Rules:
- Sent to Exa: one public headline per actor, the official domain list and a start date. Nothing private.
- ``EXA_API_KEY`` comes from the existing 1Password wrapper; it is only placed in the request header,
  never stored, logged or returned. Missing key -> ``skipped``.
- At most ``MAX_QUERIES`` requests per run, no retry. HTTP 402 (credits used up) stops the run and is
  recorded as ``credits_exhausted``; nothing is ever paid or topped up from here.
- Host and path of each result are re-checked against the actor's scopes; index pages are refused.
- Exa's ``publishedDate`` is usually a day: ``published_precision`` says so and nothing turns it into a
  time. The page's own date line is kept as raw text (its time zone is not stated).
- The page text is not stored: only its sha256, length and one excerpt cut from it (so the excerpt is
  verifiably the page's own words).
- A page is linked to a headline only by shared content words (``MATCH_MIN``) and dates; the parent still
  judges whether the official text supports the headline. Headlines linked to the same page are one
  original statement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import time
from typing import Callable, Iterable, Optional
from urllib.parse import urlsplit

UTC = timezone.utc
ENDPOINT = 'https://api.exa.ai/search'
MAX_QUERIES = 3  # actors searched per run
MAJOR_EVENT_HEADLINES = 5
MAX_REQUESTS = 5  # with the fallback queries; a request with no result costs nothing (Exa, 2026-11-03)
NUM_RESULTS = 5
TEXT_CHARS = 10000
MAX_AGE_HOURS = 24  # Exa refetches cached copies older than this
TIMEOUT_SECONDS = 20
MATCH_MIN = 0.4  # share of a headline's content words found in the page
MIN_BODY_WORDS = 30  # words beyond the title; Exa's copy of a JS page or a redirect stub has none (IRNA, Strategy 8-K)
EXCERPT_CHARS = 400
LOOKBACK_EXTRA = timedelta(days=2)  # pages may predate the first headline (Exa dates are days)


class PrimaryFetchError(Exception):
    """Transport failure with a fixed label only."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class Actor:
    actor_id: str
    label: str
    assets: frozenset
    pattern: re.Pattern
    scopes: tuple  # 'host' or 'host/path-prefix' (Exa includeDomains syntax)
    search_name: str  # English name for the fallback query


def _actor(actor_id, label, assets, pattern, *scopes, search_name=None):
    # Case-sensitive: names are capitalised ("US strategy links ..." is not Strategy Inc.); common words
    # inside a pattern are written with (?i:...).
    return Actor(actor_id, label, frozenset(assets), re.compile(pattern), tuple(scopes), search_name or label)


REGIONAL_FED = ('newyorkfed.org', 'bostonfed.org', 'philadelphiafed.org', 'clevelandfed.org', 'richmondfed.org',
                'atlantafed.org', 'chicagofed.org', 'stlouisfed.org', 'minneapolisfed.org', 'kansascityfed.org',
                'dallasfed.org', 'frbsf.org')
ACTORS = (
    # Crypto issuers and venues (BTC).
    _actor('binance', 'Binance', {'btc'}, r'\bBinance\b', 'binance.com/en/support/announcement'),
    _actor('coinbase', 'Coinbase', {'btc'}, r'\bCoinbase\b', 'coinbase.com/blog', 'investor.coinbase.com'),
    _actor('tether', 'Tether', {'btc'}, r'\bTether\b|\bUSDT\b', 'tether.io/news'),
    _actor('circle', 'Circle', {'btc'}, r'\bUSDC\b|\bCircle\b(?=.*(?i:usdc|stablecoin|crypto|bitcoin|freez))',
           'circle.com/pressroom', 'circle.com/blog'),
    _actor('strategy', 'Strategy', {'btc'},
           r'\bMicroStrategy\b|\bSaylor\b|\bMSTR\b|\bStrategy\b(?=.*(?i:bitcoin|btc|saylor|strc|strk|preferred|treasury))',
           'strategy.com/press'),
    _actor('kraken', 'Kraken', {'btc'}, r'\bKraken\b', 'blog.kraken.com'),
    _actor('bybit', 'Bybit', {'btc'}, r'\bBybit\b', 'announcements.bybit.com'),
    _actor('okx', 'OKX', {'btc'}, r'\bOKX\b', 'okx.com/help'),
    _actor('sec', 'SEC', {'btc'}, r'\bSEC\b|Securities and Exchange Commission', 'sec.gov/newsroom'),
    _actor('cftc', 'CFTC', {'btc'}, r'\bCFTC\b', 'cftc.gov/PressRoom'),
    # US government and central banks (both; the Fed also through the regional banks' sites).
    _actor('treasury', 'US Treasury', {'btc', 'xau'}, r'\bTreasury\b(?! (?i:yields?|bonds?|notes?|bills?|market))|\bOFAC\b|'
           r'(?i:\bsanction)', 'home.treasury.gov/news', 'ofac.treasury.gov'),
    _actor('fed', 'Federal Reserve', {'btc', 'xau'},
           r"\bFed\b|\bFOMC\b|Federal Reserve|\bWarsh\b|\bPowell\b",
           'federalreserve.gov/newsevents', *REGIONAL_FED),
    _actor('us_data', 'BLS・BEA・DOL', {'xau'},
           r'\bCPI\b|\bPCE\b|\bGDP\b|\bPPI\b|\bJOLTS\b|\bECI\b|(?i:payrolls?|jobs report|jobless claims|'
           r'labou?r costs?|employment cost|wage growth|retail sales|consumer prices|producer prices)',
           'bls.gov/news.release', 'bea.gov/news', 'dol.gov/newsroom', search_name='BLS BEA economic release'),
    _actor('us_admin', 'White House・State Dept', {'btc', 'xau'},
           r'White House|State Department|(?i:\bembassy\b|non-emergency staff|executive order|\btariffs?\b)',
           'whitehouse.gov', 'state.gov', 'travel.state.gov', search_name='White House State Department'),
    _actor('boj', 'Bank of Japan', {'xau'}, r'\bBoJ\b|\bBOJ\b|Bank of Japan', 'boj.or.jp/en'),
    _actor('ecb', 'ECB', {'xau'}, r'\bECB\b|European Central Bank', 'ecb.europa.eu/press'),
    _actor('boe', 'Bank of England', {'xau'}, r'\bBoE\b|\bBOE\b|Bank of England', 'bankofengland.co.uk/news'),
    _actor('pboc', "People's Bank of China", {'xau'}, r"\bPBoC\b|\bPBOC\b|People.s Bank of China", 'pbc.gov.cn/en'),
    _actor('wgc', 'World Gold Council', {'xau'},
           r'World Gold Council|\bWGC\b|(?i:central banks?\W+(?:\w+\W+){0,3}gold)', 'gold.org/goldhub'),
    _actor('ism', 'ISM', {'xau'}, r'\bISM\b', 'ismworld.org'),
    _actor('opec', 'OPEC', {'xau'}, r'\bOPEC\b', 'opec.org'),
    # Iran's government speaks through IRNA (state news agency) and its ministries: the origin of
    # 'Iran says ...', not an independent confirmation of facts on the ground.
    _actor('iran', 'Iran（IRNA・政府）', {'xau', 'btc'}, r'\bIran(?:ian)?\b|\bTehran\b|\bAraghchi\b|\bPezeshkian\b',
           'en.irna.ir/news', 'en.irna.ir/amp', 'en.mfa.ir', 'president.ir/en', search_name='Iran government'),
    _actor('aramco', 'Saudi Aramco・SPA', {'xau'}, r'\bAramco\b|\bSaudi\b', 'aramco.com/en/news-media', 'spa.gov.sa',
           search_name='Saudi Aramco Saudi Press Agency'),
)
STOP = set('''about after again against ahead amid also among around because been before being below
between could despite down during each from further have into last latest leave like more most much near
news over price prices report reports says said since some still than that their them then there these
they this those through today under until very what when where which while will with would year years
week weeks month months says amid toward towards higher lower rises rise falls fall gains gain drops
drop holds hold remains remain keeps keep sees seen live update updates'''.split())
WORD = re.compile(r"[a-z][a-z'’]{3,}")
# The page's own date line. Only the date is kept: the shown time follows the crawler's time zone (the same
# Binance notice read 21:23, 22:23 and 13:33/15:33 in three Exa fetches on 2026-11-03), so it is not a time.
DATE_LINE = re.compile(r'(?:Published|Updated|Released|Release Date|Posted|Date)(?:\s+on)?\s*:?\s*'
                       r'(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})')


SUFFIXES = ('ations', 'ation', 'ings', 'ing', 'ions', 'ion', 'ed', 's')
GENERIC_NAMES = {'bitcoin', 'btc', 'gold', 'xau', 'usd', 'dollar', 'crypto', 'fed', 'fomc'}


def stem(word: str) -> str:
    """Crude suffix strip so investigates/investigation and hikes/hike meet; never below 4 letters."""
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[:-len(suffix)]
            break
    return word[:-1] if word.endswith('e') and len(word) > 4 else word


def content_words(text: str) -> list[str]:
    words = []
    for w in WORD.findall(text.lower().replace('’', "'")):
        w = w.split("'")[0]
        if len(w) >= 4 and w not in STOP and w not in words:
            words.append(w)
    return words


def page_stems(text: str) -> set:
    return {stem(w.split("'")[0]) for w in re.findall(r"[a-z][a-z'’]{2,}", text.lower().replace('’', "'"))}


def actor_stems(actor: 'Actor') -> set:
    """The actor's own name (every page of that site carries it, so it proves nothing): label words and the
    site names of its scopes. Trigger words of the pattern stay (staff, emergency are real content)."""
    sites = [s.partition('/')[0].split('.')[-2] for s in actor.scopes]
    return {stem(w.lower()) for w in re.findall(r'[A-Za-z]{3,}', actor.label + ' ' + ' '.join(sites))}


def names(headline: str) -> set:
    """Capitalised words after the first one in a sentence-case headline (people, firms, acronyms).

    Title-case headlines (most words capitalised) give no names: capitals carry no meaning there."""
    tokens = re.findall(r"[A-Za-z][A-Za-z'’.-]*", headline)
    rest = [t for t in tokens[1:] if len(t) >= 3]
    capital = [t for t in rest if t[0].isupper()]
    if not rest or len(capital) > len(rest) / 2:
        return set()
    return {stem(t.split("'")[0].split('’')[0].strip('.').lower()) for t in capital} - GENERIC_NAMES


def overlap(headline: str, text: str, skip: Iterable[str] = ()) -> float:
    """Share of the headline's content words (stems, the actor's own words left out) found in the page.

    A name of the headline (sentence case) missing from the page makes it 0: a page about another
    official cannot back a headline about this one (Barkin's headline vs Logan's speech)."""
    hay, skip = page_stems(text), set(skip)
    if any(n not in hay for n in names(headline) - skip):
        return 0.0
    words = [w for w in dict.fromkeys(stem(w) for w in content_words(headline)) if w not in skip]
    if not words:
        return 0.0
    return round(sum(1 for w in words if w in hay) / len(words), 2)


def body_words(text: str, title: str) -> int:
    """Latin words of the page that are not its title (a stub repeats the title or says 'Transferring ...')."""
    words = re.findall(r'[A-Za-z]{3,}', text)
    own = set(re.findall(r'[A-Za-z]{3,}', title)) | {'Transferring', 'website'}
    return sum(1 for w in words if w not in own)


def scope_match(url: str, scopes: Iterable[str]) -> Optional[str]:
    """The scope an https URL falls under (exact host or subdomain, path prefix), else None."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or port is not None or parts.username or parts.password or not host:
        return None
    path = parts.path or '/'
    for scope in scopes:
        domain, _, prefix = scope.partition('/')
        if host != domain and not host.endswith('.' + domain):
            continue
        if prefix:
            want = '/' + prefix.rstrip('/')
            if not (path == want or path.startswith(want + '/')):
                continue
            if path.rstrip('/') == want:  # the section's index page, not a statement
                return None
        elif path.strip('/') == '':
            return None
        return scope
    return None


def _day(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _time(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else None


def _z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def query_text(title: str) -> str:
    """The headline without amounts and percentages (media figures are not how the official page words it)."""
    text = re.sub(r'[$€£¥]?\d[\d.,]*\s*(?:%|[kKmMbB]n?\b|billion|million|trillion)?', ' ', title)
    return ' '.join(text.split())[:300] or title[:300]


def representative(matched: list[dict], actor: Optional[Actor] = None) -> dict:
    """A headline whose subject is the actor (named in its first words), then the one sharing the most words with
    the others of its group (the common story), the higher triage score and the newer one. The newest alone is
    often a side story (11/03: a denial by a third party); a gold story that names Iran in passing is not Iran's."""

    def subject(item):
        found = actor.pattern.search(item['title']) if actor else None
        return bool(found) and found.start() <= 12
    stems = {i['id']: {stem(w) for w in content_words(i['title'])} for i in matched}

    def shared(item):
        own = stems[item['id']]
        others = [stems[o['id']] for o in matched if o['id'] != item['id']]
        return sum(len(own & o) / (len(own | o) or 1) for o in others) / (len(others) or 1)
    return max(matched, key=lambda i: (subject(i), round(shared(i), 3),
                                       i.get('p') if isinstance(i.get('p'), (int, float)) else 0, i['published_at']))


FED_SPEAKER = re.compile(r"\bFed(?:'s|’s)?\s+(?:Governor\s+|President\s+|Chair\s+|Vice Chair\s+)?([A-Z][a-z]{2,})"
                         r"|,\s*(?:Fed(?:'s|’s)?\s+)?([A-Z][a-z]{2,})\s+says\b")
NOT_SPEAKERS = {'Rate', 'Rates', 'Holds', 'Leaves', 'Officials', 'Minutes', 'Decision', 'Policy', 'Meeting', 'Hike',
                'Cut', 'Pause', 'Chair', 'Governor', 'President', 'Board', 'Watch', 'Speakers', 'Statement'}


def fed_speaker(title: str) -> str:
    """The Fed official a headline is about ('Fed's Logan ...', '..., Logan says'), lower case, or ''."""
    for m in FED_SPEAKER.finditer(title):
        name = m.group(1) or m.group(2)
        if name not in NOT_SPEAKERS:
            return name.lower()
    return ''


# A bank's or broker's note re-published by a feed ('US Dollar: ... – DBS', 'Gold: ... - TD Securities') is
# commentary, not an actor's statement: it starts no search.
COMMENTARY = re.compile(r'\s[–—-]\s*[A-Z][A-Za-z&.]*(?:\s[A-Z][A-Za-z&.]*){0,2}\s*$')


def plan_queries(items: list[dict], asset: str, *, max_queries: int = MAX_QUERIES, min_publishers: int = 1) -> list[dict]:
    """One query per actor named in the headlines, strongest first (score, outlets, headlines)."""
    plans = []
    for actor in ACTORS:
        if asset not in actor.assets:
            continue
        own = actor_stems(actor)
        groups: dict = {}
        for item in items:
            if actor.pattern.search(item['title']) and not COMMENTARY.search(item['title']):
                # Headlines naming a person or firm (Fed's Logan, Fed's Barkin) are searched separately:
                # one query finds one statement, and different speakers are different statements.
                who = fed_speaker(item['title']) if actor.actor_id == 'fed' else ''
                groups.setdefault(who, []).append(item)
        for who, matched in groups.items():
            publishers = {i.get('publisher') for i in matched}
            if len(publishers) < min_publishers:
                continue
            best = representative(matched, actor)
            earliest = min(_time(i['published_at']) for i in matched)
            plans.append({'actor': actor.actor_id, 'actor_label': actor.label + (f'（{who.capitalize()}）' if who else ''),
                          'query': query_text(best['title']), 'include_domains': list(actor.scopes),
                          'fallback_query': f'{who.capitalize()} speech remarks' if who else f'{actor.search_name} official announcement update',
                          'start_published': _z(_day(earliest - LOOKBACK_EXTRA)),
                          'headline_ids': [i['id'] for i in matched],
                          '_rank': (max((i.get('p') or 0) for i in matched), len(publishers), len(matched))})
    plans.sort(key=lambda q: q['_rank'], reverse=True)
    for q in plans:
        q.pop('_rank')
    return plans[:max_queries]


def request_body(plan: dict) -> dict:
    return {'query': plan['query'], 'type': 'auto', 'numResults': NUM_RESULTS,
            'includeDomains': plan['include_domains'], 'startPublishedDate': plan['start_published'],
            'contents': {'text': {'maxCharacters': TEXT_CHARS}, 'maxAgeHours': MAX_AGE_HOURS}}


def _excerpt(text: str, headlines: list[str]) -> str:
    """The EXCERPT_CHARS window of the page with the most headline words, started at a sentence."""
    flat = ' '.join(text.split())
    words = {w[:5] if len(w) > 5 else w for h in headlines for w in content_words(h)}
    if len(flat) <= EXCERPT_CHARS or not words:
        return flat[:EXCERPT_CHARS]
    starts = [0] + [m.end() for m in re.finditer(r'(?<=[.!?])\s+', flat)]
    best, best_hits = 0, -1
    for s in starts:
        window = flat[s:s + EXCERPT_CHARS].lower()
        hits = sum(1 for w in words if w in window)
        if hits > best_hits:
            best, best_hits = s, hits
    return flat[best:best + EXCERPT_CHARS]


def accept_results(plan: dict, data: dict, headlines: dict, retrieved_at: datetime) -> tuple[list[dict], dict]:
    """Checked official pages of one response, and the count of refusals by reason."""
    actor = next(a for a in ACTORS if a.actor_id == plan['actor'])
    start = _time(plan['start_published'])
    pages, refused, seen = [], {}, set()

    def refuse(reason):
        refused[reason] = refused.get(reason, 0) + 1

    for row in (data.get('results') or [])[:NUM_RESULTS]:
        if not isinstance(row, dict):
            refuse('invalid_row')
            continue
        url, text = str(row.get('url') or ''), row.get('text')
        scope = scope_match(url, actor.scopes)
        if scope is None:
            refuse('outside_official_scope')
            continue
        if url in seen:
            refuse('duplicate_url')
            continue
        title = ' '.join(str(row.get('title') or '').split())[:300]
        if not isinstance(text, str) or body_words(text, title) < MIN_BODY_WORDS:
            refuse('no_body_text')
            continue
        published = _time(row.get('publishedDate')) if row.get('publishedDate') else None
        if published is None:
            refuse('no_published_date')
            continue
        if _day(published) > _day(retrieved_at) or published < start:
            refuse('outside_date_window')
            continue
        seen.add(url)
        whole = title + ' ' + text
        links = []
        skip = actor_stems(actor)
        for hid in plan['headline_ids']:
            h = headlines.get(hid)
            h_time = _time(h['published_at']) if h else None
            if h is None or h_time is None or _day(published) < _day(h_time) - timedelta(days=2):
                continue  # an older page cannot confirm it (Exa's day can be a day early: 'Withdrawals Resume (11-01)' read 10-31)
            score = overlap(h['title'], whole, skip)
            if score >= MATCH_MIN:
                links.append((hid, score))
        if not links:
            refuse('no_headline_match')  # an official page about something else is not shown
            continue
        midnight = published.hour == published.minute == published.second == 0 and published.microsecond == 0
        pages.append({
            'actor': actor.actor_id, 'actor_label': plan['actor_label'], 'url': url, 'host': urlsplit(url).hostname,
            'scope': scope, 'title': title,
            'exa_published': _z(published), 'published_precision': 'day' if midnight else 'exa_timestamp',
            'page_dates': list(dict.fromkeys(m.group(1) for m in DATE_LINE.finditer(text[:2000])))[:2],
            'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'text_chars': len(text),
            'excerpt': _excerpt(text, [headlines[h]['title'] for h, _ in links] or [plan['query']]),
            'matched': [{'headline_id': h, 'overlap': s} for h, s in links],
            'retrieved_at': _z(retrieved_at), 'via': 'exa_search',
        })
    return pages, refused


def _status_kind(code: int) -> Optional[str]:
    if 200 <= code < 300:
        return None
    return {400: 'bad_request', 401: 'auth_failed', 402: 'credits_exhausted', 403: 'auth_failed',
            429: 'rate_limited'}.get(code, 'server_error' if code >= 500 else 'http_error')


def search(items: list[dict], *, asset: str, api_key: Optional[str],
           post: Callable[[str, dict, dict, float], tuple], now: Optional[datetime] = None,
           max_queries: int = MAX_QUERIES, min_publishers: int = 1, clock=time.monotonic) -> dict:
    """Search official pages for the actors in ``items`` ({id, title, publisher, published_at, p?}).

    ``post(url, body, headers, timeout) -> (status_code, parsed_json_or_None)`` raises
    ``PrimaryFetchError`` on transport failure. Never raises; the record carries fixed labels only.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    record = {'provider': 'exa', 'endpoint': ENDPOINT, 'asset': asset, 'searched_at': _z(now), 'status': 'skipped',
              'reason': None, 'queries': [], 'pages': [], 'by_headline': {}, 'cost_usd': 0.0,
              'limits': {'max_queries': max_queries, 'num_results': NUM_RESULTS, 'max_age_hours': MAX_AGE_HOURS,
                         'match_min': MATCH_MIN}}
    usable = [i for i in items if isinstance(i, dict) and i.get('id') and i.get('title') and _time(i.get('published_at'))]
    plans = plan_queries(usable, asset, max_queries=max_queries, min_publishers=min_publishers)
    if not plans:
        record['reason'] = 'no_official_actor'
        return record
    if not api_key:
        record['reason'] = 'credentials_missing'
        record['queries'] = [{k: q[k] for k in ('actor', 'actor_label', 'query', 'headline_ids')} | {'status': 'not_attempted'}
                             for q in plans]
        return record
    headlines = {i['id']: i for i in usable}
    stopped, requests = None, 0
    queue = [(index, plan, False) for index, plan in enumerate(plans)]
    while queue:
        index, plan, is_fallback = queue.pop(0)
        entry = {k: plan[k] for k in ('actor', 'actor_label', 'query', 'include_domains', 'start_published',
                                       'headline_ids')} | {'index': len(record['queries']), 'fallback': is_fallback}
        if stopped or requests >= MAX_REQUESTS:
            entry.update(status='not_attempted', error_kind=stopped or 'request_limit')
            record['queries'].append(entry)
            continue
        requests += 1
        started = clock()
        try:
            code, data = post(ENDPOINT, request_body(plan), {'x-api-key': api_key, 'Content-Type': 'application/json'},
                              TIMEOUT_SECONDS)
            kind = _status_kind(int(code))
            if kind is None and not isinstance(data, dict):
                kind = 'parse_error'
        except PrimaryFetchError as error:
            kind, data = error.kind, None
        except Exception:  # noqa: BLE001 - fixed label only
            kind, data = 'unexpected', None
        entry['elapsed_ms'] = round((clock() - started) * 1000)
        if kind:
            entry.update(status='failed', error_kind=kind)
            if kind in ('credits_exhausted', 'auth_failed'):
                stopped = kind
            record['queries'].append(entry)
            continue
        cost = (data.get('costDollars') or {}).get('total')
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and 0 <= cost < 10:
            entry['cost_usd'] = float(cost)
            record['cost_usd'] = round(record['cost_usd'] + float(cost), 6)
        request_id = data.get('requestId')
        if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', request_id):
            entry['request_id'] = request_id
        pages, refused = accept_results(plan, data, headlines, now)
        entry.update(status='ok', results=len(data.get('results') or []), accepted=len(pages), refused=refused)
        record['queries'].append(entry)
        if not is_fallback and (not pages or len(plan['headline_ids']) >= MAJOR_EVENT_HEADLINES):
            # Media wording often finds nothing on the official site ('net outflows', 'record hack'); the site's
            # own wording ('official announcement update', a speaker's 'speech remarks') does. A story told by
            # many headlines usually has several official notices (Binance: four updates in four days).
            queue.insert(0, (index, plan | {'query': plan['fallback_query']}, True))
        for page in pages:
            if any(p['url'] == page['url'] for p in record['pages']):
                continue
            page['query_index'] = entry['index']
            page['id'] = f'p{len(record["pages"]) + 1:02d}'
            record['pages'].append(page)
    # One page per headline (the best word match, then the newest page); a page that is nobody's best is
    # dropped, so each headline points at exactly one original statement.
    best = {}
    for page in record['pages']:
        for m in page['matched']:
            rank = (m['overlap'], page['exa_published'])
            if m['headline_id'] not in best or rank > best[m['headline_id']][1]:
                best[m['headline_id']] = (page['id'], rank)
    record['by_headline'] = {h: v[0] for h, v in best.items()}
    kept = []
    for page in record['pages']:
        page['matched'] = [m for m in page['matched'] if record['by_headline'].get(m['headline_id']) == page['id']]
        if page['matched']:
            page['id'] = f'p{len(kept) + 1:02d}'
            kept.append(page)
    record['by_headline'] = {m['headline_id']: page['id'] for page in kept for m in page['matched']}
    record['pages'] = kept
    states = [q['status'] for q in record['queries']]
    record['status'] = 'ok' if all(s == 'ok' for s in states) else 'unavailable' if 'ok' not in states else 'partial'
    if stopped:
        record['reason'] = stopped
    return record


def requests_post(url: str, body: dict, headers: dict, timeout: float) -> tuple:
    """Plain HTTPS POST for the XAU collector (requests); fixed labels on failure, no redirects."""
    import requests
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout, allow_redirects=False)
    except requests.exceptions.Timeout:
        raise PrimaryFetchError('timeout') from None
    except requests.exceptions.RequestException:
        raise PrimaryFetchError('connection') from None
    if not 200 <= resp.status_code < 300:
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


def format_lines(record: Optional[dict], headlines: dict, tz=None) -> list[str]:
    """Model-input lines: what was searched, each checked official page, and the headlines it backs."""
    tz = tz or timezone(timedelta(hours=9))
    lines = ['### 一次資料（公式ページの本文・Exa検索）']
    if not isinstance(record, dict):
        lines.append('- 未実施')
        return lines
    if record.get('status') == 'skipped':
        lines.append(f"- 未実施（{record.get('reason')}）")
        return lines
    asked = []
    for q in record.get('queries') or []:
        shown = sum(1 for p in record.get('pages') or [] if p.get('query_index') == q.get('index'))
        state = (f"{shown}件採用" if q.get('status') == 'ok'
                 else f"失敗: {q.get('error_kind')}" if q.get('status') == 'failed' else f"未実施: {q.get('error_kind')}")
        asked.append(f"{q['actor_label']}（{state}）")
    lines.append(f"- 照会 {len(record.get('queries') or [])}件: {'・'.join(asked)}。費用見積 {record.get('cost_usd', 0):.3f} USD。"
                 f"取得 {datetime.fromisoformat(record['searched_at'].replace('Z', '+00:00')).astimezone(tz):%m/%d %H:%M} JST")
    for page in record.get('pages') or []:
        day = datetime.fromisoformat(page['exa_published'].replace('Z', '+00:00'))
        when = f"{day:%Y-%m-%d}（日付のみ）" if page['published_precision'] == 'day' else f"{day.astimezone(tz):%m/%d %H:%M} JST"
        own = f"、本文の日付「{' / '.join(page['page_dates'])}」" if page.get('page_dates') else ''
        backs = [m['headline_id'] for m in page.get('matched') or []]
        titles = [f"{h}「{headlines[h]['title'][:80]}」" for h in backs if h in headlines]
        lines.append(f"- {page['id']} [{page['actor_label']}・{page['host']}] {page['title']} — {page['url']}"
                     f"（Exaの公表日 {when}{own}）")
        lines.append(f"  抜粋: {page['excerpt']}")
        lines.append(f"  対応する見出し: {'、'.join(titles) if titles else 'なし（語の一致が基準未満）'}")
    if not record.get('pages'):
        lines.append('- 採用できる公式ページなし')
    lines.append('※ 公式サイトの本文（Exaの取得・キャッシュ経由）。同じページに対応する見出しは同じ元情報の再報道として一つに数える。'
                 '対応はコードの語の一致による候補で、見出しの主張を本文が支えるかは抜粋で確認する')
    return lines
