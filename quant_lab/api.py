"""Historical-only Quant Lab API. No orders, no live-signal assertions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
import time
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/quant", tags=["quant-research"])
_pool = ThreadPoolExecutor(max_workers=2)
_cache = {}
_lock = Lock()
TTL = 900
UNIVERSE = {"SPY", "QQQ", "IWM", "TLT", "GLD", "KO", "PEP", "DIA", "XLK", "XLF"}

def compute(project, symbols):
    from .lab import prices, monte_carlo, pairs, crossover
    close = prices(list(symbols))
    if project == "risk":
        return {"base": monte_carlo(close), "stress": monte_carlo(close, correlation_stress=.2)}
    if project == "pairs":
        return pairs(close, symbols[0], symbols[1])
    return crossover(close, symbols[0])

@router.get("/{project}")
def research(project: str, symbols: str = Query("SPY,QQQ,IWM,TLT,GLD")):
    if project not in {"risk", "pairs", "crossover"}:
        raise HTTPException(404, "Unknown research project")
    tickers = tuple(s.strip().upper() for s in symbols.split(","))
    expected = (2, 5) if project == "risk" else (2, 2) if project == "pairs" else (1, 1)
    if not expected[0] <= len(tickers) <= expected[1] or len(set(tickers)) != len(tickers) or any(t not in UNIVERSE for t in tickers):
        raise HTTPException(422, "Select distinct supported symbols; risk 2–5, pairs 2, crossover 1")
    key = (project, tickers)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic()-cached[0] < TTL:
            return {**cached[1], "cache": "hit"}
    # Bound waiting time; execution continues in worker and is cached on next successful request.
    future = _pool.submit(compute, project, tickers)
    try:
        data = future.result(timeout=35)
    except TimeoutError:
        raise HTTPException(503, "Historical data provider timed out; retry later")
    except Exception:
        raise HTTPException(503, "Historical research data unavailable; no result calculated")
    result = {"project":project, "symbols":list(tickers), "status":"HISTORICAL_RESEARCH",
              "calculated_at":datetime.now(timezone.utc).isoformat(),
              "data":data, "notice":"Not live market data, a trading signal, or an option quote."}
    with _lock:
        if len(_cache)>40: _cache.clear()
        _cache[key]=(time.monotonic(),result)
    return {**result,"cache":"miss"}
