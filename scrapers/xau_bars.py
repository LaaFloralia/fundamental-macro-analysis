"""XAUUSD intraday bars from Twelve Data with a local H1 cache.

The XAU-TF H1 CSV (xauusd-smc-quant) is no longer produced, so ATR, session
statistics, correlation and sweep checks had no bars. This module fetches closed
XAU/USD spot bars in UTC and keeps output/history/xau_h1.csv in the same format
(timestamp ms, open, high, low, close), so those readers work unchanged.
Unclosed bars and the weekend / daily maintenance breaks are excluded.
15-minute bars are returned for news-candle levels only and are not cached.
snapshot() / restore() store the exact bars a level computation used, with a SHA-256.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

H1_CACHE = Path(__file__).parent.parent / "output" / "history" / "xau_h1.csv"
H1_KEEP = 6000          # bars kept in the cache (about one year of trading hours)
H1_FETCH = 5000         # Twelve Data maximum outputsize
M15_FETCH = 800         # about eight trading days of 15-minute bars
NY = ZoneInfo("America/New_York")
SOURCE = "Twelve Data XAU/USD"


def market_open(ts: datetime) -> bool:
    """Spot gold trades Sunday 18:00 to Friday 17:00 New York time, with a 17:00-18:00 daily break."""
    ny = ts.astimezone(NY)
    weekday, hour = ny.weekday(), ny.hour
    if weekday == 5 or (weekday == 4 and hour >= 17) or (weekday == 6 and hour < 18):
        return False
    return hour != 17


def parse_values(values, minutes: int, now: datetime) -> list[dict]:
    """Closed, well-formed bars in ascending time order."""
    bars = {}
    for v in values or []:
        try:
            ts = datetime.strptime(str(v["datetime"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            o, h, low, c = (float(v[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError):
            continue
        if ts + timedelta(minutes=minutes) > now or not market_open(ts):
            continue
        if not (0 < low <= min(o, c) and h >= max(o, c)):
            continue
        bars[ts] = {"ts": ts, "open": o, "high": h, "low": low, "close": c}
    return [bars[k] for k in sorted(bars)]


def fetch_bars(interval: str, outputsize: int, now: Optional[datetime] = None,
               getter: Optional[Callable] = None) -> tuple[list[dict], Optional[str]]:
    minutes = {"1h": 60, "15min": 15}[interval]
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if getter is None:
        from scrapers.twelvedata import _get as getter
    data = getter("/time_series", {"symbol": "XAU/USD", "interval": interval, "outputsize": str(outputsize),
                                   "timezone": "UTC", "dp": "2"})
    if not isinstance(data, dict) or data.get("status") == "error":
        message = data.get("message") if isinstance(data, dict) else "応答なし"
        return [], f"{SOURCE} {interval} 取得不可（{message}）"
    bars = parse_values(data.get("values"), minutes, now)
    return bars, (None if bars else f"{SOURCE} {interval} の確定足なし")


def read_cache(path: Path = H1_CACHE) -> list[dict]:
    if not Path(path).exists():
        return []
    bars = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc)
                bars.append({"ts": ts, **{k: float(row[k]) for k in ("open", "high", "low", "close")}})
    except (KeyError, OSError, ValueError):
        return []
    return bars


def update_cache(bars: list[dict], path: Path = H1_CACHE, keep: int = H1_KEEP) -> list[dict]:
    """Merge newly fetched closed bars into the cache; a fresh fetch wins for the same hour."""
    merged = {b["ts"]: b for b in read_cache(path)}
    merged.update({b["ts"]: b for b in bars})
    ordered = [merged[k] for k in sorted(merged)][-keep:]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        for b in ordered:
            writer.writerow([int(b["ts"].timestamp() * 1000), b["open"], b["high"], b["low"], b["close"]])
    os.replace(temporary, path)
    return ordered


def collect(now: Optional[datetime] = None, getter: Optional[Callable] = None,
            cache: Path = H1_CACHE) -> tuple[dict, list[dict], list[dict]]:
    """Fetch H1 (cached) and M15 (in memory) closed bars. Errors are reported, never raised.

    Returns (JSON-safe metadata, H1 bars, M15 bars); the bars stay out of the saved data.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = {"source": SOURCE, "h1_path": str(cache), "h1_count": 0, "h1_last_bar_utc": None,
              "m15_count": 0, "error": None}
    errors, bars, m15 = [], [], []
    try:
        h1, error = fetch_bars("1h", H1_FETCH, now, getter)
        if error:
            errors.append(error)
        bars = update_cache(h1, cache) if h1 else read_cache(cache)
    except Exception as exc:  # network and parsing failures leave the cached bars usable
        errors.append(f"{SOURCE} 1h: {type(exc).__name__}")
        bars = read_cache(cache)
    if bars:
        result.update(h1_count=len(bars), h1_last_bar_utc=bars[-1]["ts"].strftime("%Y-%m-%d %H:%M"))
    try:
        m15, error = fetch_bars("15min", M15_FETCH, now, getter)
        if error:
            errors.append(error)
        result["m15_count"] = len(m15)
    except Exception as exc:
        errors.append(f"{SOURCE} 15min: {type(exc).__name__}")
    result["error"] = "／".join(errors) or None
    return result, bars, m15


def _digest(payload: dict) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(h1: list[dict], m15: list[dict]) -> dict:
    """JSON-safe copy of the bars used: [epoch seconds, open, high, low, close] rows and their SHA-256."""
    rows = lambda bars: [[int(b["ts"].timestamp()), b["open"], b["high"], b["low"], b["close"]] for b in bars or []]
    payload = {"h1": rows(h1), "m15": rows(m15)}
    return {"format": 1, **payload, "sha256": _digest(payload)}


def _restore_rows(rows, frame: str) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError(f"{frame}: 足の一覧がない")
    bars, previous = [], None
    for row in rows:
        if (not isinstance(row, list) or len(row) != 5 or isinstance(row[0], bool) or not isinstance(row[0], int)
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row[1:])):
            raise ValueError(f"{frame}: 足の形式が不正")
        epoch, o, h, low, c = row
        if not (0 < low <= min(o, c) and h >= max(o, c)):
            raise ValueError(f"{frame}: 高安と始終値が矛盾する足（{epoch}）")
        if previous is not None and epoch <= previous:
            raise ValueError(f"{frame}: 時刻が昇順でないか重複（{epoch}）")
        previous = epoch
        bars.append({"ts": datetime.fromtimestamp(epoch, tz=timezone.utc),
                     "open": float(o), "high": float(h), "low": float(low), "close": float(c)})
    return bars


def restore(snap: dict) -> tuple[list[dict], list[dict]]:
    """(h1, m15) bars from a snapshot; ValueError on a wrong format, a SHA mismatch or a malformed bar."""
    if not isinstance(snap, dict) or snap.get("format") != 1:
        raise ValueError("スナップショットの形式が不正")
    payload = {"h1": snap.get("h1"), "m15": snap.get("m15")}
    if snap.get("sha256") != _digest(payload):
        raise ValueError("スナップショットのSHA-256が一致しない")
    return _restore_rows(payload["h1"], "h1"), _restore_rows(payload["m15"], "m15")
