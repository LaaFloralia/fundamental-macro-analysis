"""How old an observation is, and why: weekends, US holidays, and the CFTC release schedule.

The board shows each material with its observation date. A value that is three days old because of a US
holiday looked the same as a same-day value, and a COT report that the CFTC publishes late in a holiday
week looked like a failed fetch. This module only describes the age; whether a material is too old to be
judged stays with `offchart_board.MAX_AGE_DAYS`.

US holidays are computed from the federal rules (SIFMA closes the bond market on all of them and on Good
Friday; the stock exchanges stay open on Columbus Day and Veterans Day). Unscheduled closures and early
closes are not known here. COT release dates are the CFTC's published schedule, copied into
`config.COT_RELEASE_DATES` with its source; a year without a copied schedule yields no release date.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
JST = ZoneInfo("Asia/Tokyo")
COT_RELEASE_TIME = time(15, 30)  # Eastern, per the CFTC release schedule page
WEEKDAYS_JA = "月火水木金土日"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """A holiday on Saturday is kept on Friday, one on Sunday on Monday."""
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * i - k - h + m + 32) % 7
    month = (h - m + r + 90) // 25
    return date(year, month, (h - m + r + month + 19) % 32)


def us_holidays(year: int, market: str = "bond") -> dict[date, str]:
    """{date: Japanese name} of the days a US market is closed. `market`: 'bond' or 'equity'."""
    days = {
        _observed(date(year, 1, 1)): "元日",
        _nth_weekday(year, 1, 0, 3): "キング牧師の日",
        _nth_weekday(year, 2, 0, 3): "大統領の日",
        _easter(year) - timedelta(days=2): "聖金曜日",
        _last_weekday(year, 5, 0): "戦没者追悼記念日",
        _observed(date(year, 6, 19)): "ジューンティーンス",
        _observed(date(year, 7, 4)): "独立記念日",
        _nth_weekday(year, 9, 0, 1): "レイバーデー",
        _nth_weekday(year, 11, 3, 4): "感謝祭",
        _observed(date(year, 12, 25)): "クリスマス",
    }
    if market == "bond":
        days[_nth_weekday(year, 10, 0, 2)] = "コロンブスデー"
        days[_observed(date(year, 11, 11))] = "退役軍人の日"
    return days


def _md(day: date) -> str:
    return day.strftime("%m/%d")


def age_note(as_of, stamp: datetime, market: str = "bond") -> str:
    """'' for a value of the collection day or the day before; else e.g. '3日前の値（11/11は米国の祝日）'.

    The age is counted in calendar days from the observation date to the collection's JST date. The reason
    names the weekend and the US holidays between the two dates (the collection's New York date included,
    because a holiday today means nothing newer exists yet).
    """
    try:
        day = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return ""
    local = stamp.astimezone(JST) if stamp.tzinfo else stamp
    age = (local.date() - day).days
    if age < 2:
        return ""
    until = (stamp.astimezone(NEW_YORK) if stamp.tzinfo else stamp).date()
    between = [day + timedelta(days=n) for n in range(1, (until - day).days + 1)]
    closed = {}
    for year in {d.year for d in between}:
        closed.update(us_holidays(year, market))
    holidays = [d for d in between if d in closed and d.weekday() < 5]
    reasons = []
    if any(d.weekday() >= 5 for d in between):
        reasons.append("土日")
    if holidays:
        reasons.append("・".join(_md(d) for d in holidays) + "の米国の祝日")
    if not reasons:
        return f"{age}日前の値"
    if len(reasons) == 1 and holidays:
        return f"{age}日前の値（{_md(holidays[0]) if len(holidays) == 1 else '・'.join(_md(d) for d in holidays)}は米国の祝日）"
    return f"{age}日前の値（{'と'.join(reasons)}を挟む）"


def _schedule(release_dates) -> list[date]:
    out = []
    for value in release_dates or []:
        try:
            out.append(date.fromisoformat(str(value)))
        except ValueError:
            continue
    return sorted(out)


def cot_release(observed, release_dates, delayed=()) -> Optional[dict]:
    """The scheduled release of a Tuesday observation: the first schedule date three or more days later.

    None when the schedule does not cover it (another year, or a gap of more than ten days, which means the
    schedule has no entry for that week).
    """
    try:
        day = date.fromisoformat(str(observed)[:10])
    except ValueError:
        return None
    for release in _schedule(release_dates):
        if release >= day + timedelta(days=3):
            if (release - day).days > 10:
                return None
            return {"date": release, "delayed": release.isoformat() in {str(d) for d in delayed},
                    "at": datetime.combine(release, COT_RELEASE_TIME, NEW_YORK)}
    return None


def cot_note(observed, stamp: datetime, release_dates, delayed=()) -> str:
    """Age of the COT observation and when the next week's report is due, from the CFTC schedule.

    '11日前の観測。次の公表は11/16（月）・祝日で延期（CFTCの予定表）'. When the scheduled time of a newer
    report has passed and the collection still holds the older week, it says so instead of naming a date.
    """
    try:
        day = date.fromisoformat(str(observed)[:10])
    except ValueError:
        return ""
    local = stamp.astimezone(JST) if stamp.tzinfo else stamp.replace(tzinfo=JST)
    age = (local.date() - day).days
    head = f"{age}日前の観測" if age >= 0 else ""
    following = cot_release(day + timedelta(days=7), release_dates, delayed)
    if following is None:
        return f"{head}。次の公表予定は未確認" if head else ""
    when = following["date"]
    label = f"{_md(when)}（{WEEKDAYS_JA[when.weekday()]}）"
    if local >= following["at"]:
        return f"{head}。{label}の公表予定を過ぎても次の週を取得できていない（CFTCの予定表）"
    return f"{head}。次の公表は{label}{'・祝日で延期' if following['delayed'] else ''}（CFTCの予定表）"
