"""A collection carries the series its percentiles and correlations were computed from (2026-11-14 audit, I1).

The history file and the price files change after a collection, so the edition must be enough to recompute.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers import correlation, positioning_history as ph  # noqa: E402
from scripts import evidence_check, report_bundle  # noqa: E402

NOW = '2026-11-13T18:04:00+09:00'
SNAPSHOT = {'recorded_at': NOW, 'retail': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 69.0}},
            'cot_gold_mm': {'report_date': '2026-11-03', 'mm_net_pct_oi': 26.0}}


def history(hours=40, weeks=30):
    rows = [{'recorded_at': f'2026-11-{1 + h // 24:02d}T{h % 24:02d}:10:00+09:00',
             'retail': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 50.0 + h % 23}}} for h in range(hours)]
    rows += [{'recorded_at': f'2026-04-01T00:00:{w:02d}+09:00',
              'cot_gold_mm': {'report_date': f'2026-{1 + w // 4:02d}-{1 + 7 * (w % 4):02d}', 'mm_net_pct_oi': 20.0 + w % 11}}
             for w in range(weeks)]
    # another source, a later hour and a later report week are not compared
    rows.append({'recorded_at': '2026-11-02T03:30:00+09:00', 'retail': {'XAUUSD': {'source': 'FXSSI', 'long_pct': 99.0}}})
    rows.append({'recorded_at': '2026-11-13T19:00:00+09:00', 'retail': {'XAUUSD': {'source': 'MyFXBook', 'long_pct': 99.0}}})
    rows.append({'recorded_at': NOW, 'cot_gold_mm': {'report_date': '2026-11-10', 'mm_net_pct_oi': 99.0}})
    return rows


def collection(rows=None):
    rows = history() if rows is None else rows
    return {'positioning': {'snapshot': SNAPSHOT, 'comparison': ph.positioning_comparison(SNAPSHOT, rows)},
            'offchart_features': {'positioning': ph.positioning_numbers(SNAPSHOT, rows)}}


def test_the_stored_series_is_what_the_percentile_was_ranked_against():
    rows = history()
    stored = ph.positioning_comparison(SNAPSHOT, rows)
    shown = ph.positioning_numbers(SNAPSHOT, rows)
    retail, cot = stored['retail']['XAUUSD'], stored['cot_gold_mm']
    assert len(retail['series']) == shown['retail']['XAUUSD']['history_n'] == 40
    assert len(cot['series']) == shown['cot_gold_mm']['history_n'] == 30
    assert 99.0 not in [v for _, v in retail['series']] + [v for _, v in cot['series']]
    assert retail['percentile'] == shown['retail']['XAUUSD']['percentile'] is not None
    assert cot['percentile'] == shown['cot_gold_mm']['percentile'] is not None
    assert json.loads(json.dumps(stored)) == stored  # plain JSON, saved with the collection


def test_percentiles_are_recomputed_from_the_collection_alone():
    result = evidence_check.recompute(collection())
    assert result['mismatches'] == [] and result['not_recomputable'] == []
    assert [(c['name'], c['n']) for c in result['checked']] == [('個人ロング比率 XAUUSDの百分位', 40), ('COT Managed Moneyの百分位', 30)]
    assert all(c['shown'] == c['recomputed'] for c in result['checked'])


def test_a_shown_percentile_that_the_series_does_not_give_is_a_mismatch():
    data = collection()
    data['offchart_features']['positioning']['cot_gold_mm']['percentile'] = 12.3
    assert evidence_check.recompute(data)['mismatches'] == [
        f'COT Managed Moneyの百分位: 表示 12.3・保存した数列からの再計算 {data["positioning"]["comparison"]["cot_gold_mm"]["percentile"]}（30件）']
    changed = collection()
    changed['positioning']['comparison']['retail']['XAUUSD']['series'][0][1] = 90.0
    assert len(evidence_check.recompute(changed)['mismatches']) == 1


def test_a_percentile_that_could_not_be_given_stays_absent_with_its_short_series():
    data = collection(history(hours=6, weeks=3))
    stored = data['positioning']['comparison']
    assert stored['retail']['XAUUSD']['percentile'] is None and len(stored['retail']['XAUUSD']['series']) == 6
    assert stored['cot_gold_mm']['percentile'] is None and len(stored['cot_gold_mm']['series']) == 3
    result = evidence_check.recompute(data)
    assert result['mismatches'] == [] and all(c['recomputed'] is None for c in result['checked'])


def test_an_older_collection_is_reported_as_not_recomputable_never_as_checked():
    data = json.loads((Path(__file__).parent / 'fixtures/edition_v9/2026-11-13T1802.data.json').read_text())
    result = evidence_check.recompute(data)
    assert result['checked'] == [] and result['mismatches'] == []
    assert 'COT Managed Moneyの百分位: 比較数列が収集に保存されていない' in result['not_recomputable']
    assert any(line.startswith('金との相関') for line in result['not_recomputable'])


def series(n=70):
    days = [f'2026-{8 + (i // 28):02d}-{1 + i % 28:02d}' for i in range(n)]
    xau = {d: 3800 + 7 * ((i * 37) % 23) + i for i, d in enumerate(days)}
    fred = {'DFII10': {'observations': [[d, 2.5 + 0.01 * ((i * 53) % 17) - 0.002 * i] for i, d in enumerate(days)]}}
    return xau, fred


def test_both_correlation_windows_are_recomputed_from_the_stored_observations():
    xau, fred = series()
    pairs = [p for p in correlation.build_correlations(xau, fred)['pairs'] if p['r_20d'] is not None]
    assert pairs and all(len(p['observations']) == correlation.LONG_WINDOW + 1 for p in pairs)
    for pair in pairs:
        assert pair['r_60d'] is not None
        assert correlation.recompute_pair(json.loads(json.dumps(pair))) == {'r_20d': pair['r_20d'], 'r_60d': pair['r_60d']}
    result = evidence_check.recompute({'correlation': {'pairs': pairs}})
    assert result['mismatches'] == [] and len(result['checked']) == 2 * len(pairs)


def test_a_short_history_keeps_the_60_day_coefficient_absent():
    xau, fred = series(30)
    pair = next(p for p in correlation.build_correlations(xau, fred)['pairs'] if p['r_20d'] is not None)
    assert pair['r_60d'] is None and len(pair['observations']) == 30
    assert correlation.recompute_pair(pair) == {'r_20d': pair['r_20d'], 'r_60d': None}


def test_a_changed_observation_or_coefficient_is_a_mismatch():
    xau, fred = series()
    pair = next(p for p in correlation.build_correlations(xau, fred)['pairs'] if p['r_20d'] is not None)
    wrong = deepcopy(pair)
    wrong['r_20d'] = round(pair['r_20d'] + 0.11, 2)
    assert evidence_check.recompute({'correlation': {'pairs': [wrong]}})['mismatches'] == [
        f'金との相関 {pair["pair"]} 20日: 表示 {wrong["r_20d"]}・保存した系列からの再計算 {pair["r_20d"]}']


def test_a_mismatch_is_written_into_the_limits_of_the_report_and_a_match_adds_nothing():
    base = json.loads((Path(__file__).parent / 'fixtures/edition_v9/2026-11-13T1802.data.json').read_text())
    head = (Path(__file__).parent / 'fixtures/edition_v9/2026-11-13T1802.md').read_text().split('## 図表に使用した観測値')[0]
    plain = report_bundle.build_observations(base, 'daily', head)
    assert not any(n.startswith('検算の不一致') for n in plain['limitations'])
    xau, fred = series()
    pair = next(p for p in correlation.build_correlations(xau, fred)['pairs'] if p['r_20d'] is not None)
    ok = report_bundle.build_observations({**base, 'correlation': {'pairs': [pair]}}, 'daily', head)
    assert not any(n.startswith('検算の不一致') for n in ok['limitations'])
    bad = report_bundle.build_observations({**base, 'correlation': {'pairs': [{**pair, 'r_60d': 0.99}]}}, 'daily', head)
    assert [n for n in bad['limitations'] if n.startswith('検算の不一致')] == [
        f'検算の不一致: 金との相関 {pair["pair"]} 60日: 表示 0.99・保存した系列からの再計算 {pair["r_60d"]}']
    assert f'制約: 検算の不一致: 金との相関 {pair["pair"]} 60日' in bad['appendix']
