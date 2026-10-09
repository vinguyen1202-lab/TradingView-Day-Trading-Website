import importlib.util, os, sys, json, time, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# Isolated test DB, no external market feed.
os.environ['MARKETPILOT_DB']=str(Path(tempfile.mkdtemp())/'test.sqlite')
os.environ['TV_WEBHOOK_TOKEN']='testtoken-1234567890-abcdefg-98765'
spec=importlib.util.spec_from_file_location('marketpilot_app',ROOT/'app.py')
a=importlib.util.module_from_spec(spec);sys.modules['marketpilot_app']=a;spec.loader.exec_module(a)
from fastapi.testclient import TestClient
client=TestClient(a.app)

def valid_payload():
    return {'source':'TradingView Pine v6','ticker':'PLTR','exchange':'NASDAQ','interval':'5','bar_time_ms':int(time.time()*1000)-5000,
            'open':101,'high':103,'low':100,'close':102,'volume':100000,'volume_average':80000,
            'daily_ema9':100,'daily_sma50':98,'daily_atr14':4,'prior_high':101,'prior_low':98,'premarket_high':101.2,'premarket_low':99.3,
            'relative_strength_pct':0.6,'bull_level':101.2,'bear_level':98,'stop':100.5,'target':105,
            'underlying_rr':2,'decision':'REVIEW_CALL'}

def test_without_data_wait():
    j=client.get('/api/snapshot/PLTR').json()
    assert j['status']=='NO_TRADINGVIEW_DATA' and j['decision']=='WAIT' and j['bars']==[]

def test_auth_and_signal():
    payload=valid_payload()
    assert client.post('/api/tradingview/bad-token',json=payload).status_code==403
    r=client.post('/api/tradingview/'+os.environ['TV_WEBHOOK_TOKEN'],json=payload)
    assert r.status_code==200,r.text
    j=client.get('/api/snapshot/PLTR').json()
    assert j['latest']['close']==102 and j['latest']['source']=='TradingView Pine v6'
    assert j['bars'][-1]['decision']=='REVIEW_CALL'
    assert j['decision'] in ('WAIT','REVIEW_CALL') # depends on test session hours

def test_no_fake_options_and_invalid_payload():
    j=client.get('/api/snapshot/PLTR').json()
    assert 'options' not in j and 'option_quotes' not in j
    payload=valid_payload();payload['close']=120
    assert client.post('/api/tradingview/'+os.environ['TV_WEBHOOK_TOKEN'],json=payload).status_code==422
