"""Collection plan for one USOIL run (design 3, 4; P8).

Three phases inside one budget (design 3.1: 540 s collection, wall clock from
the collection start):
1. ``collect_context`` (collect worker, the only process holding the FRED key):
   slow-moving and history sources in parallel (Yahoo OHLC, CFTC COT, EIA WPSR,
   FRED, ForexFactory, official calendars), plus news headlines only; then the
   entry, outside the sandbox, runs the Jev headline triage
   (usoil.headline_triage);
2. ``finish_collection`` (news-detail worker, sandboxed, no secrets): news
   selection, article bodies, reactions and clusters;
3. the fresh same-day quotes last (CNBC futures/term structure and the Cboe
   OVX quote), so that the reference prices are as fresh as possible when the
   collection closes (as_of).

CME official data is never contacted (paid/terms, P8); it is recorded as
terms_restricted. Only normalised records and raw_sha256 values leave this
module.
"""
from __future__ import annotations

from datetime import datetime, timezone

from usoil import SYMBOL
from usoil.common import JST
from usoil.fetch import SourceTask, collect_parallel
from usoil.sources import calendar, cot, eia, macro, market, vol

UTC = timezone.utc
COLLECTOR_VERSION = 'usoil-collector-1.0'
BUDGET_SECONDS = 540
QUOTES_PHASE_SECONDS = 110  # reserved for the fresh same-day quotes
NEWS_DETAIL_SECONDS = 120  # article bodies (90 s) + reactions
MAX_WORKERS = 6           # design 3.1: six concurrent requests overall
DAILY_LOOKBACK_HOURS = (36, 72)  # max(36 h, since last success), capped at 72 h (design 9)
WEEKLY_LOOKBACK_HOURS = 168
TERMS_RESTRICTED = (
    {'source_id': 'cme_group', 'status': 'terms_restricted', 'reason': '規約・有料のため未使用',
     'detail': 'CME Group の日次settle・公式ヒストリカル等は規約/有料のため取得しない。OHLCはYahoo、当日値はCNBCで代替する。'},
    {'source_id': 'baker_hughes', 'status': 'not_applicable', 'reason': '403で取得不可のため不採用',
     'detail': 'rigcount.bakerhughes.com は403（2026-10-16実測）。代替源が得られるまでリグ数因子は扱わない。'},
)

# Sources this collector may contact (hash recorded in machine.json provenance).
SOURCE_CATALOG = (
    ('yahoo_ohlc', 'https://query1.finance.yahoo.com/v8/finance/chart/'),
    ('cftc_cot', 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'),
    ('eia_wpsr', 'https://ir.eia.gov/wpsr/table1.csv'),
    ('fred', 'https://api.stlouisfed.org/fred/series/observations'),
    ('cnbc_quotes', 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol'),
    ('cboe_ovx', 'https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/_OVX.json'),
    ('forexfactory_week', 'https://nfs.faireconomy.media/ff_calendar_thisweek.json'),
    ('fed_calendar', 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'),
    ('bea_calendar', 'https://www.bea.gov/news/schedule'), ('bls_schedule_cache', 'https://www.bls.gov/schedule/'),
    ('news', 'rss: oilprice, investing (headlines only), '
             'eia todayinenergy, cftc'),
)


def source_catalog_sha256() -> str:
    import hashlib
    import json
    data = {'version': COLLECTOR_VERSION, 'sources': SOURCE_CATALOG,
            'not_used': [{k: v for k, v in t.items() if k != 'detail'} for t in TERMS_RESTRICTED]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def news_lookback_hours(mode: str, now: datetime, last_success: datetime | None) -> float:
    if mode == 'weekly':
        return WEEKLY_LOOKBACK_HOURS
    low, high = DAILY_LOOKBACK_HOURS
    if last_success is None:
        return low
    since = (now - last_success).total_seconds() / 3600
    return round(min(high, max(low, since)), 2)


def first_phase_tasks(mode: str, now: datetime, lookback_hours: float) -> list[SourceTask]:
    from usoil import news

    today = now.astimezone(UTC).date()
    return [
        SourceTask('yahoo_ohlc', market.yahoo_ohlc),
        SourceTask('cftc_cot', cot.cot, cot.COT_URL),
        SourceTask('eia_wpsr', eia.wpsr, eia.EIA_URL),
        SourceTask('fred', macro.fred, 'https://api.stlouisfed.org/fred/series/observations'),
        SourceTask('forexfactory_week', calendar.forexfactory_week, calendar.FF_URL),
        SourceTask('fed_calendar', lambda f: calendar.fed_calendar(f, today),
                   'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'),
        SourceTask('bea_calendar', lambda f: calendar.bea_calendar(f, today), 'https://www.bea.gov/news/schedule'),
        SourceTask('news', lambda f: news.collect_headline_source(f, now=now.astimezone(UTC),
                                                                  lookback_hours=lookback_hours)),
    ]


def quote_phase_tasks() -> list[SourceTask]:
    return [SourceTask('cnbc_quotes', macro.cnbc_quotes, macro.CNBC_URL),
            SourceTask('cboe_ovx', vol.ovx, vol.OVX_URL)]


def collect_context(mode: str, *, started_at: datetime, last_success: datetime | None = None,
                    budget_seconds: float = BUDGET_SECONDS, first=None, fetcher_factory=None) -> dict:
    """Phase 1 (collect worker); never raises for a source failure (each becomes a fixed-label record)."""
    lookback = news_lookback_hours(mode, started_at, last_success)
    first = first if first is not None else first_phase_tasks(mode, started_at, lookback)
    one = collect_parallel(first, budget_seconds=max(30.0, budget_seconds - QUOTES_PHASE_SECONDS - NEWS_DETAIL_SECONDS),
                           max_workers=MAX_WORKERS, fetcher_factory=fetcher_factory)
    return {
        'symbol': SYMBOL, 'collector_version': COLLECTOR_VERSION, 'mode': mode,
        'collection_started_at': started_at.astimezone(JST).isoformat(),
        'news_lookback_hours': lookback,
        'last_success_at': last_success.astimezone(JST).isoformat() if last_success else None,
        'budget_seconds': budget_seconds, 'budget_exhausted': one['budget_exhausted'],
        'phases': [{'name': 'context', 'elapsed_ms': one['elapsed_ms'], 'budget_seconds': one['budget_seconds']}],
        'sources': one['results'],
    }


def news_record(context: dict) -> dict | None:
    return next((r for r in context.get('sources', []) if r.get('source_id') == 'news'), None)


def _elapsed(started_at: datetime) -> float:
    return max(0.0, (datetime.now(JST) - started_at).total_seconds())


def finish_collection(context: dict, scores: dict | None, selection: dict, *, second=None,
                      fetcher_factory=None) -> dict:
    """Phases 2 and 3 (news-detail worker, no secrets) inside the remaining collection budget."""
    from usoil import news
    from usoil.common import parse_time

    started_at = parse_time(context['collection_started_at'])
    budget_seconds = float(context.get('budget_seconds') or BUDGET_SECONDS)
    sources = [dict(r) for r in context['sources']]
    phases = list(context['phases'])
    exhausted = bool(context.get('budget_exhausted'))
    index = next((i for i, r in enumerate(sources) if r.get('source_id') == 'news'), None)
    if index is not None:
        record = sources[index]
        if record.get('status') in ('ok', 'partial') and isinstance((record.get('values') or {}).get('candidates'), list):
            remaining = budget_seconds - _elapsed(started_at) - QUOTES_PHASE_SECONDS
            detail = collect_parallel(
                [SourceTask('news', lambda f: news.finish_news(f, record, scores, selection,
                                                               now=started_at.astimezone(UTC)),
                            record.get('source_url'))],
                budget_seconds=min(NEWS_DETAIL_SECONDS, max(30.0, remaining)), max_workers=1,
                fetcher_factory=fetcher_factory)
            sources[index] = detail['results'][0]
            exhausted = exhausted or detail['budget_exhausted']
            phases.append({'name': 'news_detail', 'elapsed_ms': detail['elapsed_ms'],
                           'budget_seconds': detail['budget_seconds']})
        else:
            (record.get('values') or {}).pop('candidates', None)
    second = second if second is not None else quote_phase_tasks()
    remaining = max(30.0, budget_seconds - _elapsed(started_at))
    two = collect_parallel(second, budget_seconds=min(remaining, QUOTES_PHASE_SECONDS + 30), max_workers=10,
                           fetcher_factory=fetcher_factory)
    phases.append({'name': 'quotes', 'elapsed_ms': two['elapsed_ms'], 'budget_seconds': two['budget_seconds']})
    completed = datetime.now(JST)
    return {
        'symbol': SYMBOL, 'collector_version': COLLECTOR_VERSION, 'mode': context['mode'],
        'collection_started_at': context['collection_started_at'],
        'collection_completed_at': completed.isoformat(),
        'news_lookback_hours': context['news_lookback_hours'],
        'last_success_at': context['last_success_at'],
        'budget_seconds': budget_seconds,
        'elapsed_ms': round((completed - started_at).total_seconds() * 1000, 1),
        'budget_exhausted': exhausted or two['budget_exhausted'],
        'phases': phases,
        'sources': sources + two['results'],
        'terms_restricted': [dict(item) for item in TERMS_RESTRICTED],
    }
