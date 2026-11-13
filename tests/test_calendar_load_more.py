"""The calendar reads a week to its end: "Load more" is pressed until the whole week is listed.

Found on 2026-11-14: the page lists the first 200 occurrences of a week and hides the rest behind "Load more".
The 2026-10-31 Weekly therefore had no row after Thursday 21:00 JST (no employment report on 11/06) and the
2026-11-13 18:02 Daily had no PPI at 22:30 JST the same evening.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers import economic_calendar as ec  # noqa: E402
from scripts import offchart_board as board  # noqa: E402


class Button:
    def __init__(self, page):
        self.page = page

    async def is_visible(self):
        return self.page.pages_left > 0

    async def scroll_into_view_if_needed(self):
        pass

    async def click(self):
        self.page.presses += 1
        if self.page.grows:
            self.page.rows += 100
            self.page.pages_left -= 1


class Page:
    """A week table of `rows` rows with `pages_left` more pages behind the button."""

    def __init__(self, pages_left, grows=True, has_button=True):
        self.rows, self.pages_left, self.grows, self.has_button, self.presses = 200, pages_left, grows, has_button, 0

    async def query_selector_all(self, selector):
        if selector == ec.LOAD_MORE:
            return [Button(self)] if self.has_button else []
        return [None] * self.rows

    async def wait_for_timeout(self, ms):
        pass


def test_load_more_is_pressed_until_the_week_is_complete():
    page = Page(pages_left=2)
    assert asyncio.run(ec._load_all(page)) == (2, False) and page.rows == 400


def test_a_week_that_fits_needs_no_press():
    assert asyncio.run(ec._load_all(Page(pages_left=0))) == (0, False)
    assert asyncio.run(ec._load_all(Page(pages_left=3, has_button=False))) == (0, False)


def test_a_press_that_adds_no_row_or_an_endless_button_is_reported_as_truncated():
    stuck = Page(pages_left=1, grows=False)
    assert asyncio.run(ec._load_all(stuck)) == (1, True)
    endless = Page(pages_left=ec.LOAD_MORE_MAX + 5)
    assert asyncio.run(ec._load_all(endless)) == (ec.LOAD_MORE_MAX, True)


def test_a_truncated_week_is_an_incomplete_calendar_not_a_week_without_releases():
    calendar = {'events': [{'indicator': 'CPI (MoM) (Oct)'}], 'error': None,
                'tab_counts': {'This Week': 10, 'Next Week': 8}, 'load_more': {'This Week': 1, 'Next Week': 12},
                'truncated_tabs': ['Next Week']}
    kind, line = board.calendar_shortfall(calendar)
    assert kind == 'incomplete' and 'Next Week の続きを最後まで読み込めない' in line
    assert board.calendar_shortfall({**calendar, 'truncated_tabs': []}) is None
