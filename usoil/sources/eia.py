"""EIA Weekly Petroleum Status Report (table1.csv): crude/product stocks and the crude supply/demand flows.

The CSV mixes two table shapes: stock rows carry 8 cells (label, current,
week ago, change, % change, year ago, change, % change) and flow rows 13 cells
(section, "(n) label", current, week ago, difference, year ago, difference,
then two 4-week-average blocks that are kept positionally). A fixed whitelist
of the design's factors is extracted; anything the whitelist does not find is
reported in ``missing`` (partial), and the six trailing flow cells stay
positional because the WPSR header does not name them unambiguously in the
file itself. The body contains 0x96 bytes (the "– –" percent cells) and is
decoded with replacement so they become unparsable cells (None), not a
``parse_error``.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime, timezone

from usoil.fetch import FetchError, Fetcher, SourceResult
from usoil.sources.base import ok

UTC = timezone.utc
EIA_URL = 'https://ir.eia.gov/wpsr/table1.csv'
MARKER = re.compile(r'^\(\d+\)\s*')
STOCK_TARGETS = {
    'crude_oil_incl_spr': 'Crude Oil',
    'commercial_crude_ex_spr': 'Commercial (Excluding SPR)',
    'spr': 'Strategic Petroleum Reserve (SPR)',
    'gasoline': 'Total Motor Gasoline',
    'distillate': 'Distillate Fuel Oil',
    'jet': 'Kerosene-Type Jet Fuel',
    'propane': 'Propane/Propylene',
    'total_ex_spr': 'Total Stocks (Excluding SPR)',
}
FLOW_TARGETS = {
    'production': ('Crude Oil Supply', 'Domestic Production'),
    'imports': ('Crude Oil Supply', 'Imports'),
    'exports': ('Crude Oil Supply', 'Exports'),
    'refinery_input': ('Crude Oil Supply', 'Crude Oil Input to Refineries'),
    'products_supplied': ('Products Supplied', 'Total'),
}


def _float(text):
    cleaned = str(text if text is not None else '').strip().replace(',', '').replace('%', '')
    if cleaned in ('', '-', '--', 'N/A'):
        return None
    try:
        value = float(cleaned)
        return value if math.isfinite(value) else None
    except ValueError:
        return None  # includes the decoded "– –" cells


def _day(text):
    try:
        return datetime.strptime(str(text).strip(), '%m/%d/%y').date().isoformat()
    except ValueError:
        return None


def _stock(row) -> dict:
    return {'current': _float(row[1]), 'week_ago': _float(row[2]), 'change_wow': _float(row[3]),
            'change_pct_wow': _float(row[4]), 'year_ago': _float(row[5]), 'change_yoy': _float(row[6]),
            'change_pct_yoy': _float(row[7])}


def _flow(row) -> dict:
    return {'current': _float(row[2]), 'week_ago': _float(row[3]), 'change_wow': _float(row[4]),
            'year_ago': _float(row[5]), 'change_yoy': _float(row[6]),
            'extra': [_float(cell) for cell in row[7:13]]}


def wpsr(f: Fetcher) -> SourceResult:
    r = f.get(EIA_URL)
    text = r.body.decode('utf-8', errors='replace')
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise FetchError('empty_payload')
    header = rows[0]
    week_ending = _day(header[1]) if len(header) > 1 else None
    if week_ending is None or len(header) < 6:
        raise FetchError('missing_field')
    prior_week, prior_year = _day(header[2]), _day(header[5])
    if prior_week is None or prior_year is None or not prior_year < prior_week < week_ending:
        raise FetchError('invalid_payload')
    stocks, flows, missing = {}, {}, []
    stock_by_label = {label: key for key, label in STOCK_TARGETS.items()}
    flow_by_key = {value: key for key, value in FLOW_TARGETS.items()}
    for row in rows[1:]:
        if len(row) == 8 and row[0].strip() in stock_by_label:
            stocks[stock_by_label[row[0].strip()]] = _stock(row)
        elif len(row) == 13 and MARKER.match(row[1].strip()):
            key = flow_by_key.get((row[0].strip(), MARKER.sub('', row[1].strip()).strip()))
            if key:
                flows[key] = _flow(row)
    missing = [key for key in list(STOCK_TARGETS) + list(FLOW_TARGETS) if key not in stocks and key not in flows]
    if not stocks and not flows:
        raise FetchError('missing_field')
    for key, stock in stocks.items():
        required = ('current', 'week_ago', 'change_wow')
        if any(stock[k] is None for k in required):
            missing.append(key + '.numeric')
            continue
        if min(stock['current'], stock['week_ago']) < 0 or abs(
                stock['current'] - stock['week_ago'] - stock['change_wow']) > 0.003:
            raise FetchError('invalid_payload')
    for key, flow in flows.items():
        if flow['current'] is None:
            missing.append(key + '.numeric')
    values = {'provider': 'EIA Weekly Petroleum Status Report (table1.csv)',
              'week_ending': week_ending, 'week_ago': prior_week, 'year_ago': prior_year,
              'units': {'stocks': 'million barrels', 'flows': 'thousand barrels per day'},
              'stocks': stocks, 'flows': flows, 'missing': missing}
    return ok('eia_wpsr', [r], values, status='ok' if not missing else 'partial',
              timestamp_quality='source_date')
