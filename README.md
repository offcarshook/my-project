# 🏆 Prop Pass: a frequent-trading prop challenge bot (paper only)

An intraday EMA-crossover bot for practicing simulated prop firm challenges on an **Alpaca
paper account**. One strategy, a realistic backtester, a strategy-independent risk manager, and
a paper-trading loop. It never sends live orders.

> **No result here is a promise.** The strategy has **not** been validated on real data in this
> repository yet (see [Status of results](#status-of-results)). Run the backtest with your own
> Alpaca keys and judge the untouched test period before trusting it with anything.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # paste your Alpaca PAPER keys
python propbot.py verify      # account, symbols, data, spreads
python propbot.py backtest    # one command: data -> config comparison -> test -> report
python propbot.py paper       # one command: run the paper bot during market hours
```

Other commands: `python propbot.py stop` (emergency stop), `status`, `reset --yes`.
`python propbot.py backtest --synthetic` runs the whole pipeline on random prices, only to
check that everything works. **Synthetic results mean nothing about real markets.**

## Strategy

Baseline: **9/21 EMA crossover on completed 5-minute candles** across
QQQ, SPY, IWM, DIA, XLK, SMH, TQQQ, SQQQ, SOXL, VOO (configurable).

* **Long** when the fast EMA crosses above the slow EMA; **short** on the cross below (only if
  shorting is enabled and the broker says the symbol is shortable and easy to borrow).
* Signals use completed candles only and execute at the **next candle's open**. Each candle's
  signal is acted on once; after an exit, a new entry needs a genuinely new cross.
* Entries between 09:45 and 15:30 New York time; everything is closed at 15:50 (or 10 minutes
  before an early close). No overnight positions.
* An opposite cross closes the position (and can reverse it).

Four lightweight, optional filters (off by default except liquidity; each can be toggled):

| Filter | Rule |
| --- | --- |
| Higher-timeframe trend | longs only when the last **closed** 30-min candle is above its 20 EMA, shorts only below |
| ATR volatility band | skip when ATR is under 0.5x or over 2.5x its own recent median |
| Liquidity | minimum candle dollar volume (feed-specific) and, live, a maximum bid-ask spread |
| Chop | Kaufman efficiency ratio over 12 candles must be at least 0.3 |

The backtest compares the baseline against the filtered versions and keeps filters only when
they win on validation after a complexity penalty (see [Backtesting](#backtesting)).

## Risk management

Per trade (all in `config.yaml`):

* **Risk 0.25% of equity per trade** (test 0.10–0.50%). Shares = risk ÷ (stop distance +
  a 0.05% slippage/gap buffer + estimated costs), never a fixed dollar amount.
* **ATR stop**: 1.5 × ATR(14), clamped to 0.15–1.5% of price (or a fixed % stop).
* **Take profit at 2R** (1.5R tested), optional trailing stop, exit on an opposite cross.
* Max **3 positions**, max **2 in the same correlation group and direction**, max **3x
  leverage-adjusted exposure**, max **20 new trades a day**.

The ten ETFs are not ten independent bets. Each symbol has a correlation group and an exposure
multiplier: QQQ, XLK, SMH, TQQQ (3x), SOXL (~4x vs the Nasdaq) and SQQQ (-3x, inverse) all sit
in the `nasdaq` group. Buying SQQQ counts as a **short** Nasdaq position. Leveraged ETFs
automatically get fewer shares because their ATR stop is wider. They are also path-dependent
products that decay in choppy markets.

### Challenge rules (risk manager)

`bot/risk.py` is independent of the strategy and overrides every signal:

* **Daily loss**: includes open-position losses and costs. Configurable base (% of the starting
  balance or of the day's start value), reference (balance, equity or the higher), and reset time.
* **Overall drawdown**: static, or trailing (on equity or balance, optionally locking at the
  starting balance). The trailing distance is a fixed dollar amount (% of the starting balance).
* **Safety buffer**: no new trades within 1% (configurable) of either limit, and new positions
  are sized so that the total risk to all stops fits inside the remaining room.
* **Breach** → cancel orders, close positions, **lock until `python propbot.py reset --yes`**.
* **Target** (+8%) → close everything and stop.
* It never increases risk to recover losses or chase the target.
* Missing or invalid challenge settings stop the bot from starting. Set them to your firm's
  exact rules; `confirmed_firm_rules: false` reminds you that ETFs, leveraged ETFs, shorting
  and bots must be checked against your firm before any real challenge.

## Backtesting

`python propbot.py backtest` downloads Alpaca 5-minute candles (SIP history, free with a 15-min
delay; set `backtest.data_feed: iex` if your plan can't read it), caches them per symbol and
month under `data/cache/`, and then:

1. Simulates **9 configurations** on the **train** (2023-01 → 2024-06) and **validation**
   (2024-07 → 2025-03) periods: EMA 9/21, 10/30, 20/50; ATR vs fixed stop; 2R vs 1.5R; baseline
   vs filtered; filtered + chop.
2. Rejects any config that loses money after costs in either period or trades less than 2 times
   per session. Of the rest, picks the highest **min(train, validation) t-stat of mean R**, minus
   0.15 per filter.
3. Runs **only the chosen config, once**, on the untouched **test** period (2025-04 → 2026-09).
4. Writes `reports/<timestamp>/report.md` plus CSVs.

Execution model: next-candle-open fills, half the assumed spread + slippage on every market
and stop fill, regulatory fees (SEC, FINRA TAF) on sells, take-profit limits only fill once
price trades through by half the spread, stops gapped through fill at the open, **stop first
when a candle touches both**, challenge limits judged on each candle's worst price, flat every
session (early closes included). Holidays have no candles, so they're skipped automatically.

The report covers, per config and per instrument: trades and trades per session, win rate,
average win/loss, profit factor after costs, net return, expectancy ($ and R), max daily and
overall drawdown, longest losing streak, exposure, turnover, results by month and by market
regime (trending up / sideways / declining, normal / high volatility), simulated challenges
(pass %, daily and total breaches, time-outs, average and median days), and a **Monte Carlo**
of reshuffled trading days with its assumptions spelled out.

## Paper trading

`python propbot.py paper`:

* Polls every 15 s during market hours; signals only from **completed** candles.
* Sends **bracket orders** (market entry + stop loss + take profit) to the **paper** endpoint
  only. Any other `ALPACA_BASE_URL` is refused before a request is sent.
* **No duplicate orders**: each order's `client_order_id` is derived from symbol, candle and
  side, the intent is saved before sending, and processed candles survive restarts.
* **Reconciles** with the broker every poll (the broker wins): detects positions closed by
  stops/targets, adopts quantity changes, closes positions it didn't open (configurable).
* Retries rate limits (honoring `Retry-After`), server errors and dropped connections; skips
  symbols with missing data.
* Logs every signal, order, fill, rejection, exit and risk decision to
  `logs/events-YYYYMMDD.jsonl`; writes live P&L, daily and overall drawdown, distance to each
  limit, exposure and trade counts to `state/status.json` (`python propbot.py status`, or the
  dashboard's 📡 tab).
* **Emergency stop**: `python propbot.py stop` cancels all orders and closes all positions
  immediately and writes `state/KILL`, which a running bot obeys on its next poll. Trading stays
  locked until `reset --yes`. Ctrl+C shuts down cleanly and (by default) flattens.

US pattern-day-trader rules apply to margin accounts under $25,000; the default backtest uses
$25,000 and `verify` warns about it.

## Status of results

* **Unit tests: 116 passing** (`python -m pytest`), covering signals and no-look-ahead, filters,
  position sizing, correlation and exposure limits, every drawdown method, daily resets, locks,
  fills/gaps/ties in the simulator, costs, order duplication across restarts, session shutdown,
  the kill switch and reconciliation (against a fake broker).
* **Real-data backtest: not run in this repository.** The environment it was built in can't
  reach Alpaca or any market-data provider, and no keys were available. There is no real
  performance number here, and none should be inferred.
* **Pipeline check on synthetic random-walk prices: runs end to end** (~3 minutes). On random
  prices every configuration loses after costs and selection correctly reports that none
  qualifies, which is the expected result for data with no edge. That output is not included,
  to avoid it being mistaken for performance.
* **Paper trading against the real Alpaca API: not run** (no connectivity/credentials). It's
  tested against a fake broker; run `python propbot.py verify` first.

## Layout

| Path | What it does |
| --- | --- |
| `propbot.py` | CLI: backtest, verify, paper, stop, status, reset |
| `config.yaml` | every setting, documented inline |
| `bot/config.py` | config loading and validation (fails safely) |
| `bot/data.py` | Alpaca candles, regular-hours filter, monthly disk cache |
| `bot/indicators.py` | EMA, ATR, efficiency ratio, closed higher-timeframe candles |
| `bot/strategy.py` | `EmaCross` (Prop Pass) plus the dashboard's older strategies |
| `bot/costs.py` | spread, slippage and fee model |
| `bot/risk.py` | challenge risk manager and position sizing |
| `bot/sim.py` | intraday portfolio simulator |
| `bot/pipeline.py`, `bot/report.py` | config comparison, test run, metrics, report |
| `bot/montecarlo.py` | reshuffled-day challenge simulation |
| `bot/broker.py`, `bot/trader.py`, `bot/http.py` | paper broker client, trading loop, retries |
| `tests/` | `python -m pytest` |

### Dashboard playground

`streamlit run app.py` is the earlier interactive playground (several strategies, a quick
local paper simulator and backtest, plus a 📡 tab that shows the paper bot's live status). Its
quick backtest fills at candle closes with a flat cost; **`python propbot.py backtest` is the
authoritative one.** `scripts/real_data_sim.py` runs older daily-candle experiments on index
data bundled with the `arch` package.

> Educational project, not financial advice.
