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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
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



ALPACA_API_KEY = os.getenv("ALPACA_API_KEY_ID", os.getenv("APCA_API_KEY_ID", os.getenv("ALPACA_API_KEY", ""))).strip()
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET_KEY", os.getenv("APCA_API_SECRET_KEY", os.getenv("ALPACA_SECRET_KEY", ""))).strip()
ALPACA_FEED = os.getenv("ALPACA_OPTIONS_FEED", "indicative").strip().lower()
ALPACA_CACHE = {}
ALPACA_CACHE_SECONDS = 15


def alpaca_credentials():
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        raise HTTPException(503, "Alpaca market data is not configured. Add the API key and secret to the Render service environment.")
    if ALPACA_FEED not in ("indicative", "opra"):
        raise HTTPException(503, "ALPACA_OPTIONS_FEED must be indicative or opra.")
    return ALPACA_FEED


def alpaca_get(url, params):
    query = urlencode({k: v for k, v in params.items() if v is not None})
    request = URLRequest(url + ("?" + query if query else ""), headers={
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_API_SECRET,
        "Accept": "application/json",
        "User-Agent": "MarketPilot-option-chain/1.0",
    })
    try:
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 401:
            message = "Alpaca rejected the API credentials. Check the key and secret saved in Render."
        elif exc.code == 403:
            message = "Alpaca denied this options data feed. Check that your account has access to the configured feed."
        elif exc.code == 429:
            message = "Alpaca rate limit reached. Wait briefly and refresh."
        elif exc.code == 404:
            message = "Alpaca returned no matching options data for this ticker."
        else:
            message = f"Alpaca market-data request failed (HTTP {exc.code})."
        raise HTTPException(502, message)
    except (URLError, TimeoutError):
        raise HTTPException(502, "Could not reach Alpaca market data. Try again shortly.")
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, "Alpaca returned an unreadable market-data response.")


def alpaca_contracts(ticker, start_date, end_date, option_type=None):
    rows = []
    page_token = None
    for _ in range(5):
        params = {
            "underlying_symbols": ticker, "status": "active",
            "expiration_date_gte": start_date, "expiration_date_lte": end_date,
            "type": option_type, "limit": 10000, "page_token": page_token,
        }
        payload = alpaca_get("https://paper-api.alpaca.markets/v2/options/contracts", params)
        rows.extend(payload.get("option_contracts", []))
        page_token = payload.get("next_page_token") or payload.get("page_token")
        if not page_token:
            break
    return rows


def alpaca_option_snapshots(ticker, expiration, option_type, feed):
    rows = []
    page_token = None
    for _ in range(10):
        payload = alpaca_get(
            f"https://data.alpaca.markets/v1beta1/options/snapshots/{ticker}",
            {"feed": feed, "expiration_date": expiration, "type": option_type,
             "limit": 1000, "page_token": page_token},
        )
        snapshots = payload.get("snapshots", {})
        if isinstance(snapshots, dict):
            rows.extend({"symbol": symbol, **(value or {})} for symbol, value in snapshots.items())
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    return rows


@app.get("/api/options/{ticker}/expirations")
def option_expirations(ticker: str):
    ticker = ticker.strip().upper()
    if not TICKER.fullmatch(ticker):
        raise HTTPException(400, "Invalid ticker")
    feed = alpaca_credentials()
    today = datetime.now(PT).date()
    start = today.isoformat()
    end = (today.replace() + timedelta(days=21)).isoformat()
    cache_key = ("expirations", ticker, feed)
    cached = ALPACA_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < ALPACA_CACHE_SECONDS:
        return cached[1]
    contracts = alpaca_contracts(ticker, start, end)
    expirations = sorted({str(c.get("expiration_date")) for c in contracts if c.get("expiration_date")})
    result = {"ticker": ticker, "feed": feed, "expirations": expirations,
              "note": "Indicative options feed: trades are delayed and quotes are modified." if feed == "indicative" else "OPRA options feed.",
              "open_interest_as_of": max((str(c.get("open_interest_date")) for c in contracts if c.get("open_interest_date")), default=None)}
    if len(ALPACA_CACHE) > 200: ALPACA_CACHE.clear()
    ALPACA_CACHE[cache_key] = (time.monotonic(), result)
    return result


@app.get("/api/options/{ticker}/chain")
def option_chain(ticker: str, expiration_date: str, option_type: str = "call"):
    ticker = ticker.strip().upper()
    if not TICKER.fullmatch(ticker):
        raise HTTPException(400, "Invalid ticker")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiration_date):
        raise HTTPException(400, "Expiration date must be YYYY-MM-DD")
    option_type = option_type.strip().lower()
    if option_type not in ("call", "put"):
        raise HTTPException(400, "Option type must be call or put")
    feed = alpaca_credentials()
    try:
        expiration = datetime.strptime(expiration_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Invalid expiration date")
    today = datetime.now(PT).date()
    if expiration < today or (expiration - today).days > 60:
        raise HTTPException(400, "Expiration must be within the next 60 days")
    cache_key = ("chain", ticker, expiration_date, option_type, feed)
    cached = ALPACA_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < ALPACA_CACHE_SECONDS:
        return cached[1]
    contracts = alpaca_contracts(ticker, expiration_date, expiration_date, option_type)
    snapshots = alpaca_option_snapshots(ticker, expiration_date, option_type, feed)
    snapshot_by_symbol = {row["symbol"]: row for row in snapshots}
    chain = []
    for contract in contracts:
        symbol = contract.get("symbol", "")
        snap = snapshot_by_symbol.get(symbol, {})
        quote = snap.get("latestQuote") or {}
        greeks = snap.get("greeks") or {}
        daily = snap.get("dailyBar") or {}
        chain.append({
            "symbol": symbol, "underlying": ticker, "type": option_type,
            "expiration": contract.get("expiration_date"),
            "strike": float(contract.get("strike_price") or 0),
            "bid": quote.get("bp"), "ask": quote.get("ap"),
            "quote_timestamp": quote.get("t"),
            "bid_size": quote.get("bs"), "ask_size": quote.get("as"),
            "delta": greeks.get("delta"), "implied_volatility": snap.get("impliedVolatility"),
            "volume": daily.get("v", 0),
            "open_interest": int(float(contract.get("open_interest") or 0)),
            "open_interest_date": contract.get("open_interest_date"),
        })
    result = {"ticker": ticker, "expiration": expiration_date, "type": option_type,
              "feed": feed, "quote_count": len(snapshots), "contracts_count": len(chain),
              "note": "Indicative options feed: trades are delayed and quotes are modified." if feed == "indicative" else "OPRA options feed.",
              "contracts": chain}
    ALPACA_CACHE[cache_key] = (time.monotonic(), result)
    return result



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


@app.get('/api/stock-quote/{ticker}')
def stock_quote(ticker: str):
    """Read-only Alpaca stock snapshot; does not imply a TradingView signal."""
    ticker=ticker.strip().upper()
    if not TICKER.fullmatch(ticker):
        raise HTTPException(400, "Invalid ticker")
    if not ALPACA_API_KEY or not ALPACA_API_SECRET:
        raise HTTPException(503, "Alpaca stock data is not configured in Render.")
    # IEX is the basic entitlement; other exchanges may not be represented.
    data=alpaca_get(f"https://data.alpaca.markets/v2/stocks/{ticker}/snapshot", {"feed":"iex"})
    trade=data.get("latestTrade") or {}
    minute=data.get("minuteBar") or {}
    daily=data.get("dailyBar") or {}
    previous=data.get("prevDailyBar") or {}
    price=trade.get("p") or minute.get("c")
    return {"ticker":ticker,"feed":"iex","price":price,
            "trade_time":trade.get("t") or minute.get("t"),
            "daily_open":daily.get("o"),"daily_high":daily.get("h"),
            "daily_low":daily.get("l"),"daily_volume":daily.get("v"),
            "previous_close":previous.get("c"),
            "source":"Alpaca IEX stock snapshot; not a full-market NBBO or a confirmed trade signal"}

@app.get('/api/status')
async def status():
    with db_conn() as conn:
        count=conn.execute('SELECT COUNT(DISTINCT ticker) FROM snapshots').fetchone()[0]
    return {'mode':'REAL TRADINGVIEW ALERTS ONLY','simulation':False,'received_tickers':count,
            'bridge_configured':len(TOKEN)>=24,
            'alpaca_options_configured':bool(ALPACA_API_KEY and ALPACA_API_SECRET),
            'alpaca_options_feed':ALPACA_FEED if ALPACA_API_KEY and ALPACA_API_SECRET else None,
            'note':'TradingView widgets do not feed Pine signals; Alpaca options endpoints provide read-only snapshots when configured.'}


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
