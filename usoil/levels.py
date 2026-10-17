"""WTI levels from closed Yahoo continuous-contract bars, using ET date windows.

The continuous contract can differ from CNBC @CL.1. Historical states describe
Yahoo bars only; nearest targets and reference-price range position remain
unverified until contract alignment is proved. No session levels are inferred.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

UTC = timezone.utc
ET = ZoneInfo('America/New_York')
HOUR, DAY = timedelta(hours=1), timedelta(days=1)
BROKEN_HOURS = 6
PERIODS = (('prev_day', '前日'), ('prev_week', '前週'), ('prev_month', '前月'))
SIDE_WORDS = {'high': '高値', 'low': '安値'}
STATE_WORDS = {'resting': '未到達', 'swept': '掃引済み', 'beyond': '抜けて推移', 'broken': '抜けて定着',
               'reference': '参考水準・比較未確認'}
COMPARISON_NOTE = ('参照価格はCNBC @CL.1、過去高安はYahoo CL=F連続限月。限月・ロールの整合が未確認のため、'
                   '現在値との距離・最寄り未到達・週間レンジ内位置は未判定。高安の到達状態はYahooバー同士の観測。')


def _time(value) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('naive_timestamp')
    return stamp.astimezone(UTC)


def _z(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z') if value else None


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=ET).astimezone(UTC)


def _bars(rows, step: timedelta, now: datetime | None = None) -> list[dict]:
    out = {}
    for row in rows or []:
        try:
            start = _time(row['t'])
            end = _midnight(start.astimezone(ET).date() + DAY) if step == DAY else start + step
            if now is not None and end > now:
                continue
            h, l, c = (float(row[k]) for k in ('h', 'l', 'c'))
            if not all(math.isfinite(v) for v in (h, l, c)) or not 0 < l <= c <= h:
                continue
            out[start] = {'start': start, 'end': end, 'h': h, 'l': l, 'c': c}
        except (KeyError, TypeError, ValueError, OverflowError):
            continue  # null maintenance/forming bars are never filled
    return [out[k] for k in sorted(out)]


def _expected_hour(stamp: datetime) -> bool:
    local = stamp.astimezone(ET)
    return local.hour != 17 and local.weekday() != 5 and not (local.weekday() == 4 and local.hour >= 17) \
        and not (local.weekday() == 6 and local.hour < 18)


def _gaps(bars: list[dict], step: timedelta, label: str) -> list[dict]:
    out = []
    for a, b in zip(bars, bars[1:]):
        if step == HOUR:
            cursor, missing = a['end'], 0
            while cursor < b['start']:
                missing += int(_expected_hour(cursor))
                cursor += HOUR
        else:
            first, last = a['start'].astimezone(ET).date() + DAY, b['start'].astimezone(ET).date()
            missing = sum((first + i * DAY).weekday() < 5 for i in range(max(0, (last - first).days)))
        if missing:
            out.append({'code': f'{label}_gap', 'from': _z(a['end']), 'to': _z(b['start']), 'missing_bars': missing,
                        'text': f'{"1時間足" if label == "h1" else "日足"}が{missing}本欠ける（17:00 ETメンテと週末は除外）'})
    return out


def period_window(kind: str, today: date) -> tuple[datetime, datetime]:
    if kind == 'prev_day':
        first, last = today - DAY, today
    elif kind == 'prev_week':
        last = today - timedelta(days=today.weekday())
        first = last - timedelta(days=7)
    elif kind == 'prev_month':
        last = today.replace(day=1)
        first = (last - DAY).replace(day=1)
    else:
        raise ValueError('unknown_period')
    return _midnight(first), _midnight(last)


def _range(bars: list[dict], start: datetime, end: datetime, step: timedelta = DAY):
    first, last = start.astimezone(ET).date(), end.astimezone(ET).date()
    expected = {first + i * DAY for i in range((last - first).days) if (first + i * DAY).weekday() < 5}
    inside = {b['start'].astimezone(ET).date(): b for b in bars if start <= b['start'] < end
              and b['start'].astimezone(ET).weekday() < 5}
    if not expected or set(inside) != expected:
        return None, len(inside)
    return (max(b['h'] for b in inside.values()), min(b['l'] for b in inside.values())), len(inside)


def level_state(level: float, side: str, formed_at: datetime, h1: list[dict], d1: list[dict]) -> dict:
    first_hour = h1[0]['start'] if h1 else None
    scan = [b for b in d1 if b['start'] >= formed_at and (first_hour is None or b['start'] < first_hour)]
    scan += [b for b in h1 if b['start'] >= formed_at]
    touched = next((b for b in scan if (b['h'] > level if side == 'high' else b['l'] < level)), None)
    if touched is None:
        return {'state': 'resting', 'touched_at': None, 'hours_outside': 0.0}
    hours, after = 0.0, None
    # A daily close proves only where that close was, never 24 consecutive
    # hours outside a level. Duration requires contiguous closed hourly bars.
    hourly = [b for b in h1 if b['start'] >= formed_at]
    for bar in reversed(hourly):
        if not (bar['c'] > level if side == 'high' else bar['c'] < level) or (after is not None and bar['end'] != after):
            break
        hours += (bar['end'] - bar['start']).total_seconds() / 3600
        after = bar['start']
    daily_only = not hourly and bool(scan) and (scan[-1]['c'] > level if side == 'high' else scan[-1]['c'] < level)
    state = 'beyond' if daily_only else 'swept' if hours == 0 else 'broken' if hours >= BROKEN_HOURS else 'beyond'
    result = {'state': state, 'touched_at': _z(touched['start']), 'hours_outside': hours}
    if daily_only:
        result['state_word'] = '日足終値が外側（継続時間は未確認）'
    return result


def build_levels(h1_rows, d1_rows, now: datetime, price: float | None) -> dict:
    now = _time(now)
    h1, d1 = _bars(h1_rows, HOUR, now), _bars(d1_rows, DAY, now)
    cautions = _gaps(h1, HOUR, 'h1') + _gaps(d1, DAY, 'd1')
    if not h1:
        cautions.append({'code': 'h1_missing', 'text': '確定1時間足がない（高安の到達状態は日足の観測範囲のみ）'})
    items, weekly_range = [], None
    for kind, label in PERIODS:
        start, end = period_window(kind, now.astimezone(ET).date())
        bounds, count = _range(d1, start, end)
        if bounds is None:
            expected = sum((start.astimezone(ET).date() + i * DAY).weekday() < 5
                           for i in range((end.astimezone(ET).date() - start.astimezone(ET).date()).days))
            cautions.append({'code': 'window_incomplete', 'key': kind + '_range',
                             'text': f'{label}のET日付バーがそろわない（{count}/{expected}本、高安は未算出）'})
            continue
        if kind == 'prev_week':
            weekly_range = {'high': bounds[0], 'low': bounds[1], 'width': round(bounds[0] - bounds[1], 6),
                            'position_pct': None, 'comparison_status': 'unverified'}
        for side, value in zip(('high', 'low'), bounds):
            st = level_state(value, side, end, h1, d1)
            items.append({'key': f'{kind}_{side}', 'name': label + SIDE_WORDS[side], 'side': side, 'pool': None,
                          'price': value, **st, 'state_word': st.get('state_word', STATE_WORDS[st['state']]), 'formed_at': _z(end),
                          'window_start': _z(start), 'bars': 'd1', 'comparison_status': 'unverified'})
    if price is not None and math.isfinite(price) and price > 0:
        for value in range(math.ceil(price * .97), math.floor(price * 1.03) + 1):
            major = value % 5 == 0
            items.append({'key': f'round_{value}', 'name': ('主要ラウンド ' if major else 'ラウンド ') + f'${value}',
                          'side': 'high' if value >= price else 'low', 'pool': None, 'price': float(value),
                          'state': 'reference', 'state_word': STATE_WORDS['reference'], 'touched_at': None,
                          'hours_outside': 0.0, 'formed_at': _z(now), 'window_start': _z(now), 'bars': 'reference',
                          'major': major, 'comparison_status': 'unverified'})
    last = h1[-1] if h1 else d1[-1] if d1 else None
    cautions.append({'code': 'contract_comparison_unverified', 'text': COMPARISON_NOTE})
    return {'status': 'partial' if items else 'missing', 'last_bar_start': _z(last['start']) if last else None,
            'last_bar_end': _z(last['end']) if last else None, 'items': items,
            'nearest': {'high': None, 'low': None}, 'cautions': cautions, 'weekly_range': weekly_range,
            'reference_position_pct': None, 'comparison_status': 'unverified', 'comparison_note': COMPARISON_NOTE}
