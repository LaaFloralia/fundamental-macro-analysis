"""schemaVersion 7 editions (made before r12) keep passing every check that reads a stored edition.

Fixture: tests/fixtures/edition_v7/2026-10-14T1800.* was made with master cb85a56 (schemaVersion 7) by
report_bundle.prepare from tests/fixtures/real_daily/2026-10-14T1800.data.json (the same bytes: dataSha256) and the
section 0-8 body of real_daily/2026-10-14T1800-r3.md; the bundle's paths were reduced to file names. It carries the
r11 wording that r12 changed (「+250.41（+6.04%）」, 「最初に抜けた足 …」, 「個人の61%がロング・平均で含み損 -5.7%」, the
per-row fetch line and the long notice). The r12 renderer draws it as a tree and still shows every stored string.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

import pytest

from scripts import offchart_dashboard as dash
from scripts import report_acceptance as acceptance
from scripts import report_bundle as bundle

FIXTURES = Path(__file__).parent / 'fixtures'
EDITION = FIXTURES / 'edition_v7' / '2026-10-14T1800'
DATA = FIXTURES / 'real_daily' / '2026-10-14T1800.data.json'


def stored():
    manifest = json.loads(Path(f'{EDITION}.bundle.json').read_text())
    figures = json.loads(Path(f'{EDITION}.figures.json').read_text())
    summary = json.loads(Path(f'{EDITION}.summary.json').read_text())
    source = Path(f'{EDITION}.md').read_text(encoding='utf-8')
    return json.loads(DATA.read_text()), manifest, figures, summary, source


def test_the_fixture_is_a_v7_edition_with_the_r11_wording():
    data, manifest, figures, summary, source = stored()
    assert manifest['schemaVersion'] == 7 < bundle.MANIFEST_VERSION
    for path, key in ((DATA, 'dataSha256'), (Path(f'{EDITION}.figures.json'), 'figuresSha256'),
                      (Path(f'{EDITION}.summary.json'), 'summarySha256'), (Path(f'{EDITION}.md'), 'sourceSha256')):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[key]
    liq = {f['id']: f for f in figures}['liquidity']
    assert {i['state_word'] for i in liq['items'] if i['kind'] == 'retail'} == \
        {'個人の61%がロング・平均で含み損 -5.7%', '個人の39%がショート・平均で含み損 -3.7%'}
    assert {i['touched'] for i in liq['items'] if i['kind'] == 'zone'} == {'MyFXBook・10/13 の取得分（時刻の記録なし）'}
    assert any(i['touched'].startswith('最初に抜けた足 ') for i in liq['items'] if i['kind'] == 'level')
    assert any(re.fullmatch(r'[+-][\d,.]+（[+-][\d.]+%）', i['distance_text']) for i in liq['items'])
    assert [n['text'] for n in liq['notices']] == ['個人の注文・建玉は、最新の取得に失敗したため10/13の取得分（時刻の記録なし）を表示しています。']


def test_bundle_checks_of_a_stored_v7_edition_pass():
    data, manifest, figures, summary, source = stored()
    bundle.check_bindings(data, manifest, figures)
    bundle.check_regenerated(data, manifest, figures, source, summary)  # not rebuilt: older schemaVersion


def test_a_v7_edition_is_not_rebuilt_but_the_same_inputs_now_make_a_v8_edition():
    data, manifest, figures, summary, source = stored()
    body = source.split(bundle.APPENDIX)[0].rstrip()
    rebuilt = bundle.build_observations(data, 'daily', body)
    assert body + rebuilt['appendix'] != source  # the r12 wording changed the appendix
    manifest = {**manifest, 'schemaVersion': bundle.MANIFEST_VERSION}
    with pytest.raises(bundle.BundleError, match='differ from a rebuild'):
        bundle.check_regenerated(data, manifest, figures, source, summary)


def test_a_stored_v7_edition_validates_and_renders_as_a_tree_without_losing_a_shown_text():
    data, manifest, figures, summary, source = stored()
    dash.validate(summary, figures, source)
    page, audit = dash.build(source, summary, figures, title='XAUUSD Daily', source_name='daily.md')
    assert audit['numeric_check'] == 'pass'
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    assert [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text] == []
    assert page.count('class="lv-node"') == len(figures[1]['items']) and 'ladder-head' not in page
    assert '最初に抜けた足 10/12 09:00 JST（アジア）' in page  # a stored touched line is still shown


@pytest.mark.parametrize('mutate,message', [
    (lambda s, f: f[1]['items'][1].update(touched='MyFXBook・10/12 の取得分'), r'liquidity\[1\]: "MyFXBook・10/12 の取得分"'),
    (lambda s, f: f[1]['items'][0].update(distance_text='+370.86（+8.95%）'), r'liquidity\[0\]: "\+370.86')])
def test_a_changed_v7_row_is_still_rejected(mutate, message):
    data, manifest, figures, summary, source = stored()
    summary, figures = deepcopy(summary), deepcopy(figures)
    mutate(summary, figures)
    with pytest.raises(dash.DashboardError, match=message):
        dash.validate(summary, figures, source)
