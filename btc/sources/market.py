"""Spot quotes (reference price inputs), Kraken BTC/USD bars and observed order books.

Books: three snapshots per venue (0/30/60 s). Only aggregates are kept (P8):
mid, best bid/ask, depth within 25/50/100/200 bp, reach, and 25 bp buckets on
a fixed absolute grid anchored at the first snapshot's mid (±200 bp).
Bars: closed 1 h and 1 d Kraken XBTUSD bars (USD venue) for the named levels
(btc/levels.py); the newest bar is still forming and is dropped.
"""
from __future__ import annotations

import time

from btc.fetch import FetchError, Fetcher, SourceResult
from datetime import datetime, timedelta

from btc.sources.base import from_s, json_of, num, ok, z

BUCKET_BPS = 25
RANGE_BPS = 200
DEPTH_BANDS = (25, 50, 100, 200)
SNAPSHOT_OFFSETS = (0, 30, 60)
OHLC_INTERVALS = (('h1', 60), ('d1', 1440))  # (part, minutes): about 720 bars each


# ------------------------------------------------------------------ quotes

def _kraken_pair(f: Fetcher, pair: str, key: str, source_id: str) -> SourceResult:
    r = f.get('https://api.kraken.com/0/public/Ticker', params={'pair': pair})
    data = json_of(r)
    if data.get('error'):
        raise FetchError('invalid_payload')
    row = data['result'][key]
    bid, ask, last = num(row['b'][0]), num(row['a'][0]), num(row['c'][0])
    if not 0 < bid <= ask:
        raise FetchError('invalid_payload')
    # Kraken's ticker carries no timestamp: the observation is the retrieval interval.
    return ok(source_id, [r], {'bid': bid, 'ask': ask, 'mid': (bid + ask) / 2, 'last': last,
                               'venue': 'kraken', 'pair': pair, 'quote_currency': 'USD'},
              timestamp_quality='receipt_interval')


def kraken_ticker(f: Fetcher) -> SourceResult:
    return _kraken_pair(f, 'XBTUSD', 'XXBTZUSD', 'kraken_ticker')


def kraken_usdt(f: Fetcher) -> SourceResult:
    return _kraken_pair(f, 'USDTUSD', 'USDTZUSD', 'kraken_usdt')


def bitstamp_ticker(f: Fetcher) -> SourceResult:
    r = f.get('https://www.bitstamp.net/api/v2/ticker/btcusd/')
    row = json_of(r)
    bid, ask = num(row['bid']), num(row['ask'])
    if not 0 < bid <= ask:
        raise FetchError('invalid_payload')
    return ok('bitstamp_ticker', [r], {'bid': bid, 'ask': ask, 'mid': (bid + ask) / 2, 'last': num(row['last']),
                                       'venue': 'bitstamp', 'pair': 'BTCUSD', 'quote_currency': 'USD'},
              observed_at=from_s(row['timestamp']))


def binance_spot(f: Fetcher) -> SourceResult:
    r = f.get('https://api.binance.com/api/v3/ticker/bookTicker', params={'symbol': 'BTCUSDT'})
    row = json_of(r)
    bid, ask = num(row['bidPrice']), num(row['askPrice'])
    if not 0 < bid <= ask:
        raise FetchError('invalid_payload')
    return ok('binance_spot', [r], {'bid': bid, 'ask': ask, 'mid': (bid + ask) / 2, 'venue': 'binance',
                                    'pair': 'BTCUSDT', 'quote_currency': 'USDT'},
              timestamp_quality='receipt_interval')


# -------------------------------------------------------------------- bars

def _kraken_bars(f: Fetcher, interval: int):
    r = f.get('https://api.kraken.com/0/public/OHLC', params={'pair': 'XBTUSD', 'interval': interval})
    data = json_of(r)
    if not isinstance(data, dict) or data.get('error'):
        raise FetchError('invalid_payload')
    result = data.get('result') or {}
    series = [v for k, v in result.items() if k != 'last']  # keyed by the pair name, e.g. 'XXBTZUSD'
    if len(series) != 1 or not isinstance(series[0], list):
        raise FetchError('missing_field')
    # 'last' is the start of the newest bar, which is still open: keep bars that closed before retrieval.
    received = datetime.fromisoformat(r.retrieved_at).timestamp()
    bars, invalid = {}, 0
    for row in series[0]:
        start = int(num(row[0]))
        if start + interval * 60 > received:
            continue
        o, h, l, c = (num(x) for x in row[1:5])
        if not 0 < l <= min(o, c) <= max(o, c) <= h:
            invalid += 1  # dropped, never repaired: the hole shows up as a gap caution in btc/levels.py
            continue
        bars[start] = {'t': from_s(start), 'o': o, 'h': h, 'l': l, 'c': c, 'v': num(row[6])}
    if not bars:
        raise FetchError('empty_payload')
    return r, [bars[k] for k in sorted(bars)], invalid


def kraken_ohlc(f: Fetcher) -> SourceResult:
    responses, values, failures, invalid = {}, {}, [], {}
    for part, interval in OHLC_INTERVALS:
        try:
            responses[part], values[part], invalid[part] = _kraken_bars(f, interval)
        except (FetchError, KeyError, IndexError, TypeError) as error:
            failures.append({'part': part, 'error_kind': error.kind if isinstance(error, FetchError) else 'missing_field'})
            values[part] = []
    if 'h1' not in responses:
        raise FetchError(failures[0]['error_kind'])
    last = values['h1'][-1]['t']
    return ok('kraken_ohlc', responses, {'venue': 'kraken', 'pair': 'XBTUSD', 'quote_currency': 'USD',
                                         'h1': values['h1'], 'd1': values['d1'], 'failures': failures,
                                         'invalid_bars': invalid},
              observed_at=z(datetime.fromisoformat(last.replace('Z', '+00:00')) + timedelta(hours=1)),
              status='ok' if not failures else 'partial')


# ------------------------------------------------------------------- books

def summarize_book(bids: list, asks: list, anchor_mid: float | None = None) -> dict:
    """Aggregate one snapshot. ``bids``/``asks``: [(price, qty_btc)]."""
    bids = sorted(((p, q) for p, q in bids if p > 0 and q > 0), key=lambda x: -x[0])
    asks = sorted(((p, q) for p, q in asks if p > 0 and q > 0), key=lambda x: x[0])
    if not bids or not asks:
        raise FetchError('empty_payload')
    best_bid, best_ask = bids[0][0], asks[0][0]
    if best_bid >= best_ask:
        raise FetchError('invalid_payload')  # crossed book
    mid = (best_bid + best_ask) / 2
    anchor = anchor_mid or mid

    def bps(price, base):
        return (price / base - 1) * 10000

    depth = {}
    for band in DEPTH_BANDS:
        depth[f'bid_{band}'] = sum(p * q for p, q in bids if -bps(p, mid) <= band)
        depth[f'ask_{band}'] = sum(p * q for p, q in asks if bps(p, mid) <= band)
    reach_bid = -bps(bids[-1][0], mid)
    reach_ask = bps(asks[-1][0], mid)
    buckets = {}
    for side, levels in (('bid', bids), ('ask', asks)):
        for p, q in levels:
            offset = bps(p, anchor)
            if abs(offset) > RANGE_BPS:
                continue
            index = int(offset // BUCKET_BPS)  # floor on the anchored grid
            key = f'{side}:{index}'
            buckets[key] = buckets.get(key, 0.0) + p * q
    return {'mid': mid, 'best_bid': best_bid, 'best_ask': best_ask, 'depth_usd': depth,
            'reach_bps': {'bid': reach_bid, 'ask': reach_ask}, 'anchor_mid': anchor,
            'buckets_usd': buckets, 'levels': {'bid': len(bids), 'ask': len(asks)}}


VENUES = {
    # id: (url, params, parser, quote currency, market type)
    'binance_book': ('https://api.binance.com/api/v3/depth', {'symbol': 'BTCUSDT', 'limit': 5000},
                     lambda d: (d['bids'], d['asks']), 'USDT', 'spot'),
    'binance_futures_book': ('https://fapi.binance.com/fapi/v1/depth', {'symbol': 'BTCUSDT', 'limit': 1000},
                             lambda d: (d['bids'], d['asks']), 'USDT', 'linear_perp'),
    'bybit_book': ('https://api.bybit.com/v5/market/orderbook', {'category': 'linear', 'symbol': 'BTCUSDT', 'limit': 500},
                   lambda d: (d['result']['b'], d['result']['a']), 'USDT', 'linear_perp'),
    'okx_book': ('https://www.okx.com/api/v5/market/books', {'instId': 'BTC-USDT', 'sz': 400},
                 lambda d: (d['data'][0]['bids'], d['data'][0]['asks']), 'USDT', 'spot'),
    'kraken_book': ('https://api.kraken.com/0/public/Depth', {'pair': 'XBTUSD', 'count': 500},
                    lambda d: (d['result']['XXBTZUSD']['bids'], d['result']['XXBTZUSD']['asks']), 'USD', 'spot'),
    'bitstamp_book': ('https://www.bitstamp.net/api/v2/order_book/btcusd/', {'group': 1},
                      lambda d: (d['bids'], d['asks']), 'USD', 'spot'),
}


def _levels(rows) -> list:
    return [(num(row[0]), num(row[1])) for row in rows]


def book(source_id: str, sleep=time.sleep, offsets=SNAPSHOT_OFFSETS):
    url, params, parse, quote, market = VENUES[source_id]

    def run(f: Fetcher) -> SourceResult:
        snapshots, responses, failures = [], [], []
        anchor = None
        start = time.monotonic()
        for offset in offsets:
            wait = offset - (time.monotonic() - start)
            if wait > 0:
                if f.deadline.remaining() < wait + 15:
                    failures.append('budget_exhausted')
                    break
                sleep(wait)
            try:
                r = f.get(url, params=params)
                data = json_of(r)
                if isinstance(data, dict) and data.get('error') and source_id == 'kraken_book':
                    raise FetchError('invalid_payload')
                bids, asks = parse(data)
                summary = summarize_book(_levels(bids), _levels(asks), anchor)
            except (FetchError, KeyError, IndexError, TypeError) as error:
                failures.append(error.kind if isinstance(error, FetchError) else 'missing_field')
                continue
            anchor = anchor or summary['mid']
            responses.append(r)
            summary['retrieved_at'] = z(datetime.fromisoformat(r.retrieved_at))
            summary['retrieval_started_at'] = z(datetime.fromisoformat(r.started_at))
            summary['raw_sha256'] = r.raw_sha256
            snapshots.append(summary)
        if not snapshots:
            raise FetchError(failures[-1] if failures else 'unexpected')
        status = 'ok' if len(snapshots) == len(offsets) else 'partial'
        return ok(source_id, responses, {'venue': source_id.replace('_book', '').replace('_futures', ''),
                                         'market_type': market, 'quote_currency': quote,
                                         'snapshots': snapshots, 'failures': failures,
                                         'bucket_bps': BUCKET_BPS, 'range_bps': RANGE_BPS},
                  status=status, timestamp_quality='receipt_interval')
    return run
