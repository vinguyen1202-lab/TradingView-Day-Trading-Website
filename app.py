"""TradingView alert relay for paper-trading research only.

No scraped TradingView data, no fictitious quotes, no live broker/order integration.
Trusted TradingView Pine JSON arrives via a private webhook token; public readers see
only the most recent confirmed five-minute TradingView-sourced snapshots.
"""
import hmac
import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent
VERIFIED_HISTORY = BASE / "static" / "verified_history.json"
DB = Path(os.getenv("MARKETPILOT_DB", str(BASE / "marketpilot_signals.sqlite")))
TOKEN = os.getenv("TV_WEBHOOK_TOKEN", "")
PT = ZoneInfo("America/Los_Angeles")
TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
KEYS = ("open", "high", "low", "close", "volume", "volume_average", "daily_ema9", "daily_sma50", "daily_atr14", "prior_high", "prior_low", "premarket_high", "premarket_low", "relative_strength_pct", "bull_level", "bear_level", "stop", "target", "underlying_rr")
app = FastAPI(title="MarketPilot — Real TradingView Alert Relay", docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=str(BASE/'static')), name='static')


def db_conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=8)
    conn.execute("CREATE TABLE IF NOT EXISTS snapshots (ticker TEXT NOT NULL, bar_time_ms INTEGER NOT NULL, received_at_ms INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(ticker,bar_time_ms))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(received_at_ms)")
    return conn


def parse_payload(obj):
    if not isinstance(obj, dict):
        raise ValueError("Object required")
    ticker = str(obj.get('ticker', '')).strip().upper()
    if not TICKER.fullmatch(ticker):
        raise ValueError("Ticker invalid")
    if obj.get('source') != 'TradingView Pine v6' or str(obj.get('interval')) != '5':
        raise ValueError('TradingView 5m indicator payload required')
    label = obj.get('decision')
    if label not in ('WAIT','REVIEW_CALL','REVIEW_PUT'):
        raise ValueError('Unexpected signal label')
    bar_time_ms = obj.get('bar_time_ms')
    if isinstance(bar_time_ms, bool) or not isinstance(bar_time_ms, int) or bar_time_ms < 1500000000000:
        raise ValueError('Valid bar time in milliseconds required')
    now = int(time.time()*1000)
    if bar_time_ms > now + 120_000:
        raise ValueError('Future candle time is not allowed')
    clean = {'source':'TradingView Pine v6','ticker':ticker,'exchange':str(obj.get('exchange',''))[:20],
             'interval':'5','bar_time_ms':bar_time_ms,'decision':label}
    for key in KEYS:
        value=obj.get(key)
        if value is None:
            clean[key]=None
        elif isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value):
            raise ValueError(f'Invalid numeric field {key}')
        else:
            clean[key]=float(value)
    if not all(clean.get(k) is not None and clean[k] > 0 for k in ('open','high','low','close')):
        raise ValueError('Valid OHLC required')
    if clean['high'] < max(clean['open'],clean['close'],clean['low']) or clean['low'] > min(clean['open'],clean['close'],clean['high']):
        raise ValueError('OHLC inconsistent')
    if clean['volume'] is not None and clean['volume'] < 0:
        raise ValueError('Negative volume')
    if label != 'WAIT':
        if any(clean.get(k) is None for k in ('stop','target','underlying_rr')):
            raise ValueError('Signal requires stop, target and R/R')
        if clean['underlying_rr'] < 1.5:
            raise ValueError('Insufficient underlying reward/risk')
        if label == 'REVIEW_CALL' and not (clean['stop'] < clean['close'] < clean['target']):
            raise ValueError('Bullish stop/target invalid')
        if label == 'REVIEW_PUT' and not (clean['target'] < clean['close'] < clean['stop']):
            raise ValueError('Bearish stop/target invalid')
    return clean


@app.get('/')
async def home():
    return FileResponse(BASE / 'static' / 'index.html', headers={'Cache-Control':'no-store'})


@app.get('/health')
async def health():
    return {'ok': True, 'service':'MarketPilot paper-research bridge'}


@app.get('/api/verified-history/{ticker}')
async def verified_history(ticker: str):
    """Pinned genuine TradingView MCP historical dataset; NEVER a live signal."""
    ticker = ticker.strip().upper()
    if not TICKER.fullmatch(ticker):
        raise HTTPException(400, 'Invalid ticker')
    original = json.loads(VERIFIED_HISTORY.read_text())
    q = original['quotes'].get(ticker)
    bars = original['bars'].get(ticker, [])
    return {'ticker': ticker, 'source': original['source'],
            'status': 'HISTORICAL_ONLY' if q else 'NO_HISTORICAL_DATA',
            'decision':'WAIT', 'quote': q, 'bars': bars,
            'capture_date': original['capture_date'],
            'note':'Verified historical TradingView data from October 9, 2026, NOT a live entry, option quote, or current stock price.',
            'exchange_delay_notice':original['exchange_delay_notice'],
            'bar_time_note':original['bar_time_note']}



@app.post('/api/tradingview/{secret}')
async def push_from_tradingview(secret: str, request: Request):
    if not TOKEN or len(TOKEN) < 24 or not hmac.compare_digest(secret, TOKEN):
        raise HTTPException(403, 'Forbidden')
    if int(request.headers.get('content-length', '0') or 0) > 8192:
        raise HTTPException(413, 'Too large')
    try:
        raw = await request.body()
        if len(raw) > 8192:
            raise ValueError('Too large')
        clean = parse_payload(json.loads(raw))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc))
    now = int(time.time()*1000)
    with db_conn() as conn:
        conn.execute('INSERT INTO snapshots(ticker,bar_time_ms,received_at_ms,payload) VALUES (?,?,?,?) '
                     'ON CONFLICT(ticker,bar_time_ms) DO UPDATE SET received_at_ms=excluded.received_at_ms,payload=excluded.payload',
                     (clean['ticker'], clean['bar_time_ms'], now, json.dumps(clean, separators=(',',':'))))
        conn.execute('DELETE FROM snapshots WHERE bar_time_ms < ?', (now - 20*86400*1000,))
        conn.commit()
    return {'received':True,'ticker':clean['ticker'],'decision':clean['decision']}


@app.get('/api/status')
async def status():
    with db_conn() as conn:
        count=conn.execute('SELECT COUNT(DISTINCT ticker) FROM snapshots').fetchone()[0]
    return {'mode':'REAL TRADINGVIEW ALERTS ONLY','simulation':False,'received_tickers':count,
            'bridge_configured':len(TOKEN)>=24,
            'note':'TradingView embedded widgets cannot supply data to code. No option quotes are fetched.'}


@app.get('/api/tickers')
async def tickers():
    with db_conn() as conn:
        rows=conn.execute('SELECT ticker,MAX(bar_time_ms) FROM snapshots GROUP BY ticker ORDER BY ticker').fetchall()
    return {'tickers':[{'ticker':ticker,'last_bar_ms':ms} for ticker,ms in rows]}


@app.get('/api/snapshot/{ticker}')
async def snapshot(ticker: str):
    ticker=ticker.strip().upper()
    if not TICKER.fullmatch(ticker):
        raise HTTPException(400,'Invalid ticker')
    with db_conn() as conn:
        rows=conn.execute('SELECT payload,received_at_ms FROM snapshots WHERE ticker=? ORDER BY bar_time_ms DESC LIMIT 150',(ticker,)).fetchall()
    if not rows:
        return {'ticker':ticker,'status':'NO_TRADINGVIEW_DATA','latest':None,'bars':[],
                'decision':'WAIT','message':'No completed 5-minute TradingView alerts received for this ticker.'}
    records=[dict(json.loads(row[0]),received_at_ms=row[1]) for row in reversed(rows)]
    last=records[-1]
    now_ms=int(time.time()*1000)
    dt=datetime.now(PT)
    market_open=(dt.weekday()<5 and (dt.hour*60+dt.minute)>=390 and (dt.hour*60+dt.minute)<780)
    stale=(now_ms-last['bar_time_ms']>9*60_000)
    if stale or not market_open:
        decision='WAIT'
        msg='Market closed or TradingView signal stale. Do not treat an older alert as a current entry.'
    else:
        decision=last['decision']
        msg='Confirmed five-minute TradingView signal; check executable option quotes separately.'
    return {'ticker':ticker,'status':'STALE_OR_CLOSED' if stale or not market_open else 'FRESH_TRADINGVIEW_ALERT',
            'decision':decision,'latest':last,'bars':records[-90:],'message':msg,
            'age_seconds':round((now_ms-last['bar_time_ms'])/1000), 'market_open':market_open}
