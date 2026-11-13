"""schemaVersion 9 editions (made before the 2026-11-14 accuracy pass) keep passing every stored-edition check.

Fixture: tests/fixtures/edition_v9/2026-11-13T1802.* is the published Daily of 2026-11-13 18:02 JST (job edition
parent-daily-20261113T180239-0fbdee72, runtime snapshot 01a01c6), copied unchanged; only the bundle's paths were
reduced to file names. It carries what schemaVersion 10 changed: the central-bank months 8〜10月 beside a total of
7〜9月, the dollar index's forming daily bar dated like a close, no age notes, and Tier B listed two calendar days
ahead (so the Friday table stops before Tuesday's retail sales).
"""
import hashlib
import json
from pathlib import Path
import re

import pytest

from scripts import offchart_dashboard as dash
from scripts import report_acceptance as acceptance
from scripts import report_bundle as bundle

EDITION = Path(__file__).parent / 'fixtures' / 'edition_v9' / '2026-11-13T1802'


def stored():
    load = lambda suffix: json.loads(Path(f'{EDITION}.{suffix}.json').read_text())  # noqa: E731
    return load('data'), load('bundle'), load('figures'), load('summary'), Path(f'{EDITION}.md').read_text(encoding='utf-8')


def rows(figures, figure_id):
    return {i['label']: i for i in {f['id']: f for f in figures}[figure_id]['items']}


def test_the_fixture_is_the_published_v9_edition():
    data, manifest, figures, summary, source = stored()
    assert manifest['schemaVersion'] == 9 < bundle.MANIFEST_VERSION
    for suffix, key in (('data.json', 'dataSha256'), ('figures.json', 'figuresSha256'),
                        ('summary.json', 'summarySha256'), ('md', 'sourceSha256')):
        assert hashlib.sha256(Path(f'{EDITION}.{suffix}').read_bytes()).hexdigest() == manifest[key]
    cb = rows(figures, 'bias')['中銀の金購入（IMF報告国・3ヶ月）']
    assert cb['display'] == '+148.6 t（7〜9月）' and cb['note'] == '月別 8月 +45.8 t・9月 +34.3 t・10月 +5.5 t'
    assert all('fresh' not in i for i in rows(figures, 'bias').values())


def test_bundle_checks_of_a_stored_v9_edition_pass():
    data, manifest, figures, summary, source = stored()
    bundle.check_bindings(data, manifest, figures)
    bundle.check_regenerated(data, manifest, figures, source, summary)  # not rebuilt: older schemaVersion


def test_a_stored_v9_edition_validates_and_renders_without_losing_a_shown_text():
    data, manifest, figures, summary, source = stored()
    dash.validate(summary, figures, source)
    page, audit = dash.build(source, summary, figures, title='XAUUSD Daily', source_name='daily.md')
    assert audit['numeric_check'] == 'pass'
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    assert [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text] == []


def test_the_same_collection_now_makes_a_v10_edition_with_the_corrected_rows():
    data, manifest, figures, summary, source = stored()
    body = source.split(bundle.APPENDIX)[0].rstrip()
    rebuilt = bundle.build_observations(data, 'daily', body)
    assert body + rebuilt['appendix'] != source
    with pytest.raises(bundle.BundleError, match='differ from a rebuild'):
        bundle.check_regenerated(data, {**manifest, 'schemaVersion': bundle.MANIFEST_VERSION}, figures, source, summary)
    bias = rows(rebuilt['figures'], 'bias')
    assert bias['実質金利（米10年TIPS）']['fresh'] == '3日前の値（11/11は米国の祝日）'
    assert bias['投機筋の買い越し（COT Managed Money）']['fresh'] == \
        '10日前の観測。次の公表は11/16（月）・祝日で延期（CFTCの予定表）'
    dollar = bias['ドル指数DXY（20営業日）']
    assert dollar['asof'] == '11/13 日足の途中値' and dollar['display'] == '103.140'
    assert dollar['note'] == '当日の行の現在値とは取得時点が違う（日足の表の値）'
    cb = bias['中銀の金購入（IMF報告国・3ヶ月）']
    assert cb['asof'] == '2026年09月分まで（10月は速報）'
    # The stored collection kept 8〜10月 only: the month of the total it lacks is named, not replaced by 10月
    assert cb['note'] == '内訳 7月 記録なし・8月 +45.8 t・9月 +34.3 t／10月 +5.5 t（12か国の速報・合計に含めない）'
    for label in ('米2年債利回り（当日）', 'ドル指数DXY（当日）', 'GLD保有量（5営業日）', '個人のロング比率（MyFXBook）'):
        assert 'fresh' not in bias[label]                    # values of the collection day or the day before
    for item in bias.values():
        assert item.get('fresh', '') in item['source_quote']
    events = [i['label'] for i in {f['id']: f for f in rebuilt['figures']}['calendar']['items']]
    assert any(label.startswith('小売売上高') for label in events)   # Tuesday 11/17, hidden from the Friday table before
    assert {i['symbol'] for i in bias.values()} == {i['symbol'] for i in rows(figures, 'bias').values()}
    assert [(i['label'], i['symbol']) for i in bias.values()] == \
        [(i['label'], i['symbol']) for i in rows(figures, 'bias').values()]   # no verdict changed
