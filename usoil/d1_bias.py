"""D1 typed per-timeframe bias reference for USOIL (standard library only; shares the core with XAUUSD).

Why the entry: the sandboxed worker cannot read the gateway token (boundary.sb denies /Users/laa), so the
signed gateway call is made by the entry, outside the sandbox, after the prepare worker has written facts.json,
request.json and analysis-input.md. The entry then places one reference line under the code's decision block in
analysis-input.md and records the answer in request.json (``d1_bias``) together with the new input hash, so the
render phase still verifies every input digest. The state sent is public material only: the fact displays that
feed the code's groups and the triage-kept headlines. The code's own direction and score are not sent.

The final USOIL label is the code's (direction status, candidate direction, strength), not the parent's: the
parent selects news and writes interpretation. The reference therefore has no 'reason' duty here; it is logged
against the code's 総合 label for later calibration, and 長期 / 短期 have no USOIL counterpart (logged as null).

Disabled by default; USOIL_D1_ENABLED=1 is required at the entry. The shared
core has no registered USOIL purpose yet; enabling this flag does not provision one.
Never raises from the entry or render hooks: any failure leaves the run exactly as it was without D1.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

CORE_RELATIVE = ('scripts', 'd1_bias.py')
MAX_HEADLINES = 12
HINT = ('最終の向きはコードの判定で、この行は比較用の参考。コードの判定と違うと考える場合は limitations に理由を書く'
        '（D1の行がなくても書式は変わらない）')


def load_core(runtime: Path | None = None, source: Path | None = None):
    """scripts/d1_bias.py from the runtime snapshot (or the repo when running from source)."""
    candidates = []
    if runtime is not None:
        candidates.append(Path(runtime).joinpath(*CORE_RELATIVE))
    if source is not None:
        candidates.append(Path(source).joinpath(*CORE_RELATIVE))
    candidates.append(Path(__file__).resolve().parent.parent.joinpath(*CORE_RELATIVE))
    for path in candidates:
        if path.is_file():
            spec = importlib.util.spec_from_file_location('usoil_entry_d1_bias_core', path)
            module = importlib.util.module_from_spec(spec)
            sys.modules.setdefault('usoil_entry_d1_bias_core', module)
            spec.loader.exec_module(module)
            return module
    return None


# ------------------------------------------------------------------ state

def _fact_ids(state: dict) -> list[str]:
    ids = list(state.get('macro', {}).get('change_fact_ids') or [])
    for block, keys in {
        'inventory': ('change_crude', 'change_products', 'change_4w', 'production'),
        'curve': ('spread_pct', 'second_third_pct'),
        'cot': ('mm_net_ratio', 'mm_net_ratio_percentile', 'oi_change_pct', 'mm_net_change'),
        'vol': ('level', 'percentile', 'change_1d', 'change_1w'),
    }.items():
        ids.extend(state.get(block, {}).get(key) for key in keys)
    seen, out = set(), []
    for fact_id in ids:
        if fact_id and fact_id not in seen:
            seen.add(fact_id)
            out.append(fact_id)
    return out


def _row(fact: dict) -> str:
    when = fact.get('observed_at') or fact.get('observation_date') or fact.get('period_end') or ''
    name = str(fact.get('logical_id') or fact.get('fact_id') or '')
    if name.startswith('usoil.'):
        name = name[len('usoil.'):]
    return f'{name}: {fact.get("display")}（{fact.get("status")}・{when}）'


def usoil_state(facts: dict, mode: str) -> dict | None:
    """Material rows (fact displays behind the code's groups) and triage-kept headlines. None without rows."""
    index = {f['fact_id']: f for f in facts.get('facts', []) if isinstance(f, dict) and 'fact_id' in f}
    rows = []
    for fact_id in _fact_ids(facts.get('state', {})):
        fact = index.get(fact_id)
        if fact and fact.get('value') is not None and fact.get('status') in ('ok', 'provisional', 'lagged') \
                and not fact.get('stale') and fact.get('display') not in (None, ''):
            rows.append(_row(fact))
    if not rows:
        return None
    news = [n for n in facts.get('state', {}).get('news', {}).get('items', []) if isinstance(n, dict) and n.get('title')]
    news.sort(key=lambda n: -(n.get('p') or 0))
    headlines = [{'source': str(n.get('publisher', ''))[:40], 'title': str(n['title'])[:200],
                  'published': str(n.get('published_at', '')), 'relevance': n.get('p'),
                  'verification': n.get('code_verification')} for n in news[:MAX_HEADLINES]]
    return {'symbol': 'USOIL', 'mode': mode, 'as_of': facts.get('as_of'), 'rows': rows, 'headlines': headlines,
            'direction_note': 'Rows are observed facts for WTI crude oil; judge what each means for the USOIL price. '
                              'Headlines are unverified leads unless marked primary.'}


# ------------------------------------------------------------------ entry hook

def _read_regular(path: Path) -> bytes:
    """Bytes of a regular, single-link file owned by this user, opened without following symlinks.
    The entry runs outside the sandbox on files the sandboxed worker can write, so every read and write
    refuses links instead of trusting a path check made earlier."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError('not a private regular file')
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def _digest(path: Path) -> str:
    return hashlib.sha256(_read_regular(path)).hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` through a fresh exclusive temp file in the same folder (never a predictable name)."""
    folder = path.parent
    info = os.lstat(folder)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PermissionError('folder is not a directory')
    if os.path.lexists(path) and stat.S_ISLNK(os.lstat(path).st_mode):
        raise PermissionError('target is a symlink')
    fd, temp = tempfile.mkstemp(dir=folder, prefix='.d1-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def entry_reference(root: Path, mode: str, *, runtime: Path | None = None, decide=None) -> str:
    """Entry side (outside the sandbox), never raises. Returns a fixed label for the entry log:
    ok | skipped | unavailable | no_request."""
    if os.environ.get('USOIL_D1_ENABLED') != '1':
        return 'skipped'
    try:
        root = Path(root)
        latest = json.loads(_read_regular(root / f'latest-{mode}.json').decode('utf-8'))
        if latest.get('status') != 'awaiting_parent_authoring' or not latest.get('request_path'):
            return 'no_request'
        raw_request = Path(latest['request_path'])
        work = (root / 'work').resolve()
        if not raw_request.is_absolute() or raw_request.is_symlink() or raw_request.parent.is_symlink():
            return 'no_request'
        request_path = raw_request.resolve()
        if request_path != raw_request or work not in request_path.parents:
            return 'no_request'
        request = json.loads(_read_regular(request_path).decode('utf-8'))
        if request.get('d1_bias') or request.get('mode') != mode:
            return 'skipped'
        folder = request_path.parent
        facts = json.loads(_read_regular(folder / Path(request['facts_path']).name).decode('utf-8'))
        input_path = folder / Path(request['input_path']).name
        if _digest(input_path) != request['input_sha256']:
            return 'skipped'
        core = load_core(runtime)
        if core is None:
            return 'unavailable'
        state = usoil_state(facts, mode)
        d1 = core.judge('USOIL', mode, state, decide=decide) if state else None
        line = core.format_line(d1, hint=HINT)
        if not line:
            return 'unavailable'
        text = core.insert_line(_read_regular(input_path).decode('utf-8'), line, anchors=('- coverage:',))
        _write_atomic(input_path, text.encode('utf-8'))
        request['input_sha256'] = _digest(input_path)
        request['d1_bias'] = d1
        _write_atomic(request_path, (json.dumps(request, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        return 'ok'
    except Exception:  # noqa: BLE001 - fixed label only; the run goes on without D1
        return 'unavailable'


# ------------------------------------------------------------------ render hooks

def request_reference(work_dir: Path) -> dict | None:
    """The D1 record the entry stored in request.json, validated; None when absent or malformed."""
    try:
        request = json.loads((Path(work_dir) / 'request.json').read_text(encoding='utf-8'))
        d1 = request.get('d1_bias')
        if not isinstance(d1, dict):
            return None
        core = load_core()
        clean = {}
        for name, entry in d1.items():
            if name in core.TIMEFRAMES and isinstance(entry, dict) and entry.get('label') in core.LABELS \
                    and isinstance(entry.get('probs'), dict) and isinstance(entry.get('ms'), int) \
                    and isinstance(entry.get('provider'), str):
                clean[name] = {'label': entry['label'],
                               'probs': {k: v for k, v in entry['probs'].items()
                                         if k in core.LABELS and isinstance(v, (int, float)) and 0 <= v <= 1},
                               'provider': entry['provider'], 'ms': entry['ms']}
        return clean or None
    except Exception:  # noqa: BLE001
        return None


def final_labels(machine: dict) -> dict:
    """USOIL has one final verdict, the code's 総合 (status/direction/strength); 長期 and 短期 are not produced."""
    usoil = machine.get('usoil') or {}
    if usoil.get('direction_status') != 'available' or usoil.get('candidate_direction') not in ('up', 'down'):
        return {'総合': '中立'}
    word = '上向き' if usoil['candidate_direction'] == 'up' else '下向き'
    return {'総合': f'{word}・{"強" if usoil.get("direction_strength") == "高" else "弱"}'}


def record_scores(root: Path, d1: dict | None, machine: dict, *, run: str, at: str) -> int:
    """Append D1 vs the code's final 総合 label to <job>/logs/d1-bias-scores.jsonl. Never raises."""
    try:
        core = load_core()
        if core is None or not d1:
            return 0
        date = str(machine.get('data_as_of') or '')
        rows = core.score_rows(d1, final_labels(machine), symbol='USOIL', mode=machine.get('mode', ''), run=run,
                               date=date, at=at)
        return core.append_scores(Path(root) / 'logs' / core.SCORES_NAME, rows)
    except Exception:  # noqa: BLE001
        return 0
