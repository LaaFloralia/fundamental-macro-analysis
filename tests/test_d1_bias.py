"""D1 typed per-timeframe bias reference (scripts/d1_bias.py): state, call, line, reason check, scoring log."""
import json
import os
import stat

import pytest

from scripts import d1_bias as d1
from scripts import offchart_board as board
from tests.test_offchart_board import SOURCE, sample

KEYS = {'long': 'up_weak', 'short': 'neutral', 'overall': 'down_weak'}


def probs(top):
    out = {key: 0.05 for key in d1.CRITERIA}
    out[top] = 0.8
    return out


def reply(choices=KEYS, provider='d1'):
    return {'answers': {q: {'type': 'choice', 'choice': c, 'confidence': 0.8, 'probabilities': probs(c)}
                        for q, c in choices.items()},
            'usage': {}, 'provider': provider, 'model': 'liquid', 'ms': 321, 'cached': False}


def fake(body=None, calls=None):
    def decide(purpose, state, questions, **kw):
        if calls is not None:
            calls.append({'purpose': purpose, 'state': state, 'questions': questions, **kw})
        if isinstance(body, Exception):
            raise body
        return body if body is not None else reply()
    return decide


def with_news():
    data = sample()
    data['news_headlines'] = {'kept': [
        {'source': 'FXStreet', 'title': 'Gold: Recovery hinges on real yields', 'published': '2026-10-13T07:41:08+00:00', 'p': 0.92},
        {'source': 'Investing.com', 'title': 'U.S. producer prices rise', 'published': '2026-10-12T13:38:59+00:00', 'p': 0.88}]}
    return data


# ---------------------------------------------------------------- state

def test_state_is_the_tallied_rows_and_selected_headlines_without_the_tally():
    state = d1.xau_state(with_news(), 'daily')
    lines = board.format_board_lines(with_news(), 'daily')
    material = [x[2:] for x in lines if x.startswith('- ') and '｜' in x and not x.startswith('- 集計')
                and '｜基準:' in x]
    assert state['rows'] == material and len(material) >= 5
    assert [h['title'] for h in state['headlines']] == ['Gold: Recovery hinges on real yields', 'U.S. producer prices rise']
    blob = json.dumps(state, ensure_ascii=False)
    assert '集計' not in blob and 'tally' not in blob
    # Public material only: no account, position or conversation fields reach the gateway.
    assert set(state) == {'symbol', 'mode', 'as_of', 'rows', 'missing', 'headlines', 'direction_note'}


def test_state_without_rows_is_none():
    assert d1.xau_state({'timestamp': '2026-10-13T22:14:00+09:00', 'mode': 'daily'}, 'daily') is None


# ---------------------------------------------------------------- the call

def test_judge_asks_one_choice_per_timeframe_over_the_five_labels_as_public_data():
    calls = []
    out = d1.xau_judge(with_news(), 'daily', decide=fake(calls=calls))
    call = calls[0]
    assert call['purpose'] == 'bias-xauusd' and call['data_class'] == 'public'
    assert set(call['questions']) == {'long', 'short', 'overall'}
    for question in call['questions'].values():
        assert question['type'] == 'choice' and list(question['criteria']) == list(d1.CRITERIA)
        assert [v.split(':')[0] for v in question['criteria'].values()] == list(d1.LABELS)
    assert d1.LABELS == ('上向き・強', '上向き・弱', '中立', '下向き・弱', '下向き・強')
    assert {n: out[n]['label'] for n in d1.TIMEFRAMES} == {'長期': '上向き・弱', '短期': '中立', '総合': '下向き・弱'}
    assert out['長期']['probs']['上向き・弱'] == 0.8 and set(out['長期']['probs']) == set(d1.LABELS)
    assert out['長期']['provider'] == 'd1' and isinstance(out['長期']['ms'], int)


def test_weekly_and_btc_purposes_and_wording():
    calls = []
    d1.judge('BTCUSD', 'weekly', {'rows': ['x']}, decide=fake(calls=calls))
    assert calls[0]['purpose'] == 'bias-btcusd'
    assert 'coming week' in calls[0]['questions']['short']['instructions']
    assert 'bitcoin' in calls[0]['questions']['long']['instructions']


@pytest.mark.parametrize('body', [RuntimeError('gateway down'), {'answers': {}}, {'answers': 'x'}, {},
                                  {'answers': {'long': {'choice': 'moon'}, 'short': 3, 'overall': None}}])
def test_any_failure_is_fail_open(body):
    assert d1.xau_judge(with_news(), 'daily', decide=fake(body)) is None


def test_missing_client_and_disabled_switch_are_fail_open(monkeypatch, tmp_path):
    monkeypatch.setattr(d1, 'GATEWAY_CLIENT', tmp_path / 'absent.py')
    assert d1.xau_judge(with_news(), 'daily') is None
    monkeypatch.setenv('LAA_D1_BIAS_DISABLED', '1')
    assert d1.xau_judge(with_news(), 'daily', decide=fake()) is None


def test_one_valid_timeframe_is_kept_and_a_bad_one_is_dropped():
    body = reply()
    body['answers']['short'] = {'choice': 'nope'}
    out = d1.xau_judge(with_news(), 'daily', decide=fake(body))
    assert set(out) == {'長期', '総合'}


def test_out_of_range_probabilities_are_not_kept():
    body = reply()
    body['answers']['long']['probabilities'] = {'up_weak': 1.4, 'neutral': 0.2, 'down_weak': True}
    out = d1.xau_judge(with_news(), 'daily', decide=fake(body))
    assert out['長期']['probs'] == {'中立': 0.2}


def test_the_real_gateway_client_is_loadable_and_signs_requests():
    client = d1.load_client()
    if client is None:
        pytest.skip('laa-jev gateway client not installed')
    assert callable(client.decide) and client.GATEWAY == ('127.0.0.1', 8891)


# ---------------------------------------------------------------- writer line

def test_line_sits_directly_under_the_code_tally():
    out = d1.xau_judge(with_news(), 'daily', decide=fake())
    line = d1.format_line(out)
    assert line.startswith('- D1判定（参考。最終判断は親') and '長期 上向き・弱（p=0.80）' in line
    assert '短期 中立' in line and '総合 下向き・弱' in line and 'D1と違う理由' in line
    text = '\n'.join(board.format_board_lines(with_news(), 'daily'))
    placed = d1.insert_line(text, line).split('\n')
    tally = max(i for i, x in enumerate(placed) if x.startswith('- 集計 短期:'))
    assert placed[tally + 1] == line and placed[tally + 2].startswith('- 長期にはこの後')


def test_no_d1_means_no_line_and_unchanged_text():
    assert d1.format_line(None) == '' and d1.format_line({}) == ''
    assert d1.insert_line('abc', '') == 'abc'
    assert d1.insert_line('no tally here', '- D1判定: x').endswith('- D1判定: x\n')


# ---------------------------------------------------------------- writer is the final judge

FINAL = {'長期': '上向き・弱', '短期': '中立', '総合': '下向き・弱'}
D1 = {n: {'label': FINAL[n], 'probs': {}, 'provider': 'd1', 'ms': 1} for n in d1.TIMEFRAMES}


def test_same_labels_need_no_reason():
    d1.check_reason(D1, FINAL, SOURCE)


def test_a_different_label_needs_a_reason_naming_the_horizon():
    final = {**FINAL, '短期': '上向き・弱'}
    with pytest.raises(ValueError, match='短期'):
        d1.check_reason(D1, final, SOURCE)
    d1.check_reason(D1, final, SOURCE + '\nD1と違う理由: 短期は当日のドル安と指標の結果が揃って金に追い風になった。')
    d1.check_reason(D1, final, SOURCE + '\n集計と違う理由: 短期は米2年債とドルの動きを重く見たため。')
    with pytest.raises(ValueError):  # a reason that names another horizon is not enough
        d1.check_reason(D1, final, SOURCE + '\nD1と違う理由: 長期は金利の動きを重く見たため。')
    with pytest.raises(ValueError):  # too short to be a reason
        d1.check_reason(D1, final, SOURCE + '\nD1と違う理由: 短期')


def test_no_d1_never_blocks():
    d1.check_reason(None, {'短期': '上向き・強'}, SOURCE)


def test_final_labels_come_from_the_verdict_line():
    assert d1.source_final(SOURCE) == {'長期': '下向き・弱', '短期': '上向き・弱', '総合': '中立'}


# ---------------------------------------------------------------- scoring log

def test_scoring_rows_carry_d1_and_final_labels_without_any_accuracy_claim(tmp_path):
    out = d1.xau_judge(with_news(), 'daily', decide=fake())
    rows = d1.score_rows(out, d1.source_final(SOURCE), symbol='XAUUSD', mode='daily', run='parent-daily-1',
                         date='2026-10-13', at='2026-10-13T22:30:00+09:00')
    assert [r['timeframe'] for r in rows] == ['長期', '短期', '総合']
    first = rows[0]
    assert first['d1_label'] == '上向き・弱' and first['final_label'] == '下向き・弱' and first['differs'] is True
    assert set(first['d1_probs']) == set(d1.LABELS)
    assert not any('hit' in k or 'accuracy' in k for k in first)
    path = tmp_path / 'logs' / d1.SCORES_NAME
    assert d1.append_scores(path, rows) == 3 and d1.append_scores(path, rows[:1]) == 1
    saved = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(saved) == 4 and saved[0]['symbol'] == 'XAUUSD' and saved[0]['date'] == '2026-10-13'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_missing_final_is_recorded_as_none_not_guessed():
    rows = d1.score_rows(D1, {'総合': '中立'}, symbol='BTCUSD', mode='daily', run='r', date='2026-10-13', at='t')
    assert {r['timeframe']: r['final_label'] for r in rows} == {'長期': None, '短期': None, '総合': '中立'}
    assert all(r['differs'] is None for r in rows if r['final_label'] is None)


def test_append_failure_is_silent(tmp_path):
    blocker = tmp_path / 'file'
    blocker.write_text('x')
    assert d1.append_scores(blocker / 'logs' / 'x.jsonl', [{'a': 1}]) == 0
    assert d1.append_scores(tmp_path / 'none.jsonl', []) == 0
