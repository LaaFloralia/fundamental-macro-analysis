"""MyFXBook Order Book (Positions) スクレイパー

対象 URL: https://www.myfxbook.com/community/outlook/{symbol}
取得データ: ページの "Order Book (Positions)" チャートの価格別件数（Asks / Bids 各上位 15 件）。

取得経路（2026-10-14 に実ページで確認）:
  1. order_book_json: ページ自身が読み込む XHR
     ``/community/order-book-data.json?symbolName={symbol}`` の応答
     ``{"content": {"asks": [[price, count], ...], "bids": [...], "decimalCount": 2}}`` を
     Playwright の response として受け取る。Asks / Bids は系列名で識別する。
  1b. in_page_fetch: ページ表示後 XHR_WAIT_S 秒たってもページ自身の XHR が見えないときだけ、
     ページ内から同じ URL を 1 回要求する（同一オリジン・同一 Cookie）。ページ自身の XHR が
     403 などで拒否された場合は行わない。2026-10-14 の実測では、ページ内から出した要求は
     ページ自身の要求が届いた後でも届く前でも 8 回すべて 403（Cloudflare "Attention Required"）
     だったため、成功は期待しにくい。note に状態を残して効果を確かめる。
  2. highcharts: 上記を取り逃した場合、``#orderBookChartContainer`` の Highcharts 系列
     （name = "Asks" / "Bids"、point.price と point.y）を page.evaluate で読む。
  3. body_text: 最後の手段として body の inner_text を解析する。テキストでは
     "count\nprice" の組が Asks 系列 → Bids 系列の順に並ぶ（前半 = Asks、後半 = Bids）。
  現在価格は ``/community/market-depth-data.json`` の ``content.actual`` を優先し、
  取れなければ Market Depth セクションの価格ラベル中央値で推定する。

ページ上の表記と、分かっていないこと:
  - 見出しは "Order Book (Positions)"、系列名は "Asks" / "Bids"、Y 軸は
    "Amount of ask orders" / "Amount of bid orders"、ツールチップの単位は "positions"。
    値は件数で、ロット数ではない。各系列は件数の多い順に上位 15 件で、価格順ではない。
  - 同じ URL を同じページから 2 回目に要求すると 403、ページの再読込も 403 になる（2026-10-14 実測）。
  - Asks も Bids も現在価格の上下両側に分布する（2026-10-14 の XAUUSD では現値 4151.73 に対し
    Asks 4103.99〜4190.00、Bids 3458.12〜5175.78）。通常の板（Bid は下、Ask は上）とは並びが違う。
  - これが未約定の指値・逆指値注文なのか、保有ポジションの価格分布なのかをページは説明していない。
    Stop / Limit の区別も示されていない。
  - ページ下部に "Data is based on verified and real accounts only and is delayed by 60 minutes." とある。

本モジュールの BSL / SSL 分類（従来どおり）:
  - Asks と Bids を合算し、現在価格より上を BSL 候補、下を SSL 候補として価格帯クラスタにまとめる。
  - Bid / Ask の別は分類に使っていない。上方の集中を「買いの損切り燃料」とみなすのは本モジュールの
    解釈で、ページが述べている意味ではない。ICT の流動性プールの代理指標として扱う。

取得失敗の主因（2026-10-14 調査）:
  Playwright 既定の headless（chromium-headless-shell）は Cloudflare の対話型確認
  （"Just a moment..." / "Verify you are human"、HTTP 403・cf-mitigated: challenge）で止まり、
  60 秒待っても通過しなかった。同じ時間帯に新 headless（channel="chromium"）+ 実在の Chrome UA では
  確認画面が出ずに 200 で表示された。そのため新 headless を優先し、確認画面は自動操作で突破しない。
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.async_api import async_playwright
from config import BROWSER_TIMEOUT, USER_AGENT

OUTLOOK_URL = "https://www.myfxbook.com/community/outlook/{symbol}"
ORDER_BOOK_XHR = "/community/order-book-data.json"
MARKET_DEPTH_XHR = "/community/market-depth-data.json"
ORDER_BOOK_CONTAINER_ID = "orderBookChartContainer"

# 1 試行内のデータ待ちの上限（秒）。order-book-data.json は通常ロード開始から 4 秒前後で届く。
DATA_WAIT_S = 20.0
# ページ表示後この秒数たってもページ自身の XHR が見えなければ、ページ内から同じ URL を 1 回だけ要求する。
XHR_WAIT_S = 8.0
# ページ内要求のタイムアウト（秒）。
IN_PAGE_FETCH_TIMEOUT_S = 8.0
# 現在価格用の market-depth-data.json を order book の後に待つ上限（秒）。
MARKET_DEPTH_WAIT_S = 3.0
# Cloudflare の非対話チャレンジが自動で抜けるのを待つ上限（秒）。対話型は待っても抜けない。
CHALLENGE_WAIT_S = 8.0
# 試行は最大 3 回、間隔 5 秒。全体（全試行と待ち）を TOTAL_BUDGET_S 秒以内に収める。
RETRY_DELAY_S = 5.0
MAX_ATTEMPTS = 3
TOTAL_BUDGET_S = 88.0
# 残り時間がこれ未満なら次の試行を始めない（起動 + 表示 + XHR 待ちに必要な最小時間）。
MIN_ATTEMPT_S = 15.0



def _now() -> float:
    """締切計算用の単調時計（テストで差し替える）。"""
    return time.monotonic()


CHALLENGE_TITLES = ("Just a moment", "Attention Required", "Performing security verification")

ERR_NO_PAIRS = "Order Book ペアを抽出できず (DOM 変更の可能性)"
ERR_CHALLENGE = "Cloudflare のボット確認で遮断され Order Book を取得できず"

_JS_ORDER_BOOK_SERIES = """(containerId) => {
  if (typeof Highcharts === 'undefined' || !Highcharts.charts) return null;
  const charts = Highcharts.charts.filter(Boolean);
  let chart = charts.find(c => c.renderTo && c.renderTo.id === containerId);
  if (!chart) {
    chart = charts.find(c => {
      const names = c.series.map(s => s.name);
      return names.includes('Asks') && names.includes('Bids') && c.series.every(s => s.type === 'bar');
    });
  }
  if (!chart) return null;
  return chart.series.map(s => ({
    name: s.name,
    points: s.points.map(p => [p.price !== undefined ? p.price : (p.options && p.options.price), p.y]),
  }));
}"""

# ページ自身の XHR と同じ URL 形（symbolName と乱数 z）・同じ X-Requested-With で、
# 同一オリジン・同一 Cookie のまま 1 回だけ要求する。応答は先頭 maxChars 文字だけ返す。
_JS_IN_PAGE_FETCH = """async ([path, symbol, maxChars, timeoutMs]) => {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const url = path + '?symbolName=' + encodeURIComponent(symbol) + '&z=' + Math.random();
    const r = await fetch(url, {credentials: 'same-origin', signal: ctl.signal,
                                headers: {'X-Requested-With': 'XMLHttpRequest'}});
    const text = await r.text();
    return {status: r.status, text: maxChars > 0 ? text.slice(0, maxChars) : text};
  } catch (e) {
    return {status: null, error: String(e && e.name || e)};
  } finally {
    clearTimeout(timer);
  }
}"""


def _pairs_to_dicts(rows: Any) -> List[dict]:
    """[[price, count], ...] を {"volume": int, "price": float} のリストにする。不正行は捨てる。"""
    out: List[dict] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price, count = row[0], row[1]
        if isinstance(price, bool) or isinstance(count, bool):
            continue
        try:
            price_f = float(price)
            count_f = float(count)
        except (TypeError, ValueError):
            continue
        if price_f <= 0 or count_f < 0:
            continue
        out.append({"volume": int(round(count_f)), "price": price_f})
    return out


def _parse_orderbook_json(payload: Any) -> tuple[List[dict], List[dict]]:
    """order-book-data.json の応答（文字列または dict）から (bids, asks) を返す。

    想定形: {"error": false, "content": {"asks": [[price, count], ...], "bids": [...]}}
    価格は 4.19E+3 のような指数表記や整数でも来る。
    """
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return [], []
    if not isinstance(payload, dict) or payload.get("error"):
        return [], []
    content = payload.get("content")
    if not isinstance(content, dict):
        return [], []
    return _pairs_to_dicts(content.get("bids")), _pairs_to_dicts(content.get("asks"))


def _parse_highcharts_series(series: Any) -> tuple[List[dict], List[dict]]:
    """Highcharts 系列 [{"name": "Asks"|"Bids", "points": [[price, y], ...]}] から (bids, asks)。

    系列名で Bid / Ask を決める。名前が想定外の系列は無視する。
    """
    bids: List[dict] = []
    asks: List[dict] = []
    if not isinstance(series, list):
        return bids, asks
    for s in series:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip().lower()
        rows = _pairs_to_dicts(s.get("points"))
        if name in ("bids", "bid"):
            bids.extend(rows)
        elif name in ("asks", "ask"):
            asks.extend(rows)
    return bids, asks


def _parse_market_depth_actual(text_head: Optional[str]) -> Optional[float]:
    """market-depth-data.json 応答の先頭から content.actual（現在価格）を取り出す。

    応答全体は 5MB 前後あるため、先頭部分の文字列だけを正規表現で見る。
    """
    if not text_head:
        return None
    m = re.search(r'"actual"\s*:\s*(-?[\d.]+(?:[eE][+-]?\d+)?)', text_head)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if v > 0 else None


def _is_challenge(title: Optional[str] = None, body_text: Optional[str] = None,
                  cf_mitigated: Optional[str] = None) -> bool:
    """Cloudflare のボット確認ページかどうか。"""
    if cf_mitigated and "challenge" in cf_mitigated.lower():
        return True
    if title and any(t.lower() in title.lower() for t in CHALLENGE_TITLES):
        return True
    if body_text and "Performing security verification" in body_text and "Cloudflare" in body_text:
        return True
    return False


def _user_agent_for(browser_version: Optional[str]) -> str:
    """config.USER_AGENT の Chrome バージョンを実ブラウザのメジャー版に合わせる。

    新 headless の既定 UA は "HeadlessChrome/..." を含み Cloudflare に遮断されるため UA は差し替える。
    版数だけ実エンジンと揃え、UA と実際の機能の食い違いを減らす。
    """
    major = None
    if browser_version:
        m = re.match(r"(\d+)\.", browser_version)
        if m:
            major = m.group(1)
    if not major:
        return USER_AGENT
    return re.sub(r"Chrome/[\d.]+", f"Chrome/{major}.0.0.0", USER_AGENT)


def _parse_orderbook_pairs(body_text: str) -> tuple[List[dict], List[dict]]:
    """body_text から Order Book の (count, price) 組を抽出する（フォールバック用）。

    inner_text では Asks 系列の組が先、Bids 系列の組が後に並ぶ（2026-10-14 に
    SVG の aria-label "Price: 4151.490, Asks: 83" と XHR の asks/bids で照合）。

    Returns: (bids, asks) — それぞれ {"volume": int, "price": float} のリスト。
    """
    # "Order Book (Positions)" から次のセクションマーカーまでを切り出す
    start_marker = "Order Book (Positions)"
    end_markers = ["End of interactive chart.", "Forex Sentiment", "Top Forex Brokers"]

    s_idx = body_text.find(start_marker)
    if s_idx < 0:
        return [], []

    e_idx = len(body_text)
    for em in end_markers:
        em_idx = body_text.find(em, s_idx + len(start_marker))
        if em_idx > 0 and em_idx < e_idx:
            e_idx = em_idx
            break

    section = body_text[s_idx:e_idx]

    # count(整数) + price(小数、カンマ区切り可) の組を抽出
    lines = [ln.strip() for ln in section.splitlines() if ln.strip()]

    pairs: List[tuple[int, float]] = []
    i = 0
    while i < len(lines) - 1:
        v_match = re.fullmatch(r"\d{1,4}", lines[i])
        p_match = re.fullmatch(r"[\d,]+\.\d{1,5}", lines[i + 1])
        if v_match and p_match:
            try:
                volume = int(v_match.group())
                price = float(p_match.group().replace(",", ""))
                pairs.append((volume, price))
                i += 2
                continue
            except ValueError:
                pass
        i += 1

    if not pairs:
        return [], []

    # 系列の境目はテキストに出ないため、2 系列が同数（通常 15 + 15）である前提で半分に割る。
    # 前半 = Asks、後半 = Bids。奇数件のときは Asks 側を 1 件少なくする。
    half = len(pairs) // 2
    asks_raw = pairs[:half]
    bids_raw = pairs[half:]

    bids = [{"volume": v, "price": p} for v, p in bids_raw]
    asks = [{"volume": v, "price": p} for v, p in asks_raw]
    return bids, asks

def _extract_current_price(body_text: str, symbol: str) -> Optional[float]:
    """body_text から現在価格を抽出する。Mid-Market price セクションから推定。

    XAUUSD/USDJPY 等の outlook ページの Market Depth セクションには
    "Mid-Market price" の表記があり、その近辺の価格列の中央値を採用する。
    """
    # Market Depth セクション
    s = body_text.find("Market Depth")
    if s < 0:
        return None
    e = body_text.find("End of interactive chart.", s)
    if e < 0:
        e = len(body_text)
    section = body_text[s:e]

    # 数値パターン (4 桁以上.数桁、または 2-3 桁.数桁) を全部抜き、中央値を取る
    nums = re.findall(r"[\d,]+\.\d{2,5}", section)
    vals = []
    for n in nums:
        try:
            v = float(n.replace(",", ""))
            vals.append(v)
        except ValueError:
            continue
    if not vals:
        return None

    vals.sort()
    return vals[len(vals) // 2]


def _cluster_and_classify(
    bids: List[dict],
    asks: List[dict],
    current_price: Optional[float],
    cluster_pct: float = 0.005,
    max_distance_pct: float = 0.10,
) -> dict:
    """価格帯クラスタリング + BSL/SSL 分類。

    cluster_pct: 同じクラスタとみなす価格幅の割合（デフォルト 0.5%）。
    max_distance_pct: 現在価格からの許容乖離（デフォルト ±10%）。
        チャート軸ラベル等の混入による異常値（例: 現値 4411 に対し 5175 のクラスタ）を
        排除する妥当性フィルタ。実オーダーでもこの距離のものは当面の流動性として無意味。
    """
    all_pairs = [{"side": "bid", **b} for b in bids] + [{"side": "ask", **a} for a in asks]
    if not all_pairs or current_price is None:
        return {"bsl_candidates": [], "ssl_candidates": []}

    # 妥当性フィルタ: 現在価格 ±max_distance_pct の範囲外は除外
    all_pairs = [
        p for p in all_pairs
        if abs(p["price"] / current_price - 1.0) <= max_distance_pct
    ]
    if not all_pairs:
        return {"bsl_candidates": [], "ssl_candidates": []}

    above = [p for p in all_pairs if p["price"] > current_price]
    below = [p for p in all_pairs if p["price"] < current_price]

    def cluster(items: List[dict]) -> List[dict]:
        if not items:
            return []
        items_sorted = sorted(items, key=lambda x: x["price"])
        clusters: List[dict] = []
        for it in items_sorted:
            if clusters and abs(it["price"] - clusters[-1]["high"]) / max(it["price"], 1.0) < cluster_pct:
                # 既存クラスタに追加
                clusters[-1]["high"] = max(clusters[-1]["high"], it["price"])
                clusters[-1]["low"] = min(clusters[-1]["low"], it["price"])
                clusters[-1]["volume_sum"] += it["volume"]
                clusters[-1]["entries"] += 1
            else:
                clusters.append({
                    "low": it["price"],
                    "high": it["price"],
                    "volume_sum": it["volume"],
                    "entries": 1,
                })
        return clusters

    bsl_clusters = cluster(above)
    ssl_clusters = cluster(below)

    # share_pct: 同サイド（現在価格の上/下）の総ボリュームに占める割合
    def annotate_share(clusters: List[dict], side_pairs: List[dict]) -> None:
        side_total = sum(p["volume"] for p in side_pairs)
        for c in clusters:
            c["share_pct"] = round(c["volume_sum"] / side_total * 100, 1) if side_total > 0 else None

    annotate_share(bsl_clusters, above)
    annotate_share(ssl_clusters, below)

    # 集中度上位 3 件
    bsl_top = sorted(bsl_clusters, key=lambda c: c["volume_sum"], reverse=True)[:3]
    ssl_top = sorted(ssl_clusters, key=lambda c: c["volume_sum"], reverse=True)[:3]

    return {
        "bsl_candidates": bsl_top,
        "ssl_candidates": ssl_top,
    }


async def _in_page_fetch(page, path: str, symbol: str, max_chars: int) -> dict:
    """ページ内（同一オリジン・同一 Cookie）から path を 1 回要求する。例外は status None で返す。"""
    try:
        res = await page.evaluate(
            _JS_IN_PAGE_FETCH, [path, symbol, max_chars, int(IN_PAGE_FETCH_TIMEOUT_S * 1000)])
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "error": type(exc).__name__}
    return res if isinstance(res, dict) else {"status": None, "error": "invalid"}


async def _load_outlook_once(p, symbol: str, use_new_headless: bool,
                             deadline: Optional[float] = None) -> dict:
    """outlook ページを 1 回開き、Order Book の材料を集める。

    deadline: _now()（time.monotonic）基準の締切。待ちはこれを超えない。

    Returns: {"order_book_json", "order_book_fetch", "market_depth_head", "market_depth_source",
              "highcharts", "body_text", "challenge", "xhr_status", "fetch_status",
              "launch", "elapsed_s"}
      xhr_status: ページ自身の order-book XHR の HTTP 状態（見えなければ None）
      fetch_status: ページ内要求の HTTP 状態（行わなければ None、失敗は "error"）
    """
    out: dict = {
        "order_book_json": None,
        "order_book_fetch": None,
        "market_depth_head": None,
        "market_depth_source": None,
        "highcharts": None,
        "body_text": "",
        "challenge": False,
        "xhr_status": None,
        "fetch_status": None,
        "launch": None,
        "elapsed_s": None,
    }
    t0 = _now()
    if deadline is None:
        deadline = t0 + TOTAL_BUDGET_S
    own_fetch = {"active": False}
    browser = None
    try:
        if use_new_headless:
            try:
                browser = await p.chromium.launch(headless=True, channel="chromium")
                out["launch"] = "chromium-new-headless"
            except Exception:  # noqa: BLE001 — フル Chromium 未導入時は旧 headless へ
                browser = None
        if browser is None:
            browser = await p.chromium.launch(headless=True)
            out["launch"] = "chromium-headless-shell"

        context = await browser.new_context(
            user_agent=_user_agent_for(browser.version),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        async def _on_response(resp) -> None:
            try:
                if own_fetch["active"] and resp.request.resource_type == "fetch":
                    return  # 自前のページ内要求は戻り値で受け取る
                url = resp.url
                if ORDER_BOOK_XHR in url and out["xhr_status"] is None:
                    out["xhr_status"] = resp.status
                    if resp.status == 200:
                        out["order_book_json"] = await resp.text()
                elif MARKET_DEPTH_XHR in url and out["market_depth_head"] is None and resp.status == 200:
                    body = await resp.body()
                    out["market_depth_head"] = body[:2000].decode("utf-8", "replace")
                    out["market_depth_source"] = "xhr"
            except Exception:  # noqa: BLE001 — 応答本文が取れなくても他経路で続行
                pass

        page.on("response", lambda r: asyncio.ensure_future(_on_response(r)))

        goto_ms = max(1000, int(min(BROWSER_TIMEOUT / 1000, deadline - _now() - 3) * 1000))
        resp = await page.goto(OUTLOOK_URL.format(symbol=symbol), timeout=goto_ms,
                               wait_until="domcontentloaded")
        cf_mitigated = resp.headers.get("cf-mitigated") if resp else None

        data_deadline = min(_now() + DATA_WAIT_S, deadline - 3)
        challenge_deadline = _now() + CHALLENGE_WAIT_S
        shown_at: Optional[float] = None
        scrolled = False
        while _now() < data_deadline:
            if out["order_book_json"] is not None or out["order_book_fetch"] is not None:
                break
            if out["xhr_status"] is not None and out["xhr_status"] != 200:
                break  # ページ自身の要求が拒否された。待っても届かない
            try:
                title = await page.title()
            except Exception:  # noqa: BLE001 — 遷移中
                title = ""
            if _is_challenge(title=title, cf_mitigated=cf_mitigated):
                if _now() > challenge_deadline:
                    out["challenge"] = True
                    break
                # 非対話チャレンジは通過すると自動で本ページへ遷移する
                await page.wait_for_timeout(500)
                continue
            cf_mitigated = None  # 本ページへ遷移済み。初回応答のヘッダは以後見ない
            if shown_at is None:
                shown_at = _now()
            if not scrolled:
                try:
                    await page.locator(f"#{ORDER_BOOK_CONTAINER_ID}").scroll_into_view_if_needed(timeout=2000)
                    scrolled = True
                except Exception:  # noqa: BLE001 — 要素がまだ無い
                    pass
            if (out["xhr_status"] is None and out["fetch_status"] is None
                    and _now() - shown_at >= XHR_WAIT_S):
                own_fetch["active"] = True
                res = await _in_page_fetch(page, ORDER_BOOK_XHR, symbol, 0)
                out["fetch_status"] = res.get("status") if res.get("status") is not None else "error"
                if res.get("status") == 200 and res.get("text"):
                    out["order_book_fetch"] = res["text"]
                    if out["market_depth_head"] is None:
                        md = await _in_page_fetch(page, MARKET_DEPTH_XHR, symbol, 2000)
                        if md.get("status") == 200 and md.get("text"):
                            out["market_depth_head"] = md["text"]
                            out["market_depth_source"] = "in_page_fetch"
                break  # 拒否された場合も同じ要求は繰り返さず、次の試行に回す
            await page.wait_for_timeout(500)

        if not out["challenge"]:
            if out["order_book_json"] is not None:
                # 現在価格用の market-depth-data.json を少しだけ待つ（通常は order book と同時に届く）
                md_deadline = min(_now() + MARKET_DEPTH_WAIT_S, deadline - 2)
                while out["market_depth_head"] is None and _now() < md_deadline:
                    await page.wait_for_timeout(250)
            elif out["order_book_fetch"] is None:
                try:
                    out["highcharts"] = await page.evaluate(_JS_ORDER_BOOK_SERIES, ORDER_BOOK_CONTAINER_ID)
                except Exception:  # noqa: BLE001
                    out["highcharts"] = None
        try:
            out["body_text"] = await page.inner_text("body", timeout=3000)
        except Exception:  # noqa: BLE001
            out["body_text"] = ""
        if not out["challenge"] and _is_challenge(body_text=out["body_text"]):
            out["challenge"] = True
    finally:
        if browser is not None:
            try:
                await asyncio.wait_for(browser.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        out["elapsed_s"] = round(_now() - t0, 1)
    return out


def _extract_from_payload(payload: dict) -> tuple[List[dict], List[dict], Optional[str]]:
    """収集物から (bids, asks, method) を決める。

    優先順: ページ自身の XHR → ページ内要求 → Highcharts → body テキスト。
    """
    bids, asks = _parse_orderbook_json(payload.get("order_book_json"))
    if bids or asks:
        return bids, asks, "order_book_json"
    bids, asks = _parse_orderbook_json(payload.get("order_book_fetch"))
    if bids or asks:
        return bids, asks, "in_page_fetch"
    bids, asks = _parse_highcharts_series(payload.get("highcharts"))
    if bids or asks:
        return bids, asks, "highcharts"
    bids, asks = _parse_orderbook_pairs(payload.get("body_text") or "")
    if bids or asks:
        return bids, asks, "body_text"
    return [], [], None


def _attempt_state(payload: dict, method: Optional[str]) -> str:
    """note に残す 1 試行分の要約。例: "データなし (chromium-new-headless, 12.0s, XHR 403)"。"""
    state = "challenge" if payload.get("challenge") else (method or "データなし")
    parts = [str(payload.get("launch")), f"{payload.get('elapsed_s')}s"]
    if not payload.get("challenge") and method != "order_book_json":
        xs = payload.get("xhr_status")
        parts.append(f"XHR {xs if xs is not None else '未着'}")
        if payload.get("fetch_status") is not None:
            parts.append(f"ページ内要求 {payload['fetch_status']}")
    return f"{state} ({', '.join(parts)})"


async def scrape_myfxbook_open_orders(symbol: str, current_price_hint: Optional[float] = None) -> dict:
    """MyFXBook の Order Book (Positions) から価格別件数の集中帯を取得する。

    Args:
        symbol: 銘柄名 (例: "XAUUSD", "USDJPY")
        current_price_hint: TwelveData 等で取得済みの現在価格。
            ページ由来の現在価格と 2% 以上乖離する場合は hint を優先する
            （ページ側パースの異常検知用クロスチェック）。

    Returns:
        {
            "source": "MyFXBook (Order Book Positions)",
            "symbol": str,
            "current_price": float | None,
            "bid_count": int,
            "ask_count": int,
            "bsl_candidates": [{"low": float, "high": float, "volume_sum": int,
                                "entries": int, "share_pct": float | None}, ...],
            "ssl_candidates": 同上,
            "bsl_concentration": dict | None,   # top-1 互換用
            "ssl_concentration": dict | None,   # top-1 互換用
            "summary": {"bid": int, "ask": int},  # 互換用
            "buckets": [],  # 旧スキーマ互換 (空 list、後方互換のため残置)
            "note": str | None,   # 取得方法・試行回数・価格の出典など
            "error": str | None,
        }
        volume_sum は件数（positions）の合計で、ロット数ではない。
    """
    result = {
        "source": "MyFXBook (Order Book Positions)",
        "symbol": symbol,
        "current_price": None,
        "bid_count": 0,
        "ask_count": 0,
        "bsl_candidates": [],
        "ssl_candidates": [],
        "bsl_concentration": None,
        "ssl_concentration": None,
        "summary": {},
        "buckets": [],
        "note": None,
        "error": None,
    }

    payload: Optional[dict] = None
    bids: List[dict] = []
    asks: List[dict] = []
    method: Optional[str] = None
    attempts: List[str] = []
    last_exc: Optional[Exception] = None

    deadline = _now() + TOTAL_BUDGET_S
    try:
        async with async_playwright() as p:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if attempt > 1:
                    if deadline - _now() < RETRY_DELAY_S + MIN_ATTEMPT_S:
                        attempts.append(f"{attempt}:時間上限で打切り")
                        break
                    await asyncio.sleep(RETRY_DELAY_S)
                remaining = deadline - _now()
                try:
                    payload_i = await asyncio.wait_for(
                        _load_outlook_once(p, symbol, use_new_headless=True, deadline=deadline),
                        timeout=max(remaining, 1.0))
                except Exception as exc:  # noqa: BLE001 — 次の試行へ
                    last_exc = exc
                    attempts.append(f"{attempt}:例外 {type(exc).__name__}")
                    continue
                payload = payload_i
                bids, asks, method = _extract_from_payload(payload)
                attempts.append(f"{attempt}:{_attempt_state(payload, method)}")
                if method:
                    break
    except Exception as exc:  # noqa: BLE001
        last_exc = exc

    notes: List[str] = []
    if attempts:
        notes.append("試行 " + " / ".join(attempts))

    if payload is None:
        result["error"] = f"ページ取得失敗: {last_exc}"
        result["note"] = "; ".join(notes) or None
        return result

    result["bid_count"] = len(bids)
    result["ask_count"] = len(asks)
    result["summary"] = {"bid": len(bids), "ask": len(asks)}

    if not bids and not asks:
        result["error"] = ERR_CHALLENGE if payload.get("challenge") else ERR_NO_PAIRS
        result["note"] = "; ".join(notes) or None
        return result

    notes.insert(0, f"取得方法: {method}")

    current_price = _parse_market_depth_actual(payload.get("market_depth_head"))
    if current_price is not None:
        via = "ページ内要求" if payload.get("market_depth_source") == "in_page_fetch" else "XHR"
        notes.append(f"現在価格: market-depth-data.json の actual（{via}）")
    else:
        current_price = _extract_current_price(payload.get("body_text") or "", symbol)
        if current_price is not None:
            notes.append("現在価格: Market Depth 表示の価格中央値")

    # クロスチェック: ページ由来の値と hint が 2% 以上乖離したら hint を採用
    if current_price_hint is not None:
        if current_price is None:
            current_price = current_price_hint
            notes.append("現在価格はページから抽出できず、外部ヒント値を使用")
        elif abs(current_price / current_price_hint - 1.0) > 0.02:
            notes.append(
                f"ページ推定価格 {current_price} が外部ヒント {current_price_hint} と "
                f"2% 以上乖離 → ヒント値を採用"
            )
            current_price = current_price_hint

    result["current_price"] = current_price
    result["note"] = "; ".join(notes)

    if current_price is None:
        result["error"] = "現在価格を抽出できず、BSL/SSL 分類スキップ"
        return result

    cls = _cluster_and_classify(bids, asks, current_price)
    result["bsl_candidates"] = cls["bsl_candidates"]
    result["ssl_candidates"] = cls["ssl_candidates"]

    # 旧スキーマ互換 (top-1 を bsl_concentration / ssl_concentration に格納)。
    # type は従来の便宜上のラベルで、クラスタは Asks と Bids を合算した価格帯（Ask / Bid 由来ではない）。
    if cls["bsl_candidates"]:
        b = cls["bsl_candidates"][0]
        result["bsl_concentration"] = {
            "type": "Ask cluster (BSL candidate)",
            "low": b["low"],
            "high": b["high"],
            "share_pct": b.get("share_pct"),
            "volume_sum": b["volume_sum"],
        }
    if cls["ssl_candidates"]:
        s = cls["ssl_candidates"][0]
        result["ssl_concentration"] = {
            "type": "Bid cluster (SSL candidate)",
            "low": s["low"],
            "high": s["high"],
            "share_pct": s.get("share_pct"),
            "volume_sum": s["volume_sum"],
        }

    return result


# ---- 旧スキーマ互換ヘルパー (テスト用) ----
def _summarize_buckets(buckets: List[dict]) -> dict:
    """旧スキーマで {type, low, high, share_pct} のリストを受け取り、BSL/SSL 集中帯を返す。

    後方互換のため残置 (test_open_orders_summarizer_picks_max_concentration が使用)。
    新パーサーは _parse_orderbook_pairs + _cluster_and_classify を使う。
    """
    if not buckets:
        return {
            "summary": {},
            "bsl_concentration": None,
            "ssl_concentration": None,
        }

    summary: dict = {}
    for b in buckets:
        cat = b["type"]
        summary.setdefault(cat, []).append(b)

    bsl_max = None
    ssl_max = None
    for b in buckets:
        if "Buy" in b["type"] and "Stop" in b["type"]:
            if bsl_max is None or b["share_pct"] > bsl_max["share_pct"]:
                bsl_max = b
        if "Sell" in b["type"] and "Stop" in b["type"]:
            if ssl_max is None or b["share_pct"] > ssl_max["share_pct"]:
                ssl_max = b

    return {
        "summary": {k: len(v) for k, v in summary.items()},
        "bsl_concentration": bsl_max,
        "ssl_concentration": ssl_max,
    }


if __name__ == "__main__":
    async def _test():
        for sym in ["XAUUSD", "USDJPY"]:
            d = await scrape_myfxbook_open_orders(sym)
            print(f"\n--- {sym} ---")
            print(f"  source: {d['source']} | error: {d['error']}")
            print(f"  current_price: {d['current_price']}")
            print(f"  bids: {d['bid_count']} / asks: {d['ask_count']}")
            print(f"  BSL candidates ({len(d['bsl_candidates'])}):")
            for b in d['bsl_candidates']:
                print(f"    {b}")
            print(f"  SSL candidates ({len(d['ssl_candidates'])}):")
            for s in d['ssl_candidates']:
                print(f"    {s}")
    asyncio.run(_test())
