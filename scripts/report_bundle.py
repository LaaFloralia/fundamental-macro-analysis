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
REQUIRED_FIGURES = ('bias', 'price', 'calendar', 'retail', 'etf', 'cot')
LADDER_SCALE_PCT = 2.5  # levels farther than this from the price are listed, not plotted


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
    return {key: quote.get(key) for key in ('symbol', 'close', 'datetime', 'timestamp', 'previous_close', 'high', 'low')}


def observations(data, now=None, mode='daily', source=None):
    from scripts import offchart_board
    stamp = require_fresh(data, now)
    figures, rows, missing, bindings, limitations = [], [], [], [], []

    def add(ident, kind, title, unit, caption, source, observed, specs):
        items, item_rows, item_bindings = [], [], []
        for spec in specs:
            # 7番目の要素は、図の既定と提供元・時点が異なる項目の観測時刻（例: 価格図の平均建値）。
            label, value, pointer, digits, suffix, detail = spec[:6]
            seen = spec[6] if len(spec) > 6 else observed
            value = number(value, digits)
            display = f'{value:,.{digits}f}{suffix}'
            text = f'{label}: {display}。観測: {seen}。{detail}'.strip()
            item_rows.append(text)
            item = {'label': label, 'value': value, 'display': display, 'source_quote': text,
                    'tone': 'negative' if value < 0 else 'neutral'}
            if detail:
                item['detail'] = detail
            items.append(item)
            item_bindings.append({'figure': ident, 'label': label, 'path': pointer, 'value': value,
                             'digits': digits, 'unit': unit, 'observedAt': seen})
        rows.extend(item_rows)
        bindings.extend(item_bindings)
        figures.append({'id': ident, 'type': kind, 'title': title, 'unit': unit,
                        'subtitle': f'{unit} / 観測: {observed}', 'caption': caption,
                        'source_label': source[0], 'source_url': source[1], 'items': items})

    q = data.get('report_quote') or data.get('_raw_quote_XAUUSD')
    if not isinstance(q, dict) or number(q.get('close')) <= 0:
        raise BundleError('Required XAUUSD quote is missing')
    price = number(q.get('close'))
    r = (data.get('retail_sentiment') or {}).get('XAUUSD')
    quote_time = str(q.get('datetime') or q.get('timestamp') or '市場観測時刻は未確認')
    liq = data.get('liquidity_levels') if isinstance(data.get('liquidity_levels'), dict) else {}
    board = offchart_board.bias_figure(data, stamp, mode, source)
    if board:
        figures.append(board['figure']); rows.extend(board['rows']); bindings.extend(board['bindings'])
        limitations.extend(board['limitations'])
    price_specs = [('現在値', q.get('close'), 'report_quote.close', 2, '', '図の基準。収集時点の記録価格')]
    band_detail = 'GVZ（GLDオプションの予想変動率）を252営業日で日次換算した±1標準偏差。到達範囲の予測ではない'
    for key, label in (('band_upper', '想定変動 上限（+1σ）'), ('band_lower', '想定変動 下限（−1σ）')):
        if isinstance(liq.get(key), (int, float)) and not isinstance(liq.get(key), bool):
            price_specs.append((label, liq[key], f'liquidity_levels.{key}', 2, '', band_detail))
    levels = [(index, lv) for index, lv in enumerate(liq.get('levels') or []) if isinstance(lv, dict)
              and isinstance(lv.get('level'), (int, float)) and not isinstance(lv.get('level'), bool)]
    if levels:
        for index, lv in levels:  # index is the position in the original list, which the binding path names
            level = number(lv['level'])
            side = '上' if level > price else '下' if level < price else '同値'
            inside = (isinstance(liq.get('band_upper'), (int, float)) and isinstance(liq.get('band_lower'), (int, float))
                      and number(liq['band_lower']) <= level <= number(liq['band_upper']))
            stops = {'上': 'ショートの損切り（買い注文）が少し外に集まりやすいとされる（為替研究の傾向。金の注文量は未検証、支持抵抗の強さではない）',
                     '下': 'ロングの損切り（売り注文）が少し外に集まりやすいとされる（為替研究の傾向。金の注文量は未検証、支持抵抗の強さではない）',
                     '同値': '現在値と同じ水準で、どちらの損切りが近いかは言えない'}[side]
            position = f'現在値より{side}' if side != '同値' else '現在値と同値'
            price_specs.append((f'キリ番 {level:,.0f}（{side}）', lv['level'], f'liquidity_levels.levels.{index}.level', 2, '',
                                f'{position}{"・想定変動の範囲内" if inside else "・想定変動の範囲外"}。'
                                f'{"100ドル" if lv.get("major") else "50ドル"}刻み。{stops}'))
    else:
        for key, label, detail in (('nearest_below', 'キリ番（下）', '現在値より下。ロングの損切り（売り注文）が少し外に集まりやすいとされる（為替研究の傾向。金の注文量は未検証）'),
                                   ('nearest_above', 'キリ番（上）', '現在値より上。ショートの損切り（買い注文）が少し外に集まりやすいとされる（為替研究の傾向。金の注文量は未検証）')):
            if isinstance(liq.get(key), (int, float)) and not isinstance(liq.get(key), bool):
                price_specs.append((label, liq[key], f'liquidity_levels.{key}', 2, '', detail))
    if usable(r):
        rt = ('観測日 ' + str(r['as_of_date'])) if r.get('as_of_date') else ('観測時刻は未確認 / 記録 ' + str(r.get('timestamp') or '未確認'))
        provider = r.get('source') or '提供元は未確認'
        provider_url = 'https://fxssi.com/tools/current-ratio' if provider.upper() == 'FXSSI' else 'https://www.myfxbook.com/community/outlook'
        for key, label in (('avg_long_entry', '個人Long平均建値'), ('avg_short_entry', '個人Short平均建値')):
            if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool):
                above = number(r[key]) > price
                price_specs.append((f'{label}（{"上" if above else "下"}）', r[key], f'retail_sentiment.XAUUSD.{key}', 2, '',
                                    f'現在値より{"上" if above else "下"}。{provider}の建玉集計の平均。損益率・SL位置・注文集中は示さない', rt))
    gvz_source = liq.get('gvz_source') or 'FRED GVZCLS'
    gvz_note = (f"GVZ {liq.get('gvz')}（{liq.get('gvz_as_of')}時点・{gvz_source}）" if liq.get('gvz') else 'GVZは取得できず、想定変動は表示していません')
    entry_note = ''
    if any('平均建値' in spec[0] for spec in price_specs):
        entry_note = f'個人の平均建値は{provider}の建玉集計（{rt}）で、価格とは提供元・時点が異なります。'
    add('price', 'price_map', '価格帯の目安（現在値・想定変動・キリ番・個人平均建値）', 'USD/oz',
        f'現在値の上下に、1日の想定変動（{gvz_note}、帯で表示）、キリ番、個人の平均建値を並べています。（上）（下）は現在値から見た側で、'
        '上のキリ番の少し外にはショートの損切り（買い注文）、下のキリ番の少し外にはロングの損切り（売り注文）が集まりやすいという為替研究の傾向を示す参考です。'
        '金での注文量・到達範囲・支持抵抗の強さは未検証です。' + entry_note + '観測した注文集中は取得できません。小数第2位へ丸めています。',
        ('Twelve Data / FRED GVZCLS', 'https://twelvedata.com/'), quote_time, price_specs)
    figures[-1]['subtitle'] = f'USD/oz / 価格の観測: {quote_time}'
    figures[-1]['kicker'] = '価格帯の意識'
    if isinstance(liq.get('band_lower'), (int, float)) and isinstance(liq.get('band_upper'), (int, float)):
        figures[-1]['band'] = {'lower': number(liq['band_lower']), 'upper': number(liq['band_upper']), 'label': '想定変動 ±1σ'}
    for item in figures[-1]['items']:
        if item['label'] == '現在値':
            item['tone'] = 'current'
        elif item['label'].startswith('想定変動'):
            item['tone'] = 'reference'
        elif item['label'].startswith('キリ番'):
            item['tone'] = 'caution'
        if abs(item['value'] - price) / price * 100 > LADDER_SCALE_PCT:
            item['offscale'] = True
    figures[-1]['source_links'] = [{'label': 'Twelve Data', 'url': 'https://twelvedata.com/'},
                                    {'label': 'TradingView CBOE:GVZ', 'url': 'https://www.tradingview.com/symbols/CBOE-GVZ/'}
                                    if gvz_source.startswith('TradingView') else
                                    {'label': 'FRED GVZCLS', 'url': 'https://fred.stlouisfed.org/series/GVZCLS'}]
    if entry_note and r.get('source'):
        figures[-1]['source_links'].append({'label': provider, 'url': provider_url})
    if len(price_specs) == 1:
        figures[-1]['availability'] = 'partial'
    if usable(r) and not all(isinstance(r.get(k), (int, float)) for k in ('avg_long_entry', 'avg_short_entry')):
        limitations.append('Long・Shortの平均建値は取得できず、価格との比較はできません。この比較に基づく方向判断は行いません')
        rows.append(limitations[-1])
    if usable(r):
        try:
            long, short = number(r.get('long_pct')), number(r.get('short_pct'))
            if min(long, short) < 0 or abs(long + short - 100) > .01:
                raise BundleError('Retail percentages do not add to 100')
            add('retail', 'split', '参加口座の建玉比率', '%',
                '取得された比率を小数第2位で表示しています。母集団・分母は提供元の定義に依存し、市場全体の比率を示すものではありません。',
                (provider, provider_url), rt, [
                    ('Long', long, 'retail_sentiment.XAUUSD.long_pct', 2, '%', ''),
                    ('Short', short, 'retail_sentiment.XAUUSD.short_pct', 2, '%', '')])
        except BundleError:
            missing.append('retail: 建玉の一部欠測または比率不整合')
    else:
        missing.append('retail: 建玉データは欠測または古い')
    etf = data.get('gold_etf')
    if usable(etf) and etf.get('as_of_date'):
        try:
            add('etf', 'diverging_bars', 'GLD保有量の変化', 'トン',
                '期間は重複するため合算しません。保有量変化は価格収益率や資金流入額ではありません。',
                ('SPDR Gold Shares', 'https://www.spdrgoldshares.com/usa/historical-data/'), etf['as_of_date'], [
                    ('直近5営業日', etf.get('change_5d_t'), 'gold_etf.change_5d_t', 2, ' t', '期間は直近5営業日'),
                    ('直近20営業日', etf.get('change_20d_t'), 'gold_etf.change_20d_t', 2, ' t', '期間は直近20営業日')])
            streak = etf.get('streak_days')
            direction = etf.get('streak_direction')
            if isinstance(streak, int) and streak > 0 and direction in {'inflow', 'outflow'}:
                word = '保有増' if direction == 'inflow' else '保有減'
                note = f'GLDは直近{streak}営業日連続の{word}。観測: {etf["as_of_date"]}。中期の変化と直近の動きを分けて確認します。'
                rows.append(note)
                figures[-1]['caption'] += note
                limitations.append(note)
        except BundleError:
            missing.append('etf: 保有量変化は欠測')
    cot = data.get('cot_disaggregated')
    if usable(cot) and isinstance(cot.get('data'), dict) and cot['data'].get('date'):
        c = cot['data']
        try:
            specs = []
            for key, label in [('managed_money', 'Managed Money'), ('swap_dealers', 'Swap Dealers'),
                               ('producer_merchant', 'Producer / Merchant'), ('other_reportables', 'Other Reportables')]:
                v = c.get(key) or {}
                if number(v.get('long'), 0) - number(v.get('short'), 0) != number(v.get('net'), 0):
                    raise BundleError('COT net does not match long minus short')
                specs.append((label, v['net'], f'cot_disaggregated.data.{key}.net', 0, ' 枚', ''))
            add('cot', 'diverging_bars', 'Gold先物・主体別ネット建玉', '枚',
                'ネットはLong−Short。週次の先物建玉であり、スポット注文や過熱の判定ではありません。',
                ('CFTC Disaggregated', 'https://www.cftc.gov/dea/futures/other_lf.htm'), c['date'], specs)
        except BundleError:
            missing.append('cot: 主体別建玉の欠測または算術不整合')
    events = offchart_board.event_figure(data, stamp, mode)
    if events:
        figures.append(events['figure']); rows.extend(events['rows'])
    order = {key: index for index, key in enumerate(REQUIRED_FIGURES)}
    figures.sort(key=lambda figure: order.get(figure['id'], len(order)))
    if data.get('synthetic') is True:
        for figure in figures:
            figure['caption'] = '合成値による表示検証です。リンクは参照用で、数値の取得元ではありません。' + figure['caption']
            figure['title'] += '（合成値）'
    present = {f['id'] for f in figures}
    missing.extend(f'{key}: 図の作成に必要なデータが不足' for key in REQUIRED_FIGURES if key not in present)
    appendix = ('\n\n## 図表に使用した観測値\n\n'
                f'データ収集: {stamp.isoformat()}。以下の数値は同じ収集JSONから図表と本文へ出力しています。丸めは各図に表示しています。'
                '各行は「項目: 現在値（時点）｜基準｜変化｜向き｜規則」または「日時｜国｜指標｜前回｜予想｜結果｜差｜向き｜時間帯」の順で、'
                '向きは金（XAUUSD）に対する方向（▲▼ 閾値以上、△▽ 閾値未満、— 中立・票なし、？ 判定保留）です。\n\n' +
                '\n\n'.join(rows) + '\n')
    if missing:
        appendix += '\n欠測・除外: ' + '。'.join(dict.fromkeys(missing)) + '\n'
    return {'asOf': stamp.isoformat(), 'figures': figures, 'appendix': appendix,
            'missing': list(dict.fromkeys(missing)), 'limitations': limitations, 'bindings': bindings}


def check_bindings(data, manifest, figures):
    """Recompute chart values from recorded source paths; matching digits elsewhere never pass."""
    indexed = {(f['id'], i['label']): i for f in figures for i in f['items'] if 'value' in i}
    expected = {(b['figure'], b['label']) for b in manifest['bindings']}
    if set(indexed) != expected:
        raise BundleError('Figure bindings are missing or unexpected')
    for b in manifest['bindings']:
        value = data
        for key in b['path'].split('.'):
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
        actual = number(value, b['digits'])
        if actual != b['value'] or actual != indexed[(b['figure'], b['label'])]['value']:
            raise BundleError('Figure number does not match its source field')


def make_summary(source, mode, as_of):
    """Quote complete source paragraphs; never invent an editorial conclusion."""
    sections = re.split(r'^##\s+', source, flags=re.M)
    intro = next((s for s in sections if re.match(r'.*(?:サマリー|要約)', s.split('\n', 1)[0])), '')
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', intro.partition('\n')[2])
                  if p.strip() and not p.lstrip().startswith(('#', '|', '<'))]
    if not paragraphs:
        raise BundleError('Report summary section has no source paragraph')
    first = paragraphs[0]
    if len(first) > 1100:
        raise BundleError('Summary paragraph is too long; edit without truncating conditions')
    # Keep complete source statements; a long metrics list is not a useful lead.
    conclusion_text = first
    lines = [line.strip() for line in first.splitlines() if line.strip()]
    if len(lines) > 3:
        lead = next((line for line in lines if re.match(r'XAUUSD\s*(?:[:：]|は)', line)), None)
        if lead:
            conclusion_text = lead
    candidates = [p.strip() for p in re.split(r'\n\s*\n', source.split('## 図表に使用した観測値')[0])
                  if p.strip() and not p.lstrip().startswith(('#', '|', '<')) and p.strip() != first and len(p) <= 700]
    def plain(text):
        # Cards use plain text. Preserve readable citation labels here and the
        # complete original links in source_quote / the full source section.
        text = re.sub(r'\[([^\]\n]+)\]\(https?://[^\s)]+\)', r'\1', text)
        return text.replace('**', '').replace('`', '').replace('  \n', '\n')
    conditions = []
    for line in lines:
        if re.match(r'(?:観察時間|観察期限|最大リスク|無効化条件)[:：]', plain(line)):
            title, text = re.split(r'[:：]', plain(line), maxsplit=1)
            conditions.append({'title': title, 'text': text.strip(), 'source_quote': line})
    # Preserve each plan's complete conditions, including negative qualifications.
    # An arbitrary first match can select plan B and discard plan A's prerequisites.
    for match in re.finditer(r'^#{2,3}\s+([^\n]*プラン\s*[ABＡＢ12１２][^\n]*)\n(.*?)(?=^#{1,3}\s|\Z)',
                             source.split('## 図表に使用した観測値')[0], re.M | re.S):
        title, body = match.group(1).strip(), match.group(2).strip()
        if not body or len(body) > 2800:
            raise BundleError('Plan conditions require a complete readable source passage')
        display = []
        for paragraph in re.split(r'\n\s*\n', body):
            if paragraph.lstrip().startswith('|'):
                rows = [[cell.strip() for cell in row.strip().strip('|').split('|')]
                        for row in paragraph.splitlines() if row.strip()]
                if rows[0][0] == '#':
                    continue  # score arithmetic stays in the score/full-source sections
                display.extend(': '.join(row) for row in rows[2:])
            elif not re.fullmatch(r'\*\*スコア内訳[^\n]*\*\*[:：]?', paragraph.strip()):
                display.append(paragraph)
        if not display:
            raise BundleError('Plan conditions are missing')
        conditions.append({'title': title, 'text': plain('\n\n'.join(display)), 'source_quote': match.group(0).strip()})
    if not conditions:
        condition = next((p for p in candidates if re.search('確認|条件|見送', p)), None)
        if condition:
            conditions.append({'title': '次に確認する条件', 'text': plain(condition), 'source_quote': condition})
    metrics = []
    score_line = next((line for line in lines if '信頼度' in line and 'スコア' in line), None)
    if score_line:
        match = re.search(r'信頼度[:：]\s*(\w+)', score_line)
        if match:
            metrics.append({'label': '信頼度', 'value': match.group(1), 'context': plain(score_line), 'source_quote': score_line})
    return {'eyebrow': f'GOLD {mode.upper()}', 'short_title': 'チャート外分析',
            'report_date': timestamp(as_of).date().isoformat(), 'status': '観測・条件・未確認事項',
            'conclusion': {'title': '今回の見立て', 'text': plain(conclusion_text), 'source_quote': first},
            'metrics': metrics,
            'conditions': conditions,
            'source_note': '収集時刻と市場の観測時点は異なります。各図の出典・本文の制約を確認してください。',
            'note': '判断条件と数値は原稿の引用です。売買の確率を示すものではありません。'}


def prepare(md_path, data_path, mode, now=None):
    md_path, data_path = Path(md_path), Path(data_path)
    data = json.loads(data_path.read_text())
    source = md_path.read_text(encoding='utf-8')
    marker = '\n\n## 図表に使用した観測値\n'
    source = source.split(marker)[0].rstrip()
    result = observations(data, now, mode, source)
    source += result['appendix']
    md_path.write_text(source, encoding='utf-8')
    summary = make_summary(source, mode, result['asOf'])
    summary['limitations'] = [{'title': '今回の制約と直近の変化', 'text': note, 'source_quote': note}
                              for note in result['limitations']]
    summary_path, figures_path, manifest_path = [md_path.with_suffix(s) for s in ('.summary.json', '.figures.json', '.bundle.json')]
    check_bindings(data, result, result['figures'])
    for path, value in ((summary_path, summary), (figures_path, result['figures'])):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    manifest = {'schemaVersion': 1, 'kind': mode, 'asOf': result['asOf'],
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
