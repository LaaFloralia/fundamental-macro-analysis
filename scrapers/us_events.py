"""Which US releases matter for gold: a fixed two-tier allowlist.

Tier A (always shown): FOMC decision / statement / press conference, Fed Chair remarks,
the employment report (payrolls, unemployment, hourly earnings) and CPI.
Tier B (shown in the near window): retail sales, GDP, PCE prices, PPI, ISM PMIs,
durable goods, FOMC minutes, Michigan inflation expectations, jobless claims, ADP, JOLTS.
Everything else (housing, auctions, inventories, regional surveys, S&P Global PMIs,
consumer confidence) is excluded from the report figures.

Grounding: gold's intraday response is largest for payrolls, CPI, GDP and retail sales
and for FOMC decisions (Elder-Miao-Ramchander 2012, Cai-Cheung-Wong 2001,
Roache-Rossi 2009, Smales-Yang 2015). The ADP / JOLTS / claims entries are the
labour-market releases the Fed watches between payroll reports.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
import math
import re
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NOWCAST_MAX_AGE_DAYS = 6    # a nowcast whose last data point is older is not used
GAP_NEUTRAL_PP = 0.05       # |nowcast - consensus| below this is 予想並み
NOWCAST_MAX_PER_WINDOW = 3  # nowcast rows kept (nearest releases first); parent input, board and table share them
NOWCAST_CLOCK_SKEW = timedelta(minutes=5)  # a fetch time this far after the cutoff is still the same collection
# Whole JST days shown after the stamp's date per tier; results run from the previous weekday
# (Daily; Friday on a Monday) or seven days back (Weekly).
AHEAD_DAYS = {("daily", "A"): 7, ("daily", "B"): 2, ("weekly", "A"): 8, ("weekly", "B"): 8}

TIER_A = (
    r"\bfed interest rate decision\b", r"\bfomc statement\b", r"\bfomc press conference\b",
    r"\bfomc economic projections\b", r"\bfed chair\b", r"\bjackson hole\b",
    r"^nonfarm payrolls\b", r"^unemployment rate\b", r"^average hourly earnings\b",
    r"^(core )?cpi\b",
)
TIER_B = (
    r"^(core )?retail sales\b", r"^gdp\b", r"^(core )?pce price index\b", r"^(core )?ppi\b",
    r"^ism (manufacturing|non-manufacturing|services) pmi\b", r"^(core )?durable goods orders\b",
    r"^fomc meeting minutes\b", r"^michigan \d+-year inflation expectations\b",
    r"^initial jobless claims\b", r"^adp nonfarm employment change\b", r"^jolts job openings\b",
)
# Display names for the event table (Japanese, kept short).
NAMES = (
    (r"fed interest rate decision", "FOMC 政策金利"), (r"fomc statement", "FOMC 声明"),
    (r"fomc press conference", "FOMC 会見"), (r"fomc economic projections", "FOMC 経済見通し"),
    (r"fed chair", "FRB議長 発言"), (r"jackson hole", "ジャクソンホール"),
    (r"^nonfarm payrolls", "雇用統計 非農業部門雇用者数"), (r"^unemployment rate", "雇用統計 失業率"),
    (r"^average hourly earnings", "雇用統計 平均時給"),
    (r"^core cpi", "コアCPI"), (r"^cpi", "CPI"),
    (r"^core retail sales", "小売売上高 除自動車"), (r"^retail sales", "小売売上高"),
    (r"^gdp", "GDP"), (r"^core pce price index", "コアPCE価格指数"), (r"^pce price index", "PCE価格指数"),
    (r"^core ppi", "コアPPI"), (r"^ppi", "PPI"),
    (r"^ism manufacturing pmi", "ISM製造業"), (r"^ism (non-manufacturing|services) pmi", "ISM非製造業"),
    (r"^core durable goods orders", "耐久財受注 除輸送"), (r"^durable goods orders", "耐久財受注"),
    (r"^fomc meeting minutes", "FOMC 議事要旨"),
    (r"^michigan (\d+)-year inflation expectations", "ミシガン大 {0}年先インフレ期待"),
    (r"^initial jobless claims", "新規失業保険申請"), (r"^adp nonfarm employment change", "ADP雇用"),
    (r"^jolts job openings", "JOLTS求人"),
)


def _clean(indicator) -> str:
    return re.sub(r"\s+", " ", str(indicator or "")).strip()


def tier(event: dict) -> Optional[str]:
    """'A', 'B' or None (excluded). Only United States events qualify."""
    if not isinstance(event, dict) or event.get("country") != "United States":
        return None
    text = _clean(event.get("indicator")).lower()
    if any(re.search(p, text) for p in TIER_A):
        return "A"
    if any(re.search(p, text) for p in TIER_B):
        return "B"
    return None


def display_name(indicator) -> str:
    """Japanese name plus the provider's period / measure suffix, e.g. 'CPI（前月比・9月）'."""
    text = _clean(indicator)
    lower = text.lower()
    base = next((name.format(*m.groups()) for pattern, name in NAMES if (m := re.search(pattern, lower))), text)
    tags = []
    for raw, ja in (("(MoM)", "前月比"), ("(YoY)", "前年比"), ("(QoQ)", "前期比年率")):
        if raw.lower() in lower:
            tags.append(ja)
    month = re.search(r"\((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\)", text)
    if month:
        tags.append(f"{['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].index(month.group(1)) + 1}月")
    quarter = re.search(r"\((Q[1-4])\)", text)
    if quarter:
        tags.append(quarter.group(1))
    return f"{base}（{'・'.join(tags)}）" if tags else base


def results_start(stamp: datetime, mode: str) -> datetime:
    """JST midnight that starts the released-results window."""
    day = stamp.astimezone(JST).date()
    back = 7 if mode == "weekly" else {0: 3, 6: 2}.get(day.weekday(), 1)
    return datetime.combine(day - timedelta(days=back), time(0), JST)


def event_window(stamp: datetime, mode: str, level: str) -> tuple[datetime, datetime]:
    """[start, end) in JST for a tier. Whole days, so the dates in the table title are exact."""
    day = stamp.astimezone(JST).date()
    return results_start(stamp, mode), datetime.combine(day + timedelta(days=AHEAD_DAYS[(mode, level)] + 1), time(0), JST)


def window_label(stamp: datetime, mode: str) -> str:
    start, day = results_start(stamp, mode), stamp.astimezone(JST).date()
    end = {level: (day + timedelta(days=AHEAD_DAYS[(mode, level)])).strftime("%m/%d") for level in ("A", "B")}
    if mode == "weekly":
        return f"{start.strftime('%m/%d')}〜{day.strftime('%m/%d')}の結果と{end['A']}までの予定"
    return f"{start.strftime('%m/%d')}からの結果と、Tier Aは{end['A']}・Tier Bは{end['B']}までの予定"


def gap_text(gap: float) -> str:
    """Signed pp with enough decimals to stay on the same side of zero and of ±GAP_NEUTRAL_PP."""
    for places in range(2, 7):
        value = round(gap, places)
        if (value == 0) == (gap == 0) and (abs(value) >= GAP_NEUTRAL_PP) == (abs(gap) >= GAP_NEUTRAL_PP):
            return f"{value:+.{places}f}pp"
    return f"{gap:+.6f}pp"


def nowcast_gap(event: dict, nowcast: dict) -> Optional[dict]:
    """Cleveland Fed nowcast minus the consensus for a month-over-month CPI / PCE release.
    The difference is exact in decimal (not rounded), so 0.049 stays below 0.05."""
    from scrapers.inflation_nowcast import series_key, target_month
    from scrapers.macro_surprise import event_datetime_jst, parse_value
    if not isinstance(nowcast, dict) or nowcast.get("error"):
        return None
    key, when = series_key(event.get("indicator")), event_datetime_jst(event)
    month = target_month(event.get("indicator"), when.date()) if when and key else None
    point = ((nowcast.get("months") or {}).get(month) or {}).get(key) if month else None
    forecast = parse_value(event.get("forecast"))
    value = point.get("value") if isinstance(point, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if forecast is None or forecast[1] != "%" or not math.isfinite(forecast[0]):
        return None
    gap = float(Decimal(str(value)) - Decimal(str(forecast[0])))
    return {"nowcast": value, "as_of": point.get("as_of"), "forecast": forecast[0],
            "gap": gap, "month": month, "series": key}


def gap_display(gap: dict) -> tuple[str, str, str]:
    """(nowcast, consensus, difference) as text without units, with enough decimals that the shown
    difference is the difference of the shown values and sits on the same side of zero and of
    +-GAP_NEUTRAL_PP as the exact gap (0.2496 vs 0.2 -> '0.2496', '0.2', '+0.0496', never 0.25 / +0.05)."""
    forecast, exact = Decimal(f"{gap['forecast']:g}"), Decimal(str(gap["gap"]))
    neutral = Decimal(str(GAP_NEUTRAL_PP))
    forecast_places = max(0, -forecast.as_tuple().exponent)
    for places in range(max(2, forecast_places), 11):
        value = Decimal(str(gap["nowcast"])).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
        diff = value - forecast
        if (diff == 0) == (exact == 0) and (abs(diff) >= neutral) == (abs(exact) >= neutral):
            break
    return f"{value:.{places}f}", f"{forecast:.{forecast_places}f}", f"{diff:+.{places}f}"


def nowcast_age(as_of, stamp: datetime) -> Optional[int]:
    """Days from the nowcast's last data point ('MM/DD') to the stamp's date, or None."""
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", str(as_of or ""))
    if not match:
        return None
    try:
        day = date(stamp.year, int(match.group(1)), int(match.group(2)))
        if day > stamp.date():
            day = day.replace(year=stamp.year - 1)
    except ValueError:
        return None
    return (stamp.date() - day).days


def nowcast_fetch_problem(nowcast, stamp: datetime) -> Optional[str]:
    """Why the nowcast record cannot be used at the cutoff, or None: a failed fetch (no record or an
    error), no fetch time, a fetch after the cutoff (plus NOWCAST_CLOCK_SKEW), or a fetch older than
    NOWCAST_MAX_AGE_DAYS."""
    if not isinstance(nowcast, dict) or nowcast.get("error"):
        return "取得失敗"
    raw = nowcast.get("timestamp")
    fetched = stamp_of(raw) if raw else None
    if fetched is None:
        return "取得時刻の記録なし"
    if fetched > stamp + NOWCAST_CLOCK_SKEW:
        return "取得が収集時刻より後"
    if fetched < stamp - timedelta(days=NOWCAST_MAX_AGE_DAYS):
        return f"取得が{NOWCAST_MAX_AGE_DAYS}日より前"
    return None


def adopted_nowcasts(events, nowcast, stamp: datetime, mode: str) -> list[tuple]:
    """(when, event, gap) for CPI / PCE releases after the stamp inside their tier window. The
    nowcast must have been fetched at or before the cutoff (nowcast_fetch_problem) and its last
    data point be at most NOWCAST_MAX_AGE_DAYS old. One per series and month (the earliest release),
    at most NOWCAST_MAX_PER_WINDOW, nearest first. `events` is the calendar's event list or the
    calendar record itself; a record with an error adopts nothing. The parent input, the board and
    the event table all use this one list."""
    from scrapers.macro_surprise import event_datetime_jst
    if isinstance(events, dict):
        if events.get("error"):
            return []
        events = events.get("events")
    if nowcast_fetch_problem(nowcast, stamp):
        return []
    dated = sorted(((event_datetime_jst(ev), k, ev) for k, ev in enumerate(events or [])),
                   key=lambda x: (x[0] is None, x[0] or stamp, x[1]))
    picked, seen = [], set()
    for when, _, ev in dated:
        level = tier(ev) if when is not None else None
        if level is None or not (stamp < when < event_window(stamp, mode, level)[1]):
            continue
        gap = nowcast_gap(ev, nowcast)
        age = nowcast_age(gap.get("as_of"), stamp) if gap else None
        if age is None or not 0 <= age <= NOWCAST_MAX_AGE_DAYS or (gap["series"], gap["month"]) in seen:
            continue
        seen.add((gap["series"], gap["month"]))
        picked.append((when, ev, gap))
    return picked[:NOWCAST_MAX_PER_WINDOW]


def stamp_of(value) -> Optional[datetime]:
    """A datetime or ISO text as an aware datetime (naive = JST); None when unparsable."""
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=JST)


def format_us_event_lines(events, nowcast, now, mode: str = "daily") -> list[str]:
    """Parent-model input: US Tier A/B releases, with the nowcast gap only where the board uses it.
    `events` is the calendar record (preferred: its error is checked the same way as on the board) or
    its event list."""
    from scrapers.macro_surprise import event_datetime_jst
    stamp = stamp_of(now)
    calendar, events = events, (events.get("events") if isinstance(events, dict) else events)
    # With a time, the same releases and period as the HTML table (event_window).
    lines = [f"### 米国の重要指標（{window_label(stamp, mode)}。HTMLの表と同じ期間、Tier A=常時・Tier B=近接時、コード分類）"
             if stamp else "### 米国の重要指標（Tier A=常時・Tier B=近接時、コード分類）"]
    adopted = {id(ev): gap for _, ev, gap in adopted_nowcasts(calendar, nowcast, stamp, mode)} if stamp else {}
    rows = []
    for ev in events or []:
        level, when = tier(ev), event_datetime_jst(ev)
        if level is None or when is None:
            continue
        if stamp and not event_window(stamp, mode, level)[0] <= when < event_window(stamp, mode, level)[1]:
            continue
        gap = adopted.get(id(ev))
        extra = ""
        if gap:
            shown_now, shown_consensus, shown_diff = gap_display(gap)
            extra = (f"｜Cleveland連銀nowcast {shown_now}%（{gap['as_of']}時点）− 市場予想 {shown_consensus}% = "
                     f"{shown_diff}pp")
        rows.append((when, f"- Tier {level}｜{when.strftime('%m/%d %H:%M')} JST｜{display_name(ev.get('indicator'))}"
                           f"｜前回 {ev.get('previous', 'N/A')}｜予想 {ev.get('forecast', 'N/A')}"
                           + (f"｜結果 {ev.get('actual')}" if ev.get("actual") not in (None, "", "N/A") else "")
                           + extra))
    lines.extend(text for _, text in sorted(rows, key=lambda r: r[0]))
    if not rows:
        lines.append("- 該当なし（収集範囲内に米国のTier A/B指標なし）")
    lines.append("- 指標の採否: Tier A=FOMC・FRB議長・雇用統計・CPI、Tier B=小売・GDP・PCE・PPI・ISM・耐久財・議事要旨・"
                 "ミシガン期待インフレ・失業保険・ADP・JOLTS。それ以外（住宅・入札・在庫・地区連銀・S&P PMI等）は図に載せない")
    lines.append("- nowcast差の読み方: 予測が市場予想より低い（負）なら下振れ寄り＝金に追い風、高い（正）なら上振れ寄り＝逆風。"
                 "差が±0.05pp未満は中立。発表結果の予測ではない。差は発表前の指標のうち、nowcastが"
                 f"{NOWCAST_MAX_AGE_DAYS}日以内に更新され、HTMLの表の期間に入るものだけに付ける（バイアス盤と同じ規則）")
    return lines
