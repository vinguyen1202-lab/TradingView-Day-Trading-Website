# MarketPilot | TradingView-connected paper-trading website

MarketPilot is a simple, **read-only** research dashboard for beginner options paper trading. It never places brokerage orders.

## What is actually connected?

- **Embedded TradingView chart:** the official TradingView widget; the exchange may delay quotes on public embeds. It is a visual-only widget and does not feed the site's strategy calculations.
- **Verified historical research data:** real TradingView MCP values fetched in this ChatGPT conversation on October 9, 2026. The pinned JSON in `static/verified_history.json` contains nine historical U.S. stock/ETF quote snapshots plus 25 actual regular-session five-minute candles each for PLTR, CRWV, and NVDA. They are *always* labeled historical, never eligible for a fresh trade.
- **New/fresh signals:** your TradingView account runs `MarketPilot_TV_Signals.pine` on a five-minute chart. With paid-plan webhook alerts enabled, TradingView sends the close of each completed candle to this website's authenticated receiver. Only recent, in-session alerts can generate `REVIEW_CALL` or `REVIEW_PUT`. If the alert does not arrive, or the market is closed, it shows **WAIT**.
- **Options chain:** TradingView's options chain is linked, but TradingView MCP and embedded widgets do not expose an official options-chain API for filling independent websites. You enter **actual bid/ask/delta/volume/open interest**, plus your projected exit bids; the latter are **estimates**, not market quotes.

## What I can do safely

Choose any ticker (and exchange), compare an actual chart with native verified historical candles and fresh Pine bars, review call/put candidates, and calculate planned option loss using your estimated exit bid plus fees/slippage. The paper-trade calculator has **no preset dollar-loss cap**; planned loss is an estimate, not a guarantee. For a long option, the premium paid can be lost in full. Journal entries save only in your own browser local storage. The interface accepts a positive whole-number contract quantity without a preset maximum.

## Time zone display

The Pine strategy keeps its session checks in New York exchange time. The entry window (9:35–11:00 a.m. ET) is 6:35–8:00 a.m. Pacific Time; premarket is 1:00–6:30 a.m. Pacific, and the regular session is 6:30 a.m.–1:00 p.m. Pacific. The website formats timestamps with the America/Los_Angeles time zone, so they switch between PST and PDT automatically.

## One-time deployment: GitHub → Render

1. Create a **private GitHub repository** called `marketpilot-tradingview` and upload the *contents* of this folder to its root. Do not upload secrets, `.env`, or SQLite files. If automatic deploy with the repository works best under your Render plan, grant Render repository read access.
2. At [Render Dashboard](https://dashboard.render.com/), choose **New → Blueprint**, select the GitHub repository and authorize deployment. Render recognizes `render.yaml`; **do not change the plan to paid without deciding to incur charges.** The committed blueprint uses a free service.
3. Render automatically creates a random `TV_WEBHOOK_TOKEN` secret. After deployment, find that environment variable under **Render → your service → Environment**. **Keep the token private** (it controls which signal posts the site accepts). The site will have a HTTPS address such as `https://<your-actual-service>.onrender.com`. Do NOT invent or assume your URL; copy the URL Render actually provides.
4. Open [TradingView Supercharts](https://www.tradingview.com/chart/) in your paid account. On a stock's **5-minute** chart, enable Extended Trading Hours, open Pine Editor, paste `MarketPilot_TV_Signals.pine`, **Save → Add to chart**, then create an alert with **Condition: MarketPilot → Any alert() function call** and **Webhook URL: `https://<your-actual-service>.onrender.com/api/tradingview/<TV_WEBHOOK_TOKEN>`**. TradingView requires two-factor authentication for webhooks. Never paste the token in a public comment or chat. TradingView's `alert()` automatically supplies the JSON message; no manual JSON is needed.
5. Repeat the alert on other stock charts you want to monitor. Open the deployed website. Navigate to **Connect TradingView → Recheck connection** and look at **Dashboard → Completed five-minute candles**. Only genuine received alerts will update the current signal panel.

### Important hosting limitation

Render **free** web services can sleep and use ephemeral storage, so a TradingView webhook can time out or fail on a cold start, and historical received alerts can vanish on redeploy. TradingView only waits about **3 seconds** for webhook processing. Therefore **the free-tier deployment is not reliable for real-time trading alerts**. For a dependable always-on signal receiver you would need a suitable always-on hosting tier and persistent storage; that could incur fees. Do not trade real money based on missed or stale alerts.

### Security

The TradingView webhook is protected by a generated high-entropy secret embedded in the URL. Do not share the full webhook URL. URL-path secrets can appear in private server logs, so lock down log access and rotate the token if exposed. The site is read-only/public unless you configure hosting-layer access controls; never publish brokerage access tokens there. Hosting an API that uses MCP authorization requires a separate server-side authenticated connector and is **not** included: the ChatGPT TradingView MCP connection is not transferable to this website.

## Run on your Mac locally

```bash
cd MarketPilot_Live_Deployment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TV_WEBHOOK_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. To test the site with actual Pine webhooks, TradingView must reach a **public HTTPS** URL; localhost is not enough. Do not post fake alerts to the published site.

## API endpoints

- `GET /health` — uptime check
- `GET /api/status` — active webhook backend status (never credentials)
- `GET /api/verified-history/{ticker}` — dated genuine historical TradingView MCP data; decision WAIT always
- `GET /api/snapshot/{ticker}` — genuinely received Pine webhook data, stale automatically forced to WAIT
- `GET /api/tickers` — symbols with received alerts
- `POST /api/tradingview/{secret}` — authenticated JSON alert receiver

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q tests/
```

Tests use locally generated fixtures only for automated tests and **never** inject sample prices into website production data.

## References

- [TradingView webhook alerts](https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/)
- [Render blueprint configuration](https://render.com/docs/blueprint-spec)
- [TradingView widget data policy](https://www.tradingview.com/widget-docs/faq/data/)
