"""Macro and geopolitics headlines (Investing.com): zone-less UTC times, their own places, headlines only. No network."""
from datetime import datetime, timezone

from btc import headline_triage as triage
from btc import news
from btc.fetch import SourceResult

NOW = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)
INVESTING = b'''<?xml version="1.0"?><rss><channel><title>t</title><item><title>Oil rises as Iran tensions grow</title>
<link>https://www.investing.com/news/commodities-news/oil-1</link><pubDate>2026-10-14 23:10:00</pubDate></item>
</channel></rss>'''
ZONED = b'''<?xml version="1.0"?><rss><channel><title>t</title><item><title>Bitcoin ETF flows</title>
<link>https://www.coindesk.com/markets/a</link><pubDate>Wed, 14 Oct 2026 23:00:00 +0000</pubDate></item>
</channel></rss>'''


def row(index, source_id='coindesk_rss', published='2026-10-14T20:00:00Z', title=None):
    return {'id': f'n{index:03d}', 'source_id': source_id, 'publisher': 'x', 'official': False,
            'title': title or f'Synthetic headline {index}', 'url': f'https://www.coindesk.com/synthetic/{index}',
            'published_at': published, 'excerpt': ''}


class FeedFetcher:
    class Response:
        def __init__(self, body):
            self.body, self.retrieved_at, self.raw_sha256 = body, '2026-10-15T00:00:00+00:00', '0' * 64

    def get(self, url, timeout=15):
        return self.Response(INVESTING if 'investing.com' in url else ZONED)


def test_investing_times_without_a_zone_are_read_as_utc():
    candidates, feeds = news.collect_headlines(FeedFetcher(), datetime(2026, 10, 13, 12, tzinfo=timezone.utc), NOW)
    oil = [c for c in candidates if c['publisher'] == 'Investing.com']
    assert [(c['source_id'], c['published_at']) for c in oil] == [('investing_commodities_rss', '2026-10-14T23:10:00Z')]
    assert {f['source_id']: f['items'] for f in feeds}['investing_economy_rss'] == 0  # the same item, de-duplicated
    assert len(feeds) == len(news.GENERAL_FEEDS) + len(news.OFFICIAL_FEEDS)


def test_macro_headlines_keep_their_places_and_never_push_out_btc_news():
    crypto = [row(i) for i in range(1, 31)]
    macro = [row(i, 'investing_commodities_rss') for i in range(31, 39)]
    scores = {c['id']: 0.9 for c in crypto} | {m['id']: 0.6 + i / 100 for i, m in enumerate(macro)}
    kept = news.select(crypto + macro, scores)
    assert len(kept) == triage.KEEP_MAX
    assert [k['id'] for k in kept if k['source_id'] in news.MACRO_IDS] == ['n038', 'n037', 'n036', 'n035', 'n034']
    # Two macro headlines above the threshold: the other feeds take the rest of the places.
    kept = news.select(crypto + macro, scores | {m['id']: 0.1 for m in macro[2:]})
    assert len(kept) == triage.KEEP_MAX
    assert [k['id'] for k in kept if k['source_id'] in news.MACRO_IDS] == ['n032', 'n031']


def test_keyword_rule_keeps_geopolitics_within_the_macro_places():
    crypto = [row(i, title=f'Bitcoin ETF flows {i}') for i in range(1, 31)]
    macro = [row(i, 'investing_economy_rss', f'2026-10-14T{i - 30:02d}:00:00Z', f'Oil rises as Iran tensions grow {i}')
             for i in range(31, 39)]
    kept = news.select(crypto + macro, None)
    assert len(kept) == triage.KEEP_MAX
    assert [k['id'] for k in kept if k['source_id'] in news.MACRO_IDS] == ['n038', 'n037', 'n036', 'n035', 'n034']
    assert news.FALLBACK.search('Oil rises as Houthi-Saudi clashes, Iranian attacks on ships boost supply fears')
    assert news.FALLBACK.search('U.S. proposes unfreezing Russian assets to restart Ukraine peace talks')
    assert not news.FALLBACK.search('Analysts warn of slower growth toward year end')  # "war" only as a word


def test_macro_headlines_are_never_fetched_and_use_no_body_place(monkeypatch):
    calls = []
    monkeypatch.setattr(news, 'body_check', lambda f, item: calls.append(item['id']) or {'status': 'retrieved'})
    monkeypatch.setattr(news, 'reaction', lambda *a: {'status': 'ok'})

    class F:
        class deadline:
            remaining = staticmethod(lambda: 100.0)
    rows = [row(i, 'investing_economy_rss', f'2026-10-14T2{i}:00:00Z') for i in range(1, 4)] + \
        [row(i) for i in range(4, 14)]
    record = SourceResult('news', status='ok', retrieved_at='2026-10-15T00:00:00Z', source_url=news.GENERAL_FEEDS[0][2],
                          values={'window_start': 'a', 'window_end': 'b', 'lookback_hours': 36, 'feeds': [],
                                  'candidate_count': len(rows), 'candidates': rows,
                                  'feeds_ok': {'general': 7, 'official': 3}, 'headline_elapsed_ms': 5}).to_dict()
    values = news.finish_news(F(), record, {r['id']: 0.9 for r in rows}, {'mode': 'jev'}, now=NOW).to_dict()['values']
    macro = [k for k in values['kept'] if k['source_id'] in news.MACRO_IDS]
    assert len(macro) == 3 and all(k['body'] == {'status': 'not_attempted', 'error_kind': 'not_used_by_terms'}
                                   and k['code_verification'] == 'headline_only' for k in macro)
    assert len(calls) == news.MAX_BODIES and not set(calls) & {k['id'] for k in macro}
    assert values['selection_rule']['macro_keep_max'] == news.MACRO_KEEP
    assert not news.body_url_allowed('https://www.investing.com/news/commodities-news/oil-1', 'investing_commodities_rss')
