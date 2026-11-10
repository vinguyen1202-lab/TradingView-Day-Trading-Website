"""MarketPilot Quant Lab: reproducible research, never live trading advice."""
import argparse
import json
import numpy as np
import pandas as pd

def prices(tickers, period="5y"):
    import yfinance as yf
    raw = yf.download(tickers, period=period, auto_adjust=True, progress=False)
    close = raw["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    close = close.reindex(columns=tickers).dropna()
    if len(close) < 252:
        raise ValueError("Insufficient overlapping price history")
    return close

def monte_carlo(close, weights=None, n=10000, days=21, seed=42, correlation_stress=0):
    returns = close.pct_change().dropna()
    k = returns.shape[1]
    w = np.ones(k)/k if weights is None else np.asarray(weights, dtype=float)
    if len(w) != k or (w < 0).any() or not np.isclose(w.sum(), 1):
        raise ValueError("Weights must be nonnegative and sum to 1")
    mu = returns.mean().to_numpy()
    vol = returns.std().to_numpy()
    corr = returns.corr().to_numpy()
    # Stress all off-diagonal correlations toward +1; preserves PSD.
    if not 0 <= correlation_stress <= 1:
        raise ValueError("correlation_stress must be in [0,1]")
    corr = (1-correlation_stress)*corr + correlation_stress*np.ones_like(corr)
    covariance = np.outer(vol,vol)*corr
    chol = np.linalg.cholesky(covariance + np.eye(k)*1e-14)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n,days,k))
    simulated = mu + z @ chol.T
    # Fixed initial weights; portfolio rebalanced to target daily.
    monthly = np.prod(1 + simulated @ w, axis=1) - 1
    q = float(np.quantile(monthly, .05))
    return {"symbols":list(close.columns), "observations":len(returns),
            "weights":w.tolist(),"days":days,"simulations":n,
            "var95_loss_pct":round(-100*q,3),
            "es95_loss_pct":round(-100*float(monthly[monthly<=q].mean()),3),
            "median_return_pct":round(100*float(np.median(monthly)),3),
            "probability_of_loss_pct":round(100*float(np.mean(monthly<0)),3),
            "correlation_stress":correlation_stress,
            "correlation":pd.DataFrame(corr,index=close.columns,columns=close.columns).round(3).to_dict()}

def pairs(close, a, b, train_fraction=.7, window=30, entry=2, exit_z=.5, stop=3.5, cost_bps=10):
    from statsmodels.tsa.stattools import coint
    if a==b or a not in close or b not in close:
        raise ValueError("Choose two distinct available tickers")
    x=np.log(close[b]); y=np.log(close[a]); cut=int(len(close)*train_fraction)
    if cut < 100 or len(close)-cut < window+5:
        raise ValueError("Not enough train/test data")
    pvalue=float(coint(y.iloc[:cut],x.iloc[:cut])[1])
    beta=float(np.polyfit(x.iloc[:cut],y.iloc[:cut],1)[0])
    alpha=float(y.iloc[:cut].mean()-beta*x.iloc[:cut].mean())
    spread=y-alpha-beta*x
    # All rolling statistics lagged to avoid using the observation being scored.
    mean=spread.rolling(window).mean().shift(1)
    std=spread.rolling(window).std().shift(1)
    z=((spread-mean)/std.replace(0,np.nan)).replace([np.inf,-np.inf],np.nan)
    lag=spread.iloc[:cut].shift(1)
    delta=spread.iloc[:cut].diff()
    valid=pd.concat([lag,delta],axis=1).dropna()
    slope=float(np.polyfit(valid.iloc[:,0],valid.iloc[:,1],1)[0])
    half_life=float(-np.log(2)/np.log(1+slope)) if -1<slope<0 else None
    # Trade next close after observed z signal; test set only.
    position=0; desired=[]
    for value in z:
        if not np.isfinite(value):
            desired.append(0); continue
        if position and (abs(value)<exit_z or abs(value)>stop):
            position=0
        elif not position and abs(value)>=entry and abs(value)<=stop and pvalue<.05:
            position=-1 if value>0 else 1
        desired.append(position)
    pos=pd.Series(desired,index=close.index).shift(1).fillna(0)
    # Approximate log-price spread return on dollar-neutral notional, scaled by gross exposure.
    ret=(pos*(y.diff()-beta*x.diff())/(1+abs(beta))).fillna(0)
    turnover=pos.diff().abs().fillna(pos.abs())
    net=ret-turnover*cost_bps/10000
    test=net.iloc[cut:]
    return {"pair":[a,b],"train_pvalue":round(pvalue,6),"cointegrated_train":pvalue<.05,
            "hedge_beta":round(beta,4),"half_life_days":None if half_life is None else round(half_life,2),
            "test_trades":int((turnover.iloc[cut:]>0).sum()),
            "test_total_log_return_pct":round(100*float(test.sum()),3),
            "test_sharpe":sharpe(test),"test_max_drawdown_pct":drawdown(test,log_returns=True),
            "note":"Research approximation: hedge ratios and execution costs require broker-specific sizing."}

def sharpe(r):
    sd=float(r.std())
    return round(float(np.sqrt(252)*r.mean()/sd),3) if sd>0 else None

def drawdown(r,log_returns=False):
    equity=np.exp(r.cumsum()) if log_returns else (1+r).cumprod()
    return round(100*float((equity/equity.cummax()-1).min()),3)

def crossover(close, ticker="SPY", fast=50, slow=200, cost_bps=10):
    p=close[ticker].dropna()
    if len(p)<slow+100: raise ValueError("Insufficient data")
    sma_fast=p.rolling(fast).mean(); sma_slow=p.rolling(slow).mean()
    desired=(sma_fast>sma_slow).astype(float)
    # Signal at close t, position active from close t+1 onward.
    position=desired.shift(1).fillna(0)
    r=p.pct_change().fillna(0)
    turnover=position.diff().abs().fillna(position.abs())
    strategy=(position*r-turnover*cost_bps/10000).iloc[slow:]
    hold=r.iloc[slow:]
    def metrics(series):
        equity=(1+series).cumprod()
        years=len(series)/252
        return {"total_return_pct":round(100*float(equity.iloc[-1]-1),3),
                "annualized_return_pct":round(100*float(equity.iloc[-1]**(1/years)-1),3),
                "max_drawdown_pct":drawdown(series),"sharpe":sharpe(series)}
    return {"ticker":ticker,"fast":fast,"slow":slow,"cost_bps":cost_bps,
            "execution":"signal close t; position from next close-to-close period",
            "strategy":metrics(strategy),"buy_and_hold":metrics(hold),
            "position_changes":int((turnover.iloc[slow:]>0).sum())}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("project",choices=["risk","pairs","crossover","all"])
    parser.add_argument("--tickers",nargs="+",default=["SPY","QQQ","IWM","TLT","GLD"])
    parser.add_argument("--period",default="5y")
    args=parser.parse_args()
    close=prices(args.tickers,args.period)
    output={}
    if args.project in ("risk","all"):
        output["risk"]=monte_carlo(close)
        output["risk_stress"]=monte_carlo(close,correlation_stress=.2)
    if args.project in ("pairs","all"):
        output["pairs"]=pairs(close,args.tickers[0],args.tickers[1])
    if args.project in ("crossover","all"):
        output["crossover"]=crossover(close,args.tickers[0])
    print(json.dumps(output,indent=2,allow_nan=False))

if __name__=="__main__":
    main()
