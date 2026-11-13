"""The day-over-day change of an index uses the previous session's close, read from the dated daily bars.

Found by the 2026-11-14 audit: Yahoo's `chartPreviousClose` is the close before the first bar of the
requested 5-day range. With `previousClose` absent, the 2026-11-13 collection reported NDX +6.23% when
the change against the 11/11 close was -0.30%.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scrapers.premarket import _previous_close  # noqa: E402

# ^NDX daily bars as returned on 2026-11-13 (09:30 New York opens), closes of 11/06 .. 11/12
TS = [1794234600 - 86400 * 4, 1794234600 - 86400 * 3, 1794234600, 1794321000, 1794407400]
META = {'regularMarketPrice': 28289.188, 'chartPreviousClose': 26629.81, 'gmtoffset': -18000}


def test_previous_close_is_the_bar_before_the_quoted_session_not_the_range_start():
    closes = [27100.0, 27300.5, 27907.63, 28374.25, 28289.19]
    close, day = _previous_close({**META, 'regularMarketTime': TS[-1] + 6 * 3600}, TS, closes)
    assert close == 28374.25 and day == '2026-11-10'
    assert round((28289.188 / close - 1) * 100, 2) == -0.30


def test_a_missing_bar_is_skipped_and_never_read_as_zero():
    closes = [27100.0, 27300.5, 27907.63, None, 28289.19]
    assert _previous_close({**META, 'regularMarketTime': TS[-1] + 3600}, TS, closes) == (27907.63, '2026-11-09')


def test_before_the_next_session_opens_the_last_bar_is_the_quoted_session():
    # Saturday morning JST: the quote time is still Friday's close, so Thursday's close is the previous one.
    closes = [27100.0, 27300.5, 27907.63, 28374.25, 28289.19]
    close, _ = _previous_close({**META, 'regularMarketTime': TS[-1] + 6.5 * 3600}, TS, closes)
    assert close == 28374.25


def test_without_dated_bars_the_range_start_is_not_used():
    assert _previous_close({**META, 'regularMarketTime': TS[-1]}, [], []) == (None, None)
    assert _previous_close({**META, 'regularMarketTime': TS[-1], 'previousClose': 28000.0}, None, None) == (28000.0, None)
