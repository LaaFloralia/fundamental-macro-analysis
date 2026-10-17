"""Oil semantics: observed-source contracts, strict thresholds, freshness and fail-closed gates.

Synthetic inputs follow W1's normalized shape. No production/network/secret access.
"""
from __future__ import annotations
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from usoil import analysis, facts as facts_mod, report, scoring
from usoil.common import write_json, parse_time
from usoil.history import History
from usoil.jsonschema_lite import validate

UTC = timezone.utc
AT = datetime(2026,10,13,16,tzinfo=UTC)
SHA = 'a'*64


def stamp(t):
    return t.isoformat().replace('+00:00','Z')


def rec(source, values, at=AT, observed=None, status='ok'):
    return {'source_id':source,'status':status,'retrieved_at':stamp(at),'observed_at':stamp(observed) if observed else None,
            'error_kind':None,'values':{'raw_parts':{'r0':SHA,'cl_d1':SHA,'cl_h1':SHA},**values}}


def collection(at=AT):
    q = lambda value,prev: {'last':value,'previous_close':prev,'change':value-prev,'change_pct':100*(value/prev-1),
                            'last_time':stamp(at-timedelta(minutes=1)),'time_kind':'quote_time','expiration_date':'2026-10-20'}
    weeks = []
    for i in range(106):
        weeks.append({'report_date':(at.date()-timedelta(weeks=105-i)).isoformat(),
                      'mm_long':200000+i*100,'mm_short':100000,'mm_net':100000+i*100,'open_interest':2000000,
                      'change_open_interest':0,'change_mm_long':100,'change_mm_short':0})
    history=[]
    day=at.date()-timedelta(days=1)
    while len(history)<280:
        if day.weekday()<5:
            history.append({'date':day.isoformat(),'value':30+len(history)*0.01})
        day-=timedelta(days=1)
    series={sid:{'frequency':'daily','unit':unit,'label':sid,'raw':SHA,'rows':rows} for sid,unit,rows in (
        ('DFII10','percent',[{'date':'2026-10-09','value':2.0},{'date':'2026-10-12','value':1.9}]),
        ('DTWEXBGS','index',[{'date':'2026-10-09','value':100},{'date':'2026-10-12','value':99}]),
        ('OVXCLS','index',list(reversed(history))),
        ('DCOILWTICO','USD/bbl',[{'date':'2026-10-12','value':1000}]))}
    return {'collection_started_at':stamp(at-timedelta(minutes=2)),'collection_completed_at':stamp(at),
            'sources':[
                rec('cnbc_quotes',{'quotes':{'@CL.1':q(100,98),'@CL.2':q(99,97),'@CL.3':q(98,96),'.DXY':q(99,100)}},at),
                rec('yahoo_ohlc',{'symbols':{'CL=F':{'d1':[],'h1':[]}}},at),
                rec('eia_wpsr',{'week_ending':'2026-10-09','stocks':{
                    'commercial_crude_ex_spr':{'current':415,'change_wow':-4,'change_yoy':-10},
                    'gasoline':{'current':200,'change_wow':0,'change_yoy':-1},
                    'distillate':{'current':100,'change_wow':0,'change_yoy':-1}},'flows':{
                    'production':{'current':13000},'imports':{'current':6000},'exports':{'current':3000},
                    'refinery_input':{'current':15000}}},at),
                rec('cftc_cot',{'series':weeks,'latest':weeks[-1]},at),
                rec('cboe_ovx',{'last':40,'previous_close':40,'change_pct':0},at,observed=at-timedelta(minutes=1)),
                rec('fred',{'series':series},at),
                rec('forexfactory_week',{'events':[{'title':'Crude Oil Inventories','start_at':'2026-10-14T14:30:00Z',
                    'importance':'low','previous':'-3.2M','forecast':'0.2M'}]},at),
                rec('news',{'kept':[],'feeds':[],'selection':{},'selection_rule':'synthetic','candidate_count':0,
                            'window_start':stamp(at-timedelta(hours=36)),'window_end':stamp(at),'lookback_hours':36,
                            'feeds_ok':{'general':2,'official':1}},at)]}


def build(tmp_path, c=None, mode='daily'):
    c=c or collection()
    path=tmp_path/'collection.json'
    write_json(path,c)
    return facts_mod.build(c,mode=mode,session_slot='weekly' if mode=='weekly' else 'pm',
                           collection_path=path,history_dir=tmp_path/'history')


def source(c,key):
    return next(r for r in c['sources'] if r['source_id']==key)


def value(f,key,field):
    return facts_mod.index(f)[f['state'][key][field]]


def test_source_contract_schema_and_oil_only_state(tmp_path):
    f=build(tmp_path)
    assert validate(f,json.loads(Path('usoil/schemas/facts.schema.json').read_text()))==[]
    assert f['state']['reference_price']['provider']=='cnbc'
    assert value(f,'reference_price','fact_id')['value']==100
    assert not ({'etf','stable','derivatives','options','book','dominance'} & f['state'].keys())
    assert value(f,'inventory','change_crude')['value']==-4
    assert value(f,'curve','spread_pct')['value']==pytest.approx(100/99)
    assert value(f,'cot','mm_net_ratio')['value']==pytest.approx(110500/2000000)
    assert value(f,'vol','percentile')['coverage']['observed_count']==252
    for x in f['facts']:
        if x['source_id']=='computed':
            assert x['raw_sha256'] is None and x['input_manifest_sha256']==f['input_manifest_sha256']
        elif x['value'] is not None:
            assert x['raw_sha256']==SHA


@pytest.mark.parametrize('crude,products,state', [(-4,0,'supportive'),(4,0,'adverse'),(-3.999,0,'neutral'),
                                                    (-4,4,'mixed'),(4,-4,'mixed'),(-4,3.999,'supportive')])
def test_inventory_exact_thresholds_and_product_conflict(tmp_path,crude,products,state):
    c=collection();stocks=source(c,'eia_wpsr')['values']['stocks']
    stocks['commercial_crude_ex_spr']['change_wow']=crude
    stocks['gasoline']['change_wow']=products
    f=build(tmp_path,c)
    assert scoring.inventory_component(f)['state']==state


def test_curve_thresholds_conflict_and_dedup(tmp_path):
    f=build(tmp_path)
    assert scoring.inventory_group(f)['state']=='supportive'
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert [x['key'] for x in d['items']]==list(scoring.ITEM_KEYS)
    assert next(x for x in d['items'] if x['key']=='curve_alignment')['status']=='deduplicated'
    curve=value(f,'curve','spread_pct')
    curve['value']=-0.5
    assert scoring.inventory_group(f)['state']=='mixed'
    curve['value']=0.499999
    assert scoring.curve_component(f)['state']=='neutral'
    curve['value']=0.5
    assert scoring.curve_component(f)['state']=='supportive'


def test_weekly_four_weeks_never_invented_and_fresh_single_kept(tmp_path):
    f=build(tmp_path,mode='weekly')
    assert value(f,'inventory','change_4w')['value'] is None
    assert scoring.inventory_component(f,'weekly')['window']=='1w'
    history=History(tmp_path/'history')
    history.append([{'source':'eia','instrument':'wpsr','metric':'crude_change_1w',
                     'observed_at':d+'T00:00:00Z','observation_date':d,'value':v,'raw_sha256':SHA,
                     'available_at':d+'T23:00:00Z'} for d,v in [('2026-10-02',4),('2026-09-25',4),('2026-09-18',4)]],
                   recorded_at='2026-10-10T00:00:00Z')
    f=build(tmp_path,mode='weekly')
    assert value(f,'inventory','change_4w')['value']==8
    part=scoring.inventory_component(f,'weekly')
    assert part['state']=='adverse' and part['window']=='4w'


def test_future_history_arrival_cannot_enter_weekly_window(tmp_path):
    history=History(tmp_path/'history')
    history.append([{'source':'eia','instrument':'wpsr','metric':'crude_change_1w','observed_at':d+'T00:00:00Z',
                     'observation_date':d,'value':-3,'raw_sha256':SHA,'available_at':'2026-10-14T00:00:00Z'}
                    for d in ('2026-10-02','2026-09-25','2026-09-18')],recorded_at='2026-10-14T00:00:00Z')
    assert value(build(tmp_path),'inventory','change_4w')['value'] is None


def test_macro_same_day_real_yield_and_fred_common_date_fallback(tmp_path):
    c=collection();f=build(tmp_path,c)
    assert f['state']['macro']['status']=='supportive'
    assert f['state']['macro']['window']=='same_day_dxy_latest_real10'
    source(c,'cnbc_quotes')['values']['quotes'].pop('.DXY')
    f=build(tmp_path,c)
    assert f['state']['macro']['window']=='daily_common_observation'
    assert f['state']['macro']['common_date']=='2026-10-12'
    assert all('dcoil' not in ident for ident in f['state']['macro']['change_fact_ids'])
    c['collection_completed_at']='2026-10-18T16:00:00Z'
    f=build(tmp_path,c)
    assert f['state']['macro']['status']=='unknown'


def test_cot_candidate_dependent_and_history_minimum(tmp_path):
    c=collection()
    rows=source(c,'cftc_cot')['values']['series']
    rows[-1].update(change_open_interest=100000,mm_net=600000)
    f=build(tmp_path,c)
    assert scoring.cot_item(f,1)['points']==-1
    assert scoring.cot_item(f,-1)['points']==0
    source(c,'cftc_cot')['values']['series']=rows[-104:]
    f=build(tmp_path,c)
    assert value(f,'cot','mm_net_ratio_percentile')['status']=='warming_up'
    assert scoring.cot_item(f,1)['status']=='unassessed'


def test_ovx_directionless_warning_and_warmup(tmp_path):
    c=collection();v=source(c,'cboe_ovx')['values'];v.update(last=60,change_pct=10)
    f=build(tmp_path,c)
    assert scoring.vol_item(f,0)['points']==-1
    assert scoring.vol_item(f,-1)['points']==-1
    v.update(last=32.9997,change_pct=100)
    f2=build(tmp_path,c)
    value(f2,'vol','percentile')['value']=90
    assert scoring.vol_item(f2,1)['points']==0
    source(c,'fred')['values']['series']['OVXCLS']['rows']=source(c,'fred')['values']['series']['OVXCLS']['rows'][-59:]
    assert scoring.vol_item(build(tmp_path,c),1)['status']=='unassessed'


def test_no_ovx_time_cannot_use_receipt_for_freshness(tmp_path):
    c=collection();source(c,'cboe_ovx')['observed_at']=None
    f=build(tmp_path,c)
    assert value(f,'vol','level')['value'] is None
    assert scoring.vol_item(f,1)['status']=='unassessed'


def test_stale_cot_eia_and_ovx_only_display_no_hard_hold(tmp_path):
    f=build(tmp_path)
    for group,fields in (('cot',('mm_net_ratio','mm_net_ratio_percentile','oi_change_pct')),
                         ('inventory',('change_crude',)),('vol',('level','percentile','change_1d'))):
        for field in fields:
            x=value(f,group,field);x.update(status='stale',stale=True)
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert not d['hard_invalid']
    assert d['groups']['inventory_supply']['reason_code']=='curve_only_weak'
    assert 'unassessed_cot_or_vol_max_0.60' in d['caps']


def test_calendar_week_boundary_and_low_eia_stop_window(tmp_path):
    c=collection();f=build(tmp_path,c)
    assert f['state']['calendar']['next_24h']=='ok'
    e=next(e for e in f['state']['events'] if e['id']=='eia.inventories.2026-10-14')
    assert e['importance']=='low' and e['stop_window']['rule_id']=='eia_release_m15_p30'
    f['as_of']='2026-10-14T14:15:00Z'
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert d['trade_gate']['status']=='scheduled_pause'
    f['as_of']='2026-10-14T15:00:00Z'
    assert scoring.evaluate(f,mode='daily',assessments=[])['trade_gate']['status']=='scheduled_pause'
    f['as_of']='2026-10-14T15:00:01Z'
    assert scoring.evaluate(f,mode='daily',assessments=[])['trade_gate']['status']=='clear'
    at=datetime(2026,10,17,16,tzinfo=UTC)
    c=collection(at)
    f=build(tmp_path,c)
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert 'calendar_coverage_unknown' in d['hard_invalid']
    assert d['confidence']==0 and d['bias']==0 and d['trade_gate']['status']=='data_hold'


@pytest.mark.parametrize('category,rule,minutes', [('opec_meeting','opec_meeting_m2h_p6h',(-120,360)),
    ('cot_release','cot_release_m15_p30',(-15,30)),('eia_release','eia_release_m15_p30',(-15,30))])
def test_tentative_stop_windows_do_not_become_confirmed(category,rule,minutes):
    e={'id':'test','category':category,'start_at':stamp(AT),'status':'tentative'}
    w=facts_mod.stop_window(e)
    assert w['rule_id']==rule
    assert (parse_time(w['start_at'])-AT).total_seconds()==minutes[0]*60
    assert (parse_time(w['end_at'])-AT).total_seconds()==minutes[1]*60
    assert e['status']=='tentative'


def test_future_and_invalid_facts_fail_closed(tmp_path):
    f=build(tmp_path)
    value(f,'reference_price','fact_id')['observed_at']=stamp(AT+timedelta(minutes=1))
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert 'future_observation' in d['hard_invalid'] and d['confidence']==0
    value(f,'reference_price','fact_id').update(status='invalid',value=None)
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert 'invalid_fact' in d['hard_invalid'] and d['no_trade']


def test_yahoo_fallback_is_provisional_and_capped_spot_never_reference(tmp_path):
    c=collection();source(c,'cnbc_quotes')['values']['quotes'].pop('@CL.1')
    source(c,'yahoo_ohlc')['values']['symbols']['CL=F']['d1']=[
        {'t':'2026-10-12T04:00:00Z','o':98,'h':101,'l':97,'c':99,'v':1}]
    f=build(tmp_path,c)
    assert f['state']['reference_price']['fallback']
    assert value(f,'reference_price','fact_id')['status']=='provisional'
    assert value(f,'reference_price','fact_id')['value']==99
    assert 'reference_fallback_max_0.60' in scoring.evaluate(f,mode='daily',assessments=[])['caps']
    source(c,'yahoo_ohlc')['values']['symbols']['CL=F']['d1']=[]
    f=build(tmp_path,c)
    assert 'reference_price_unavailable' in scoring.evaluate(f,mode='daily',assessments=[])['hard_invalid']


def test_parent_rules_and_schema_reject_crypto_and_raw_numbers(tmp_path):
    f=build(tmp_path)
    assert 'inventory_direction_flip' in analysis.RULES and 'etf_direction_flip' not in analysis.RULES
    draft=analysis.skeleton('parent-daily-20261013T160000-0000beef',f)
    for field in ('thesis','positioning_summary'):
        assert draft[field]['fact_ids']
    assert validate(draft,analysis.schema())==[]
    problems=[]
    analysis._check_claim({'text':'OVXは25Δ','fact_ids':[],'claim_kind':'hypothesis'},'x',{}, {},problems)
    assert 'x: raw_number_outside_tokens' in problems
    assert not any('btc' in x or 'etf' in x for x in scoring.ITEM_KEYS)


def test_ovx_after_close_previous_equals_current_uses_prior_fred(tmp_path):
    c=collection()
    source(c,'cboe_ovx')['values'].update(last=33,previous_close=33,change_pct=0)
    f=build(tmp_path,c)
    ch=value(f,'vol','change_1d')
    assert ch['value']==10
    assert ch['method_id']=='cboe_level_over_prior_fred_close_minus_one'
    assert len(ch['source_fact_ids'])==2
    value(f,'vol','percentile')['value']=90
    assert scoring.vol_item(f,0)['points']==-1


def test_common_fred_dollar_exact_point_three_threshold(tmp_path):
    c=collection()
    source(c,'cnbc_quotes')['values']['quotes'].pop('.DXY')
    source(c,'fred')['values']['series']['DTWEXBGS']['rows'][-1]['value']=99.7
    source(c,'fred')['values']['series']['DFII10']['rows'][-1]['value']=1.95
    f=build(tmp_path,c)
    assert f['state']['macro']['status']=='supportive'
    assert [facts_mod.index(f)[x]['value'] for x in f['state']['macro']['change_fact_ids']]==[-5.0,-0.3]


def test_three_groups_opposition_hold_expiry_and_curve_only_policy(tmp_path):
    f=build(tmp_path)
    n={'id':'oil-primary','event_cluster_id':'opec-cut','url':'https://www.eia.gov/test',
       'published_at':stamp(AT-timedelta(hours=1)),'code_verification':'primary_body_retrieved',
       'body':{'status':'retrieved'},'title':'Confirmed crude supply reduction'}
    f['state']['news']['items']=[n]
    a={'news_id':'oil-primary','event_cluster_id':'opec-cut','group_id':'oil_specific_event',
       'verification':'primary_confirmed','importance':'high','impact':'supportive','fact_ids':[]}
    d=scoring.evaluate(f,mode='daily',assessments=[a])
    assert d['direction_strength']=='高' and d['bias']==.75 and d['band']=='High'
    a['impact']='adverse'
    d=scoring.evaluate(f,mode='daily',assessments=[a])
    assert d['bias']==0 and d['direction_reason_label']=='材料混在' and d['no_trade']
    a['impact']='supportive'
    value(f,'inventory','change_crude').update(status='stale',stale=True)
    d=scoring.evaluate(f,mode='daily',assessments=[a])
    assert d['groups']['inventory_supply']['reason_code']=='curve_only_weak'
    assert d['direction_strength']=='高' and d['coverage_present']==7
    assert d['band']!='High'
    d=scoring.evaluate(f,mode='daily',assessments=[a],finalize_at=AT+timedelta(hours=4,seconds=1))
    assert d['trade_gate']['status']=='expired' and d['no_trade']


def test_all_groups_unknown_is_hard_invalid_and_geopolitics_not_oil_group(tmp_path):
    f=build(tmp_path)
    f['state']['macro']['status']='unknown'
    for group,field in [('inventory','change_crude'),('curve','spread_pct')]:
        value(f,group,field).update(status='missing',value=None)
    f['state']['news']['status']='missing'
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert 'all_groups_unknown' in d['hard_invalid'] and d['confidence']==0
    f['state']['news'].update(status='ok',items=[{'id':'geo','event_cluster_id':'geo','url':'https://www.eia.gov/test',
        'published_at':stamp(AT),'code_verification':'primary_body_retrieved'}])
    a={'news_id':'geo','event_cluster_id':'geo','group_id':'macro','verification':'primary_confirmed',
       'importance':'high','impact':'supportive','fact_ids':[]}
    assert scoring.event_group(f,[a],'daily',AT,None)['direction']==0


def test_freshness_date_rules_and_ovx_exact_fifteen_minutes(tmp_path):
    c=collection()
    source(c,'cboe_ovx')['observed_at']=stamp(AT-timedelta(minutes=15))
    assert value(build(tmp_path,c),'vol','level')['status']=='ok'
    source(c,'cboe_ovx')['observed_at']=stamp(AT-timedelta(minutes=15,seconds=1))
    assert value(build(tmp_path,c),'vol','level')['status']=='stale'
    source(c,'eia_wpsr')['values']['week_ending']='2026-10-05'
    assert value(build(tmp_path,c),'inventory','change_crude')['status']=='ok'
    source(c,'eia_wpsr')['values']['week_ending']='2026-10-04'
    assert value(build(tmp_path,c),'inventory','change_crude')['status']=='stale'
    rows=source(c,'cftc_cot')['values']['series']
    for row in rows:
        row['report_date']=(datetime.fromisoformat(row['report_date']).date()-timedelta(days=10)).isoformat()
    assert value(build(tmp_path,c),'cot','mm_net_ratio')['status']=='ok'
    for row in rows:
        row['report_date']=(datetime.fromisoformat(row['report_date']).date()-timedelta(days=21)).isoformat()
    f=build(tmp_path,c)
    assert value(f,'cot','mm_net_ratio')['status']=='stale' and f['state']['cot']['publication_stalled']
    assert 'invalid_fact' not in scoring.evaluate(f,mode='daily',assessments=[])['hard_invalid']


def test_previous_week_width_is_derived_from_two_real_level_facts(tmp_path):
    c=collection()
    source(c,'yahoo_ohlc')['values']['symbols']['CL=F']['d1']=[
        {'t':f'2026-10-{d:02d}T04:00:00Z','o':90,'h':100+i,'l':80-i,'c':95,'v':1}
        for i,d in enumerate(range(5,10))]
    f=build(tmp_path,c)
    weekly=f['state']['levels']['weekly_range']
    width=facts_mod.index(f)[weekly['fact_id']]
    assert width['value']==28 and len(width['source_fact_ids'])==2
    assert weekly['position_pct'] is None


def opec_item(text, *, retrieved=True, title='OPEC+ will meet soon'):
    return {'id':'opec-schedule','event_cluster_id':'opec-schedule','title':title,
            'url':'https://oilprice.com/test-schedule','publisher':'OilPrice','official':False,
            'published_at':stamp(AT-timedelta(hours=2)),'first_seen_at':stamp(AT-timedelta(hours=1)),
            'code_verification':'secondary_body_retrieved' if retrieved else 'headline_only',
            'body':{'status':'retrieved' if retrieved else 'unavailable','excerpt':text if retrieved else ''},
            'excerpt':text,'feed_raw_sha256':SHA}


@pytest.mark.parametrize('text', [
    'OPEC+ will meet on 2026-10-14 at 12:00 UTC to discuss production.',
    'OPEC+ is scheduled to convene on October 14, 2026 at 14:00 +02:00.',
    'The JMMC will hold its meeting on 2026-10-14T12:00:00Z.',
])
def test_explicit_opec_schedule_becomes_tentative_window(tmp_path,text):
    c=collection();source(c,'news')['values']['kept']=[opec_item(text)]
    f=build(tmp_path,c)
    e=next(e for e in f['state']['events'] if e['category']=='opec_meeting')
    assert e['start_at']=='2026-10-14T12:00:00Z'
    assert e['start_at'] != e['published_at']
    assert e['status']=='tentative' and e['verified_at'] is None
    assert e['stop_window']['start_at']=='2026-10-14T10:00:00Z'
    assert e['stop_window']['end_at']=='2026-10-14T18:00:00Z'
    assert e['source_url']=='https://oilprice.com/test-schedule'
    assert e['schedule_evidence_quote'] in text
    assert not f['state']['news']['opec_schedule_unconfirmed']
    f['as_of']='2026-10-14T10:00:00Z'
    d=scoring.evaluate(f,mode='daily',assessments=[])
    assert d['trade_gate']['status']=='scheduled_pause'
    assert any(e['id'] in w['event_ids'] for w in d['trade_gate']['active_windows'])


@pytest.mark.parametrize('text,retrieved', [
    ('OPEC+ will meet on 2026-10-14 to discuss production.',True),
    ('OPEC+ will meet on October 14 at 12:00 UTC.',True),
    ('OPEC+ will meet on 2026-10-14 at 12:00.',True),
    ('OPEC+ will meet soon.',True),
    ('OPEC+ will meet on 2026-10-14 at 12:00 UTC.',False),
    ('OPEC+ will meet on 2026-10-14 at 12:00 EST.',True),
    ('OPEC+ will meet on 2026-10-14 at 12:00 UTC and again on 2026-10-15 at 12:00 UTC.',True),
])
def test_untimed_or_ambiguous_opec_lead_keeps_limitation_without_window(tmp_path,text,retrieved):
    c=collection();source(c,'news')['values']['kept']=[opec_item(text,retrieved=retrieved)]
    f=build(tmp_path,c)
    assert not any(e['category']=='opec_meeting' for e in f['state']['events'])
    assert f['state']['news']['opec_schedule_unconfirmed']
    preview=scoring.evaluate(f,mode='daily',assessments=[])
    brief=analysis.briefing(f,preview,edition_id='parent-daily-20261013T160000-0000beef',mode='daily',session_slot='pm')
    assert 'OPEC予定の未確認事項（limitationsへ残す）' in brief
    assert '公表時刻を会合時刻に代用しない' in brief


def test_opec_timestamp_in_publication_header_is_not_schedule(tmp_path):
    c=collection()
    source(c,'news')['values']['kept']=[opec_item(
        'Published 2026-10-13T14:00:00Z. OPEC+ will meet soon to discuss output.')]
    f=build(tmp_path,c)
    assert not f['state']['news']['opec_schedule_detected']
    assert f['state']['news']['opec_schedule_unconfirmed']


@pytest.mark.parametrize('level_kind', ['yahoo','reference'])
def test_unverified_contract_comparison_rejects_destination_even_with_rationale(tmp_path,level_kind):
    f=build(tmp_path)
    ref=value(f,'reference_price','fact_id')
    candidate=copy.deepcopy(ref)
    candidate.update(fact_id='usoil.liquidity.test.named_level',value=105,
                     metric='named_level_price',instrument='cl_continuous' if level_kind=='yahoo' else 'cl_front')
    f['facts'].append(candidate)
    f['state']['levels'].update(comparison_status='unverified',items=[{'fact_id':candidate['fact_id']}])
    choice={'side':'up','kind':'named_level_price','level_fact_ids':[candidate['fact_id']],
            'invalidation_rule_ids':['named_level_reformed'],
            'rationale':{'text':'限月差を了承して選ぶ','fact_ids':[],'claim_kind':'hypothesis'}}
    assert analysis.destination_problems(choice,f,facts_mod.index(f))==['destination: contract_comparison_unverified']
    choice.update(side='none',kind='none',level_fact_ids=[])
    assert analysis.destination_problems(choice,f,facts_mod.index(f))==[]


def test_curve_second_third_key_consistent_when_missing(tmp_path):
    c=collection();source(c,'cnbc_quotes')['values']['quotes'].pop('@CL.3')
    f=build(tmp_path,c)
    assert value(f,'curve','second_third_pct')['value'] is None
    assert 'second_third_spread_pct' not in f['state']['curve']


@pytest.mark.parametrize('text', [
    'OPEC+ will meet soon, article published on 2026-10-14 at 12:00 UTC.',
    'No OPEC+ meeting is scheduled on 2026-10-14 at 12:00 UTC.',
    'OPEC+ meeting scheduled on 2026-10-14 at 12:00 UTC was cancelled.',
])
def test_publication_or_negated_schedule_never_opens_opec_window(tmp_path,text):
    c=collection();source(c,'news')['values']['kept']=[opec_item(text)]
    f=build(tmp_path,c)
    assert not f['state']['news']['opec_schedule_detected']
    assert f['state']['news']['opec_schedule_unconfirmed']
