# MarketPilot Quant Lab (research-only)

Three reproducible quantitative research projects for the existing MarketPilot repository. **No order placement and no fabricated live market data.**

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r quant_lab/requirements.txt
python -m quant_lab.lab all
```
On Windows activate with `.venv\\Scripts\\activate`.

Default universe: SPY, QQQ, IWM, TLT, GLD. Daily adjusted prices from Yahoo Finance, 5-year lookback. Internet required for downloads.

## Modules
1. **Risk:** 10,000 seeded 21-session correlated Gaussian simulations using Cholesky; reports 95% VaR and expected shortfall and +0.2 shrinkage toward perfect correlation.
2. **Pairs:** 70% training / 30% test split, Engle-Granger cointegration on training data, training-only OLS hedge ratio, 30-session lagged z-score, ±2 entries, ±0.5 exits and 3.5 stop. Includes half-life and transaction costs. SPY/QQQ is an *example*, not guaranteed cointegrated. No trading when training p ≥ 0.05.
3. **Crossover:** 50/200 SMA, one-bar delayed position, 10 bps each position change, same-window buy-and-hold comparison.

## Important limitations
- Outputs are **historical research**, not real-time signals or options quotes.
- Gaussian returns understate jumps and changing volatility; VaR/ES are model estimates, not worst-case guarantees.
- Pair spread returns approximate a continuously rebalanced, gross-normalized hedge; short borrow, financing, execution gaps and dynamic hedge sizing are omitted. Half-life is only an estimate.
- Crossover uses next close-to-close return as an approximation to execution after signal close. It does not simulate next-open fills.
- Historical data can be revised, unavailable, or delayed. Walk-forward validation, data quality checks and realistic options execution are needed before any trading use.
- Do not treat SPY and QQQ as diversified holdings or assume pair cointegration.
- The current Render deployment is unchanged by this branch. Integration into the UI/API and tests are the next stage.

## Run examples
```bash
python -m quant_lab.lab risk
python -m quant_lab.lab pairs --tickers KO PEP
python -m quant_lab.lab crossover --tickers SPY
```
