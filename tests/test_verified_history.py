import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ['MARKETPILOT_DB']=str(Path(tempfile.mkdtemp())/'actual_history_tests.sqlite')
spec=importlib.util.spec_from_file_location('marketpilot_live_test',ROOT/'app.py')
appmodule=importlib.util.module_from_spec(spec)
sys.modules['marketpilot_live_test']=appmodule
spec.loader.exec_module(appmodule)
from fastapi.testclient import TestClient
client=TestClient(appmodule.app)

def test_verified_history_is_not_a_live_signal():
    r=client.get('/api/verified-history/PLTR')
    assert r.status_code==200
    data=r.json()
    assert data['source'].startswith('TradingView MCP')
    assert data['decision']=='WAIT'
    assert data['capture_date']=='2026-10-09'
    assert data['quote']['close']==209.05
    assert len(data['bars'])==25
    assert data['bars'][-1]['bar_time_ms']==1791575700000
    assert all(x['decision']=='WAIT' for x in data['bars'])

def test_custom_ticker_missing_historical_is_not_fabricated():
    data=client.get('/api/verified-history/GOOGL').json()
    assert data['status']=='NO_HISTORICAL_DATA'
    assert data['quote'] is None
    assert data['bars']==[]
    assert data['decision']=='WAIT'

def test_health_and_html():
    assert client.get('/health').json()['ok']
    text=client.get('/').text
    assert 'Verified TradingView historical reference' in text
    assert 'NOT A LIVE QUOTE' in text
    assert 'render' in (ROOT/'render.yaml').read_text().lower() or 'uvicorn' in (ROOT/'render.yaml').read_text().lower()

def test_no_token_in_frontend():
    html=(ROOT/'static/index.html').read_text()
    assert 'testtoken-1234567890' not in html
    assert 'TV_WEBHOOK_TOKEN=' not in html
