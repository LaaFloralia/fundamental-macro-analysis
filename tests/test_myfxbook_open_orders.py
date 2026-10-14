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
    base = {"order_book_json": None, "market_depth_head": None, "highcharts": None,
            "body_text": "", "challenge": False, "launch": "chromium-new-headless", "elapsed_s": 1.0}
    base.update(kw)
    return base


@pytest.fixture
def stub_browser(monkeypatch):
    calls = []

    def install(payloads):
        async def fake_once(p, symbol, use_new_headless):
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


def test_scrape_reports_challenge_when_both_attempts_blocked(stub_browser):
    calls = stub_browser([_payload(challenge=True), _payload(challenge=True)])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert len(calls) == 2
    assert r["error"] == oo.ERR_CHALLENGE
    assert r["bid_count"] == 0 and r["bsl_candidates"] == []


def test_scrape_keeps_dom_change_error_when_page_loaded_without_data(stub_browser):
    stub_browser([_payload(body_text="Forex Sentiment"), _payload(body_text="Forex Sentiment")])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == oo.ERR_NO_PAIRS == "Order Book ペアを抽出できず (DOM 変更の可能性)"


def test_scrape_text_fallback_and_hint_override(stub_browser):
    stub_browser([_payload(body_text=BODY.read_text())])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD", current_price_hint=4400.0))
    assert r["note"].startswith("取得方法: body_text")
    assert "ヒント値を採用" in r["note"]
    assert r["current_price"] == 4400.0


def test_scrape_exception_on_every_attempt_returns_fetch_error(stub_browser):
    stub_browser([RuntimeError("boom"), RuntimeError("boom")])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == "ページ取得失敗: boom"
    assert "例外 RuntimeError" in r["note"]


def test_scrape_keeps_challenge_error_when_retry_raises(stub_browser):
    stub_browser([_payload(challenge=True), RuntimeError("closed")])
    r = asyncio.run(oo.scrape_myfxbook_open_orders("XAUUSD"))
    assert r["error"] == oo.ERR_CHALLENGE
    assert "1:challenge" in r["note"] and "2:例外 RuntimeError" in r["note"]
