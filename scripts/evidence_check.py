"""Recompute the percentiles and correlations of a collection from the series stored with it.

A percentile was ranked against a history file and a correlation against price files that both change
after the collection, so an edition could not show later what its numbers rested on (2026-11-14 audit, I1).
The collection now carries the compared series (`positioning.comparison`, `correlation.pairs[].observations`);
this module recomputes from them alone and compares with the values the report shows.

Nothing is filled in: a value whose series is not stored is listed as not recomputable, and a percentile or
coefficient that was absent stays absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.correlation import recompute_pair  # noqa: E402
from scrapers.positioning_history import percentile_rank  # noqa: E402


def recompute(data: dict) -> dict:
    """{'checked': [...], 'mismatches': [...], 'not_recomputable': [...]} for one collection."""
    checked, mismatches, absent = [], [], []
    shown = (data.get('offchart_features') or {}).get('positioning') or {}
    comparison = (data.get('positioning') or {}).get('comparison')
    targets = [(f'個人ロング比率 {symbol}', (shown.get('retail') or {}).get(symbol), entry)
               for symbol, entry in ((comparison or {}).get('retail') or {}).items()]
    if comparison is None:
        targets = [(f'個人ロング比率 {symbol}', entry, None) for symbol, entry in (shown.get('retail') or {}).items()]
    if shown.get('cot_gold_mm') or (comparison or {}).get('cot_gold_mm'):
        targets.append(('COT Managed Money', shown.get('cot_gold_mm'), (comparison or {}).get('cot_gold_mm')))
    for name, displayed, stored in targets:
        if not stored or stored.get('series') is None:
            absent.append(f'{name}の百分位: 比較数列が収集に保存されていない')
            continue
        value = stored.get('value')
        again = percentile_rank([v for _, v in stored['series']], value) if value is not None else None
        # The board shows the percentile of the features record; when that record is absent nothing is shown
        # and the recomputation is compared with the value stored beside the series.
        displayed_value = displayed.get('percentile') if displayed else stored.get('percentile')
        record = {'name': f'{name}の百分位', 'n': len(stored['series']), 'shown': displayed_value, 'recomputed': again}
        checked.append(record)
        if again != displayed_value or again != stored.get('percentile'):
            mismatches.append(f'{name}の百分位: 表示 {displayed_value}・保存した数列からの再計算 {again}（{len(stored["series"])}件）')
    for pair in (data.get('correlation') or {}).get('pairs') or []:
        name = f'金との相関 {pair.get("pair")}'
        if pair.get('r_20d') is None and pair.get('r_60d') is None:
            continue  # not computed: nothing is shown, nothing to recompute
        again = recompute_pair(pair)
        if not again:
            absent.append(f'{name}: 計算に使った系列が収集に保存されていない')
            continue
        for key, label in (('r_20d', '20日'), ('r_60d', '60日')):
            checked.append({'name': f'{name} {label}', 'n': len(pair['observations']), 'shown': pair.get(key),
                            'recomputed': again[key]})
            if again[key] != pair.get(key):
                mismatches.append(f'{name} {label}: 表示 {pair.get(key)}・保存した系列からの再計算 {again[key]}')
    return {'checked': checked, 'mismatches': mismatches, 'not_recomputable': absent}


if __name__ == '__main__':
    result = recompute(json.loads(Path(sys.argv[1]).read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    sys.exit(1 if result['mismatches'] else 0)
