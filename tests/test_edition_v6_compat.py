"""schemaVersion 6 editions (made before r11) keep passing every check that reads a stored edition.

Fixture: tests/fixtures/edition_v6/2026-10-14T1800.* was made with master 5195c85 (schemaVersion 6) by
report_bundle.prepare from tests/fixtures/real_daily/2026-10-14T1800.data.json (the same bytes: dataSha256) and the
section 0-8 body of real_daily/2026-10-14T1800-r3.md; the bundle's paths were reduced to file names. It carries the
r10 wording that r11 changed (kicker, long titles and subtitles, 「61%・含み損 -5.7%」, 「…・種類不明・前回取得 10/13
（今回は取得失敗）」) and the older one-paragraph conclusion. The r11 renderer draws it with the new layout.
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
EDITION = FIXTURES / 'edition_v6' / '2026-10-14T1800'
DATA = FIXTURES / 'real_daily' / '2026-10-14T1800.data.json'


def stored():
    manifest = json.loads(Path(f'{EDITION}.bundle.json').read_text())
    figures = json.loads(Path(f'{EDITION}.figures.json').read_text())
    summary = json.loads(Path(f'{EDITION}.summary.json').read_text())
    source = Path(f'{EDITION}.md').read_text(encoding='utf-8')
    return json.loads(DATA.read_text()), manifest, figures, summary, source


def test_the_fixture_is_a_v6_edition_with_the_r10_wording():
    data, manifest, figures, summary, source = stored()
    assert manifest['schemaVersion'] == 6 < bundle.MANIFEST_VERSION
    for path, key in ((DATA, 'dataSha256'), (Path(f'{EDITION}.figures.json'), 'figuresSha256'),
                      (Path(f'{EDITION}.summary.json'), 'summarySha256'), (Path(f'{EDITION}.md'), 'sourceSha256')):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[key]
    by_id = {f['id']: f for f in figures}
    assert by_id['bias']['kicker'] == '期間ごとのバイアス' and by_id['calendar']['subtitle'].startswith('時刻はJST。')
    words = {i['state_word'] for i in by_id['liquidity']['items'] if i['kind'] == 'retail'}
    assert words == {'61%・含み損 -5.7%', '39%・含み損 -3.7%'}
    assert {i['touched'] for i in by_id['liquidity']['items'] if i['kind'] == 'zone'} == \
        {'MyFXBook・60分遅れ・種類不明・前回取得 10/13（今回は取得失敗）'}
    assert 'notices' not in by_id['liquidity'] and dash.conclusion_parts(summary['conclusion']['text']) is None


def test_bundle_checks_of_a_stored_v6_edition_pass():
    data, manifest, figures, summary, source = stored()
    bundle.check_bindings(data, manifest, figures)
    bundle.check_regenerated(data, manifest, figures, source, summary)  # not rebuilt: older schemaVersion


def test_a_v6_edition_is_not_rebuilt_but_the_same_inputs_now_make_a_v7_edition():
    data, manifest, figures, summary, source = stored()
    body = source.split(bundle.APPENDIX)[0].rstrip()
    rebuilt = bundle.build_observations(data, 'daily', body)
    assert body + rebuilt['appendix'] != source  # the r11 wording changed the appendix
    manifest = {**manifest, 'schemaVersion': bundle.MANIFEST_VERSION}
    with pytest.raises(bundle.BundleError, match='differ from a rebuild'):
        bundle.check_regenerated(data, manifest, figures, source, summary)


def test_a_stored_v6_edition_validates_and_renders_with_the_r11_layout_without_losing_a_shown_text():
    data, manifest, figures, summary, source = stored()
    dash.validate(summary, figures, source)
    page, audit = dash.build(source, summary, figures, title='XAUUSD Daily', source_name='daily.md')
    assert audit['numeric_check'] == 'pass'
    text = acceptance.compact(re.sub(r'<[^>]+>', '', page.replace('&amp;', '&')))
    assert [t for t in acceptance.shown_texts(summary, figures) if acceptance.compact(t) not in text] == []
    assert 'class="kicker"' not in page and 'class="jump"' not in page and 'class="conf"' not in page
    assert '<span class="tile-conf">信頼度 低</span>' in page and '<dl class="facts">' in page
    assert '<p class="conclusion">XAUUSD：' in page  # the older one-paragraph conclusion
    assert page.count('<div class="horizon">') == 2 and 'side-above' in page and 'side-below' in page


@pytest.mark.parametrize('mutate,message', [
    (lambda s, f: f[1]['items'][0].update(touched='MyFXBook・10/13 の取得分'), r'liquidity\[0\]: "MyFXBook・10/13 の取得分"'),
    (lambda s, f: s['chips'][1].update(text=s['chips'][1]['text'].replace('4,169.97', '4,169.99')), r'chips\[1\]')])
def test_a_changed_v6_row_or_chip_is_still_rejected(mutate, message):
    data, manifest, figures, summary, source = stored()
    summary, figures = deepcopy(summary), deepcopy(figures)
    mutate(summary, figures)
    with pytest.raises(dash.DashboardError, match=message):
        dash.validate(summary, figures, source)
