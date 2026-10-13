"""Build report observations and figures from one fresh, immutable collection.

No data collection, LLM, credentials, Brain, or network access in this module.
Numeric facts are rendered once into an appendix and referenced by every figure.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
REQUIRED_FIGURES = ('bias', 'liquidity', 'calendar')
MANIFEST_VERSION = 3  # 2: release rows carry text bindings; 3: figures are regenerated at validation
APPENDIX = '\n\n## 図表に使用した観測値\n'
CONCLUSION_MAX = 220  # the master prompts ask for one line of 220 characters


class BundleError(ValueError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timestamp(value):
    try:
        t = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return t.replace(tzinfo=JST) if t.tzinfo is None else t.astimezone(JST)
    except (ValueError, TypeError, AttributeError):
        raise BundleError('Missing or invalid data timestamp') from None


def require_fresh(data, now=None):
    stamp = timestamp(data.get('timestamp'))
    current = (now or datetime.now(JST)).astimezone(JST)
    if stamp > current or current - stamp > timedelta(hours=6):
        raise BundleError('Data is stale or future-dated; maximum age is six hours')
    return stamp


def analysis_cutoff(data):
    """The board's "now": the end of collection when recorded (at most an hour after its start),
    otherwise its start. Releases up to it count as released and windows end there."""
    start = timestamp(data.get('timestamp'))
    try:
        end = timestamp(data['collection_finished_at']) if data.get('collection_finished_at') else None
    except BundleError:
        end = None
    return end if end and start <= end <= start + timedelta(hours=1) else start


def number(value, digits=2):
    if isinstance(value, bool):
        raise BundleError('Boolean is not a numeric observation')
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return float(result.quantize(Decimal(1).scaleb(-digits)))
    except (InvalidOperation, ValueError):
        raise BundleError('Numeric observation is missing or nonfinite') from None


def usable(value):
    return isinstance(value, dict) and not value.get('error') and value.get('stale') is not True


def quote_snapshot(data):
    """Whitelist the price fields before main.save_scraped removes transient quotes."""
    quote = data.get('_raw_quote_XAUUSD')
    if not isinstance(quote, dict) or number(quote.get('close')) <= 0:
        raise BundleError('Required XAUUSD quote is missing')
    return {key: quote.get(key) for key in ('symbol', 'close', 'datetime', 'timestamp', 'fetched_at', 'previous_close',
                                            'high', 'low')}


def _figure_gone(key, data):
    """A readable limitation for a figure that could not be drawn."""
    if key == 'bias':
        return 'バイアス盤: 判断材料がすべて欠測で、図を作れない'
    if key == 'liquidity':
        error = (data.get('xau_levels') or {}).get('error') or '水準なし'
        return f'流動性マップ: 1時間足から水準を作れない（{error}）'
    error = (data.get('economic_calendar') or {}).get('error') or '予定が0件'
    return f'米指標の表: 経済指標カレンダーの取得に失敗（{error}）'


def observations(data, now=None, mode='daily', source=None):
    """Three figures from one collection: bias board, liquidity map, US event table.

    Every displayed string is written once into the appendix rows, which become part of
    the Markdown, so each figure item quotes the same source the reader can audit.
    """
    require_fresh(data, now)
    return build_observations(data, mode, source)


def build_observations(data, mode='daily', source=None):
    """observations() without the freshness gate: the same inputs always give the same output,
    so validation can rebuild an edition and compare (check_regenerated)."""
    from scripts import offchart_board
    start, stamp = timestamp(data.get('timestamp')), analysis_cutoff(data)
    figures, rows, missing, bindings, limitations = [], [], [], [], []
    q = data.get('report_quote') or data.get('_raw_quote_XAUUSD')
    if not isinstance(q, dict) or number(q.get('close')) <= 0:
        raise BundleError('Required XAUUSD quote is missing')
    for build in (offchart_board.bias_figure, offchart_board.liquidity_figure):
        built = build(data, stamp, mode, source)
        if built:
            figures.append(built['figure'])
            rows.extend(built['rows'])
            bindings.extend(built.get('bindings') or [])
            limitations.extend(built.get('notes') or [])
    events = offchart_board.event_figure(data, stamp, mode)
    if events:
        figures.append(events['figure'])
        rows.extend(events['rows'])
        bindings.extend(events.get('bindings') or [])
        limitations.extend(events.get('notes') or [])
    lead = offchart_board.lead_chips(data, stamp, mode)
    nearest = offchart_board.nearest_chip(data)
    if nearest:
        lead.append(nearest)
    rows.extend(lead)
    lead = [{'text': text, 'source_quote': text} for text in lead]
    order = {key: index for index, key in enumerate(REQUIRED_FIGURES)}
    figures.sort(key=lambda figure: order.get(figure['id'], len(order)))
    if data.get('synthetic') is True:
        for figure in figures:
            figure['title'] += '（合成値）'
    present = {f['id'] for f in figures}
    for key in REQUIRED_FIGURES:
        if key not in present:
            missing.append(f'{key}: 図の作成に必要なデータが不足')
            limitations.append(_figure_gone(key, data))
    limitations = list(dict.fromkeys(limitations))
    appendix = (APPENDIX + '\n'
                f'データ収集: {start.isoformat()}〜{stamp.isoformat()}（判定の基準は収集終了時刻）。'
                '以下の各行は同じ収集JSONからコードで作り、HTMLの図はこの行だけを表示します。'
                '向きは金（XAUUSD）に対する方向（▲▼ 閾値以上、△▽ 閾値未満、— 中立）です。\n\n' +
                '\n\n'.join(rows) + '\n')
    if limitations:
        appendix += '\n' + '\n'.join(f'制約: {note}' for note in limitations) + '\n'
    if missing:
        appendix += '\n欠測・除外: ' + '。'.join(dict.fromkeys(missing)) + '\n'
    return {'asOf': start.isoformat(), 'cutoff': stamp.isoformat(), 'figures': figures, 'appendix': appendix,
            'lead': lead, 'missing': list(dict.fromkeys(missing)), 'limitations': limitations, 'bindings': bindings}


def resolve(data, path):
    """The value at a dotted path ('fred.DGS2.recent.20.1'); BundleError when missing."""
    value = data
    for key in path.split('.'):
        if isinstance(value, list):
            if not key.isdigit() or int(key) >= len(value):
                raise BundleError('Bound source field is missing')
            value = value[int(key)]
            continue
        if key == 'report_quote' and isinstance(value, dict) and key not in value:
            key = '_raw_quote_XAUUSD'
        if not isinstance(value, dict) or key not in value:
            raise BundleError('Bound source field is missing')
        value = value[key]
    return value


def _shown_text(record, key):
    value = record.get(key)
    return '—' if value in (None, '', 'N/A') else str(value)


def _check_text_bindings(data, texts, figures, strict):
    """Release rows re-read from their records: the calendar's previous / forecast / actual and
    result-minus-forecast, and each member of a released-indicator row on the bias board."""
    from scrapers.macro_surprise import compute_surprise
    items = {f['id']: f['items'] for f in figures}
    needed = {('calendar', n) for n, i in enumerate(items.get('calendar') or []) if not i.get('empty')}
    needed |= {('bias', n) for n, i in enumerate(items.get('bias') or []) if i['label'].startswith('指標の結果')}
    covered = [(b['figure'], b['index']) for b in texts]
    if len(set(covered)) != len(covered) or (strict and set(covered) != needed) or not set(covered) <= needed:
        raise BundleError('Text bindings are missing or unexpected')
    for b in texts:
        item = items[b['figure']][b['index']]
        if item['label'] != b['label']:
            raise BundleError('Text binding points at another row')
        records = [resolve(data, p) for p in (b.get('members') or [b.get('path')])]
        for record in records:
            if not isinstance(record, dict):
                raise BundleError('Bound source record is missing')
            s = compute_surprise(record) if _shown_text(record, 'actual') != '—' else None
            if b['figure'] == 'calendar':
                if any(item[k] != _shown_text(record, k) for k in ('previous', 'forecast', 'actual')):
                    raise BundleError('Release row does not match its source record')
                if item['released'] and s and f'{s["diff"]:+g}{s["unit"]}' not in item['diff']:
                    raise BundleError('Release difference does not match its source record')
            elif s is None or f'{s["diff"]:+g}{s["unit"]}' not in item['change']:
                raise BundleError('Indicator result does not match its source record')
        if b['figure'] == 'bias' and len(records) == 1 and (
                item['display'] != f'結果 {records[0].get("actual")}'
                or not item['baseline'].startswith(f'予想 {records[0].get("forecast")}')):
            raise BundleError('Indicator result does not match its source record')


def check_bindings(data, manifest, figures):
    """Recompute chart values, level states, sparkline series and release rows from recorded
    source paths; matching digits elsewhere never pass. Editions before schemaVersion 2 have no
    text bindings."""
    entries = [(f['id'], i) for f in figures for i in list(f['items']) + list(f.get('aux') or [])]
    numeric = [b for b in manifest['bindings'] if 'value' in b]
    texts = [b for b in manifest['bindings'] if 'value' not in b]
    indexed = {(fid, i['label']): i for fid, i in entries if 'value' in i}
    expected = {(b['figure'], b['label']) for b in numeric}
    if set(indexed) != expected or len(expected) != len(numeric):
        raise BundleError('Figure bindings are missing or unexpected')
    _check_text_bindings(data, texts, figures, strict=manifest.get('schemaVersion', MANIFEST_VERSION) >= 2)
    for b in numeric:
        actual = number(resolve(data, b['path']), b['digits'])
        item = indexed[(b['figure'], b['label'])]
        if actual != b['value'] or actual != item['value']:
            raise BundleError('Figure number does not match its source field')
        if 'state_path' in b and not (resolve(data, b['state_path']) == b['state'] == item.get('state')):
            raise BundleError('Level state does not match its source field')
    for fid, item in entries:
        viz = item.get('viz') or {}
        if viz.get('path'):
            series = [v for _, v in resolve(data, viz['path']) or []
                      if isinstance(v, (int, float)) and not isinstance(v, bool)][-viz['count']:]
            if [number(v, 6) for v in series] != [number(v, 6) for v in viz.get('values') or []]:
                raise BundleError(f'{fid}: sparkline does not match its source series')


def _plain_json(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def check_regenerated(data, manifest, figures, source, summary=None):
    """Rebuild every figure, appendix row and binding from the collection JSON and the report text
    (without its appendix) and require the stored ones to be identical: a flipped sign, swapped
    endpoints, another baseline, an edited word or a removed cell is rejected. The liquidity
    levels are recomputed from the stored bars when the edition carries them. Editions before
    schemaVersion 3 are not regenerated (the code that drew them has changed)."""
    if manifest.get('schemaVersion', 1) < 3:
        return
    body = source.split(APPENDIX)[0].rstrip()
    rebuilt = build_observations(data, manifest['kind'], body)
    if body + rebuilt['appendix'] != source:
        raise BundleError('Appendix rows differ from a rebuild of the collection')
    if _plain_json(rebuilt['figures']) != figures:
        raise BundleError('Figures differ from a rebuild of the collection')
    if _plain_json(rebuilt['bindings']) != manifest['bindings'] or rebuilt['missing'] != manifest['missing']:
        raise BundleError('Bindings differ from a rebuild of the collection')
    if summary is not None:
        # The parent stage may add its own limitations and a notice chip; the code-made ones must all stay.
        texts = {x.get('text') for x in (summary.get('chips') or []) + (summary.get('limitations') or [])
                 if isinstance(x, dict)}
        if not {x['text'] for x in rebuilt['lead']} | set(rebuilt['limitations']) <= texts:
            raise BundleError('Lead chips or limitations differ from a rebuild of the collection')
    if data.get('xau_bars_used'):
        from scrapers.xau_levels import recompute
        levels = data.get('xau_levels') or {}
        computed_at = (levels.get('inputs') or {}).get('computed_at')
        if not computed_at:
            raise BundleError('Liquidity levels do not record when they were computed')
        calendar = data.get('economic_calendar') or {}
        try:
            again = recompute(data['xau_bars_used'], computed_at, levels.get('price'),
                              None if calendar.get('error') else calendar.get('events'), manifest['kind'] == 'weekly')
        except (TypeError, ValueError) as exc:
            raise BundleError(f'Stored bars cannot be used: {exc}') from None
        if _plain_json(again) != levels:
            raise BundleError('Liquidity levels differ from a recomputation from the stored bars')


def make_summary(source, mode, as_of, lead=(), cutoff=None):
    """The conclusion quoted from the report; no editorial text is invented here.

    The dashboard shows one conclusion line, the next important release and the
    report's limitations. Plans and cancellation conditions stay in the Markdown.
    """
    sections = re.split(r'^##\s+', source, flags=re.M)
    intro = next((s for s in sections if re.match(r'.*(?:サマリー|要約)', s.split('\n', 1)[0])), '')
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', intro.partition('\n')[2])
                  if p.strip() and not p.lstrip().startswith(('#', '|', '<'))]
    if not paragraphs:
        raise BundleError('Report summary section has no source paragraph')
    lines = [line.strip() for p in paragraphs for line in p.splitlines() if line.strip()]
    conclusion = next((line for line in lines if re.match(r'XAUUSD\s*(?:[:：]|は)', line)), paragraphs[0])

    def plain(text):
        text = re.sub(r'\[([^\]\n]+)\]\(https?://[^\s)]+\)', r'\1', text)
        return text.replace('**', '').replace('`', '')
    if len(plain(conclusion)) > CONCLUSION_MAX:
        raise BundleError(f'Conclusion line is over {CONCLUSION_MAX} characters; shorten it without dropping conditions')
    stamp = timestamp(as_of)
    end = timestamp(cutoff) if cutoff else stamp
    span = stamp.strftime('%H:%M') + (f'〜{end.strftime("%H:%M")}' if end.strftime('%H:%M') != stamp.strftime('%H:%M') else '')
    return {'eyebrow': f'GOLD {mode.upper()}', 'short_title': 'チャート外分析',
            'report_date': stamp.date().isoformat(),
            'as_of_label': f'{stamp.strftime("%m/%d")}（{"月火水木金土日"[stamp.weekday()]}）{span} JST 収集',
            'conclusion': {'title': '結論', 'text': plain(conclusion), 'source_quote': conclusion},
            'chips': list(lead),
            'note': '向き・価格・指標は原稿と収集データの引用です。売買の確率や到達を示すものではありません。'}


def prepare(md_path, data_path, mode, now=None):
    md_path, data_path = Path(md_path), Path(data_path)
    data = json.loads(data_path.read_text())
    source = md_path.read_text(encoding='utf-8')
    source = source.split(APPENDIX)[0].rstrip()
    result = observations(data, now, mode, source)
    source += result['appendix']
    md_path.write_text(source, encoding='utf-8')
    summary = make_summary(source, mode, result['asOf'], result['lead'], result['cutoff'])
    summary['limitations'] = [{'title': '今回の制約と直近の変化', 'text': note, 'source_quote': note}
                              for note in result['limitations']]
    summary_path, figures_path, manifest_path = [md_path.with_suffix(s) for s in ('.summary.json', '.figures.json', '.bundle.json')]
    check_bindings(data, result, result['figures'])
    for path, value in ((summary_path, summary), (figures_path, result['figures'])):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    manifest = {'schemaVersion': MANIFEST_VERSION, 'kind': mode, 'asOf': result['asOf'],
                'reportDate': timestamp(result['asOf']).date().isoformat(), 'synthetic': data.get('synthetic') is True,
                'dataPath': str(data_path.absolute()), 'dataSha256': digest(data_path),
                'sourcePath': str(md_path.absolute()), 'sourceSha256': digest(md_path),
                'summaryPath': str(summary_path), 'summarySha256': digest(summary_path),
                'figuresPath': str(figures_path), 'figuresSha256': digest(figures_path),
                'missing': result['missing'], 'limitations': result['limitations'], 'bindings': result['bindings'],
                'requiredFigures': list(REQUIRED_FIGURES), 'figureIds': [f['id'] for f in result['figures']],
                'numericCheck': 'passed', 'semanticReview': 'pending', 'publicationReady': False}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return {'summary_path': str(summary_path), 'visuals_path': str(figures_path), 'bundle_path': str(manifest_path)}


def calendar_events_24h(data, as_of):
    """Keep every supplied US/Euro Zone event inside the exact next-24h window."""
    start = timestamp(as_of)
    events = []
    for event in (data.get('economic_calendar') or {}).get('events') or []:
        if event.get('country') not in {'United States', 'Euro Zone'}:
            continue
        try:
            day = datetime.strptime(event['date'], '%A, %B %d, %Y').date()
            when = datetime.fromisoformat(f'{day.isoformat()}T{event["time_jst"]}').replace(tzinfo=JST)
        except (KeyError, ValueError, TypeError):
            continue
        if start <= when <= start + timedelta(hours=24):
            label = f'{when.date().isoformat()} {when.strftime("%H:%M")} JST {event["country"]} / {event["indicator"]}（提供カレンダー、確認状況は本文参照）'
            events.append((when, label))
    return list(dict.fromkeys(label for _, label in sorted(events)))
