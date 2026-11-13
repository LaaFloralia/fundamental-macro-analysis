"""Age and holiday notes of the board's materials, and the fixes found on the 2026-11-13 Daily."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from scrapers import freshness as fr  # noqa: E402
from scrapers import us_events as ue  # noqa: E402
from scripts import offchart_board as board  # noqa: E402

JST = ZoneInfo('Asia/Tokyo')


def jst(text):
    return datetime.fromisoformat(text).replace(tzinfo=JST)


def test_us_holidays_follow_the_federal_rules_and_the_markets_differ():
    bond, equity = fr.us_holidays(2026), fr.us_holidays(2026, 'equity')
    assert bond[date(2026, 11, 11)] == '退役軍人の日' and bond[date(2026, 10, 12)] == 'コロンブスデー'
    assert date(2026, 11, 11) not in equity and date(2026, 10, 12) not in equity   # the stock exchanges are open
    assert date(2026, 7, 3) in bond and date(2026, 4, 3) in bond                   # 7/4 on a Saturday; Good Friday
    assert date(2027, 12, 24) in fr.us_holidays(2027) and date(2027, 1, 1) in fr.us_holidays(2027)


def test_age_note_names_the_weekend_and_the_holiday_and_is_empty_for_a_fresh_value():
    cutoff = jst('2026-11-13T18:04:37')
    assert fr.age_note('2026-11-10', cutoff) == '3日前の値（11/11は米国の祝日）'    # FRED real rate on 11/13
    assert fr.age_note('2026-11-12', cutoff, 'equity') == ''                       # GLD of the day before
    assert fr.age_note('2026-11-06', cutoff) == '7日前の値（土日と11/11の米国の祝日を挟む）'
    assert fr.age_note('2026-11-12', jst('2026-11-16T09:00:00')) == '4日前の値（土日を挟む）'
    assert fr.age_note('2026-11-10', cutoff, 'equity') == '3日前の値'              # no closure explains it
    assert fr.age_note('', cutoff) == '' and fr.age_note(None, cutoff) == ''


def test_cot_note_reads_the_cftc_schedule_and_flags_a_passed_release():
    args = (config.COT_RELEASE_DATES, config.COT_RELEASE_DELAYED)
    assert fr.cot_release('2026-11-03', *args)['date'] == date(2026, 11, 6)
    held = fr.cot_release('2026-11-10', *args)
    assert held['date'] == date(2026, 11, 16) and held['delayed'] is True          # Veterans Day week
    assert fr.cot_note('2026-11-03', jst('2026-11-14T07:00:00'), *args) == \
        '11日前の観測。次の公表は11/16（月）・祝日で延期（CFTCの予定表）'
    # 11/16 15:30 Eastern is 11/17 05:30 JST: a 09:00 collection that still holds 11/03 says so
    assert fr.cot_note('2026-11-03', jst('2026-11-17T09:00:00'), *args) == \
        '14日前の観測。11/16（月）の公表予定を過ぎても次の週を取得できていない（CFTCの予定表）'
    assert fr.cot_note('2026-11-03', jst('2026-11-17T05:00:00'), *args).endswith('祝日で延期（CFTCの予定表）')
    assert fr.cot_note('2026-12-22', jst('2026-12-30T09:00:00'), *args) == '8日前の観測。次の公表予定は未確認'
    assert fr.cot_note('2026-11-03', jst('2026-11-14T07:00:00'), [], []) == '11日前の観測。次の公表予定は未確認'


def test_cot_schedule_in_config_is_weekly_and_marks_only_listed_delays():
    dates = [date.fromisoformat(d) for d in config.COT_RELEASE_DATES]
    assert dates == sorted(dates) and len(dates) == 52
    assert set(config.COT_RELEASE_DELAYED) <= set(config.COT_RELEASE_DATES)
    assert all(d.weekday() == 4 for d in dates if d.isoformat() not in config.COT_RELEASE_DELAYED)   # Fridays


def test_friday_daily_lists_tier_b_through_tuesday_and_weekdays_are_unchanged():
    friday = jst('2026-11-13T18:04:37')
    assert ue.ahead_end(friday.date(), 'daily', 'B') == date(2026, 11, 17)         # was Sunday 11/15
    assert ue.event_window(friday, 'daily', 'B')[1] == jst('2026-11-18T00:00:00')
    assert ue.window_label(friday, 'daily') == '11/12からの結果と、Tier Aは11/20・Tier Bは11/17までの予定'
    assert ue.ahead_end(date(2026, 10, 14), 'daily', 'B') == date(2026, 10, 16)    # a Wednesday: as before
    assert ue.ahead_end(date(2026, 11, 14), 'weekly', 'B') == date(2026, 11, 22)   # the Weekly counts days
    assert ue.tier_scope('weekly') == '予定はTier A・Bとも8日先まで'


def test_central_bank_note_lists_the_months_of_the_total_and_sets_the_early_month_apart():
    cb = {'cumulative_3m_t': 148.6, 'as_of_date': '2026-M10',
          'cumulative_periods': ['2026-M09', '2026-M08', '2026-M07'],
          'cumulative_months': [{'period': '2026-M09', 'net_tonnes': 34.3, 'reporters': 63},
                                {'period': '2026-M08', 'net_tonnes': 45.8, 'reporters': 67},
                                {'period': '2026-M07', 'net_tonnes': 68.5, 'reporters': 66}],
          'months': [{'period': '2026-M10', 'net_tonnes': 5.5, 'reporters': 12, 'partial': True},
                     {'period': '2026-M09', 'net_tonnes': 34.3, 'reporters': 63},
                     {'period': '2026-M08', 'net_tonnes': 45.8, 'reporters': 67}]}
    asof, note = board._cb_breakdown(cb)
    assert asof == '2026年09月分まで（10月は速報）'
    assert note == '内訳 7月 +68.5 t・8月 +45.8 t・9月 +34.3 t／10月 +5.5 t（12か国の速報・合計に含めない）'
    assert round(68.5 + 45.8 + 34.3, 1) == cb['cumulative_3m_t']                   # the listed months add up
    # A collection stored before `cumulative_months`: the month it did not keep is named, not left out
    asof, note = board._cb_breakdown({k: v for k, v in cb.items() if k != 'cumulative_months'})
    assert note == '内訳 7月 記録なし・8月 +45.8 t・9月 +34.3 t／10月 +5.5 t（12か国の速報・合計に含めない）'
    # Before `cumulative_periods`: unchanged wording
    assert board._cb_breakdown({'as_of_date': '2026-M08', 'months': cb['months'][1:]}) == \
        ('2026年08月分', '月別 8月 +45.8 t・9月 +34.3 t')


def test_a_daily_bar_of_the_collection_day_is_a_value_so_far_until_new_york_closes():
    assert board._forming('2026-11-13', jst('2026-11-13T18:04:37')) is True       # 04:04 in New York
    assert board._forming('2026-11-12', jst('2026-11-13T18:04:37')) is False
    assert board._forming('2026-11-13', jst('2026-11-14T07:00:00')) is False      # Friday 17:00 New York: closed
    assert board._forming('', jst('2026-11-14T07:00:00')) is False
