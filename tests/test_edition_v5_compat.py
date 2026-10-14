"""schemaVersion 5 editions (made before r10, already published) keep passing every check that reads a stored edition.

Fixture: the published 2026-10-14 18:00 Daily edition r3 (parent-daily-20261014T180151-7a981047-r3), its figures,
summary, Markdown and bundle copied unchanged; the collection is tests/fixtures/real_daily/2026-10-14T1800.data.json
(the same bytes: the bundle's dataSha256). Its bias rows carry the small diagrams removed in r10 (bars, delta, gap,
probs, split, track) and its liquidity map the retail average entries as `aux` rows.
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
EDITION = FIXTURES / 'edition_v5' / '2026-10-14T1800-r3'
DATA = FIXTURES / 'real_daily' / '2026-10-14T1800.data.json'


def stored():
    manifest = json.loads(Path(f'{EDITION}.bundle.json').read_text())
    figures = json.loads(Path(f'{EDITION}.figures.json').read_text())
    summary = json.loads(Path(f'{EDITION}.summary.json').read_text())
    source = Path(f'{EDITION}.md').read_text(encoding='utf-8')
    return json.loads(DATA.read_text()), manifest, figures, summary, source


def test_the_fixture_is_the_published_v5_edition():
    data, manifest, figures, summary, source = stored()
    assert manifest['schemaVersion'] == 5 < bundle.MANIFEST_VERSION
    for path, key in ((DATA, 'dataSha256'), (Path(f'{EDITION}.figures.json'), 'figuresSha256'),
                      (Path(f'{EDITION}.summary.json'), 'summarySha256'), (Path(f'{EDITION}.md'), 'sourceSha256')):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[key]
    kinds = {(i.get('viz') or {}).get('type') for f in figures for i in f['items']} - {None}
    assert kinds == {'bars', 'delta', 'gap', 'probs', 'spark', 'split', 'track'}
    liquidity = next(f for f in figures if f['id'] == 'liquidity')
    assert [i['label'] for i in liquidity['aux']] == ['個人ロングの平均建値', '個人ショートの平均建値']


def test_bundle_checks_of_a_stored_v5_edition_pass():
    """hp_publication.validate_edition and report_acceptance.validate_bundle run these two on stored editions."""
    data, manifest, figures, summary, source = stored()
    bundle.check_bindings(data, manifest, figures)  # the aux entries are bound rows
    bundle.check_regenerated(data, manifest, figures, source, summary)  # not rebuilt: older schemaVersion


def test_a_stored_v5_edition_validates_and_renders_again_without_losing_a_shown_text():
    data, manifest, figures, summary, source = stored()
    checked = dash.validate(summary, figures, source)
    assert 'liquidity.aux[0]' in checked and 'liquidity.aux[1]' in checked
    page, audit = dash.build(source, summary, figures, title='XAUUSD Daily', source_name='daily.md')
    assert audit['numeric_check'] == 'pass'
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    assert [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text] == []
    liquidity = next(f for f in figures if f['id'] == 'liquidity')
    for item in liquidity['aux']:  # as in v5: a reference line inside the folded legend
        for key in ('label', 'price_text', 'state_word', 'distance_text'):
            assert acceptance.compact(item[key]) in text
    assert '水準の決め方と状態の意味・個人の平均建値（開いて確認）' in page
    # The removed diagrams are not drawn again (their numbers are in the row text); trend lines still are.
    sparks = sum(1 for f in figures for i in f['items'] if (i.get('viz') or {}).get('type') == 'spark')
    assert page.count('<svg class="viz"') == sparks


@pytest.mark.parametrize('mutate,message', [
    (lambda f: next(i for i in f[0]['items'] if (i.get('viz') or {}).get('type') == 'bars')['viz']['items'][0]
     .update(value=99.9), r'viz: values'),
    (lambda f: next(i for i in f[0]['items'] if (i.get('viz') or {}).get('type') == 'probs')['viz']['segments'][0]
     .update(label='9.99-10.00'), r'viz: "9.99-10.00" is not in its source line'),
    (lambda f: next(i for i in f[0]['items'] if (i.get('viz') or {}).get('type') == 'delta')['viz']
     .update(to='4.9'), r'viz: diagram values must be numbers'),
    (lambda f: f[1]['aux'][0].update(price_text='4,999.99'), r'liquidity\.aux\[0\]: "4,999.99" is not in its source line'),
    (lambda f: f[1]['aux'][0].update(source_quote='どこにもない行'), r'liquidity\.aux\[0\]: source_quote')])
def test_a_changed_v5_diagram_or_aux_row_is_still_rejected(mutate, message):
    data, manifest, figures, summary, source = stored()
    assert [f['id'] for f in figures] == ['bias', 'liquidity', 'calendar']
    figures = deepcopy(figures)
    mutate(figures)
    with pytest.raises(dash.DashboardError, match=message):
        dash.validate(summary, figures, source)
