"""Official pages behind BTC news leads (btc.primary_sources) and their use in BTC news. No network."""
import json
from datetime import datetime, timezone

from btc import carry, news
from btc.fetch import SourceResult
from btc import primary_sources as ps

NOW = datetime(2026, 10, 30, 9, 2, tzinfo=timezone.utc)
KEY = 'exa-test-key-never-shown'
BINANCE_TEXT = ('Security Incident Update #2 (2026-10-30) Published on 2026-10-30 15:33 Fellow Binancians, our hot wallets '
                'were compromised, resulting in a loss of approximately $6.3B USD worth of token assets. Our tech and '
                'security teams are still deep in the investigations of this complex hack. Binance will cover all '
                'stolen funds. We have worked with blockchain partners to freeze affected assets, with about $3 billion '
                'frozen so far. The initial investigation suggested a compromised engineer laptop. Withdrawals were '
                'temporarily suspended while security checks are completed. This security breach is the biggest '
                'incident in our history, and we are investigating it with law enforcement. ') * 2
LOGAN_TEXT = ('Remarks to Davidson College students. It remains clear to me, given the current outlook, that the FOMC '
              'needs to implement at least two more 25-basis-point rate increases. After that, it would make sense in '
              'my view to pause and examine developments; the path of policy should not be altered by one pause. '
              'Lorie Logan, Federal Reserve Bank of Dallas. ') * 2


def headline(ident, title, publisher='CoinDesk', published='2026-10-29T21:12:45Z', p=None):
    return {'id': ident, 'title': title, 'publisher': publisher, 'published_at': published, 'p': p}


def result(url, title, text, published='2026-10-30T00:00:00.000Z'):
    return {'url': url, 'title': title, 'publishedDate': published, 'text': text}


class Post:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, dict(headers)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


BINANCE_HEADLINES = [
    headline('n049', "Binance loses over $6 billion in 'biggest crypto hack in history'"),
    headline('n048', 'Binance investigates multibillion-dollar security breach', 'Cointelegraph', '2026-10-29T21:24:36Z'),
    headline('n040', 'Binance points to engineer’s hacked laptop in $6.3B breach', 'Cointelegraph', '2026-10-30T00:11:00Z'),
    headline('n006', 'Live updates: Binance $6.3B hack linked to North Korea’s TraderTraitor', 'Cointelegraph',
             '2026-10-30T08:00:00Z'),
]
ANNOUNCEMENT = 'https://www.binance.com/en/support/announcement/detail/d3306d9dca5243548c813b605e1d1751'


def binance_response():
    return 200, {'requestId': 'req_1', 'costDollars': {'total': 0.007}, 'results': [
        result(ANNOUNCEMENT, 'Security Incident Update #2 (2026-10-30)', BINANCE_TEXT),
        result('https://www.binance.com/en/price/marscoin-meme-bsc', 'MarsCoin Price', BINANCE_TEXT),
        result('https://www.binance.com/en/support/announcement', 'Announcements', BINANCE_TEXT),
        result('https://www.binance.com/en/support/announcement/detail/old', 'Old notice', BINANCE_TEXT,
               '2026-10-01T00:00:00.000Z'),
    ]}


def test_scope_keeps_official_statements_and_refuses_index_price_and_foreign_pages():
    scopes = ('binance.com/en/support/announcement',)
    assert ps.scope_match(ANNOUNCEMENT, scopes) == scopes[0]
    assert ps.scope_match('https://www.binance.com/en/support/announcement', scopes) is None  # the index page
    assert ps.scope_match('https://www.binance.com/en/price/btc', scopes) is None
    assert ps.scope_match('http://www.binance.com/en/support/announcement/detail/x', scopes) is None
    assert ps.scope_match('https://binance.com.evil.example/en/support/announcement/detail/x', scopes) is None
    assert ps.scope_match('https://www.binance.com:8443/en/support/announcement/detail/x', scopes) is None
    assert ps.scope_match('https://www.dallasfed.org/news/speeches/logan/2026/lkl261030', ps.REGIONAL_FED)
    assert ps.scope_match('https://www.dallasfed.org/', ps.REGIONAL_FED) is None


def test_a_named_official_must_appear_on_the_page():
    fed = next(a for a in ps.ACTORS if a.actor_id == 'fed')
    skip = ps.actor_stems(fed)
    assert ps.overlap('Fed needs at least two more rate hikes, Logan says', LOGAN_TEXT, skip) >= ps.MATCH_MIN
    assert ps.overlap('Fed’s Barkin favors gradual approach as inflation pressure spreads', LOGAN_TEXT, skip) == 0.0
    # Title case: capitals carry no meaning, so no name is required (Decrypt, Bitcoin Magazine).
    assert ps.names('Binance Drained of $5.5 Billion in Suspected Hack, Tether and Circle Freeze') == set()
    assert ps.names("Fed's Musalem calls for rate hike soon") == {'musalem'}


def test_plan_one_query_per_actor_and_speaker_strongest_first():
    items = [headline('k1', 'Fed needs at least two more rate hikes, Logan says', 'Investing.com', p=0.9),
             headline('k2', 'Fed’s Logan calls for two more rate hikes; pause won’t alter path', 'FXStreet', p=0.8),
             headline('k3', 'Fed’s Musalem calls for rate hike soon as policy remains too loose', 'FXStreet', p=0.7),
             headline('k4', 'BoJ keeps policy settings unchanged', 'FXStreet', p=0.4)]
    plans = ps.plan_queries(items, max_queries=2)
    assert [(q['actor_label'], q['headline_ids']) for q in plans] == [
        ('Federal Reserve（Logan）', ['k1', 'k2']), ('Federal Reserve（Musalem）', ['k3'])]
    assert plans[0]['query'] == items[0]['title'] and plans[0]['start_published'] == '2026-10-27T00:00:00Z'
    # Before triage: an actor named by a single outlet costs no search; other central banks are not searched.
    assert ps.plan_queries([headline('n1', 'Kraken lists a token')], min_publishers=2) == []
    assert all(q['actor'] == 'fed' for q in ps.plan_queries(items))


def test_search_links_each_headline_to_one_checked_page_and_never_returns_the_key():
    post = Post([binance_response()])
    record = ps.search(BINANCE_HEADLINES, api_key=KEY, post=post, now=NOW)
    assert record['status'] == 'ok' and record['cost_usd'] == 0.007 and len(post.calls) == 1
    body = post.calls[0][1]
    assert body['includeDomains'] == ['binance.com/en/support/announcement'] and post.calls[0][2]['x-api-key'] == KEY
    assert [p['url'] for p in record['pages']] == [ANNOUNCEMENT]
    page = record['pages'][0]
    assert page['published_precision'] == 'day' and page['page_dates'] == ['2026-10-30']
    assert page['excerpt'] in ' '.join(BINANCE_TEXT.split()) and 'text' not in page
    assert set(record['by_headline']) == {'n049', 'n048', 'n040'}  # n006 names North Korea, absent from the page
    refused = record['queries'][0]['refused']
    assert refused['outside_official_scope'] == 2 and refused['outside_date_window'] == 1  # price + index pages
    assert KEY not in json.dumps(record)


def test_missing_key_credit_exhaustion_and_transport_failures_are_fixed_labels():
    post = Post([])
    record = ps.search(BINANCE_HEADLINES, api_key=None, post=post, now=NOW)
    assert record['status'] == 'skipped' and record['reason'] == 'credentials_missing' and post.calls == []
    items = BINANCE_HEADLINES + [headline('n082', 'U.S. Treasury sanctions crypto launderers', 'Decrypt'),
                                 headline('n105', 'U.S. sanctions Russian nationals over crypto laundering', 'CoinDesk')]
    post = Post([(402, None)])
    record = ps.search(items, api_key=KEY, post=post, now=NOW)
    assert record['reason'] == 'credits_exhausted' and len(post.calls) == 1  # nothing more is asked or paid
    assert [q['status'] for q in record['queries']] == ['failed', 'not_attempted'] and record['status'] == 'unavailable'
    post = Post([ps.PrimaryFetchError('timeout'), binance_response()])
    record = ps.search(items, api_key=KEY, post=post, now=NOW)
    assert record['status'] == 'partial' and record['queries'][0]['error_kind'] == 'timeout'


def test_btc_headlines_backed_by_the_official_page_become_primary_and_one_event(monkeypatch):
    monkeypatch.setattr(news, 'body_check', lambda f, item: {'status': 'not_attempted', 'error_kind': 'budget_exhausted'})
    monkeypatch.setattr(news, 'reaction', lambda *a: None)

    class F:
        class deadline:
            remaining = staticmethod(lambda: 100.0)
    rows = [{**h, 'source_id': 'coindesk_rss', 'official': False, 'url': f'https://www.coindesk.com/a/{h["id"]}',
             'excerpt': '', 'first_seen_at': '2026-10-30T09:00:00Z'} for h in BINANCE_HEADLINES]
    search = ps.search(BINANCE_HEADLINES, api_key=KEY, post=Post([binance_response()]), now=NOW)
    record = SourceResult('news', status='ok', retrieved_at='2026-10-30T09:00:00Z', source_url=news.GENERAL_FEEDS[0][2],
                          values={'window_start': 'a', 'window_end': 'b', 'lookback_hours': 36, 'feeds': [],
                                  'candidate_count': len(rows), 'candidates': rows, 'primary_search': search,
                                  'feeds_ok': {'general': 7, 'official': 3}, 'headline_elapsed_ms': 5}).to_dict()
    values = news.finish_news(F(), record, {r['id']: 0.9 for r in rows}, {'mode': 'jev'}, now=NOW).to_dict()['values']
    kept = {k['id']: k for k in values['kept']}
    assert {i for i, k in kept.items() if k['code_verification'] == 'primary_body_retrieved'} == {'n049', 'n048', 'n040'}
    assert kept['n006']['code_verification'] == 'headline_only' and 'primary' not in kept['n006']
    assert kept['n049']['primary']['url'] == ANNOUNCEMENT and 'text' not in kept['n049']['primary']
    summary = values['primary_search']
    assert summary['pages'] == 1 and 'pages_detail' not in summary and KEY not in json.dumps(values)
    # The same official page links the headlines as one known event (URL of the page, stable across fetches).
    ids = [set(carry.item_identifiers(kept[i])) for i in ('n049', 'n048')]
    assert f'url:{ANNOUNCEMENT}' in ids[0] & ids[1]

