"""MyFXBook Order Book (Positions) の取得経路とフォールバックの回帰テスト。

フィクスチャは 2026-10-14 に XAUUSD の公開ページを 1 回読み込んだときの実データ:
  - myfxbook_order_book_data_xauusd_20261014.json: XHR /community/order-book-data.json の応答全文
  - myfxbook_order_book_highcharts_xauusd_20261014.json: 同じ読み込みでの Highcharts 系列（price, y）
  - myfxbook_outlook_body_xauusd_20261014.txt: 同じ読み込みでの body の inner_text
  - myfxbook_market_depth_head_xauusd_20261014.txt: /community/market-depth-data.json 応答の先頭
    400 文字（全体は約 5MB のため切り詰め）
  - myfxbook_cloudflare_challenge_body.txt: 既定 headless で表示された Cloudflare 確認ページ
"""

import asyncio
import json
from pathlib import Path

import pytest

from scrapers import myfxbook_open_orders as oo

FIX = Path(__file__).parent / "fixtures"
OB_JSON = FIX / "myfxbook_order_book_data_xauusd_20261014.json"
OB_HC = FIX / "myfxbook_order_book_highcharts_xauusd_20261014.json"
BODY = FIX / "myfxbook_outlook_body_xauusd_20261014.txt"
MD_HEAD = FIX / "myfxbook_market_depth_head_xauusd_20261014.txt"
CHALLENGE = FIX / "myfxbook_cloudflare_challenge_body.txt"


def _key(rows):
    return [(r["price"], r["volume"]) for r in rows]


# ---- 構造化経路 ----

def test_order_book_json_identifies_sides_by_key():
    bids, asks = oo._parse_orderbook_json(OB_JSON.read_text())
    assert len(bids) == 15 and len(asks) == 15
    assert _key(asks)[0] == (4151.49, 83)
    assert _key(bids)[0] == (5175.77, 16)
    # 指数表記 4.19E+3 / 4.16E+3 と整数 4429 も価格として読める
    assert (4190.0, 7) in _key(asks)
    assert (4160.0, 6) in _key(bids)
    assert (4429.0, 9) in _key(bids)
    assert all(isinstance(r["volume"], int) for r in bids + asks)


def test_order_book_json_rejects_error_and_malformed_payloads():
    assert oo._parse_orderbook_json('{"error": true, "content": {}}') == ([], [])
    assert oo._parse_orderbook_json("<html>Just a moment...</html>") == ([], [])
    assert oo._parse_orderbook_json(None) == ([], [])
    bids, asks = oo._parse_orderbook_json(
        {"error": False, "content": {"asks": [[4100.5, 3], ["x", 1], [4101, True], [0, 2]],
                                     "bids": [[4000, 2.0]]}})
    assert _key(asks) == [(4100.5, 3)]
    assert _key(bids) == [(4000.0, 2)]


def test_highcharts_series_matches_xhr_from_same_load():
    hc = json.loads(OB_HC.read_text())
    assert [s["name"] for s in hc] == ["Asks", "Bids"]
    assert oo._parse_highcharts_series(hc) == oo._parse_orderbook_json(OB_JSON.read_text())


def test_highcharts_series_uses_names_not_order():
    hc = [{"name": "Bids", "points": [[4000.0, 5]]}, {"name": "Asks", "points": [[4200.0, 7]]},
          {"name": "Navigator", "points": [[1.0, 1]]}]
    bids, asks = oo._parse_highcharts_series(hc)
    assert _key(bids) == [(4000.0, 5)]
    assert _key(asks) == [(4200.0, 7)]
    assert oo._parse_highcharts_series(None) == ([], [])


def test_market_depth_actual_from_response_head():
    assert oo._parse_market_depth_actual(MD_HEAD.read_text()) == 4151.73
    assert oo._parse_market_depth_actual('{"content":{"actual":4.15E+3,') == 4150.0
    assert oo._parse_market_depth_actual('{"content":{"asks":[]}}') is None
    assert oo._parse_market_depth_actual(None) is None


# ---- テキストのフォールバック ----

def test_body_text_fallback_first_half_is_asks():
    """inner_text は Asks → Bids の順。XHR と同じ組に戻ることを実テキストで確認する。"""
    assert oo._parse_orderbook_pairs(BODY.read_text()) == oo._parse_orderbook_json(OB_JSON.read_text())


def test_body_text_fallback_uses_market_depth_median_for_price():
    cp = oo._extract_current_price(BODY.read_text(), "XAUUSD")
    assert cp is not None and 4140.0 < cp < 4160.0


def test_challenge_page_detection():
    body = CHALLENGE.read_text()
    assert oo._is_challenge(body_text=body)
    assert oo._parse_orderbook_pairs(body) == ([], [])
    assert oo._is_challenge(title="Just a moment...")
    assert oo._is_challenge(title="Attention Required! | Cloudflare")
    assert oo._is_challenge(cf_mitigated="challenge")
    assert not oo._is_challenge(title="Forex Sentiment XAUUSD | Myfxbook", body_text=BODY.read_text())


def test_extract_priority_json_then_highcharts_then_text():
    body = BODY.read_text()
    hc = json.loads(OB_HC.read_text())
    full = {"order_book_json": OB_JSON.read_text(), "highcharts": hc, "body_text": body}
    assert oo._extract_from_payload(full)[2] == "order_book_json"
    assert oo._extract_from_payload({**full, "order_book_json": None,
                                     "order_book_fetch": OB_JSON.read_text()})[2] == "in_page_fetch"
    assert oo._extract_from_payload({**full, "order_book_json": "not json"})[2] == "highcharts"
    assert oo._extract_from_payload({**full, "order_book_json": None, "highcharts": None})[2] == "body_text"
    assert oo._extract_from_payload({"order_book_json": None, "highcharts": None,
                                     "body_text": CHALLENGE.read_text()}) == ([], [], None)


def test_user_agent_matches_engine_major_without_headless_token():
    ua = oo._user_agent_for("148.0.7778.96")
    assert "Chrome/148.0.0.0" in ua and "Headless" not in ua
    assert oo._user_agent_for(None) == oo.USER_AGENT


def test_fixture_classification_is_stable():
    bids, asks = oo._parse_orderbook_json(OB_JSON.read_text())
    cls = oo._cluster_and_classify(bids, asks, current_price=4151.73)
    top_bsl, top_ssl = cls["bsl_candidates"][0], cls["ssl_candidates"][0]
    assert (top_bsl["low"], top_bsl["high"], top_bsl["volume_sum"], top_bsl["entries"]) == (4153.15, 4190.0, 165, 14)
    assert (top_ssl["low"], top_ssl["high"], top_ssl["volume_sum"], top_ssl["entries"]) == (4140.14, 4151.49, 124, 8)
    # ±10% 外（5175.77 / 5175.78 / 3458.12）は除外される
    prices = [c[k] for c in cls["bsl_candidates"] + cls["ssl_candidates"] for k in ("low", "high")]
    assert max(prices) < 4600 and min(prices) > 3800


# ---- 取得処理（ブラウザはスタブ） ----

class _FakePlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _payload(**kw):
    base = {"order_book_json": None, "order_book_fetch": None, "market_depth_head": None,
            "market_depth_source": None, "highcharts": None, "body_text": "", "challenge": False,
            "xhr_status": None, "fetch_status": None, "launch": "chromium-new-headless", "elapsed_s": 1.0}
    base.update(kw)
    return base


@pytest.fixture
def stub_browser(monkeypatch):
    calls = []

    def install(payloads):
        async def fake_once(p, symbol, use_new_headless, deadline=None, teardown_log=None):
            calls.append(symbol)
            item = payloads[len(calls) - 1]
            if isinstance(item, Exception):
                raise item
            return item
        monkeypatch.setattr(oo, "async_playwright", lambda: _FakePlaywright())
        monkeypatch.setattr(oo, "_load_outlook_once", fake_once)
        monkeypatch.setattr(oo, "RETRY_DELAY_S", 0)
        return calls
    return install


def test_scrape_retries_once_after_challenge_and_records_method(stub_browser):
    calls = stub_browser([
        _payload(challenge=True, body_text=CHALLENGE.read_text()),
        _payload(order_book_json=OB_JSON.read_text(), market_depth_head=MD_HEAD.read_text(),
                 body_text=BODY.read_text()),
    ])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD", current_price_hint=4140.0))
    assert len(calls) == 2
    assert r["error"] is None
    assert r["bid_count"] == 15 and r["ask_count"] == 15
    assert r["summary"] == {"bid": 15, "ask": 15}
    assert r["current_price"] == 4151.73
    assert r["note"].startswith("取得方法: order_book_json")
    assert "1:challenge" in r["note"] and "2:order_book_json" in r["note"]
    assert "market-depth-data.json" in r["note"]
    assert r["bsl_candidates"] and r["ssl_candidates"]
    assert r["bsl_concentration"]["volume_sum"] == r["bsl_candidates"][0]["volume_sum"]
    assert set(r) == {"source", "symbol", "current_price", "bid_count", "ask_count", "bsl_candidates",
                      "ssl_candidates", "bsl_concentration", "ssl_concentration", "summary", "buckets",
                      "note", "error"}


def test_scrape_reports_challenge_when_all_attempts_blocked(stub_browser):
    calls = stub_browser([_payload(challenge=True)] * 3)
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert len(calls) == 3 == oo.MAX_ATTEMPTS
    assert r["error"] == oo.ERR_CHALLENGE
    assert r["bid_count"] == 0 and r["bsl_candidates"] == []


def test_scrape_keeps_dom_change_error_when_page_loaded_without_data(stub_browser):
    stub_browser([_payload(body_text="Forex Sentiment")] * 3)
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == oo.ERR_NO_PAIRS == "Order Book ペアを抽出できず (DOM 変更の可能性)"


def test_scrape_text_fallback_and_hint_override(stub_browser):
    stub_browser([_payload(body_text=BODY.read_text())])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD", current_price_hint=4400.0))
    assert r["note"].startswith("取得方法: body_text")
    assert "ヒント値を採用" in r["note"]
    assert r["current_price"] == 4400.0


def test_scrape_exception_on_every_attempt_returns_fetch_error(stub_browser):
    stub_browser([RuntimeError("boom")] * 3)
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == "ページ取得失敗: boom"
    assert "例外 RuntimeError" in r["note"]


def test_scrape_keeps_challenge_error_when_retry_raises(stub_browser):
    stub_browser([_payload(challenge=True), RuntimeError("closed"), RuntimeError("closed")])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == oo.ERR_CHALLENGE
    assert "1:challenge" in r["note"] and "2:例外 RuntimeError" in r["note"]


def test_scrape_three_attempts_challenge_rejected_xhr_then_success(stub_browser):
    """本番で起きた「1 回目 challenge → 2 回目データなし」の後、3 回目で取れる流れ。"""
    calls = stub_browser([
        _payload(challenge=True),
        _payload(body_text="Forex Sentiment", xhr_status=403),
        _payload(order_book_json=OB_JSON.read_text(), xhr_status=200, market_depth_head=MD_HEAD.read_text(),
                 market_depth_source="xhr"),
    ])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD", current_price_hint=4140.0))
    assert len(calls) == 3
    assert r["error"] is None and r["bid_count"] == 15
    assert "1:challenge" in r["note"]
    assert "2:データなし (chromium-new-headless, 1.0s, XHR 403)" in r["note"]
    assert "3:order_book_json" in r["note"]


def test_scrape_records_in_page_fetch_method(stub_browser):
    stub_browser([_payload(order_book_fetch=OB_JSON.read_text(), fetch_status=200,
                           market_depth_head=MD_HEAD.read_text(), market_depth_source="in_page_fetch")])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] is None and r["ask_count"] == 15
    assert r["note"].startswith("取得方法: in_page_fetch")
    assert "XHR 未着, ページ内要求 200" in r["note"]
    assert "actual（ページ内要求）" in r["note"]
    assert r["current_price"] == 4151.73


def test_scrape_stops_retrying_when_budget_is_exhausted(stub_browser, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(oo, "_now", lambda: clock["t"])
    calls = stub_browser([_payload(challenge=True)] * 3)
    real_once = oo._load_outlook_once

    async def slow_once(*a, **kw):
        clock["t"] += 75.0  # 1 回目で予算の大半を使い切る（残り 13 秒 < 待ち 0 + 最小 15 秒）
        return await real_once(*a, **kw)
    monkeypatch.setattr(oo, "_load_outlook_once", slow_once)
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert len(calls) == 1
    assert "2:時間上限で打切り" in r["note"]
    assert r["error"] == oo.ERR_CHALLENGE


# ---- _load_outlook_once のページ内要求（ブラウザを模擬） ----

class _FakeResponse:
    def __init__(self, url, status, text="", resource_type="xhr", headers=None):
        self.url, self.status, self._text = url, status, text
        self.headers = headers or {}
        self.request = type("Req", (), {"resource_type": resource_type})()

    async def text(self):
        return self._text

    async def body(self):
        return self._text.encode()


class _FakeLocator:
    async def scroll_into_view_if_needed(self, timeout=None):
        return None


class _FakePage:
    def __init__(self, page_xhr=None, fetch_results=None):
        self.page_xhr = page_xhr or []          # goto 後にページ自身が受け取る応答
        self.fetch_results = fetch_results or {}  # path -> {"status", "text"}
        self.fetch_calls = []
        self.handler = None

    def on(self, event, handler):
        self.handler = handler

    async def goto(self, url, timeout=None, wait_until=None):
        for resp in self.page_xhr:
            await self.handler(resp)
        return _FakeResponse(url, 200, resource_type="document")

    async def title(self):
        return "Forex Sentiment XAUUSD | Myfxbook"

    def locator(self, sel):
        return _FakeLocator()

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)

    async def evaluate(self, js, arg=None):
        if js == oo._JS_IN_PAGE_FETCH:
            path, symbol, max_chars, timeout_ms = arg
            self.fetch_calls.append((path, symbol, max_chars))
            res = dict(self.fetch_results.get(path, {"status": 403, "text": "<html>Attention Required</html>"}))
            if max_chars and res.get("text"):
                res["text"] = res["text"][:max_chars]
            return res
        return None  # Highcharts なし

    async def inner_text(self, sel, timeout=None):
        return "Forex Sentiment"


class _FakeBrowser:
    version = "148.0.7778.96"

    def __init__(self, page):
        self.page = page

    async def new_context(self, **kw):
        page = self.page

        class Ctx:
            async def new_page(self_inner):
                return page
        return Ctx()

    async def close(self):
        return None


def _fake_p(page):
    class Chromium:
        async def launch(self, **kw):
            return _FakeBrowser(page)
    return type("P", (), {"chromium": Chromium()})()


@pytest.fixture
def fast_waits(monkeypatch):
    monkeypatch.setattr(oo, "XHR_WAIT_S", 0.0)
    monkeypatch.setattr(oo, "DATA_WAIT_S", 2.0)
    monkeypatch.setattr(oo, "MARKET_DEPTH_WAIT_S", 0.0)


def _run_once(page):
    async def go():
        payload = await oo._load_outlook_once(_fake_p(page), "XAUUSD", use_new_headless=True)
        await asyncio.sleep(0)
        return payload
    return asyncio.run(go())


def _direct_handler(page):
    """page.on に渡された lambda は ensure_future を返す。goto 内で await できるよう包む。"""
    orig_on = page.on

    def on(event, handler):
        async def h(resp):
            fut = handler(resp)
            if fut is not None:
                await fut
        orig_on(event, h)
    page.on = on
    return page


def test_in_page_fetch_used_when_page_xhr_never_arrives(fast_waits):
    page = _direct_handler(_FakePage(fetch_results={
        oo.ORDER_BOOK_XHR: {"status": 200, "text": OB_JSON.read_text()},
        oo.MARKET_DEPTH_XHR: {"status": 200, "text": MD_HEAD.read_text()},
    }))
    payload = _run_once(page)
    assert payload["xhr_status"] is None and payload["fetch_status"] == 200
    assert [c[0] for c in page.fetch_calls] == [oo.ORDER_BOOK_XHR, oo.MARKET_DEPTH_XHR]
    assert page.fetch_calls[0][2] == 0 and page.fetch_calls[1][2] == 2000
    assert payload["market_depth_source"] == "in_page_fetch"
    bids, asks, method = oo._extract_from_payload(payload)
    assert method == "in_page_fetch" and len(bids) == 15 and len(asks) == 15
    assert oo._parse_market_depth_actual(payload["market_depth_head"]) == 4151.73


def test_in_page_fetch_rejected_is_recorded_and_not_repeated(fast_waits):
    page = _direct_handler(_FakePage())
    payload = _run_once(page)
    assert payload["fetch_status"] == 403
    assert len(page.fetch_calls) == 1
    assert oo._extract_from_payload(payload)[2] is None
    assert "ページ内要求 403" in oo._attempt_state(payload, None)


def test_no_in_page_fetch_when_page_xhr_was_rejected(fast_waits):
    page = _direct_handler(_FakePage(page_xhr=[
        _FakeResponse("https://www.myfxbook.com" + oo.ORDER_BOOK_XHR + "?symbolName=XAUUSD", 403, "blocked")]))
    payload = _run_once(page)
    assert payload["xhr_status"] == 403 and payload["fetch_status"] is None
    assert page.fetch_calls == []
    assert oo._attempt_state(payload, None).endswith("XHR 403)")


def test_page_xhr_captured_without_in_page_fetch(fast_waits):
    page = _direct_handler(_FakePage(page_xhr=[
        _FakeResponse("https://www.myfxbook.com" + oo.ORDER_BOOK_XHR + "?symbolName=XAUUSD", 200, OB_JSON.read_text()),
        _FakeResponse("https://www.myfxbook.com" + oo.MARKET_DEPTH_XHR + "?symbolName=XAUUSD", 200, MD_HEAD.read_text()),
    ]))
    payload = _run_once(page)
    assert payload["xhr_status"] == 200 and page.fetch_calls == []
    assert payload["market_depth_source"] == "xhr"
    assert oo._extract_from_payload(payload)[2] == "order_book_json"


# ---- Cloudflare: 現在のページ状態で判定し、XHR / ページ内要求の遮断を区別する ----

XHR_URL = "https://www.myfxbook.com" + oo.ORDER_BOOK_XHR + "?symbolName=XAUUSD"
CF_HEADERS = {"cf-mitigated": "challenge"}
CF_XHR_BODY = "<!DOCTYPE html><html><head><title>Just a moment...</title></head></html>"


class _ScriptedPage(_FakePage):
    """時計を進めながら、確認画面 → 本ページ → 遅れて XHR、の流れを再現する。

    challenge_polls: 最初の何回の title() で確認画面を返すか
    late_xhr: (wait_for_timeout の呼び出し回数, 応答) — その回の待ちの後にページ自身の XHR が届く
    """

    def __init__(self, clock, challenge_polls=0, goto_headers=None, late_xhr=(), body="Forex Sentiment", **kw):
        super().__init__(**kw)
        self.clock = clock
        self.body = body
        self.challenge_polls = challenge_polls
        self.goto_headers = goto_headers or {}
        self.late_xhr = list(late_xhr)
        self.title_calls = 0
        self.waits = 0

    async def goto(self, url, timeout=None, wait_until=None):
        self.title_calls = 0
        self.waits = 0
        for resp in self.page_xhr:
            await self.handler(resp)
        status = 403 if self.goto_headers else 200
        return _FakeResponse(url, status, resource_type="document", headers=self.goto_headers)

    async def title(self):
        self.title_calls += 1
        return "Just a moment..." if self._showing_challenge() else "Forex Sentiment XAUUSD | Myfxbook"

    async def wait_for_timeout(self, ms):
        self.clock["t"] += ms / 1000
        self.waits += 1
        for n, resp in self.late_xhr:
            if n == self.waits:
                await self.handler(resp)
        await asyncio.sleep(0)

    async def evaluate(self, js, arg=None):
        if js == oo._JS_BODY_HEAD:
            return CHALLENGE.read_text() if self._showing_challenge() else self.body[:3000]
        return await super().evaluate(js, arg)

    async def inner_text(self, sel, timeout=None):
        return CHALLENGE.read_text() if self._showing_challenge() else self.body

    def _showing_challenge(self):
        return self.challenge_polls > 0 and self.title_calls <= self.challenge_polls


class _FakePlaywrightWith:
    def __init__(self, p):
        self.p = p

    async def __aenter__(self):
        return self.p

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_clock(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(oo, "_now", lambda: clock["t"])
    monkeypatch.setattr(oo, "RETRY_DELAY_S", 0)
    return clock


def _scrape_with_page(monkeypatch, page, hint=None):
    monkeypatch.setattr(oo, "async_playwright", lambda: _FakePlaywrightWith(_fake_p(page)))
    return asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD", current_price_hint=hint))


def test_challenge_auto_clears_then_late_xhr_is_captured(fake_clock, monkeypatch):
    """初回応答の cf-mitigated: challenge が残っていても、本ページへ進んだら確認画面と扱わない。

    確認画面 3 回（1.5 秒）→ 本ページ → 開始 9 秒後（CHALLENGE_WAIT_S の 8 秒より後、ページ内要求を出す
    表示後 8 秒より前）にページ自身の XHR が届く。通過時の遷移応答は届かない（古いヘッダだけが残る）
    最悪の場合を再現する。初回ヘッダで判定すると 8 秒で challenge と誤判定して XHR を取り逃す。
    """
    page = _direct_handler(_ScriptedPage(
        fake_clock, challenge_polls=3, goto_headers=CF_HEADERS, body=BODY.read_text(),
        late_xhr=[(18, _FakeResponse(XHR_URL, 200, OB_JSON.read_text())),
                  (18, _FakeResponse("https://www.myfxbook.com" + oo.MARKET_DEPTH_XHR, 200, MD_HEAD.read_text()))]))
    payload = asyncio.run(oo._load_outlook_once(_fake_p(page), "XAUUSD", use_new_headless=True,
                                                deadline=fake_clock["t"] + 60))
    assert payload["challenge"] is False and payload["challenge_cleared"] is True
    assert payload["xhr_status"] == 200 and payload["xhr_refusal"] is None
    assert page.fetch_calls == []  # 表示から XHR_WAIT_S（8 秒）以内に届いたのでページ内要求はしない
    assert 1008.5 < fake_clock["t"] < 1010.0
    assert oo._extract_from_payload(payload)[2] == "order_book_json"

    r = _scrape_with_page(monkeypatch, page, hint=4140.0)
    assert r["error"] is None and r["bid_count"] == 15 and r["ask_count"] == 15
    assert "1:order_book_json (chromium-new-headless" in r["note"] and "確認画面を自動通過" in r["note"]


def test_challenge_that_never_clears_still_reported(fake_clock, monkeypatch):
    page = _direct_handler(_ScriptedPage(fake_clock, challenge_polls=10**6, goto_headers=CF_HEADERS))
    r = _scrape_with_page(monkeypatch, page)
    assert r["error"] == oo.ERR_CHALLENGE
    assert r["note"].count(":challenge (") == oo.MAX_ATTEMPTS


def test_stale_challenge_header_with_blank_page_counts_as_challenge():
    """ヘッダが challenge でタイトルも本文も空（確認画面の読み込み中）なら通過扱いにしない。"""
    class Blank:
        async def title(self):
            return ""

        async def evaluate(self, js, arg=None):
            return ""
    assert asyncio.run(oo._challenge_now(Blank(), {"cf": "challenge"})) is True
    assert asyncio.run(oo._challenge_now(Blank(), {"cf": None})) is False


def test_only_xhr_is_challenged_reports_cloudflare_block(fake_clock, monkeypatch):
    """本ページは表示されたが、ページ自身の XHR だけが Cloudflare に 403 で止められた。"""
    page = _direct_handler(_ScriptedPage(fake_clock, page_xhr=[
        _FakeResponse(XHR_URL, 403, CF_XHR_BODY, headers=CF_HEADERS)]))
    payload = asyncio.run(oo._load_outlook_once(_fake_p(page), "XAUUSD", use_new_headless=True,
                                                deadline=fake_clock["t"] + 60))
    assert payload["challenge"] is False
    assert payload["xhr_status"] == 403 and payload["xhr_refusal"] == "cloudflare"
    assert page.fetch_calls == []

    r = _scrape_with_page(monkeypatch, page)
    assert r["error"] == oo.ERR_CHALLENGE == "Cloudflare のボット確認で遮断され Order Book を取得できず"
    assert "1:Cloudflare遮断 (" in r["note"] and "XHR 403 Cloudflare" in r["note"]
    assert r["note"].count("Cloudflare遮断") == oo.MAX_ATTEMPTS


def test_xhr_challenge_detected_from_body_without_header():
    assert oo._refusal_kind(403, None, CF_XHR_BODY) == "cloudflare"
    assert oo._refusal_kind(403, "challenge", "") == "cloudflare"
    assert oo._refusal_kind(403, None, "blocked") == "http"
    assert oo._refusal_kind(500, None, None) == "http"
    assert oo._refusal_kind(200, "challenge", CF_XHR_BODY) is None
    assert oo._refusal_kind(None, None, CF_XHR_BODY) is None


def test_non_cloudflare_xhr_refusal_keeps_no_pairs_error(fake_clock, monkeypatch):
    page = _direct_handler(_ScriptedPage(fake_clock, page_xhr=[_FakeResponse(XHR_URL, 403, "blocked")]))
    r = _scrape_with_page(monkeypatch, page)
    assert r["error"] == oo.ERR_NO_PAIRS
    assert "1:データなし (chromium-new-headless" in r["note"] and "XHR 403)" in r["note"]
    assert "Cloudflare" not in r["note"]


def test_in_page_fetch_cloudflare_block_is_distinguished(fast_waits):
    page = _direct_handler(_FakePage(fetch_results={
        oo.ORDER_BOOK_XHR: {"status": 403, "text": "<title>Attention Required! | Cloudflare</title>"}}))
    payload = _run_once(page)
    assert payload["fetch_status"] == 403 and payload["fetch_refusal"] == "cloudflare"
    assert oo._attempt_state(payload, None) .startswith("Cloudflare遮断 (")
    assert "ページ内要求 403 Cloudflare" in oo._attempt_state(payload, None)

    page = _direct_handler(_FakePage(fetch_results={oo.ORDER_BOOK_XHR: {"status": 429, "text": "slow down"}}))
    payload = _run_once(page)
    assert payload["fetch_refusal"] == "http"
    assert oo._attempt_state(payload, None).endswith("ページ内要求 429)")


# ---- 全体の時間上限（起動・試行・終了処理を含む） ----

class _HangingPage(_FakePage):
    async def goto(self, url, timeout=None, wait_until=None):
        await asyncio.sleep(3600)


class _HangingCloseBrowser(_FakeBrowser):
    async def close(self):
        await asyncio.sleep(3600)


class _HangingStopPlaywright:
    def __init__(self, p, hang_start=False):
        self.p, self.hang_start = p, hang_start

    async def __aenter__(self):
        if self.hang_start:
            await asyncio.sleep(3600)
        return self.p

    async def __aexit__(self, *exc):
        await asyncio.sleep(3600)


@pytest.fixture
def tiny_budget(monkeypatch):
    """実時計のまま上限を 0.6 秒に縮める（テストを遅くしない）。"""
    monkeypatch.setattr(oo, "TOTAL_BUDGET_S", 0.6)
    monkeypatch.setattr(oo, "CLOSE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(oo, "STOP_TIMEOUT_S", 0.1)
    monkeypatch.setattr(oo, "START_TIMEOUT_S", 0.2)
    monkeypatch.setattr(oo, "MIN_ATTEMPT_S", 0.05)
    monkeypatch.setattr(oo, "RETRY_DELAY_S", 0)
    return 0.6


def _timed_scrape(monkeypatch, pw):
    import time as _time
    monkeypatch.setattr(oo, "async_playwright", lambda: pw)
    t0 = _time.monotonic()
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    return r, _time.monotonic() - t0


def test_budget_includes_cancelled_attempt_slow_close_and_slow_stop(tiny_budget, monkeypatch):
    page = _HangingPage()

    class Chromium:
        async def launch(self, **kw):
            return _HangingCloseBrowser(page)
    p = type("P", (), {"chromium": Chromium()})()
    r, elapsed = _timed_scrape(monkeypatch, _HangingStopPlaywright(p))
    assert elapsed < tiny_budget + 0.15, elapsed
    assert r["error"] == "ページ取得失敗: TimeoutError"
    assert "1:例外 TimeoutError" in r["note"] and "2:時間上限で打切り" in r["note"]
    assert "ブラウザ終了 打切り (TimeoutError)" in r["note"]
    assert "Playwright 停止 打切り (TimeoutError)" in r["note"]


def test_budget_includes_playwright_start(tiny_budget, monkeypatch):
    r, elapsed = _timed_scrape(monkeypatch, _HangingStopPlaywright(_fake_p(_FakePage()), hang_start=True))
    assert elapsed < 0.2 + 0.15, elapsed  # START_TIMEOUT_S で打切り、未起動なので停止は待たない
    assert r["error"] == "ページ取得失敗: TimeoutError"


# ---- 入力検査: NaN / Infinity / 片側欠落 ----

def test_nan_and_infinity_rows_are_rejected():
    raw = '{"error": false, "content": {"asks": [[NaN, 3], [4100, Infinity], [4101, 2], [-Infinity, 1]], "bids": [[4000, NaN]]}}'
    d = oo._parse_orderbook_json_detail(raw)
    assert _key(d["asks"]["rows"]) == [(4101.0, 2)] and d["asks"]["invalid"] == 3
    assert d["bids"] == {"rows": [], "status": "all_invalid", "invalid": 1}
    bids, asks = oo._parse_orderbook_json(
        {"content": {"asks": [[float("nan"), 1], ["inf", 2], [4100, "nan"], [4100, 1e400], [4102, 4]],
                     "bids": [[4000, 1]]}})
    assert _key(asks) == [(4102.0, 4)] and _key(bids) == [(4000.0, 1)]
    hc = [{"name": "Asks", "points": [[float("inf"), 3], [4200.0, 7]]}, {"name": "Bids", "points": [[None, 1]]}]
    hd = oo._parse_highcharts_detail(hc)
    assert hd["asks"]["invalid"] == 1 and hd["bids"]["status"] == "all_invalid"


def test_missing_wrong_type_and_empty_series_are_distinguished():
    d = oo._parse_orderbook_json_detail({"content": {"asks": [[4100, 3]]}})
    assert d["bids"]["status"] == "missing"
    d = oo._parse_orderbook_json_detail({"content": {"asks": [[4100, 3]], "bids": "x"}})
    assert d["bids"]["status"] == "wrong_type"
    d = oo._parse_orderbook_json_detail({"content": {"asks": [[4100, 3]], "bids": []}})
    assert d["bids"]["status"] == "empty"
    assert oo._parse_highcharts_detail([{"name": "Asks", "points": [[4200.0, 7]]}])["bids"]["status"] == "missing"

    one_sided = {"order_book_json": json.dumps({"content": {"asks": [[4100, 3]]}})}
    det = oo._extract_detail(one_sided)
    assert det["method"] == "order_book_json" and det["complete"] is False
    assert det["issues"] == ["Bids 系列なし"]
    det = oo._extract_detail({"order_book_json": json.dumps({"content": {"asks": [[4100, 3]], "bids": []}})})
    assert det["complete"] is True and det["issues"] == ["Bids 空配列"]


def test_one_sided_xhr_falls_through_to_complete_highcharts():
    payload = {"order_book_json": json.dumps({"content": {"asks": [[4100, 3]]}}),
               "highcharts": json.loads(OB_HC.read_text())}
    det = oo._extract_detail(payload)
    assert det["method"] == "highcharts" and det["complete"] and len(det["bids"]) == 15


def test_scrape_retries_one_sided_then_succeeds(stub_browser):
    one_sided = json.dumps({"error": False, "content": {"asks": [[4160.0, 3]]}})
    calls = stub_browser([
        _payload(order_book_json=one_sided, xhr_status=200),
        _payload(order_book_json=OB_JSON.read_text(), xhr_status=200, market_depth_head=MD_HEAD.read_text()),
    ])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert len(calls) == 2
    assert r["error"] is None and r["bid_count"] == 15 and r["ask_count"] == 15
    assert "1:order_book_json（片側）" in r["note"] and "片側のみ取得" not in r["note"]


def test_scrape_still_one_sided_after_all_attempts_returns_with_note(stub_browser):
    one_sided = json.dumps({"error": False, "content": {"asks": [[4160.0, 3], [4100.0, float("nan")]]}})
    calls = stub_browser([_payload(order_book_json=one_sided, xhr_status=200,
                                   market_depth_head=MD_HEAD.read_text())] * 3)
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert len(calls) == 3
    assert r["error"] is None
    assert r["bid_count"] == 0 and r["ask_count"] == 1 and r["summary"] == {"bid": 0, "ask": 1}
    assert "片側のみ取得（Asks 不正行 1 件を除外、Bids 系列なし。3 回の試行で両側そろわず）" in r["note"]
    assert r["note"].startswith("取得方法: order_book_json")


def test_scrape_notes_invalid_rows_on_complete_data(stub_browser):
    data = json.loads(OB_JSON.read_text())
    data["content"]["bids"].append([float("nan"), 5])
    stub_browser([_payload(order_book_json=json.dumps(data), xhr_status=200, market_depth_head=MD_HEAD.read_text())])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] is None and r["bid_count"] == 15
    assert "入力の注意: Bids 不正行 1 件を除外" in r["note"]
