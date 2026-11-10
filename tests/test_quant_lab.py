import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import app
from quant_lab.lab import monte_carlo, pairs, crossover

def sample_prices(n=650):
    rng=np.random.default_rng(7)
    dates=pd.bdate_range("2022-01-03",periods=n)
    r=rng.normal(.0003,.01,(n,3))
    return pd.DataFrame(100*np.exp(np.cumsum(r,axis=0)),index=dates,columns=["SPY","QQQ","IWM"])

def test_risk_deterministic_and_tail():
    p=sample_prices()
    a=monte_carlo(p,n=500,seed=123)
    assert a==monte_carlo(p,n=500,seed=123)
    assert a["es95_loss_pct"]>=a["var95_loss_pct"]
    assert len(a["symbols"])==3

def test_risk_rejects_invalid_weights():
    with pytest.raises(ValueError):
        monte_carlo(sample_prices(),weights=[.5,.5,.5])

def test_crossover_metrics():
    result=crossover(sample_prices(),ticker="SPY")
    assert result["position_changes"]>=0
    assert "max_drawdown_pct" in result["strategy"]
    assert "annualized_return_pct" in result["buy_and_hold"]

def test_pairs_returns_diagnostics():
    result=pairs(sample_prices(),"SPY","QQQ")
    assert 0<=result["train_pvalue"]<=1
    assert "test_sharpe" in result

def test_quant_validation_no_download():
    client=TestClient(app)
    assert client.get("/api/quant/risk?symbols=SPY,SPY").status_code==422
    assert client.get("/api/quant/pairs?symbols=SPY").status_code==422
    assert client.get("/api/quant/invalid").status_code==404

def test_quant_endpoint_mock(monkeypatch):
    import quant_lab.api as api
    monkeypatch.setattr(api,"compute",lambda project,symbols:{"test":True})
    api._cache.clear()
    response=TestClient(app).get("/api/quant/crossover?symbols=SPY")
    assert response.status_code==200
    assert response.json()["status"]=="HISTORICAL_RESEARCH"
    assert response.json()["data"]["test"] is True
