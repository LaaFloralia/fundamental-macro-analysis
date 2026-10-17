"""Explicitly synthetic oil collection; no copied BTC prices or live observations."""
from datetime import datetime, timedelta, timezone
import hashlib
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
AS_OF = datetime(2026, 10, 15, 15, tzinfo=UTC)


def iso(t):
    return t.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def record(source_id, values, observed_at=None):
    raw = hashlib.sha256(('SYNTHETIC ' + source_id).encode()).hexdigest()
    values = dict(values)
    values.setdefault('timestamp_quality', 'source_observed' if observed_at else 'source_date')
    values.setdefault('retrieval_started_at', iso(AS_OF - timedelta(seconds=20)))
    values.setdefault('raw_parts', {'r0': raw})
    return {'source_id': source_id, 'status': 'ok', 'retrieved_at': iso(AS_OF),
            'observed_at': observed_at, 'published_at': None, 'source_url': 'https://example.org/' + source_id,
            'raw_sha256': [raw], 'attempts': 1, 'elapsed_ms': 1, 'notes': [], 'values': values}


def news_item(ident='n1', *, official=True, verified=True):
    return {'id': ident, 'event_cluster_id': 'c-' + ident, 'title': 'Synthetic EIA petroleum supply update',
            'url': 'https://www.eia.gov/todayinenergy/detail.php?id=' + ident,
            'publisher': 'EIA' if official else 'OilPrice', 'official': official,
            'published_at': iso(AS_OF - timedelta(hours=1)), 'first_seen_at': iso(AS_OF),
            'code_verification': ('primary_body_retrieved' if official else 'secondary_body_retrieved')
                                 if verified else 'headline_only',
            'body': {'status': 'retrieved', 'raw_sha256': 'a'*64, 'body_length': 200,
                     'text_excerpt': 'Synthetic EIA petroleum supply update confirms a stock draw.',
                     'title_overlap': 1.0} if verified else {'status': 'not_attempted'},
            'excerpt': 'Synthetic EIA petroleum supply update', 'p': None, 'reaction': None,
            'feed_raw_sha256': 'b'*64}


def load_collection():
    quotes = {}
    at = iso(AS_OF - timedelta(minutes=1))
    for sym, last, prev in [('@CL.1',100,99),('@CL.2',98,97),('@CL.3',97,96),
                            ('@BZ.1',105,104),('@BZ.2',103,102),('@NG.1',3,3.1),
                            ('.DXY',100,101),('US2Y',4.5,4.6),('US10Y',5,5.1)]:
        quotes[sym] = {'last':last,'previous_close':prev,'change':last-prev,
                       'change_pct':(last/prev-1)*100,'last_time':at,'time_kind':'quote_time',
                       'open':prev,'high':max(last,prev)+1,'low':min(last,prev)-1,'volume':1000,
                       'open_interest':100000,'expiration_date':'2026-11-20' if sym.startswith('@') else None,
                       'unit':'USD/bbl' if sym.startswith('@CL') or sym.startswith('@BZ') else 'index'}
    cnbc=record('cnbc_quotes',{'quotes':quotes,'missing':[],'failed':[]},at)
    d1=[]
    day=datetime(2026,7,1,tzinfo=NY)
    while day.date()<AS_OF.astimezone(NY).date():
        if day.weekday()<5:
            d1.append({'t':iso(day),'o':99,'h':102,'l':98,'c':100,'v':1000})
        day+=timedelta(days=1)
    h1=[]
    hour=AS_OF-timedelta(days=5)
    while hour<AS_OF:
        local=hour.astimezone(NY)
        if local.weekday()<5 and local.hour!=17:
            h1.append({'t':iso(hour),'o':100,'h':101,'l':99,'c':100,'v':100})
        hour+=timedelta(hours=1)
    yahoo=record('yahoo_ohlc',{'symbols':{'CL=F':{'d1':d1,'h1':h1,'meta':{'symbol':'CL=F'}}},
                                 'invalid_bars':{},'failures':[], 'roll_note':'SYNTHETIC continuous futures'},iso(AS_OF))
    yahoo['values']['raw_parts']={'cl_d1':'c'*64,'cl_h1':'d'*64}
    stocks={}
    for key,current,prev in [('commercial_crude_ex_spr',420,425),('gasoline',210,208),
                              ('distillate',105,106),('spr',300,300)]:
        stocks[key]={'current':current,'week_ago':prev,'change_wow':current-prev,
                     'change_pct_wow':(current/prev-1)*100,'year_ago':prev,'change_yoy':current-prev,
                     'change_pct_yoy':(current/prev-1)*100}
    eia=record('eia_wpsr',{'week_ending':'2026-10-09','week_ago':'2026-10-02','year_ago':'2025-10-10',
                           'stocks':stocks,'flows':{k:{'current':v} for k,v in
                            [('production',13000),('imports',6000),('exports',4000),('refinery_input',16000),
                             ('products_supplied',20000)]},'missing':[],
                           'units':{'stocks':'million barrels','flows':'thousand barrels per day'}})
    series=[]
    for i in range(156):
        day=datetime(2026,10,13,tzinfo=UTC)-timedelta(weeks=155-i)
        series.append({'report_date':day.date().isoformat(),'mm_long':170000+i*10,'mm_short':130000,
                       'mm_net':40000+i*10,'open_interest':1900000,'change_open_interest':10000,
                       'change_mm_long':10,'change_mm_short':0})
    cot=record('cftc_cot',{'contract':{'code':'067651','report_type':'FutOnly','market':'WTI-PHYSICAL - NYMEX'},
                           'series':series,'latest':series[-1],'count':156})
    days=[]
    day=datetime(2025,7,1,tzinfo=UTC)
    while day.date()<=datetime(2026,10,14).date():
        if day.weekday()<5:days.append(day.date().isoformat())
        day+=timedelta(days=1)
    fseries={}
    for sid,values in [('DTWEXBGS',[101,100]),('DFII10',[2.1,2.0]),('OVXCLS',[49,50]),
                       ('VIXCLS',[20,21]),('SP500',[6000,6010]),('DGS2',[4.6,4.5]),
                       ('T10YIE',[2.3,2.4]),('BAMLH0A0HYM2',[3.2,3.1])]:
        rows=[{'date':d,'value':45+(i%20)/10} for i,d in enumerate(days)] if sid=='OVXCLS' else []
        rows=rows[:-2] if rows else []
        rows += [{'date':d,'value':v} for d,v in zip(days[-2:],values)]
        fseries[sid]={'rows':rows,'frequency':'daily','unit':'index','label':sid,'raw':'e'*64}
    fred=record('fred',{'series':fseries,'failed':[]})
    vol=record('cboe_ovx',{'last':50,'previous_close':49,'change':1,'change_pct':100/49},at)
    ff=record('forexfactory_week',{'events':[
        {'title':'Crude Oil Inventories','start_at':'2026-10-14T14:30:00Z','importance':'low',
         'forecast':'0.2M','previous':'-3.2M'},
        {'title':'Retail Sales m/m','start_at':'2026-10-16T12:30:00Z','importance':'high',
         'forecast':'0.2%','previous':'0.1%'}]})
    news=record('news',{'kept':[news_item(),news_item('n2',official=False),news_item('n3',verified=False)],
                        'feeds':[], 'feeds_ok':{'general':3,'official':2},'selection':None,
                        'selection_rule':'synthetic','candidate_count':3,
                        'window_start':iso(AS_OF-timedelta(hours=36)),'window_end':iso(AS_OF),'lookback_hours':36})
    return {'symbol':'USOIL','mode':'daily','collector_version':'synthetic-usoil',
            'collection_started_at':iso(AS_OF-timedelta(seconds=30)), 'collection_completed_at':iso(AS_OF),
            'sources':[cnbc,yahoo,eia,cot,fred,vol,ff,news],
            'terms_restricted':[{'source_id':'cme_group','status':'terms_restricted','reason':'not_used_by_terms'}]}
