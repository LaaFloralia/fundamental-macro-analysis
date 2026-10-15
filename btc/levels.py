"""Named BTC/USD liquidity levels from closed Kraken bars (pure computation, no I/O).

BTC trades 24/7, so the calendar is UTC: days start 00:00 UTC (09:00 JST),
weeks start Monday 00:00 UTC and months are UTC calendar months. Session
ranges follow each market's own clock (zoneinfo), so daylight saving moves
London and New York. A level's state after it formed is read from closed 1 h
bars (daily bars first when the formation is older than the 1 h history);
missing bars are reported as cautions and never filled.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
BROKEN_HOURS = 6  # closes outside for this long = 抜けて定着
SESSIONS = (
    # key, label, clock, local start hour, local end hour
    ('asia', 'アジア', ZoneInfo('Asia/Tokyo'), 9, 15),
    ('london', 'London', ZoneInfo('Europe/London'), 8, 13),
    ('ny', 'NY', ZoneInfo('America/New_York'), 8, 13),
)
PERIODS = (('prev_day', '前日'), ('prev_week', '前週'), ('prev_month', '前月'))
SIDE_WORDS = {'high': '高値', 'low': '安値'}
STATE_WORDS = {'resting': '未到達', 'swept': '掃引済み', 'beyond': '抜けて推移', 'broken': '抜けて定着'}


def _time(value) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('naive_timestamp')
    return stamp.astimezone(UTC)


def _z(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z') if value else None


def _bars(rows, step: timedelta) -> list[dict]:
    """Closed bars as {start, end, h, l, c}, sorted, one per start."""
    out = {}
    for row in rows or []:
        start = _time(row['t'])
        out[start] = {'start': start, 'end': start + step, 'h': float(row['h']), 'l': float(row['l']),
                      'c': float(row['c'])}
    return [out[k] for k in sorted(out)]


def _gaps(bars: list[dict], step: timedelta, label: str) -> list[dict]:
    out = []
    for a, b in zip(bars, bars[1:]):
        if b['start'] - a['end'] >= step:
            count = int((b['start'] - a['end']) / step)
            out.append({'code': f'{label}_gap', 'from': _z(a['end']), 'to': _z(b['start']), 'missing_bars': count,
                        'text': f'{"1時間足" if label == "h1" else "日足"}が{count}本欠ける'
                                f'（{a["end"]:%Y-%m-%d %H:%M}〜{b["start"]:%Y-%m-%d %H:%M} UTC）'})
    return out


def session_window(day: date, zone: ZoneInfo, start_hour: int, end_hour: int) -> tuple[datetime, datetime]:
    """One session of ``day`` on the market's local clock, in UTC."""
    start = datetime.combine(day, time(start_hour), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day, time(end_hour), tzinfo=zone).astimezone(UTC)
    return start, end


def period_window(kind: str, today: date) -> tuple[datetime, datetime]:
    """Previous UTC day, previous Monday-start week or previous calendar month."""
    if kind == 'prev_day':
        first, last = today - DAY, today
    elif kind == 'prev_week':
        last = today - timedelta(days=today.weekday())
        first = last - timedelta(days=7)
    else:
        last = today.replace(day=1)
        first = (last - DAY).replace(day=1)
    return (datetime.combine(first, time(0), tzinfo=UTC), datetime.combine(last, time(0), tzinfo=UTC))


def _range(bars: list[dict], start: datetime, end: datetime, step: timedelta):
    """(high, low) when every bar of [start, end) is present; else None (never inferred)."""
    inside = [b for b in bars if start <= b['start'] < end]
    if len(inside) != int((end - start) / step):
        return None, len(inside)
    return (max(b['h'] for b in inside), min(b['l'] for b in inside)), len(inside)


def level_state(level: float, side: str, formed_at: datetime, h1: list[dict], d1: list[dict]) -> dict:
    """State after formation: resting / swept / beyond / broken, and the first bar that traded through."""
    first_hour = h1[0]['start'] if h1 else None
    scan = []
    if first_hour is None or formed_at < first_hour:
        # Older than the 1 h history: daily bars until the hourly ones take over (the overlapping day included,
        # so a touch is never missed; its time is then the day's start).
        scan += [b for b in d1 if b['start'] >= formed_at and (first_hour is None or b['start'] < first_hour)]
    scan += [b for b in h1 if b['start'] >= formed_at]
    through = (lambda b: b['h'] > level) if side == 'high' else (lambda b: b['l'] < level)
    outside = (lambda b: b['c'] > level) if side == 'high' else (lambda b: b['c'] < level)
    touched = next((b for b in scan if through(b)), None)
    if touched is None:
        return {'state': 'resting', 'touched_at': None, 'hours_outside': 0.0}
    hours, after = 0.0, None
    for bar in reversed(scan):
        if not outside(bar) or (after is not None and bar['end'] != after):
            break  # back inside, or a missing bar: the run is only what is observed without a gap
        hours += (bar['end'] - bar['start']).total_seconds() / 3600
        after = bar['start']
    if hours == 0:
        state = 'swept'
    elif hours >= BROKEN_HOURS:
        state = 'broken'
    else:
        state = 'beyond'
    return {'state': state, 'touched_at': _z(touched['start']), 'hours_outside': hours}


def build_levels(h1_rows, d1_rows, now: datetime, price: float | None) -> dict:
    """Named levels with state, pool side and the nearest resting level above and below ``price``.

    ``h1_rows`` / ``d1_rows``: closed bars ``{'t', 'o', 'h', 'l', 'c'}`` (``t`` = bar start, UTC).
    """
    now = _time(now)
    h1, d1 = _bars(h1_rows, HOUR), _bars(d1_rows, DAY)
    cautions = _gaps(h1, HOUR, 'h1') + _gaps(d1, DAY, 'd1')
    if not h1:
        return {'status': 'missing', 'last_bar_start': None, 'last_bar_end': None, 'items': [],
                'nearest': {'high': None, 'low': None}, 'cautions': cautions}
    closed_until = h1[-1]['end']
    today = now.date()
    windows = []  # (key pattern, name pattern, start, end, bars, step, bar part); '{side}' / '{word}' filled per side
    if d1:
        for kind, label in PERIODS:
            start, end = period_window(kind, today)
            windows.append((kind + '_{side}', label + '{word}', start, end, d1, DAY, 'd1'))
    else:
        cautions.append({'code': 'd1_missing', 'text': '日足がない（前日・前週・前月の高安は未算出）'})
    for day, when, label_day in ((today - DAY, 'prev_day', '前日'), (today, 'today', '当日')):
        for key, label, zone, start_hour, end_hour in SESSIONS:
            start, end = session_window(day, zone, start_hour, end_hour)
            if end <= closed_until:  # only sessions that have ended, judged on closed bars
                windows.append((f'{key}_{{side}}_{when}', f'{label}{{word}}（{label_day}）', start, end, h1, HOUR, 'h1'))
    items = []
    for key, name, start, end, bars, step, part in windows:
        bounds, count = _range(bars, start, end, step)
        if bounds is None:
            expected = int((end - start) / step)
            cautions.append({'code': 'window_incomplete', 'key': key.format(side='range'),
                             'text': f'{name.format(word="")}の足がそろわない（{count}/{expected}本、高安は未算出）'})
            continue
        for side, value in zip(('high', 'low'), bounds):
            st = level_state(value, side, end, h1, d1)
            pool = None
            if price is not None and side == 'high' and value > price:
                pool = 'BSL'
            elif price is not None and side == 'low' and value < price:
                pool = 'SSL'
            items.append({'key': key.format(side=side), 'name': name.format(word=SIDE_WORDS[side]), 'side': side,
                          'pool': pool, 'price': value, 'state': st['state'], 'state_word': STATE_WORDS[st['state']],
                          'touched_at': st['touched_at'], 'hours_outside': st['hours_outside'], 'formed_at': _z(end),
                          'window_start': _z(start), 'bars': part})
    nearest = {'high': None, 'low': None}
    if price is not None:
        resting = [i for i in items if i['state'] == 'resting']
        above = sorted((i for i in resting if i['price'] > price), key=lambda i: i['price'] - price)
        below = sorted((i for i in resting if i['price'] < price), key=lambda i: price - i['price'])
        nearest = {'high': above[0]['key'] if above else None, 'low': below[0]['key'] if below else None}
    return {'status': 'partial' if cautions else 'ok', 'last_bar_start': _z(h1[-1]['start']),
            'last_bar_end': _z(closed_until), 'items': items, 'nearest': nearest, 'cautions': cautions}
