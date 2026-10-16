"""D1 typed per-timeframe bias judgment for the off-chart analysis (XAUUSD and BTCUSD).

After the code has tallied the material rows and before the Sol xhigh writer runs, one signed call to the
local decision gateway (127.0.0.1:8891, purpose ``bias-xauusd`` / ``bias-btcusd``, data class ``public``)
asks for one Choice per timeframe (長期, 短期, 総合) over the five bias labels, with a probability for each
label. The answer is a reference line for the writer, a ``d1_bias`` record in machine.json and a row in a
local scoring log for later calibration. It never replaces the writer's verdict and never blocks a run:
every failure returns ``None`` and the caller simply omits the D1 line.

The state sent is the tallied material rows and the selected news headlines only (public market data).
No account, position, conversation or Brain data. The aggregated code tally is deliberately not sent, so
D1 reads the rows rather than echoing the tally. Standard library only; the HMAC gateway client is the
laa-jev one (loaded by path, nothing is copied).

Scoring log (``<job>/logs/d1-bias-scores.jsonl``): one row per run and timeframe with D1's label and
probabilities and the final label. Hit rates against prices are computed later; nothing here claims accuracy.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import time

GATEWAY_CLIENT = Path(os.environ.get('LAA_D1_GATEWAY_CLIENT') or '/Users/laa/dev/laa-jev/nav/gateway_client.py')
SCORES_NAME = 'd1-bias-scores.jsonl'
DATA_CLASS = 'public'
TIMEOUT = 8.0
PURPOSES = {'XAUUSD': 'bias-xauusd', 'BTCUSD': 'bias-btcusd'}
TIMEFRAMES = ('長期', '短期', '総合')
QUESTION_KEYS = {'長期': 'long', '短期': 'short', '総合': 'overall'}
# ASCII criteria keys keep the typed answer unambiguous; the descriptions carry the meaning.
CRITERIA = {
    'up_strong': ('上向き・強', 'Several independent materials clearly point to a rise of the price.'),
    'up_weak': ('上向き・弱', 'The materials lean toward a rise but modestly, or one independent factor leads.'),
    'neutral': ('中立', 'Materials offset each other, are thin, or no side leads by at least one factor.'),
    'down_weak': ('下向き・弱', 'The materials lean toward a fall but modestly, or one independent factor leads.'),
    'down_strong': ('下向き・強', 'Several independent materials clearly point to a fall of the price.'),
}
KEY_OF = {label: key for key, (label, _) in CRITERIA.items()}
LABELS = tuple(label for label, _ in CRITERIA.values())
MAX_HEADLINES = 12
REFERENCE_NOTE = '参考。最終判断は親、コード集計の代わりではない'
LINE_PREFIX = 'D1判定'
REASON_PREFIX = 'D1と違う理由'
REASON_LINE = re.compile(r'^D1と違う理由[:：]\s*(\S.*)$', re.M)
REASON_MIN = 8


# ------------------------------------------------------------------ gateway

def load_client(path: Path | None = None):
    """The laa-jev HMAC client module (loaded by path), or None when it is not installed."""
    path = Path(path or GATEWAY_CLIENT)
    try:
        spec = importlib.util.spec_from_file_location('laa_jev_gateway_client', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 - optional reference, never a failure
        return None


def _timeframe_text(symbol: str, mode: str) -> dict:
    asset = 'gold (XAUUSD)' if symbol == 'XAUUSD' else 'bitcoin (BTCUSD)'
    short = {'daily': 'today to the next one or two days', 'weekly': 'the coming week'}.get(mode, 'the next days')
    if symbol == 'BTCUSD':
        long_text = f'the coming weeks, judged from the slow materials (rates and dollar, ETF and stablecoin flows, positioning)'
    else:
        long_text = 'the coming weeks, judged from the slow materials (real rates, dollar, central-bank buying, news)'
    return {
        'long': f'Direction of {asset} over {long_text}.',
        'short': f'Direction of {asset} over {short}, judged from the fast materials (daily moves, pricing, releases, flows).',
        'overall': f'Overall direction of {asset}, combining the long and short horizons.'}


def build_questions(symbol: str, mode: str) -> dict:
    texts = _timeframe_text(symbol, mode)
    guide = (' Rows that measure the same driver (e.g. 2y yield, policy-rate pricing and data surprises; COT and retail '
             'ratios) are one vote. Materials that offset each other or are insufficient mean neutral; choose a strong '
             'label only when two or more independent factors agree. Use only the rows and headlines given.')
    criteria = {key: f'{label}: {text}' for key, (label, text) in CRITERIA.items()}
    return {QUESTION_KEYS[name]: {'type': 'choice', 'instructions': texts[QUESTION_KEYS[name]] + guide, 'criteria': criteria}
            for name in TIMEFRAMES}


def parse_answers(answers: dict) -> dict:
    """{timeframe: {'label', 'probs'}} for each valid typed answer; an invalid timeframe is simply absent."""
    out = {}
    for name, key in QUESTION_KEYS.items():
        answer = answers.get(key)
        if not isinstance(answer, dict):
            continue
        label = (CRITERIA.get(answer.get('choice')) or (None,))[0]
        if label is None:
            continue
        raw = answer.get('probabilities') if isinstance(answer.get('probabilities'), dict) else {}
        probs = {}
        for crit_key, (text, _) in CRITERIA.items():
            value = raw.get(crit_key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
                probs[text] = round(float(value), 4)
        out[name] = {'label': label, 'probs': probs}
    return out


def judge(symbol: str, mode: str, state: dict, *, decide=None, timeout: float = TIMEOUT) -> dict | None:
    """{timeframe: {label, probs, provider, ms}} or None. Never raises."""
    purpose = PURPOSES.get(symbol)
    if purpose is None or not isinstance(state, dict) or not state.get('rows'):
        return None
    if os.environ.get('LAA_D1_BIAS_DISABLED') == '1':
        return None
    try:
        if decide is None:
            client = load_client()
            if client is None:
                return None
            decide = client.decide
        started = time.monotonic()
        body = decide(purpose, state, build_questions(symbol, mode), data_class=DATA_CLASS, timeout=timeout)
        ms = round((time.monotonic() - started) * 1000)
        parsed = parse_answers(body.get('answers') or {})
        if not parsed:
            return None
        provider = str(body.get('provider') or '')
        return {name: {**value, 'provider': provider, 'ms': ms} for name, value in parsed.items()}
    except Exception:  # noqa: BLE001 - fail open: the writer simply gets no D1 line
        return None


# ------------------------------------------------------------------ writer line

def format_line(d1: dict | None, hint: str | None = None) -> str:
    """One reference line next to the code tally; '' when there is no D1. ``hint`` replaces the XAUUSD
    instruction about the reason line (BTCUSD has no writer-chosen label)."""
    if not d1:
        return ''
    parts = []
    for name in TIMEFRAMES:
        entry = d1.get(name)
        if not entry:
            continue
        top = entry['probs'].get(entry['label'])
        parts.append(f'{name} {entry["label"]}' + (f'（p={top:.2f}）' if isinstance(top, (int, float)) else ''))
    if not parts:
        return ''
    provider = next((e['provider'] for e in d1.values() if e.get('provider')), '')
    return (f'- {LINE_PREFIX}（{REFERENCE_NOTE}）: ' + '｜'.join(parts)
            + (f'（提供: {provider}）' if provider else '')
            + ('。' + hint if hint else f'。最終の向きがこれと違う時間軸があれば、セクション0に `{REASON_PREFIX}: <1行>` '
                                          f'（時間軸を名指し）か `集計と違う理由:` を書く'))


def insert_line(text: str, line: str, anchors=('- 集計 短期:', '- 集計 長期:')) -> str:
    """Place the D1 line directly under the code tally. Without a tally block, append at the end."""
    if not line:
        return text
    lines = text.split('\n')
    for anchor in anchors:
        for index in range(len(lines) - 1, -1, -1):
            if lines[index].startswith(anchor):
                lines.insert(index + 1, line)
                return '\n'.join(lines)
    return text.rstrip('\n') + '\n\n' + line + '\n'


# ------------------------------------------------------------------ writer check

def differing(d1: dict | None, final: dict) -> list[str]:
    """Timeframes where D1 and the writer's final label differ ({timeframe: label})."""
    if not d1:
        return []
    return [name for name in TIMEFRAMES if name in d1 and final.get(name) and d1[name]['label'] != final[name]]


def reason_ok(source: str, names: list[str]) -> bool:
    """True when nothing differs, or the report states why: a `D1と違う理由:` line naming each differing
    timeframe, or the existing `集計と違う理由:` line naming it."""
    if not names:
        return True
    texts = [m.group(1) for m in REASON_LINE.finditer(source or '')]
    texts += [m.group(1) for m in re.finditer(r'^集計と違う理由[:：]\s*(\S.*)$', source or '', re.M)]
    joined = '\n'.join(texts)
    return len(joined.strip()) >= REASON_MIN and all(name in joined for name in names)


def check_reason(d1: dict | None, final: dict, source: str) -> None:
    names = differing(d1, final)
    if not reason_ok(source, names):
        raise ValueError('D1 differs from the final verdict for ' + '・'.join(names) + f': write `{REASON_PREFIX}: <1行>` '
                         'naming each time horizon (or the existing 集計と違う理由 line)')


# ------------------------------------------------------------------ scoring log

def score_rows(d1: dict | None, final: dict, *, symbol: str, mode: str, run: str, date: str, at: str) -> list[dict]:
    """One row per timeframe D1 answered. ``final`` maps timeframe to the final label or None (not produced)."""
    rows = []
    for name in TIMEFRAMES:
        entry = (d1 or {}).get(name)
        if not entry:
            continue
        rows.append({'at': at, 'date': date, 'symbol': symbol, 'mode': mode, 'run': run, 'timeframe': name,
                     'd1_label': entry['label'], 'd1_probs': entry['probs'], 'd1_provider': entry.get('provider', ''),
                     'd1_ms': entry.get('ms'), 'final_label': final.get(name),
                     'differs': (final.get(name) != entry['label']) if final.get(name) else None})
    return rows


def append_scores(path: Path, rows: list[dict]) -> int:
    """Append rows to the local jsonl (created 0600). Never raises; returns the number written."""
    if not rows:
        return 0
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        return len(rows)
    except OSError:
        return 0


# ------------------------------------------------------------------ XAUUSD state

def xau_state(data: dict, mode: str) -> dict | None:
    """Tallied material rows (without the aggregated tally) and the selected headlines. None when no rows."""
    from scripts.offchart_board import bias_rows
    from scripts.report_bundle import analysis_cutoff
    stamp = analysis_cutoff(data)
    items, _, notes = bias_rows(data, stamp, mode)
    if not items:
        return None
    rows = [item['source_quote'].split('｜規則: ')[0] for item in items]
    kept = (data.get('news_headlines') or {}).get('kept') or []
    headlines = [{'source': str(h.get('source', ''))[:40], 'title': str(h.get('title', ''))[:200],
                  'published': str(h.get('published', '')), 'relevance': h.get('p')}
                 for h in kept[:MAX_HEADLINES] if isinstance(h, dict) and h.get('title')]
    return {'symbol': 'XAUUSD', 'mode': mode, 'as_of': stamp.isoformat(), 'rows': rows, 'missing': list(notes)[:8],
            'headlines': headlines,
            'direction_note': 'Each row ends with its direction for the gold price (▲▼ one vote, △▽ half a vote, — neutral).'}


def xau_judge(data: dict, mode: str, **kwargs) -> dict | None:
    try:
        state = xau_state(data, mode)
    except Exception:  # noqa: BLE001 - the board is optional input
        return None
    return judge('XAUUSD', mode, state, **kwargs) if state else None


def source_final(source: str) -> dict:
    """The writer's final labels from the `時間軸別の向き:` line."""
    from scripts.offchart_board import source_verdicts
    return {name: value[2] for name, value in source_verdicts(source).items()}

