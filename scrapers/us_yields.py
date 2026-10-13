"""Same-day US Treasury yields (2Y / 10Y) from the CNBC quote service.

FRED DGS2 / DGS10 lag by one to two business days, so the short-term board had no
same-day rate. CNBC publishes the live yield, the previous close and the quote time;
the change is recomputed here from last minus previous close (the provider's own
percentage field is not used). A quote counts as today's only when it belongs to the
current New York trading date (17:00 boundary); otherwise it is flagged stale and the
board falls back to FRED with its own date. Failures return an error, never an invented value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import requests

URL = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
SYMBOLS = ("US2Y", "US10Y")
JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
NY = ZoneInfo("America/New_York")


def trading_date(ts: datetime):
    """New York trading date: a quote from 17:00 New York onwards belongs to the next day."""
    return (ts.astimezone(NY) + timedelta(hours=7)).date()


def _percent(text) -> Optional[float]:
    try:
        return float(str(text).replace("%", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse(payload: dict, now: datetime) -> dict:
    quotes = ((payload or {}).get("FormattedQuoteResult") or {}).get("FormattedQuote") or []
    out = {}
    for q in quotes:
        symbol = q.get("symbol")
        if symbol not in SYMBOLS:
            continue
        last, prev = _percent(q.get("last")), _percent(q.get("previous_day_closing"))
        try:
            at = datetime.strptime(str(q.get("last_time")), "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            at = None
        if last is None or prev is None or at is None:
            continue
        out[symbol] = {"value": last, "prev_close": prev, "change": round(last - prev, 3),
                       "as_of": at.isoformat(), "as_of_jst": at.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
                       "market_status": q.get("curmktstatus"), "trading_date": trading_date(at).isoformat(),
                       "stale": at > now or trading_date(at) != trading_date(now)}
    return out


def fetch_us_yields(now: Optional[datetime] = None, getter: Optional[Callable] = None) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = {"source": "CNBC", "timestamp": now.isoformat(), "error": None}
    params = {"symbols": "|".join(SYMBOLS), "requestMethod": "itv", "noform": "1", "partnerId": "2",
              "fund": "1", "exthrs": "1", "output": "json", "events": "1"}
    try:
        if getter is None:
            response = requests.get(URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
            payload = response.json()
        else:
            payload = getter(URL, params)
        parsed = parse(payload, now)
    except Exception as exc:  # network, HTTP and JSON failures
        result["error"] = f"CNBC 取得不可（{type(exc).__name__}）"
        return result
    result.update(parsed)
    missing = [s for s in SYMBOLS if s not in parsed]
    if missing:
        result["error"] = f"CNBC {'・'.join(missing)} を解析できず"
    return result
