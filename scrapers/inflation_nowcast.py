"""Cleveland Fed inflation nowcast for upcoming US CPI / PCE releases.

The market consensus is priced before a release; the reaction follows the surprise.
The Cleveland Fed nowcast (month-over-month, daily updated) is an independent estimate,
so "nowcast minus consensus" shows which side a surprise is more likely to fall on.
Only month-over-month CPI, core CPI, PCE and core PCE are mapped.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Callable, Optional

import requests

URL = "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_month.json"
PAGE = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SERIES = {"cpi": "CPI Inflation", "core_cpi": "Core CPI Inflation",
          "pce": "PCE Inflation", "core_pce": "Core PCE Inflation"}
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                      "Oct", "Nov", "Dec"), start=1)}


def parse(payload) -> dict:
    """{'YYYY-M': {'cpi': {'value': float, 'as_of': 'MM/DD'}, ...}, '_updated': str} for each target month."""
    out = {}
    for chart in payload or []:
        if not isinstance(chart, dict):
            continue
        month = str((chart.get("chart") or {}).get("subcaption") or "")
        if not re.fullmatch(r"\d{4}-\d{1,2}", month):
            continue
        # Vertical release markers ("vline") are categories without a data point.
        labels = [c.get("label") for c in ((chart.get("categories") or [{}])[0].get("category") or [])
                  if not c.get("vline")]
        entry = {}
        for dataset in chart.get("dataset") or []:
            key = next((k for k, name in SERIES.items() if dataset.get("seriesname") == name), None)
            if key is None:
                continue
            points = [(labels[i] if i < len(labels) else None, d.get("value"))
                      for i, d in enumerate(dataset.get("data") or [])]
            points = [(label, float(v)) for label, v in points if v not in (None, "")]
            if points:
                entry[key] = {"value": round(points[-1][1], 3), "as_of": points[-1][0]}
        if entry:
            out[month] = entry
        out["_updated"] = (chart.get("chart") or {}).get("_comment") or out.get("_updated")
    return out


def target_month(indicator: str, released) -> Optional[str]:
    """'CPI (MoM) (Sep)' released 2026-10-14 -> '2026-9'. A data month after the release month is last year's."""
    match = re.search(r"\((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\)", indicator or "")
    if not match or released is None:
        return None
    month = MONTHS[match.group(1)]
    return f"{released.year - 1 if month > released.month else released.year}-{month}"


def series_key(indicator: str) -> Optional[str]:
    """Month-over-month inflation releases mapped to nowcast series."""
    text = (indicator or "").lower()
    if "(mom)" not in text:
        return None
    if "pce price index" in text:
        return "core_pce" if text.startswith("core") else "pce"
    if re.match(r"^(core )?cpi\b", text):
        return "core_cpi" if text.startswith("core") else "cpi"
    return None


def fetch_nowcast(getter: Optional[Callable] = None) -> dict:
    result = {"source": "Cleveland Fed Inflation Nowcasting", "url": PAGE,
              "timestamp": datetime.now(timezone.utc).isoformat(), "months": {}, "updated": None, "error": None}
    try:
        if getter is None:
            response = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            payload = response.json()
        else:
            payload = getter(URL)
        parsed = parse(payload)
    except Exception as exc:
        result["error"] = f"取得不可（{type(exc).__name__}）"
        return result
    result["updated"] = parsed.pop("_updated", None)
    # Keep only the latest three target months: older months are already released.
    result["months"] = {k: parsed[k] for k in sorted(parsed, key=lambda m: tuple(map(int, m.split("-"))))[-3:]}
    if not result["months"]:
        result["error"] = "nowcast系列を解析できず"
    return result
