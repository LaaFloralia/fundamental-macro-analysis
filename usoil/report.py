"""Markdown, summary, figures, bindings and machine.json for one USOIL edition (design 8; P9).

Everything here is code-rendered from the frozen facts, the code's scoring
and the parent's validated analysis. Parent text arrives already checked
(no markup characters, numbers only through fact tokens) and expanded.
External strings (headlines, excerpts, provider labels, parsed calendar
names) were neutralised when they entered the facts (usoil.text.md_safe).

The page (summary, figures, bindings) is built by usoil.dashboard, which writes
section 11 「画面に表示した値」: every shown string quotes one of its lines and
each numeric item is bound to one fact (value == display_value, display ==
display). Figures without data are omitted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from usoil import SYMBOL, dashboard, scoring
from usoil.analysis import RULES
from usoil.common import JST, parse_time, read_json
from usoil.facts import fact_text
from usoil.stages import ReportParts
from usoil.text import md_safe

UTC = timezone.utc
SCHEMA_VERSION = 'usoil-offchart-1.0'
FEATURE_VERSION = 'usoil-facts-1.0'
LIQUIDITY_NOTE = '板・清算ヒートマップ・オプション建玉は未取得。Yahoo連続限月の過去高安と参考ラウンドを表示'
YAML_KEYS = ('schema_version', 'asset', 'mode', 'session_slot', 'edition', 'as_of', 'collected_at', 'generated_at',
             'valid_until', 'bias', 'direction_status', 'candidate_direction', 'direction_strength', 'confidence',
             'confidence_band', 'score_total', 'no_trade', 'no_trade_reason', 'trade_gate', 'coverage_present',
             'coverage_required')
HEADINGS = ('## 0. 判断', '## 1. データ範囲と欠測', '## 2. バイアスと固定8項目スコア', '## 3. COT・OVX・ポジショニング',
            '## 4. 流動性の観測と仮説', '## 5. EIA在庫・需給・限月構造', '## 6. マクロ・イベント',
            '## 7. ニュースと価格反応', '## 8. シナリオ・反証・再評価条件',
            '## 9. 前回との差分（Weeklyは週次振り返りを追加）', '## 10. ファクト一覧と来歴', '## 11. 画面に表示した値')
USABLE = ('ok', 'provisional', 'partial', 'stale')
KIND_LABEL = {'fact_interpretation': '解釈', 'hypothesis': '仮説', 'limitation': '制約'}
STATUS_LABEL = {'ok': '確認済み', 'provisional': '速報', 'partial': '一部', 'stale': '古い', 'missing': '欠測',
                'invalid': '不正', 'conflict': '矛盾', 'warming_up': '履歴不足', 'not_applicable': '対象外',
                'terms_restricted': '規約により未使用'}
CODE_VERIFICATION = {'primary_body_retrieved': '公式の本文取得済み', 'secondary_body_retrieved': '本文取得済み',
                     'headline_only': '見出しのみ'}
VERIFICATION = {'primary_confirmed': '一次本文で確認', 'secondary_body_confirmed': '報道本文で確認',
                'headline_only': '見出しのみ', 'disputed': '異論あり', 'retracted': '撤回'}
IMPACT = {'supportive': '上向き材料', 'adverse': '下向き材料', 'mixed': '混在', 'unknown': '不明'}
GROUP = {'macro': 'マクロ', 'inventory_supply': '在庫・供給', 'oil_specific_event': '原油固有イベント', 'context_only': '文脈のみ'}
GATE_LABEL = {'clear': '停止条件なし', 'scheduled_pause': 'イベント停止中', 'data_hold': 'データ保留',
              'incident_hold': '障害停止', 'expired': '期限切れ'}
CODE_LIMITATIONS = (
    'スコアと確度は未校正の序数で、的中率や期待収益ではない',
    '参照価格はCNBC @CL.1の単一ソース。取得不能時だけYahooへ縮退し確度を制限する',
    'OHLCはYahoo CL=F連続限月でロールが混在する。CNBC参照価格との限月整合、目的地との距離や上下比較は未確認',
    'FRED原油現物は参照価格・採点に使わない。VIX・S&P 500・HY OASは表示のみ',
    'COTは火曜基準の週次公表、OVXの日次変化はFRED履歴から計算する。価格の方向そのものではない',
    'リテールは未実装。Baker Hughes、OPEC直源、板、オプション建玉と公表前後1分足反応は未取得・不採用',
    'CMEの正式休場・契約別時刻は未確認。週末注記は停止ゲートを変更しない',
)


# ----------------------------------------------------------------- helpers

def zs(value) -> str:
    return parse_time(value).astimezone(UTC).isoformat().replace('+00:00', 'Z')


def jst(value) -> str:
    return parse_time(value).astimezone(JST).strftime('%Y-%m-%d %H:%M JST')


def hm(value) -> str:
    return parse_time(value).astimezone(JST).strftime('%H:%M')


def md_day(value) -> str:
    return parse_time(value).astimezone(JST).strftime('%m/%d')


def yaml_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)


class Doc:
    def __init__(self):
        self.lines: list[str] = []

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def item(self, text: str) -> str:
        """A bullet; returns the exact text so summaries and figures can quote it."""
        self.lines.append('- ' + text)
        return text

    def table(self, header: list[str], rows: list[list]) -> None:
        self.lines.append('| ' + ' | '.join(header) + ' |')
        self.lines.append('|' + '---|' * len(header))
        for row in rows:
            self.lines.append('| ' + ' | '.join('—' if c is None or c == '' else str(c) for c in row) + ' |')
        self.lines.append('')

    def text(self) -> str:
        return '\n'.join(self.lines).rstrip('\n') + '\n'


def _claim_text(analysis: dict, where: str, claim: dict) -> str:
    return f'{analysis["expanded"][where]}（{KIND_LABEL[claim["claim_kind"]]}）'


def _pick(facts: list, *, category: str, provider: str, metric: str, instrument: str | None = None):
    for fact in facts:
        parts = fact['logical_id'].split('.')
        if (fact['category'] == category and parts[2] == provider and fact['metric'] == metric
                and (instrument is None or parts[3] == instrument)):
            return fact
    return None


def direction_label(ev: dict) -> str:
    if ev['direction_status'] == 'withheld':
        return f'USOILは方向付与保留（{ev["direction_reason_label"]}）'
    side = '上向き' if ev['candidate_direction'] == 'up' else '下向き'
    return f'USOILは{side}（確度 {ev["direction_strength"]}）'


def report_kind(mode: str, slot: str) -> str:
    return 'weekly' if mode == 'weekly' else f'daily_{slot}'


# ----------------------------------------------------------------- freshness at finalize

def finalize_freshness(facts: dict, analysis: dict, finalize_at: datetime, hard_invalid: list) -> dict:
    expired = [f['fact_id'] for f in facts['facts']
               if f['status'] in USABLE and f['valid_until'] and parse_time(f['valid_until']) < finalize_at]
    referenced = set()
    for value in _walk_fact_ids(analysis):
        referenced.add(value)
    return {'as_of_check': 'data_hold' if hard_invalid else 'passed', 'finalize_checked_at': zs(finalize_at),
            'expired_at_finalize': expired, 'referenced_expired': sorted(referenced & set(expired)),
            'note': '収集時点で鮮度を検査し、完成時刻に有効期限を過ぎた値を列挙する。期限切れの値は採点を変えず、'
                    '目的地の根拠なら目的地を失効にする。'}


def _walk_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_values(value)
    else:
        yield node


def _walk_fact_ids(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ('fact_ids', 'level_fact_ids') and isinstance(value, list):
                yield from value
            else:
                yield from _walk_fact_ids(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_fact_ids(value)


def destination(analysis: dict, index: dict, finalize_at: datetime) -> dict:
    choice = analysis['destination_choice']
    out = {'choice': choice, 'status': 'none', 'valid_until': None}
    if choice['kind'] == 'none':
        return out
    limits = [parse_time(index[f]['valid_until']) for f in choice['level_fact_ids'] if index[f]['valid_until']]
    until = min(limits) if limits else None
    out['valid_until'] = until
    out['status'] = 'expired' if until is not None and until < finalize_at else 'active'
    return out


# ----------------------------------------------------------------- sections

def section_decision(doc: Doc, ev: dict, analysis: dict, machine_yaml: dict) -> dict:
    doc.add(HEADINGS[0], '', '```yaml', *[f'{k}: {yaml_value(machine_yaml[k])}' for k in YAML_KEYS], '```', '')
    label = direction_label(ev)
    verdict = doc.item(f'判断: {label}。NO-TRADE: ' + (f'あり（{ev["no_trade_reason"]}）' if ev['no_trade'] else 'なし'))
    score = doc.item(f'整合度: {ev["total"]}点・{ev["band"]}（confidence {ev["confidence"]:.2f}、未校正の序数で的中率ではない）。'
                     f'上限制御: {"・".join(ev["caps"]) or "なし"}')
    gate = ev['trade_gate']
    if gate['active_windows']:
        names = '、'.join(w['label'] for w in gate['active_windows'])
        stop = doc.item(f'停止時間: 停止中（{names}）')
    elif gate['upcoming_windows']:
        stop = doc.item('停止時間: ' + '、'.join(w['label'] for w in gate['upcoming_windows']))
    elif ev['calendar_next_24h'] == 'ok':
        stop = doc.item('停止時間: 次24時間の停止時間なし（公式予定の確認範囲内）')
    else:
        stop = doc.item('停止時間: 公式予定の一部が未確認のため「停止時間なし」とは言えない')
    recheck = doc.item(f'次に確認すること: {jst(gate["recheck_at"])}に再評価（{ev["recheck_reason"]}）。'
                       f'判断の有効期限は{jst(ev["valid_until"])}')
    thesis = doc.item('要旨: ' + _claim_text(analysis, 'thesis', analysis['thesis']))
    if ev.get('market_closed_note'):
        doc.item('週末注記: 金曜終値基準の観測。市場休場の注記で、停止ゲートは変更しない')
    doc.item('用途: 研究用の入力候補（research_only）。自動売買の実行は無効（execution_enabled=false）。'
             '親のレビューは独立レビューではない')
    doc.add('')
    return {'verdict': verdict, 'score': score, 'stop': stop, 'recheck': recheck, 'thesis': thesis, 'label': label}


def section_coverage(doc: Doc, facts: dict, ev: dict, fresh: dict, index: dict) -> dict:
    st = facts['state']
    col = st['collection']
    doc.add(HEADINGS[1], '')
    elapsed = (col.get('elapsed_ms') or 0) / 1000
    doc.item(f'収集: {jst(col["started_at"])}〜{jst(col["completed_at"])}（所要{elapsed:.0f}秒・上限540秒）'
             + ('。時間上限で打ち切った情報源あり' if col.get('budget_exhausted') else ''))
    ref = st.get('reference_price', {})
    ref_fact = index.get(ref.get('fact_id') or '')
    if ref_fact and ref_fact['value'] is not None:
        provider = 'Yahoo CL=F（フォールバック・連続限月）' if ref.get('fallback') else 'CNBC @CL.1（フロント限月）'
        ref_line = doc.item(f'参照価格: {ref_fact["display"]}（{provider}、単一ソース、{jst(ref_fact["retrieved_at"])}取得）')
    else:
        ref_line = doc.item('参照価格: 欠測（CNBCとYahooフォールバックが利用できない）')
    doc.item('価格比較: Yahoo OHLCは連続限月で、CNBC @CL.1との限月・ロール整合は未確認。FRED原油現物は参照価格に使わない')
    missing = [scoring.BUNDLE_LABELS[k] for k, v in ev['bundles'].items() if not v]
    coverage = doc.item(f'採点入力の充足: {ev["coverage_present"]}/8（欠け: {"・".join(missing) or "なし"}）。'
                        '充足は入力の有無で、内容の信頼度ではない')
    doc.add('', '| source_id | 状態 | 取得 | 理由 |', '|---|---|---|---|')
    for h in st['source_health']:
        doc.add(f'| {h["source_id"]} | {STATUS_LABEL.get(h["status"], h["status"])} | {jst(h["checked_at"])} | '
                f'{h["reason"] or "—"} |')
    doc.add('')
    unused = [f'{t["source_id"]}（{t["reason"]}）' for t in st.get('terms_restricted', []) + st.get('not_used', [])]
    doc.item('未使用の情報源: ' + ('、'.join(unused) or 'なし'))
    doc.item(f'完成時の鮮度: 収集時点の検査は{"合格" if fresh["as_of_check"] == "passed" else "データ保留"}。'
             f'完成時刻{jst(fresh["finalize_checked_at"])}に有効期限を過ぎた値は{len(fresh["expired_at_finalize"])}件'
             f'（気配など短期の観測）。分析が参照した期限切れ: {"、".join(fresh["referenced_expired"]) or "なし"}')
    doc.item('CME: 契約別の公式満期・メンテナンス時刻はMacから取得できない（403）ため未確認。最終金曜の推定で停止時間を作らない')
    doc.add('')
    return {'reference': ref_line, 'coverage': coverage}


def section_score(doc: Doc, facts: dict, ev: dict, index: dict) -> dict:
    doc.add(HEADINGS[2], '')
    lines = {}
    for key, g in ev['groups'].items():
        support = '、'.join(fact_text(index[f]) for f in g['fact_ids'] if f in index)
        lines[key] = doc.item(f'{scoring.GROUP_LABELS[key]}: {scoring.STATE_TEXT[g["state"]]}（{g["reason_code"]}）'
                              + (f'。根拠: {support}' if support else ''))
    doc.item(f'方向: {direction_label(ev)}（{"・".join(ev["direction_reason_codes"])}）')
    doc.add('', '| 項目 | key | 点 | 状態 | 理由 |', '|---|---|---|---|---|')
    for item in ev['items']:
        doc.add(f'| {scoring.ITEM_LABELS[item["key"]]} | {item["key"]} | {item["points"]:+d} | {item["status"]} | '
                f'{item["reason_code"]} |')
    doc.add('')
    doc.item(f'合計: {ev["total"]}点で{ev["band"]}（High: 5点以上かつ3群、Med: 4点以上かつ2群、Med-cautious: 3点かつ2群、'
             '他はLow。確度「低」は必ずLow）')
    gate = ev['trade_gate']
    doc.item(f'trade_gate: {gate["status"]}（{GATE_LABEL[gate["status"]]}。{"・".join(gate["reason_codes"]) or "理由なし"}）')
    doc.item(f'policy: {scoring.POLICY_VERSION}（policy_hash {scoring.POLICY_HASH[:12]}）')
    doc.add('')
    return lines


def _state_fact_lines(doc: Doc, block: dict, index: dict, fields: tuple) -> dict:
    lines = {}
    for key, label in fields:
        fact = index.get(block.get(key) or '')
        if fact is not None:
            lines[key] = doc.item(f'{label}: {fact_text(fact)}')
        else:
            lines[key] = doc.item(f'{label}: 欠測（観測・履歴不足は補完しない）')
    return lines


def section_positioning(doc: Doc, facts: dict, analysis: dict, index: dict) -> dict:
    st = facts['state']
    doc.add(HEADINGS[3], '')
    summary = doc.item('ポジショニング要約: ' + _claim_text(analysis, 'positioning_summary', analysis['positioning_summary']))
    domain = doc.item('ポジショニングの解釈: ' + _claim_text(analysis, 'three_domains.positioning', analysis['three_domains']['positioning']))
    _state_fact_lines(doc, st.get('cot', {}), index, (
        ('mm_long', 'WTI Managed Moneyロング'), ('mm_short', 'WTI Managed Moneyショート'),
        ('mm_net', 'WTI Managed Moneyネット'), ('oi', 'WTI建玉'), ('mm_net_ratio', 'MMネット/建玉'),
        ('mm_net_ratio_percentile', 'MMネット/建玉の過去順位'), ('oi_change_pct', 'COT建玉の週次変化'),
        ('mm_net_change', 'MMネットの週次変化')))
    doc.item('COT: CFTC Disaggregated Futures Only、WTI-PHYSICAL NYMEX・067651。火曜基準の週次値で日中フローではない')
    _state_fact_lines(doc, st.get('vol', {}), index, (
        ('level', 'OVX'), ('percentile', 'OVXの履歴百分位'), ('change_1d', 'OVX日次変化（FRED履歴）'),
        ('change_1w', 'OVX週次変化（FRED履歴）')))
    doc.item('OVX: 引け後のCboe previous=currentを変化ゼロの根拠にしない。日次変化はFREDの異なる観測日から算出')
    doc.item('リテールセンチメント: v0.1未実装・採点不採用')
    doc.add('')
    return {'summary': summary, 'domain': domain}


def section_liquidity(doc: Doc, facts: dict, analysis: dict, index: dict, dest: dict,
                      finalize_at: datetime) -> dict:
    st = facts['state']
    doc.add(HEADINGS[4], '')
    fixed = doc.item(LIQUIDITY_NOTE)
    domain = doc.item('流動性の解釈: ' + _claim_text(analysis, 'three_domains.liquidity', analysis['three_domains']['liquidity']))
    levels = st.get('levels') or {}
    for item in levels.get('items', []):
        fact = index.get(item.get('fact_id') or '')
        doc.item(f'{item["name"]}: {fact_text(fact) if fact else "欠測"}（{item["state_word"]}、Yahooバー内の状態。限月比較未確認）')
    for caution in levels.get('cautions') or []:
        doc.item('水準の注意: ' + caution['text'])
    width = index.get((levels.get('weekly_range') or {}).get('fact_id') or '')
    if width:
        doc.item(f'前週の週間レンジ幅: {fact_text(width)}。現在値のレンジ内位置は限月整合未確認で未判定')
    choice = dest['choice']
    if choice['kind'] == 'none':
        dline = doc.item('目的地: なし（親の選択。水準の限月比較は未確認）')
    elif dest['status'] == 'expired':
        dline = doc.item('目的地: 失効（根拠の観測の有効期限を完成時刻が過ぎた）')
    else:
        values = '、'.join(fact_text(index[f]) for f in choice['level_fact_ids'])
        dline = doc.item(f'目的地（仮説・到達保証なし）: 名前付き水準 {values}。参照価格との限月整合・上下比較は未確認')
    doc.item('目的地の根拠: ' + _claim_text(analysis, 'destination_choice.rationale', choice['rationale']))
    doc.item('目的地の反対ケース: ' + _claim_text(analysis, 'destination_choice.counter_case', choice['counter_case']))
    if choice['invalidation_rule_ids']:
        doc.item('目的地の無効化: ' + '、'.join(f'{r}（{RULES[r]}）' for r in choice['invalidation_rule_ids']))
    doc.add('')
    return {'fixed': fixed, 'domain': domain, 'destination': dline}


def section_spot(doc: Doc, facts: dict, analysis: dict, index: dict) -> dict:
    st = facts['state']
    doc.add(HEADINGS[5], '')
    lines = _state_fact_lines(doc, st.get('inventory', {}), index, (
        ('crude', 'EIA商業原油在庫'), ('gasoline', 'EIAガソリン在庫'), ('distillate', 'EIA留出油在庫'),
        ('change_crude', '商業原油在庫の週次変化'), ('change_products', '製品在庫の週次変化'),
        ('change_4w', '商業原油在庫の4週累積変化'), ('production', '原油生産'), ('imports', '原油輸入'),
        ('exports', '原油輸出'), ('refinery_input', '精製投入')))
    doc.item('在庫はEIA週次CSVの実績。ForexFactoryの空欄actualを推測で補わない')
    lines.update(_state_fact_lines(doc, st.get('curve', {}), index, (
        ('spread', 'CNBC WTI第1−第2限月差'), ('spread_pct', 'WTI第1−第2限月差率'),
        ('second_third_pct', 'WTI第2−第3限月差率'))))
    doc.item('限月構造: 期近が高いバックワーデーションは需給逼迫の参考、コンタンゴは逆。単独では強い方向を与えない')
    doc.item('Baker HughesとOPEC直源: 取得経路未採用。OPEC情報は本文確認できるニュースに限定')
    doc.add('')
    return lines


def section_macro(doc: Doc, facts: dict, ev: dict, index: dict) -> dict:
    st = facts['state']
    doc.add(HEADINGS[6], '')
    macro = st.get('macro', {})
    group = doc.item('ドル・実金利群: ' + scoring.STATE_TEXT.get(macro.get('status'), 'データ不足')
                     + f'（{macro.get("reason", "inputs_missing")}）')
    same = macro.get('same_day') or {}
    _state_fact_lines(doc, same, index, (('dxy', 'ドル指数DXY（CNBC）'), ('dxy_change', 'DXY前日比'),
                                       ('ust_2y', '米2年利回り（表示のみ）')))
    _state_fact_lines(doc, macro, index, (('DTWEXBGS', '米ドル広義指数（FRED）'), ('DFII10', '米10年実質利回り（FRED）'),
        ('DFII10_change', '米10年実質利回り変化'), ('VIXCLS', 'VIX（表示のみ）'),
        ('SP500', 'S&P 500（表示のみ）'), ('BAMLH0A0HYM2', 'HY OAS（表示のみ）')))
    cal = st.get('calendar', {})
    cal_line = doc.item(f'予定の確認: ForexFactory週間予定、次24時間={cal.get("next_24h", "unknown")}。'
                        'EIAは掲載時刻を優先し、無ければ水曜10:30 ETの暫定時刻。OPECはニュース検知の暫定窓。'
                        '次週予定が未確認ならデータ保留')
    events = [e for e in st.get('events', []) if parse_time(e['start_at']) >= ev['as_of']]
    for e in events[:12]:
        window = e.get('stop_window')
        stop = f'、停止{hm(window["start_at"])}〜{hm(window["end_at"])} JST' if window else ''
        source = scoring.SOURCE_LABELS.get(e['source_id'], e['source_id'])
        status = scoring.STATUS_LABELS.get(e['status'], e['status'])
        cons = e.get('consensus')
        cons = f'、{cons["label"]} 前回 {cons["previous"] or "—"}・予想 {cons["forecast"] or "—"}（ForexFactory）' if cons else ''
        doc.item(f'{jst(e["start_at"])} {e["name"]}（{source}、{status}、重要度{e["importance"]}{stop}{cons}）')
    for e in st.get('calendar_extra', []):
        doc.item(f'{jst(e["start_at"])} {e["name"]}（ForexFactoryの予定、重要度{e["importance"]}、停止なし、'
                 f'前回 {e["previous"] or "—"}・予想 {e["forecast"] or "—"}）')
    if not events:
        doc.item('今後8日の公式予定: 取得範囲内に掲載なし' if cal.get('next_24h') == 'ok' else '今後8日の公式予定: 未確認')
    risk = ev['risk_events_next_24h']
    doc.item('次24時間の高重要イベント: ' + ('、'.join(risk) if risk else 'なし（確認範囲内）'))
    doc.add('')
    return {'group': group, 'calendar': cal_line}


def incident_lines(doc: Doc, st: dict, ev: dict) -> None:
    """Open incidents and every manual release of the last 14 days. ``released_by`` is the operator's own
    assertion (no authentication), so the release stays visible in each edition instead."""
    open_ = ev.get('incidents_open') or []
    if open_:
        for inc in open_:
            doc.item(f'重大障害（継続中）: {inc["incident_id"]}「{md_safe(inc.get("title") or "")}」'
                     f'（公表 {jst(inc["published_at"])}、影響source '
                     f'{"、".join(inc.get("affected_source_ids") or []) or "指定なし（親では解除できない）"}）')
    else:
        doc.item('重大障害（継続中）: なし')
    for r in st.get('incidents', {}).get('recently_released') or []:
        doc.item(f'障害の手動解除（解除から14日間表示）: {r["incident_id"]}「{md_safe(r.get("title") or "")}」、'
                 f'解除 {jst(r["released_at"])}、理由: {md_safe(r.get("release_reason") or "")}、'
                 f'記録者: {md_safe(r.get("released_by") or "")}（実行者の申告で、認証ではない）')


def section_news(doc: Doc, facts: dict, analysis: dict, ev: dict, index: dict) -> dict:
    st = facts['state']
    news = st.get('news', {})
    doc.add(HEADINGS[7], '')
    incident_lines(doc, st, ev)
    if news.get('status') == 'missing':
        line = doc.item('ニュース: 欠測（RSSを取得できず）')
        doc.add('')
        return {'news': line}
    sel = news.get('selection') or {}
    if sel.get('mode') == 'jev':
        method = f'Jevの判定・{md_safe(sel.get("model") or "モデル不明")}・応答{sel.get("elapsed_ms", "—")}ms'
    else:
        reason = {'disabled': '無効', 'timeout': '時間切れ', 'triage_failed': '失敗', 'triage_unavailable': '利用不可',
                  'triage_unreadable': '応答を読めず', 'no_candidates': '候補なし'}.get(sel.get('reason'), '利用不可')
        method = f'キーワード規則（Jev: {reason}）'
    model = ''
    feeds = lambda official: sum(1 for x in news.get('feeds') or [] if bool(x.get('official')) is official)
    line = doc.item(f'ニュースの取得: 一般RSS {news["feeds_ok"]["general"]}/{feeds(False)}・'
                    f'公式RSS {news["feeds_ok"]["official"]}/{feeds(True)}が成功。'
                    f'候補{news["candidate_count"]}件から{len(news["items"])}件を選別（{method}{model}）。'
                    f'期間 {jst(news["window_start"])}〜{jst(news["window_end"])}（{news["lookback_hours"]:g}時間）')
    doc.item('見出しは未確認の手掛かり。本文を取得できたものだけ「本文取得済み」。価格反応は公表時刻付近の動きで因果ではない'
             '。原油1分足は未取得で、分単位の価格反応は欠測')
    assessed = {a['news_id']: (i, a) for i, a in enumerate(analysis['news_assessments'])}
    counted = set(ev['groups']['oil_specific_event']['components'].get('news_ids', []))
    candidates = []
    for n in news['items']:
        doc.item(f'{n["id"]}（{n["publisher"]}、{jst(n["published_at"])}）: {n["title"]}'
                 f'（{CODE_VERIFICATION.get(n["code_verification"], n["code_verification"])}）')
        if n['id'] in assessed:
            i, a = assessed[n['id']]
            doc.item(f'{n["id"]} 親の評価: {GROUP[a["group_id"]]}・{IMPACT[a["impact"]]}・重要度{a["importance"]}・'
                     f'{VERIFICATION[a["verification"]]}' + ('・USOIL固有イベント群に算入' if n['id'] in counted else '')
                     + '。' + _claim_text(analysis, f'news_assessments[{i}].interpretation', a['interpretation']))
        facts_r = [index[f] for f in n.get('fact_ids', [])]
        if facts_r:
            parts = dict(zip(('前5分', '後1分', '後5分'), facts_r))
            doc.item(f'{n["id"]} 公表前5分{parts["前5分"]["display"]}・公表後1分{parts["後1分"]["display"]}')
            after5 = parts.get('後5分')
            if after5 is not None:
                label = f'{n["publisher"]} {hm(n["published_at"])}'
                rline = doc.item(f'{n["id"]} 公表後5分の反応（{label} JST公表）: {after5["display"]}')
                priority = 0 if n['id'] in assessed and assessed[n['id']][1]['importance'] in ('critical', 'high') else 1
                candidates.append((priority, n['published_at'], (label, after5, rline, None)))
    doc.item('USOIL固有イベント群に算入したニュース: ' + ('、'.join(sorted(counted)) or 'なし'))
    doc.add('')
    return {'news': line}


def section_scenarios(doc: Doc, analysis: dict, ev: dict, facts: dict | None = None) -> dict:
    doc.add(HEADINGS[8], '')
    lines = {'reevaluate': [], 'limitations': []}
    for i, s in enumerate(analysis['scenarios']):
        doc.item(f'シナリオ {s["id"]} 条件: ' + '、'.join(f'{r}（{RULES[r]}）' for r in s['condition_rule_ids']))
        doc.item(f'シナリオ {s["id"]} 想定: ' + _claim_text(analysis, f'scenarios[{i}].expected_effect', s['expected_effect']))
        doc.item(f'シナリオ {s["id"]} 反対ケース: ' + _claim_text(analysis, f'scenarios[{i}].counter_case', s['counter_case']))
        doc.item(f'シナリオ {s["id"]} 無効化: ' + '、'.join(f'{r}（{RULES[r]}）' for r in s['invalidation_rule_ids']))
    for i, c in enumerate(analysis['counter_cases']):
        doc.item('反証: ' + _claim_text(analysis, f'counter_cases[{i}]', c))
    for i, r in enumerate(analysis['reevaluation_conditions']):
        lines['reevaluate'].append(doc.item(f'再評価 {r["rule_id"]}: {RULES[r["rule_id"]]}。'
                                            + _claim_text(analysis, f'reevaluation_conditions[{i}].explanation', r['explanation'])))
    for i, c in enumerate(analysis['limitations']):
        lines['limitations'].append(doc.item('制約: ' + _claim_text(analysis, f'limitations[{i}]', c)))
    for text in CODE_LIMITATIONS:
        lines['limitations'].append(doc.item('制約（コード）: ' + text))
    if (facts or {}).get('state', {}).get('news', {}).get('opec_schedule_unconfirmed'):
        lines['limitations'].append(doc.item('制約（コード）: OPEC/JMMCの会合予定候補に日時未確認のものがある。'
                                             '公表時刻を会合時刻へ代用せず、停止窓は未作成。構造化予定か親の日時確認が必要。'))
    doc.add('')
    return lines


def _compare(previous: dict, machine: dict) -> list[str]:
    out = []
    pb, cb = previous.get('usoil', {}), machine['usoil']
    pairs = (('方向', pb.get('direction_status'), cb['direction_status']),
             ('候補方向', pb.get('candidate_direction'), cb['candidate_direction']),
             ('確度', pb.get('direction_strength'), cb['direction_strength']),
             ('band', (pb.get('score') or {}).get('band'), cb['score']['band']),
             ('整合度', (pb.get('score') or {}).get('total'), cb['score']['total']),
             ('NO-TRADE', previous.get('no_trade'), machine['no_trade']),
             ('trade_gate', (pb.get('trade_gate') or {}).get('status'), cb['trade_gate']['status']),
             ('EIA状態', (pb.get('inventory') or {}).get('status'), cb['inventory']['status']))
    for label, before, after in pairs:
        out.append(f'{label}: {before} → {after}' + ('（変化なし）' if before == after else '（変化）'))
    return out


def weekly_review(root, as_of: datetime) -> list[str]:
    """Daily editions of the closing week, including withheld, rejected and failed runs (design 9)."""
    start = as_of - timedelta(days=7)
    counts, directions, seen = {}, {}, set()
    base = root / 'reports' / 'daily' / 'editions'
    for path in sorted(base.glob('*/*.edition.json')) if base.is_dir() else []:
        try:
            record = read_json(path)
            collected = parse_time(record['collected_at'])
        except (OSError, KeyError, ValueError, TypeError):
            continue
        if not start <= collected <= as_of:
            continue
        seen.add(record.get('work_path') or str(path.parent))
        status = record.get('review_status') if record.get('status') == 'succeeded_local' else record.get('status')
        counts[status] = counts.get(status, 0) + 1
        if record.get('review_status') == 'parent_passed':
            try:
                m = read_json(record['outputs']['json_path'])
                key = m['usoil']['direction_status'] if m['usoil']['direction_status'] == 'withheld' else m['usoil']['candidate_direction']
                directions[key] = directions.get(key, 0) + 1
            except (OSError, KeyError, ValueError, TypeError):
                directions['読めない'] = directions.get('読めない', 0) + 1
    failed = 0
    log = root / 'logs' / 'runs.jsonl'
    if log.is_file():
        starts = set()
        for raw in log.read_text(encoding='utf-8').splitlines():
            try:
                rec = json.loads(raw)
                stamp = parse_time(rec.get('started_at') or rec.get('collected_at'))
            except (ValueError, TypeError):
                continue
            if rec.get('mode') == 'daily' and rec.get('status') in ('failed', 'needs_attention') \
                    and not rec.get('edition_path') and start <= stamp <= as_of:
                starts.add(rec.get('started_at'))
        failed = len(starts)
    lines = [f'週次振り返りの対象: {start.astimezone(JST).strftime("%Y-%m-%d %H:%M")}〜{jst(as_of)}のDaily版（保存済みの当時版だけ）',
             'Daily版の状態: ' + ('、'.join(f'{k}={v}' for k, v in sorted(counts.items())) or 'なし'),
             '親レビュー通過版の方向: ' + ('、'.join(f'{k}={v}' for k, v in sorted(directions.items())) or 'なし'),
             f'版を作る前に失敗・要確認になった実行: {failed}件',
             '値動きとの一致は因果や売買収益の証明ではない。当時未公開のデータで補完しない']
    return lines


def section_previous(doc: Doc, ctx, machine: dict, as_of: datetime) -> None:
    doc.add(HEADINGS[9], '')
    previous = None
    for p in ctx.previous_editions:
        try:
            previous = read_json(p['machine_path'])
            prev_info = p
            break
        except (OSError, KeyError, ValueError):
            continue
    if previous is None:
        doc.item('前回版: なし（このジョブの親レビュー通過版がまだない）')
    else:
        doc.item(f'前回版: {jst(prev_info["collected_at"])}（{prev_info.get("session_slot")}）。前回の方向を一致点として加点しない')
        for line in _compare(previous, machine):
            doc.item(line)
    if ctx.mode == 'weekly':
        for line in weekly_review(ctx.root, as_of):
            doc.item(line)
    doc.add('')


def section_facts(doc: Doc, facts: dict, provenance: dict) -> None:
    doc.add(HEADINGS[10], '')
    for key in ('collector_version', 'feature_version', 'policy_hash', 'input_manifest_sha256', 'analysis_sha256',
                'source_catalog_sha256'):
        doc.item(f'{key}: {provenance[key]}')
    doc.add('')
    rows = []
    for f in facts['facts']:
        value = 'null' if f['value'] is None else repr(f['value'])
        if f['value'] is None:
            value = f'null ({f["missing_reason"]})'
        when = f['observed_at'] or f['observation_date'] or f['period_end'] or '—'
        rows.append([f['fact_id'], value, f['unit'], f['status'], when, f['published_at'], f['retrieved_at'],
                     f['source_id'], f['method_id']])
    doc.table(['fact_id', 'value', 'unit', 'status', 'observed_at/date', 'published_at', 'retrieved_at', 'source_id',
               'method_id'], rows)


# ----------------------------------------------------------------- machine.json

def _claim(analysis: dict, where: str, claim: dict) -> dict:
    return {'text': analysis['expanded'][where], 'fact_ids': list(claim['fact_ids']), 'claim_kind': claim['claim_kind']}


def _rule(rule_id: str, fact_ids: list) -> dict:
    return {'rule_id': rule_id, 'fact_ids': list(fact_ids),
            'explanation': {'text': RULES[rule_id], 'fact_ids': [], 'claim_kind': 'limitation'}}


def _window(w: dict) -> dict:
    return {'start_at': w['start_at'], 'end_at': w['end_at'], 'event_ids': list(w['event_ids']), 'rule_id': w['rule_id']}


def machine_parts(facts: dict, analysis: dict, ev: dict, fresh: dict, dest: dict, ctx, analysis_sha: str,
                  collection: dict) -> tuple[dict, dict]:
    from usoil.collect import source_catalog_sha256
    st = facts['state']
    gate = ev['trade_gate']
    core = {'bias': ev['bias'], 'no_trade': ev['no_trade'], 'no_trade_reason': ev['no_trade_reason'],
            'risk_events_next_24h': list(ev['risk_events_next_24h']),
            'positioning_summary': analysis['expanded']['positioning_summary'], 'confidence': ev['confidence']}
    choice = dest['choice']
    if choice['kind'] == 'none' or dest['status'] == 'expired':
        prefix = '目的地は失効（根拠の観測の有効期限を完成時刻が過ぎた）。' if dest['status'] == 'expired' else ''
        destination = {'side': 'none', 'kind': 'none', 'level_fact_ids': [], 'valid_until': None,
                       'rationale': {**_claim(analysis, 'destination_choice.rationale', choice['rationale']),
                                     'text': prefix + analysis['expanded']['destination_choice.rationale']},
                       'counter_case': _claim(analysis, 'destination_choice.counter_case', choice['counter_case']),
                       'invalidation_rules': [_rule(r, []) for r in choice['invalidation_rule_ids']]}
    else:
        destination = {'side': choice['side'], 'kind': choice['kind'], 'level_fact_ids': list(choice['level_fact_ids']),
                       'valid_until': zs(dest['valid_until']) if dest['valid_until'] else None,
                       'rationale': _claim(analysis, 'destination_choice.rationale', choice['rationale']),
                       'counter_case': _claim(analysis, 'destination_choice.counter_case', choice['counter_case']),
                       'invalidation_rules': [_rule(r, choice['level_fact_ids']) for r in choice['invalidation_rule_ids']]}
    by_cat = lambda *cats: [f['fact_id'] for f in facts['facts'] if f['category'] in cats]  # noqa: E731
    news_items = {n['id']: n for n in st.get('news', {}).get('items', [])}
    news = []
    for i, a in enumerate(analysis['news_assessments']):
        n = news_items[a['news_id']]
        reaction = dict(n['reaction'] or {'status': 'missing', 'market_id': None, 'reaction_basis': 'unknown',
                                          'event_at': None, 'baseline_at': None, 'return_before_5m_bps': None,
                                          'return_after_1m_bps': None, 'return_after_5m_bps': None,
                                          'timing_error_bound_seconds': 60, 'confounded': False,
                                          'confound_event_ids': [], 'note': '反応を計測していない（件数上限）'})
        for key in ('raw_sha256', 'bars', 'bars_required', 'bars_missing', 'bars_duplicated'):
            reaction.pop(key, None)  # kept in facts/data (recomputable); not part of the machine reaction record
        reaction['fact_ids'] = list(n.get('fact_ids', []))
        news.append({'id': n['id'], 'event_cluster_id': n['event_cluster_id'], 'title': n['title'], 'url': n['url'],
                     'published_at': n['published_at'], 'first_seen_at': n['first_seen_at'] or n['published_at'],
                     'verification': a['verification'], 'group_id': a['group_id'], 'impact': a['impact'],
                     'importance': a['importance'], 'fact_ids': list(dict.fromkeys(a['fact_ids'] + n.get('fact_ids', []))),
                     'reaction': reaction})
    groups = {k: {'state': g['state'], 'direction': g['direction'], 'reason_code': g['reason_code'],
                  'fact_ids': list(g['fact_ids']), 'components': g.get('components', {})} for k, g in ev['groups'].items()}
    scope = list(CODE_LIMITATIONS)
    def oil_block(key):
        block = st.get(key) or {}
        ids = [f for f in _walk_values(block) if isinstance(f, str) and f in {r['fact_id'] for r in facts['facts']}]
        return {'status': block.get('status') or 'missing', 'fact_ids': list(dict.fromkeys(ids))}
    usoil = {
        'direction_status': ev['direction_status'],
        'direction_reason_codes': list(ev['direction_reason_codes']),
        'candidate_direction': ev['candidate_direction'],
        'confidence_kind': 'uncalibrated_ordinal',
        'score': {'policy_version': scoring.POLICY_VERSION,
                  'items': [{k: i[k] for k in ('key', 'points', 'status', 'reason_code', 'fact_ids', 'group_id')}
                            for i in ev['items']],
                  'total': ev['total'], 'band': ev['band'], 'coverage_ratio': ev['coverage_ratio']},
        'trade_gate': {'status': gate['status'], 'reason_codes': list(dict.fromkeys(gate['reason_codes'])),
                       'active_windows': [_window(w) for w in gate['active_windows']],
                       'upcoming_windows': [_window(w) for w in gate['upcoming_windows']],
                       'recheck_at': zs(gate['recheck_at'])},
        'source_health': st['source_health'],
        'facts': facts['facts'],
        'coverage': {'required': 8, 'present': ev['coverage_present'],
                     'missing': [k for k, v in ev['bundles'].items() if not v], 'scope_notes': scope},
        'reference_price': {'fact_id': st['reference_price']['fact_id'],
                            'venue_fact_ids': list(st['reference_price'].get('venue_fact_ids') or
                                                   ([st['reference_price']['fact_id']] if st['reference_price'].get('fact_id') else [])),
                            'status': st['reference_price']['status']},
        'inventory': oil_block('inventory'), 'curve': oil_block('curve'),
        'cot': oil_block('cot'), 'vol': oil_block('vol'),
        'liquidity': {'book_fact_ids': [], 'liquidation_fact_ids': [], 'heatmap_status': 'unavailable',
                      'level_fact_ids': [i['fact_id'] for i in st.get('levels', {}).get('items', []) if i.get('fact_id')],
                      'scope_note': 'Yahoo連続限月の水準。CNBC参照価格との限月整合・上下比較は未確認',
                      'destination': destination},
        'macro': {'fact_ids': list(st.get('macro', {}).get('fact_ids', [])) + list(st.get('macro', {}).get('change_fact_ids', [])),
                  'window': st.get('macro', {}).get('window', 'unknown'),
                  'proxy_labels': list(st.get('macro', {}).get('proxy_labels', []))},
        'events': [{k: e[k] for k in ('id', 'name', 'category', 'start_at', 'end_at', 'timezone', 'importance', 'status',
                                      'source_id', 'source_url', 'published_at', 'verified_at', 'stop_window')}
                   for e in st.get('events', [])],
        'news': news,
        'analysis': {
            'thesis': _claim(analysis, 'thesis', analysis['thesis']),
            'three_domains': {k: _claim(analysis, f'three_domains.{k}', analysis['three_domains'][k])
                              for k in ('liquidity', 'positioning', 'bias')},
            'scenarios': [{'id': s['id'], 'condition_rule_ids': list(s['condition_rule_ids']), 'fact_ids': list(s['fact_ids']),
                           'expected_effect': _claim(analysis, f'scenarios[{i}].expected_effect', s['expected_effect']),
                           'counter_case': _claim(analysis, f'scenarios[{i}].counter_case', s['counter_case']),
                           'invalidation_rule_ids': list(s['invalidation_rule_ids'])}
                          for i, s in enumerate(analysis['scenarios'])],
            'counter_cases': [_claim(analysis, f'counter_cases[{i}]', c) for i, c in enumerate(analysis['counter_cases'])],
            'reevaluation_conditions': [{'rule_id': r['rule_id'], 'fact_ids': list(r['fact_ids']),
                                         'explanation': _claim(analysis, f'reevaluation_conditions[{i}].explanation',
                                                               r['explanation'])}
                                        for i, r in enumerate(analysis['reevaluation_conditions'])],
            'limitations': [_claim(analysis, f'limitations[{i}]', c) for i, c in enumerate(analysis['limitations'])],
        },
        'provenance': {'collector_version': collection.get('collector_version') or 'unknown',
                       'feature_version': FEATURE_VERSION, 'policy_hash': scoring.POLICY_HASH,
                       'input_manifest_sha256': facts['input_manifest_sha256'], 'analysis_sha256': analysis_sha,
                       'source_catalog_sha256': source_catalog_sha256()},
        'direction_strength': ev['direction_strength'],
        'direction_label': direction_label(ev),
        'groups': groups,
        'confidence_caps': list(ev['caps']),
        'freshness': {k: fresh[k] for k in ('as_of_check', 'finalize_checked_at', 'expired_at_finalize', 'note')},
        'terms_restricted': [dict(t) for t in st.get('terms_restricted', [])],
    }
    generated = zs(ctx.generated_at)
    extensions = {'schema_version': SCHEMA_VERSION, 'asset': SYMBOL, 'report_kind': report_kind(ctx.mode, ctx.session_slot),
                  'edition_id': ctx.work_dir.name, 'available_at': generated, 'valid_until': zs(ev['valid_until']),
                  'research_only': True, 'execution_enabled': False, 'usoil': usoil}
    return core, extensions


# ----------------------------------------------------------------- entry

def _window_label(w: dict, events: dict) -> str:
    names = '・'.join(events[i]['name'] for i in w['event_ids'] if i in events)
    start = parse_time(w['start_at']).astimezone(JST)
    return f'{start.strftime("%m-%d %H:%M")}〜{hm(w["end_at"])} JST（{names}）'


def build(analysis: dict, facts: dict, collection: dict, ctx) -> ReportParts:
    finalize_at = parse_time(ctx.generated_at).astimezone(UTC)
    index = {f['fact_id']: f for f in facts['facts']}
    ev = scoring.evaluate(facts, mode=ctx.mode, assessments=analysis['news_assessments'], finalize_at=finalize_at,
                          recoveries=analysis.get('incident_recoveries') or [])
    events = {e['id']: e for e in facts['state'].get('events', [])}
    for key in ('active_windows', 'upcoming_windows'):
        for w in ev['trade_gate'][key]:
            w['label'] = _window_label(w, events)
    gate = ev['trade_gate']
    recheck = parse_time(gate['recheck_at'])
    if any(parse_time(w['end_at']) == recheck for w in gate['active_windows']):
        ev['recheck_reason'] = '停止時間の終了'
    elif any(parse_time(w['start_at']) == recheck for w in gate['upcoming_windows']):
        ev['recheck_reason'] = '次の停止時間の開始'
    else:
        ev['recheck_reason'] = '判断の有効期限'
    ev['calendar_next_24h'] = facts['state'].get('calendar', {}).get('next_24h')
    fresh = finalize_freshness(facts, analysis, finalize_at, ev['hard_invalid'])
    dest = destination(analysis, index, finalize_at)
    raw = {k: v for k, v in analysis.items() if k != 'expanded'}
    analysis_sha = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    core, extensions = machine_parts(facts, analysis, ev, fresh, dest, ctx, analysis_sha, collection)
    yaml_values = yaml_mapping(core, extensions, mode=ctx.mode, session_slot=ctx.session_slot, as_of=facts['as_of'],
                               collected_at=facts['collected_at'], generated_at=zs(ctx.generated_at))
    collected_jst = parse_time(facts['collected_at']).astimezone(JST)
    doc = Doc()
    doc.add(f'# USOIL チャート外分析 {ctx.mode.capitalize()} — {collected_jst.strftime("%Y-%m-%d %H:%M")} JST', '')
    s0 = section_decision(doc, ev, analysis, yaml_values)
    s1 = section_coverage(doc, facts, ev, fresh, index)
    s2 = section_score(doc, facts, ev, index)
    s3 = section_positioning(doc, facts, analysis, index)
    s4 = section_liquidity(doc, facts, analysis, index, dest, finalize_at)
    s5 = section_spot(doc, facts, analysis, index)
    s6 = section_macro(doc, facts, ev, index)
    section_news(doc, facts, analysis, ev, index)
    s8 = section_scenarios(doc, analysis, ev, facts)
    machine_preview = {**core, **extensions, 'no_trade': ev['no_trade']}
    section_previous(doc, ctx, machine_preview, finalize_at)
    section_facts(doc, facts, extensions['usoil']['provenance'])
    st = facts['state']['collection']
    label = (f'{dashboard._day(st["started_at"])} {dashboard._hm(st["started_at"])}〜{dashboard._hm(st["completed_at"])} JST 収集')
    page = dashboard.build(doc, facts, analysis, ev, index, dest, mode=ctx.mode, finalize_at=finalize_at, s8=s8,
                           title=f'USOIL チャート外分析 {ctx.mode.capitalize()}', as_of_label=label)
    markdown = doc.text()
    figures = page['figures']
    ids = [f['id'] for f in figures]
    summary = {**page['summary'], 'report_date': collected_jst.strftime('%Y-%m-%d')}
    return ReportParts(markdown=markdown, summary=summary, figures=figures, bindings=page['bindings'], machine_core=core,
                       machine_extensions=extensions, required_figures=ids,
                       missing=[f'bundle:{k}' for k, v in ev['bundles'].items() if not v],
                       limitations=list(CODE_LIMITATIONS))


def yaml_mapping(core: dict, ext: dict, *, mode: str, session_slot: str, as_of: str, collected_at: str,
                 generated_at: str) -> dict:
    usoil = ext['usoil']
    return {'schema_version': ext['schema_version'], 'asset': ext['asset'], 'mode': mode, 'session_slot': session_slot,
            'edition': ext['edition_id'], 'as_of': as_of, 'collected_at': collected_at, 'generated_at': generated_at,
            'valid_until': ext['valid_until'], 'bias': core['bias'], 'direction_status': usoil['direction_status'],
            'candidate_direction': usoil['candidate_direction'], 'direction_strength': usoil['direction_strength'],
            'confidence': core['confidence'], 'confidence_band': usoil['score']['band'], 'score_total': usoil['score']['total'],
            'no_trade': core['no_trade'], 'no_trade_reason': core['no_trade_reason'], 'trade_gate': usoil['trade_gate']['status'],
            'coverage_present': usoil['coverage']['present'], 'coverage_required': usoil['coverage']['required']}


def machine_yaml(machine: dict) -> dict:
    usoil = machine['usoil']
    return {'schema_version': machine['schema_version'], 'asset': machine['asset'], 'mode': machine['mode'],
            'session_slot': machine['session_slot'], 'edition': machine['edition_id'], 'as_of': machine['as_of'],
            'collected_at': machine['collected_at'], 'generated_at': machine['generated_at'],
            'valid_until': machine['valid_until'], 'bias': machine['bias'], 'direction_status': usoil['direction_status'],
            'candidate_direction': usoil['candidate_direction'], 'direction_strength': usoil['direction_strength'],
            'confidence': machine['confidence'], 'confidence_band': usoil['score']['band'],
            'score_total': usoil['score']['total'], 'no_trade': machine['no_trade'],
            'no_trade_reason': machine['no_trade_reason'], 'trade_gate': usoil['trade_gate']['status'],
            'coverage_present': usoil['coverage']['present'], 'coverage_required': usoil['coverage']['required']}


class ReportError(ValueError):
    pass


def check_markdown(markdown: str, machine: dict) -> None:
    """MD structure and the decision block must match machine.json (P9-4); mismatch rejects the render."""
    import yaml
    lines = markdown.split('\n')
    kind = machine['mode'].capitalize()
    if not re.fullmatch(rf'# USOIL チャート外分析 {kind} — \d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}} JST', lines[0]):
        raise ReportError('markdown_h1_invalid')
    if lines[0].startswith('---') or markdown.startswith('---'):
        raise ReportError('front_matter_not_allowed')
    headings = [line for line in lines if line.startswith('## ')]
    if tuple(headings) != HEADINGS:
        raise ReportError('markdown_headings_invalid')
    start = lines.index(HEADINGS[0])
    if lines[start + 1] != '' or lines[start + 2] != '```yaml':
        raise ReportError('decision_block_missing')
    end = lines.index('```', start + 3)
    block = lines[start + 3:end]
    keys = [line.split(':', 1)[0] for line in block]
    if tuple(keys) != YAML_KEYS:
        raise ReportError('decision_block_keys_invalid')
    parsed = yaml.safe_load('\n'.join(block))
    expected = machine_yaml(machine)
    for key in YAML_KEYS:
        a, b = parsed[key], expected[key]
        if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or isinstance(a, str) or isinstance(b, str):
            same = a == b and type(a) is type(b)
        else:
            same = float(a) == float(b)
        if not same:
            raise ReportError(f'decision_block_mismatch:{key}')
    if LIQUIDITY_NOTE not in markdown:
        raise ReportError('liquidity_note_missing')
