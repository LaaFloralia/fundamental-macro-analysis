"""Named XAUUSD liquidity levels with their sweep state (pure computation, no network).

Levels: previous day / week / month high-low, today's Asia, London and New York session
ranges, relative equal highs-lows, the high-low of recent important US release candles and the
New York midnight open. A high above price is a buy-side liquidity candidate (BSL) and a low
below price a sell-side candidate (SSL): places where stops are commonly assumed to rest.
The orders themselves are not observed.

Conventions:
  * Daily, weekly and monthly ranges use the New York 17:00 close, the boundary of most
    FX/CFD daily candles.
  * Sessions follow each market's own clock, so they move with daylight saving time:
    Asia 09-16 Tokyo, London 08-13 London, New York 08-13 New York. In JST that is London
    16-21 and New York 21-02 while the UK and the US are on summer time, 17-22 and 22-03
    in winter, and the two overlap for an hour in the weeks when only one has switched.
    scrapers/session_stats.py keeps its fixed UTC windows for the 250-day statistics.
  * State after the level formed, from closed H1 bars (M15 bars for the rest of the hour
    after a 15-minute release candle). touched_at is the first trade through; the state
    describes the latest closes:
      resting  未到達    no bar traded through the level
      swept    掃引済み  traded through, and the latest close is back inside
      beyond   抜けて推移 traded through, closes outside for less than SWEEP_HOURS
      broken   抜けて定着 traded through, closes outside for SWEEP_HOURS or more
    A failed break returns inside within a few bars; this describes what happened and
    predicts nothing.
  * Coverage: a stale last bar while the market is open and missing hours in the previous
    day, week and month and the current trading day become cautions. Each level carries
    gaps = {"formation", "scan"}: expected market hours (0.25 per 15-minute bar) missing from
    its formation window and from the bars scanned for its state. A state is not asserted
    across a gap: the state word names the missing hours and nearest_resting skips the level.
    Outside closes count towards SWEEP_HOURS only across a run with no missing bar.
  * Inputs are trimmed by level_inputs() and stored with xau_bars.snapshot(), so recompute()
    reproduces a result from the saved bars.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from scrapers.macro_surprise import event_datetime_jst
from scrapers.us_events import display_name, tier
from scrapers.xau_bars import market_open, restore

NY = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
SWEEP_HOURS = 6
EQ_TOL_PCT = 0.025          # equal highs/lows: swing extremes within 0.025% (about $1 at $4,200)
EQ_MIN_GAP_BARS = 3
SESSIONS = (("asia", "アジア", "Asia/Tokyo", 9, 16), ("london", "London", "Europe/London", 8, 13),
            ("ny", "NY", "America/New_York", 8, 13))  # local opening and closing hours
STATE_WORD = {"resting": "未到達", "swept": "掃引済み", "beyond": "抜けて推移", "broken": "抜けて定着", "forming": "形成中"}
MISSING_BARS = 1            # missing hourly bars in a formation window that raise a caution
STALE_BARS = 2              # missing hourly bars after the last bar that raise a caution
HOUR, QUARTER = timedelta(hours=1), timedelta(minutes=15)


def trading_date(ts: datetime) -> date:
    """NY 17:00 boundary: a bar from Monday 17:00 NY onwards belongs to Tuesday."""
    return (ts.astimezone(NY) + timedelta(hours=7)).date()


def current_trading_date(now: datetime) -> date:
    d = trading_date(now)
    return d + timedelta(days={5: 2, 6: 1}.get(d.weekday(), 0))  # weekend -> next Monday


def previous_trading_date(day: date) -> date:
    return day - timedelta(days=3 if day.weekday() == 0 else 1)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC start and end of a trading date: 17:00 New York on the day before to 17:00 on the day."""
    return (datetime.combine(day - timedelta(days=1), time(17), NY).astimezone(timezone.utc),
            datetime.combine(day, time(17), NY).astimezone(timezone.utc))


def week_window(year: int, week: int) -> tuple[datetime, datetime]:
    """UTC start of Monday's and end of Friday's trading date of an ISO week."""
    monday = date.fromisocalendar(year, week, 1)
    return day_bounds(monday)[0], day_bounds(monday + timedelta(days=4))[1]


def month_window(year: int, month: int) -> tuple[datetime, datetime]:
    """UTC start of the first and end of the last weekday trading date of a calendar month."""
    first = date(year, month, 1)
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    first += timedelta(days={5: 2, 6: 1}.get(first.weekday(), 0))
    last -= timedelta(days={5: 1, 6: 2}.get(last.weekday(), 0))
    return day_bounds(first)[0], day_bounds(last)[1]


def session_windows(day: date) -> list[tuple[str, str, datetime, datetime]]:
    """(key, name, start, end) in UTC for the sessions of a trading date, each in its market's clock."""
    out = []
    for key, name, zone, start, end in SESSIONS:
        tz = ZoneInfo(zone)
        out.append((key, name, datetime.combine(day, time(start), tz).astimezone(timezone.utc),
                    datetime.combine(day, time(end), tz).astimezone(timezone.utc)))
    return out


def session_of(ts: datetime) -> str:
    """The session holding ts; where London and New York overlap, the later-opening one."""
    found = [(start, name) for _, name, start, end in session_windows(trading_date(ts)) if start <= ts < end]
    return max(found)[1] if found else "時間外"


def _jst(ts: Optional[datetime]) -> Optional[str]:
    return ts.astimezone(JST).strftime("%m/%d %H:%M") if ts else None


def _slots(start: datetime, end: datetime, step: timedelta = HOUR):
    """Bar starts the market should produce in [start, end) on a grid of `step`."""
    minutes = int(step.total_seconds() // 60)
    t = start.replace(minute=start.minute - start.minute % minutes, second=0, microsecond=0)
    if t < start:
        t += step
    while t < end:
        if market_open(t):
            yield t
        t += step


def expected_bars(start: datetime, end: datetime) -> int:
    """Hourly bars the market should produce with a start in [start, end)."""
    return sum(1 for _ in _slots(start, end))


def missing_bars(have: set, start: datetime, end: datetime, step: timedelta = HOUR) -> int:
    """Expected bar starts in [start, end) that are not in `have`."""
    return sum(1 for t in _slots(start, end, step) if t not in have)


def latest_expected_bar(now: datetime) -> Optional[datetime]:
    """Start of the latest hourly bar that should be closed at `now`."""
    t = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    for _ in range(80):  # a weekend is 49 closed hours
        if market_open(t):
            return t
        t -= timedelta(hours=1)
    return None


def scan_missing(h1_ts: set, m15_ts: set, start: datetime, now: datetime, until: Optional[datetime] = None) -> float:
    """Expected bars missing from `start` to the latest expected closed bar at `now` (or to the bar
    starting at `until`), in hours. Like scan_bars, a started hour is covered by 15-minute bars:
    each missing one counts 0.25.
    """
    latest, hour = until or latest_expected_bar(now), start.replace(minute=0, second=0, microsecond=0)
    if latest is None or hour > latest:
        return 0.0
    if hour == start:
        return float(missing_bars(h1_ts, start, latest + HOUR))
    return missing_bars(m15_ts, start, hour + HOUR, QUARTER) * 0.25 + missing_bars(h1_ts, hour + HOUR, latest + HOUR)


def _gap_between(before: dict, after: dict) -> bool:
    """True when an expected market slot lies between the end of `before` and the start of `after`."""
    return next(_slots(before["ts"] + before.get("hours", 1.0) * HOUR, after["ts"], QUARTER), None) is not None


def sweep_state(level: float, side: str, bars: list[dict]) -> tuple[str, Optional[datetime]]:
    """(state, first trade through the level). The state describes the latest closes, not the first cross.

    A bar may carry "hours" (0.25 for a 15-minute bar); closes outside are counted in hours, and only
    across the latest run with no missing market slot between consecutive bars.
    """
    crossed = (lambda b: b["high"] > level) if side == "high" else (lambda b: b["low"] < level)
    outside = (lambda b: b["close"] > level) if side == "high" else (lambda b: b["close"] < level)
    first = next((b["ts"] for b in bars if crossed(b)), None)
    if first is None:
        return "resting", None
    hours = 0.0
    for i in range(len(bars) - 1, -1, -1):
        if bars[i]["ts"] < first or not outside(bars[i]):
            break
        hours += bars[i].get("hours", 1.0)
        if i and _gap_between(bars[i - 1], bars[i]):
            break  # a missing bar: the closes before it are not counted as staying outside
    if hours == 0:
        return "swept", first
    return ("broken" if hours >= SWEEP_HOURS else "beyond"), first


def scan_bars(h1: list[dict], m15: list[dict], start: datetime) -> list[dict]:
    """Bars from `start`: 15-minute bars for the rest of a started hour, then hourly bars."""
    hour = start.replace(minute=0, second=0, microsecond=0)
    if hour == start:
        return [b for b in h1 if b["ts"] >= start]
    following = hour + timedelta(hours=1)
    part = [{**b, "hours": 0.25} for b in m15 or [] if start <= b["ts"] < following]
    return part + [b for b in h1 if b["ts"] >= following]


def _days(bars):
    days = OrderedDict()
    for b in bars:
        d = days.setdefault(trading_date(b["ts"]), {"bars": []})
        d["bars"].append(b)
    for d in days.values():
        d["high"] = max(b["high"] for b in d["bars"])
        d["low"] = min(b["low"] for b in d["bars"])
        d["start"] = d["bars"][0]["ts"]
        d["end"] = d["bars"][-1]["ts"]
    return days


def _extreme(bars, side):
    pick = max if side == "high" else min
    bar = pick(bars, key=lambda b: b[side])
    return bar[side], bar["ts"]


def equal_levels(bars: list[dict], price: float, side: str, lookback: int) -> list[dict]:
    """Relative equal highs (or lows): two or more H1 swing extremes within EQ_TOL_PCT, still untouched."""
    recent = bars[-lookback:]
    swings = []
    for i in range(2, len(recent) - 2):
        value = recent[i][side]
        left, right = [recent[j][side] for j in (i - 2, i - 1)], [recent[j][side] for j in (i + 1, i + 2)]
        if side == "high" and value > max(left) and value >= max(right):
            swings.append((i, value))
        if side == "low" and value < min(left) and value <= min(right):
            swings.append((i, value))
    tolerance = price * EQ_TOL_PCT / 100
    found = []
    used = set()
    for a, (i, value) in enumerate(swings):
        if a in used:
            continue
        cluster = [(i, value)] + [(j, v) for b, (j, v) in enumerate(swings) if b > a and abs(v - value) <= tolerance]
        indexes = sorted(j for j, _ in cluster)
        if len(cluster) < 2 or indexes[-1] - indexes[0] < EQ_MIN_GAP_BARS:
            continue
        used.update(b for b, (j, v) in enumerate(swings) if (j, v) in cluster)
        level = max(v for _, v in cluster) if side == "high" else min(v for _, v in cluster)
        between = recent[indexes[0] + 1:indexes[-1]]
        if between and ((side == "high" and max(b["high"] for b in between) > level)
                        or (side == "low" and min(b["low"] for b in between) < level)):
            continue  # price already traded through between the touches: not equal highs/lows
        after = recent[indexes[-1] + 1:]
        state, at = sweep_state(level, side, after)
        if state != "resting":
            continue  # equal highs/lows are liquidity only while untouched
        found.append({"price": level, "count": len(cluster), "first_at": recent[indexes[0]]["ts"],
                      "formed_at": recent[indexes[-1]]["ts"]})
    side_ok = (lambda lv: lv["price"] > price) if side == "high" else (lambda lv: lv["price"] < price)
    found = sorted((lv for lv in found if side_ok(lv)), key=lambda lv: abs(lv["price"] - price))
    return found[:2]


def news_candles(h1, m15, events, now: datetime, since: datetime, tiers=("A", "B")) -> list[dict]:
    """High-low of the candle holding each US release of the given tiers released in [since, now].

    The 15-minute candle that starts at the release is used; without it the 1-hour candle
    holding the release, named 時刻を含む1時間足 because it also holds the minutes before. Releases that fall in the same candle are merged into one entry.
    """
    by_m15 = {b["ts"]: b for b in m15 or []}
    by_h1 = {b["ts"]: b for b in h1 or []}
    groups = OrderedDict()
    for ev in events or []:
        when, level = event_datetime_jst(ev), tier(ev)
        if when is None or level not in tiers:
            continue
        at = when.astimezone(timezone.utc)
        if not (since <= at <= now):
            continue
        start = at.replace(minute=at.minute - at.minute % 15, second=0, microsecond=0)
        candle, frame = by_m15.get(start), "15分足"
        if candle is None:
            candle, frame = by_h1.get(at.replace(minute=0, second=0, microsecond=0)), "時刻を含む1時間足"
        if candle is None:
            continue
        g = groups.setdefault((candle["ts"], frame), {"released_at": at, "tier": level, "names": [], "frame": frame,
                                                      "high": candle["high"], "low": candle["low"],
                                                      "end": candle["ts"] + timedelta(minutes=15 if frame == "15分足" else 60)})
        g["tier"] = min(g["tier"], level)
        g["released_at"] = min(g["released_at"], at)
        name = display_name(ev.get("indicator")).split("（")[0]
        if name not in g["names"]:
            g["names"].append(name)
    return [{**g, "name": "・".join(g["names"][:3])} for g in sorted(groups.values(), key=lambda g: g["released_at"])]


def _previous_periods(days, today: date) -> tuple:
    """The latest trading date, ISO week and calendar month with bars before `today` (None when absent)."""
    past = [d for d in days if d < today]
    week, month = today.isocalendar()[:2], (today.year, today.month)
    return (past[-1] if past else None,
            max((d.isocalendar()[:2] for d in past if d.isocalendar()[:2] < week), default=None),
            max(((d.year, d.month) for d in past if (d.year, d.month) < month), default=None))


def coverage(h1: list[dict], days, today: date, now: datetime) -> tuple[dict, list[str]]:
    """Freshness of the last bar and completeness of the previous day, week, month and the current trading day."""
    last = h1[-1]["ts"]
    info, cautions = {"last_bar_utc": last.strftime("%Y-%m-%d %H:%M"), "stale_hours": 0}, []
    expected_last = latest_expected_bar(now)
    if expected_last is not None and expected_last > last:
        missing = expected_bars(last + timedelta(hours=1), expected_last + timedelta(hours=1))
        info["stale_hours"] = missing
        if missing >= STALE_BARS:
            cautions.append(f"最終確定足が{_jst(last)} JSTで、その後の{missing}時間分の足がない。"
                            "以降の値動きは状態に入っていない")
    prev, prev_week, prev_month = _previous_periods(days, today)
    if prev:
        wanted = previous_trading_date(today)
        if prev != wanted:
            cautions.append(f"{wanted.strftime('%m/%d')}の1時間足がない（休場か取得欠落）。"
                            f"前日高安は{prev.strftime('%m/%d')}の値")
        start, end = day_bounds(prev)
        have, want = len(days[prev]["bars"]), expected_bars(start, end)
        info["previous_day"] = {"date": prev.isoformat(), "bars": have, "expected": want}
        if want - have >= MISSING_BARS:
            cautions.append(f"前日（{prev.strftime('%m/%d')}）の1時間足が{have}/{want}本。欠けた時間の高安は入っていない")
    current = days.get(today)
    if current:
        start, _ = day_bounds(today)
        have, want = len(current["bars"]), expected_bars(start, current["end"] + timedelta(hours=1))
        info["today"] = {"bars": have, "expected": want}
        if want - have >= MISSING_BARS:
            cautions.append(f"当日の1時間足に{want - have}本の欠落。その時間の値動きは状態に入っていない")
    periods = []
    if prev_week:
        first = min(d for d in days if d.isocalendar()[:2] == prev_week)
        periods.append(("previous_week", first.isoformat(), f"前週（{first.strftime('%m/%d')}週）", week_window(*prev_week)))
    if prev_month:
        periods.append(("previous_month", "%d-%02d" % prev_month, f"前月（{prev_month[1]}月）", month_window(*prev_month)))
    stamps = {b["ts"] for b in h1}
    for key, period, label, (start, end) in periods:
        want = expected_bars(start, end)
        have = want - missing_bars(stamps, start, end)
        info[key] = {"period": period, "bars": have, "expected": want}
        if want - have >= MISSING_BARS:
            cautions.append(f"{label}の1時間足が{have}/{want}本。欠けた時間の高安は入っていない")
    return info, cautions


def _news_since(today: date, now: datetime, weekly: bool) -> datetime:
    """Start of the release-candle window: the past week (Weekly) or the previous trading date (Daily)."""
    return now - timedelta(days=7) if weekly else day_bounds(previous_trading_date(today))[0]


def level_inputs(h1: list[dict], m15: list[dict], now: datetime, weekly: bool = False) -> tuple[list[dict], list[dict]]:
    """The bars build_levels uses: closed H1 bars from the first trading date of the previous calendar
    month, and closed M15 bars overlapping the release-candle window."""
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    today = current_trading_date(now)
    last_month = today.replace(day=1) - timedelta(days=1)
    start, since = month_window(last_month.year, last_month.month)[0], _news_since(today, now, weekly)
    return ([b for b in h1 or [] if b["ts"] >= start and b["ts"] + HOUR <= now],
            [b for b in m15 or [] if b["ts"] + QUARTER > since and b["ts"] + QUARTER <= now])


def _span(bars: list[dict]) -> dict:
    stamp = lambda b: b["ts"].strftime("%Y-%m-%d %H:%M")
    return {"count": len(bars), "first_utc": stamp(bars[0]) if bars else None,
            "last_utc": stamp(bars[-1]) if bars else None}


def _level(key, name, side, value, price, formed_at, state, at, group, note, gaps) -> dict:
    """One level row. Missing scan hours go into the state word, missing formation hours into the note."""
    word = STATE_WORD[state] + (f"・欠測{gaps['scan']:g}時間" if gaps["scan"] > 0 else "")
    if gaps["formation"] > 0:
        note = "・".join(filter(None, [note, f"形成期間に欠測{gaps['formation']:g}時間"]))
    return {"key": key, "name": name, "side": side, "price": round(value, 2), "group": group, "formed": _jst(formed_at),
            "state": state, "state_word": word, "touched_at": _jst(at), "touched_session": session_of(at) if at else None,
            "distance": round(value - price, 2), "distance_pct": round((value - price) / price * 100, 2),
            "note": note, "gaps": gaps}


def build_levels(h1: list[dict], m15: list[dict], now: datetime, price: Optional[float] = None,
                 events: Optional[list] = None, weekly: bool = False) -> dict:
    """Named levels with their states and gaps, from the bars level_inputs() keeps at `now`."""
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    h1, m15 = level_inputs(h1, m15, now, weekly)
    result = {"source": "Twelve Data XAU/USD 1時間足（UTC、確定足）", "day_boundary": "NY 17:00",
              "price": price, "last_bar_utc": None, "trading_day": None, "midnight_open": None,
              "levels": [], "coverage": None, "cautions": [], "error": None,
              "inputs": {"computed_at": now.isoformat(timespec="seconds"), "h1": _span(h1), "m15": _span(m15)}}
    if not h1:
        result["error"] = "1時間足なし（水準と掃引状態は計算できません）"
        return result
    price = price if isinstance(price, (int, float)) and price > 0 else h1[-1]["close"]
    result.update(price=price, last_bar_utc=h1[-1]["ts"].strftime("%Y-%m-%d %H:%M"))
    today = current_trading_date(now)
    result["trading_day"] = today.isoformat()
    days = _days(h1)
    result["coverage"], result["cautions"] = coverage(h1, days, today, now)
    have_h1, have_m15 = {b["ts"] for b in h1}, {b["ts"] for b in m15}
    # Per-level gaps are holes up to the last bar received; hours missing after it (a provider
    # delay) are one caution for every level (coverage), not a gap of each level.
    until = h1[-1]["ts"]
    closed_end = until + HOUR
    levels = []

    def add(key, name, side, value, formed_at, scan_from, group, window=None, note=""):
        state, at = sweep_state(value, side, scan_bars(h1, m15, scan_from))
        gaps = {"formation": float(missing_bars(have_h1, window[0], min(window[1], closed_end))) if window else 0.0,
                "scan": scan_missing(have_h1, have_m15, scan_from, now, until)}
        levels.append(_level(key, name, side, value, price, formed_at, state, at, group, note, gaps))

    prev, prev_week, prev_month = _previous_periods(days, today)
    current = days.get(today)
    # A previous range is scanned from the end of its window, so missing days after it count as gaps.
    if prev:
        window = day_bounds(prev)
        for side, key, name in (("high", "PDH", "前日高値"), ("low", "PDL", "前日安値")):
            value, at = _extreme(days[prev]["bars"], side)
            add(key, f"{name}（{prev.strftime('%m/%d')}）", side, value, at, window[1], "daily", window)
    if prev_week:
        window, week_days = week_window(*prev_week), [d for d in days if d.isocalendar()[:2] == prev_week]
        wk = [b for d in week_days for b in days[d]["bars"]]
        for side, key, name in (("high", "PWH", "前週高値"), ("low", "PWL", "前週安値")):
            value, at = _extreme(wk, side)
            add(key, f"{name}（{week_days[0].strftime('%m/%d')}週）", side, value, at, window[1], "weekly", window)
    if prev_month:
        window = month_window(*prev_month)
        mb = [b for d in days if (d.year, d.month) == prev_month for b in days[d]["bars"]]
        for side, key, name in (("high", "PMH", "前月高値"), ("low", "PML", "前月安値")):
            value, at = _extreme(mb, side)
            add(key, f"{name}（{prev_month[1]}月）", side, value, at, window[1], "monthly", window)
    month = (today.year, today.month)
    mtd = [b for d in days if (d.year, d.month) == month for b in days[d]["bars"]]
    if weekly and mtd:  # every closed bar of this month, today's included
        window = (month_window(*month)[0], closed_end)
        for side, key, name in (("high", "MTDH", "今月高値"), ("low", "MTDL", "今月安値")):
            value, at = _extreme(mtd, side)
            add(key, f"{name}（{month[1]}月）", side, value, at, at + HOUR, "monthly", window)
    # Session highs and lows are intraday levels: Daily only (a weekday rerun of the Weekly stays weekly).
    if current and not weekly:
        midnight = datetime.combine(today, datetime.min.time(), NY).astimezone(timezone.utc)
        bar = next((b for b in current["bars"] if b["ts"] == midnight), None)
        if bar:
            result["midnight_open"] = round(bar["open"], 2)
        for key, name, lo, hi in session_windows(today):
            bars = [b for b in current["bars"] if lo <= b["ts"] < hi]
            if not bars:
                continue
            for side, suffix in (("high", "高値"), ("low", "安値")):
                value, at = _extreme(bars, side)
                if now < hi:  # forming: formation gaps so far, nothing scanned yet
                    gaps = {"formation": float(missing_bars(have_h1, lo, min(hi, closed_end))), "scan": 0.0}
                    levels.append(_level(f"{key.upper()}_{side[0].upper()}", f"{name}{suffix}（当日・形成中）", side,
                                         value, price, at, "forming", None, "session", "", gaps))
                else:
                    add(f"{key.upper()}_{side[0].upper()}", f"{name}{suffix}（当日）", side, value, at, hi, "session",
                        (lo, hi))
    lookback = 20 * 24 if weekly else 5 * 24
    for side, key, name in (("high", "EQH", "等高値"), ("low", "EQL", "等安値")):
        for lv in equal_levels(h1, price, side, lookback):
            gaps = {"formation": float(missing_bars(have_h1, lv["first_at"], lv["formed_at"])),
                    "scan": scan_missing(have_h1, have_m15, lv["formed_at"] + HOUR, now, until)}
            levels.append(_level(key, f"{name}（{lv['count']}回・{_jst(lv['formed_at'])}まで）", side, lv["price"],
                                 price, lv["formed_at"], "resting", None, "equal", "", gaps))
    # Release candles stay through the next trading day (Friday's payrolls are still listed on Monday).
    # Weekly: only the top tier over the past week.
    since = _news_since(today, now, weekly)
    for candle in news_candles(h1, m15, events, now, since, ("A",) if weekly else ("A", "B")):
        released = candle["released_at"].astimezone(JST).strftime("%m/%d %H:%M")
        for side, suffix in (("high", "高値"), ("low", "安値")):
            add(f"NEWS_{side[0].upper()}", f"{candle['name']} 発表{candle['frame']}{suffix}（{released}）", side,
                candle[side], candle["released_at"], candle["end"], "news", note=f"Tier {candle['tier']}")
    result["levels"] = merge_levels(levels)
    return result


def recompute(snap: dict, computed_at_iso: str, price: Optional[float] = None, events: Optional[list] = None,
              weekly: bool = False) -> dict:
    """build_levels again from a stored bar snapshot (xau_bars.snapshot) and its inputs.computed_at."""
    h1, m15 = restore(snap)
    now = datetime.fromisoformat(computed_at_iso)
    return build_levels(h1, m15, now if now.tzinfo else now.replace(tzinfo=timezone.utc), price, events, weekly)


def merge_levels(levels: list[dict]) -> list[dict]:
    """One row per price, state and gaps: the same high or low often carries several names
    (e.g. last week's low = last month's low). Prices only a few cents apart stay separate rows,
    and so do equal prices whose states or gaps differ; forming session levels are never merged.
    """
    rows = OrderedDict()
    for lv in levels:
        gaps = tuple(sorted((lv.get("gaps") or {}).items()))
        key = (lv["side"], round(lv["price"], 2), lv["state"], lv["touched_at"], gaps)
        if lv["state"] == "forming" or key not in rows:
            rows[key if lv["state"] != "forming" else (key, len(rows))] = dict(lv)
            continue
        row = rows[key]
        row["name"] = f"{row['name']}・{lv['name']}"
        row["key"] = f"{row['key']}+{lv['key']}"
        row["note"] = "・".join(dict.fromkeys(p for n in (row["note"], lv["note"]) for p in n.split("・") if p))
    return sorted(rows.values(), key=lambda x: -x["price"])


def nearest_resting(lv: Optional[dict], price: Optional[float]) -> dict:
    """The closest untouched high above and low below the price: {'high': level, 'low': level}.

    Levels with missing bars in their formation or scan window are skipped: their state is not certain.
    Nothing is chosen while the last bar is stale (STALE_BARS or more hours missing after it).
    """
    out = {}
    if not isinstance(lv, dict) or lv.get("error") or not isinstance(price, (int, float)):
        return out
    if ((lv.get("coverage") or {}).get("stale_hours") or 0) >= STALE_BARS:
        return out
    for side, ok in (("high", lambda p: p > price), ("low", lambda p: p < price)):
        rows = [x for x in lv.get("levels") or [] if x.get("side") == side and x.get("state") == "resting"
                and ok(x["price"]) and not any((x.get("gaps") or {}).values())]
        if rows:
            out[side] = min(rows, key=lambda x: abs(x["price"] - price))
    return out


def format_level_lines(lv: Optional[dict]) -> list[str]:
    """Parent-model input: every level with its price, distance and sweep state."""
    lines = ["### 流動性の位置（名前付き水準と掃引状態、コード計算）"]
    if not isinstance(lv, dict) or lv.get("error"):
        lines.append(f"- 取得不可（{(lv or {}).get('error', '不明')}）")
        return lines
    lines.append(f"- 基準: {lv['source']}、日足の区切り {lv['day_boundary']}、最終確定足 {lv['last_bar_utc']} UTC、"
                 f"現在値 {lv['price']:,.2f}、当日（取引日）{lv['trading_day']}"
                 + (f"、NY 0時始値 {lv['midnight_open']:,.2f}" if lv.get("midnight_open") else ""))
    lines.append("- 状態: 未到達=確定足で一度も抜けていない／掃引済み=一度抜け、最新の終値は内側／"
                 "抜けて推移=最新の終値が外側で6時間未満／抜けて定着=6時間以上外側。時刻は最初に抜けた足。"
                 "高値の上をBSL、安値の下をSSLの候補と呼ぶ（損切りが置かれやすいとされる位置で、注文は観測していない）")
    lines.append("- 欠測: 水準の形成期間や形成後に足が欠けた水準は、状態の後に欠測時間を付け、直近の未到達候補から外す")
    lines.append("- セッションは各市場の現地時間: アジア 東京9〜16時、London ロンドン8〜13時、NY ニューヨーク8〜13時"
                 "（JSTでは夏時間 London 16〜21時・NY 21〜翌2時、冬時間 17〜22時・22〜翌3時）")
    for caution in lv.get("cautions") or []:
        lines.append(f"- 注意: {caution}")
    for item in sorted(lv.get("levels") or [], key=lambda x: -x["price"]):
        touched = f"・{item['touched_at']} JST（{item['touched_session']}）" if item.get("touched_at") else ""
        lines.append(f"- {item['name']}: {item['price']:,.2f}（現在値比 {item['distance']:+,.2f}／{item['distance_pct']:+.2f}%）"
                     f"｜{'BSL' if item['side'] == 'high' else 'SSL'}｜{item['state_word']}{touched}"
                     + (f"｜{item['note']}" if item.get("note") else ""))
    near = nearest_resting(lv, lv.get("price"))
    if near:
        lines.append("- 直近の未到達: " + "／".join(
            f"{'上' if side == 'high' else '下'} {near[side]['name']} {near[side]['price']:,.2f}"
            for side in ("high", "low") if side in near))
    return lines
