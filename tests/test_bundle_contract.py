"""Edition contract: what check_regenerated requires and what the page shows at the top."""
from copy import deepcopy
from datetime import timedelta

import pytest

from scripts import report_bundle as bundle
from tests.test_offchart_board import DRAW_LINE, NOW, SOURCE, prepared, sample


def test_levels_shown_without_their_stored_bars_are_rejected(tmp_path):
    data = sample()
    assert data['xau_levels']['levels'] and 'xau_bars_used' not in data
    manifest, figures, source, summary = prepared(tmp_path, data)
    with pytest.raises(bundle.BundleError, match='without the stored bars'):
        bundle.check_regenerated(data, manifest, figures, source, summary)
    failed = deepcopy(data)
    failed['xau_levels'] = {'error': 'Twelve Data 1h 取得不可', 'levels': []}
    manifest, figures, source, summary = prepared(tmp_path, failed, source=SOURCE.replace(DRAW_LINE, '方向: none'))
    bundle.check_regenerated(failed, manifest, figures, source, summary)


def test_a_figure_that_cannot_be_drawn_is_named_at_the_top(tmp_path):
    data = sample()
    data['xau_levels'] = {'error': 'Twelve Data 1h 取得不可', 'levels': []}
    result = bundle.observations(data, NOW + timedelta(minutes=10), 'daily', SOURCE.replace(DRAW_LINE, '方向: none'))
    chips = [x['text'] for x in result['lead']]
    assert bundle.GONE_CHIP['liquidity'] in chips
    assert bundle.GONE_CHIP['liquidity'] in result['appendix']  # the chip is a quoted row like the others
    assert any(x.startswith('流動性マップ: 1時間足から水準を作れない') for x in result['limitations'])
    assert bundle.GONE_CHIP['bias'] not in chips


def test_the_cutoff_is_the_end_of_collection_even_after_an_hour():
    data = {'timestamp': '2026-10-13T22:14:00+09:00', 'collection_finished_at': '2026-10-13T23:40:00+09:00'}
    assert bundle.analysis_cutoff(data).isoformat() == '2026-10-13T23:40:00+09:00'
    with pytest.raises(bundle.BundleError, match='収集終了時刻が開始より前'):  # an end before the start stops the edition
        bundle.analysis_cutoff({**data, 'collection_finished_at': '2026-10-13T22:00:00+09:00'})
    assert bundle.analysis_cutoff({'timestamp': data['timestamp']}).isoformat() == '2026-10-13T22:14:00+09:00'


def test_a_long_collection_is_named_in_the_limits():
    data = sample()
    data['collection_finished_at'] = '2026-10-13T23:40:00+09:00'
    result = bundle.build_observations(data, 'daily', SOURCE)
    assert any(x.startswith('収集に86分かかった') for x in result['limitations'])
    assert result['cutoff'] == '2026-10-13T23:40:00+09:00'


def test_a_failed_or_incomplete_calendar_is_named_at_the_top():
    data = sample()
    data['economic_calendar'] = {'error': 'timeout', 'events': []}
    chips = [x['text'] for x in bundle.observations(data, NOW + timedelta(minutes=10), 'daily', SOURCE)['lead']]
    assert bundle.CALENDAR_CHIP['failed'] in chips
    data = sample()
    data['economic_calendar']['missing_tabs'] = ['nextWeek']
    chips = [x['text'] for x in bundle.observations(data, NOW + timedelta(minutes=10), 'daily', SOURCE)['lead']]
    assert bundle.CALENDAR_CHIP['incomplete'] in chips and bundle.CALENDAR_CHIP['failed'] not in chips
    chips = [x['text'] for x in bundle.observations(sample(), NOW + timedelta(minutes=10), 'daily', SOURCE)['lead']]
    assert not set(bundle.CALENDAR_CHIP.values()) & set(chips)
