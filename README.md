# 🏆 Prop Pass: a frequent-trading prop challenge bot (paper only)

An intraday ETF bot for practicing simulated prop firm challenges on an **Alpaca paper
account**. Four switchable strategies (EMA crossover, opening range breakout, VWAP mean
reversion, ICT liquidity sweep + structure shift + fair value gap), a realistic backtester, a
strategy-independent risk manager, and a paper-trading loop. It never sends live orders.

> **No result here is a promise.** See [Status of results](#status-of-results) for what the
> real-data backtests found. Judge the untouched test period before trusting any strategy.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # paste your Alpaca PAPER keys
python propbot.py verify      # account, symbols, data, spreads
python propbot.py backtest    # one command: data -> config comparison -> test -> report
python propbot.py paper       # one command: run the paper bot during market hours
```

Got your own candle files (e.g. FirstRate Data)? `python propbot.py backtest --data path/to/folder`
reads one `.parquet` or `.csv` per symbol (symbol in the file name; columns datetime/open/high/low/
close/volume, naive times = New York). Keep licensed data out of git: `data/` is ignored.

Other commands: `python propbot.py stop` (emergency stop), `status`, `reset --yes` (stop the bot first).
`python propbot.py backtest --synthetic` runs the whole pipeline on random prices, only to
check that everything works. **Synthetic results mean nothing about real markets.**

## Strategy

Pick one with `strategy.type` in `config.yaml`; each has its own settings block there. All of
them trade completed 5-minute candles across QQQ, SPY, IWM, DIA, XLK, SMH, TQQQ, SQQQ, SOXL, VOO
(configurable) and share the rules, filters and risk management below.

| `type` | Idea | Stop / target |
| --- | --- | --- |
| `ema_cross` | fast/slow EMA crossover (9/21 by default) | ATR stop, `risk.reward_risk` target |
| `orb` | close breaks the first 15 (or 30) minutes' high/low; one trade per side per symbol per day, until 11:30; ranges too narrow or too wide are skipped | other side of the range (or its midpoint); target from `risk.reward_risk` (0 = hold until the flatten time or an opposite breakout) |
| `vwap_reversion` | price stretched 2+ ATRs from session VWAP, then a reversal candle (close beyond the previous candle's high/low); optional "choppy days only" via the efficiency ratio | beyond the recent extreme + 0.5 ATR; target **at VWAP**, skipped if that's under 1R |
| `ict_fvg` | **liquidity sweep** of the prior day's low/high or the latest swing, closing back inside → **market structure shift** (close beyond the last swing point) → entry on the retest of the **fair value gap** left by the move | just beyond the sweep extreme; target from `risk.reward_risk` |

Strategies that place their own stop or target hand them over as price levels from the signal
candle's close, so the backtest and the paper bot use the same levels; `risk.stop` min/max %
still bound the stop.

* **Shorts** only if shorting is enabled and the broker says the symbol is shortable and easy
  to borrow.
* Signals use completed candles only and execute at the **next candle's open**. Each candle's
  signal is acted on once; after an exit, a new entry needs a genuinely new cross.
* Entries between 09:45 and 15:30 New York time; everything is closed at 15:50 (or 10 minutes
  before an early close, from the exchange calendar). No overnight positions.
* ATR and the chop filter are measured within the session, so an overnight gap doesn't
  inflate stops or make a choppy morning look like a trend.
* An opposite signal (EMA cross, opposite ORB breakout) closes the position (and can reverse it).

Four lightweight, optional filters (off by default except liquidity; each can be toggled; the
backtest only tries the trend/ATR/chop filters on the EMA strategy):

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

* **Risk 0.25% of equity per trade** by default. The backtest sweeps 0.25 / 0.375 / 0.5 / 0.75%
  and recommends a level (see [Backtesting](#backtesting)). Shares = risk ÷ (stop distance + a
  0.02% gap buffer + estimated costs), never a fixed dollar amount.
* **ATR stop**: 1.5 × ATR(14), clamped to 0.15–3% of price (or a fixed % stop). The backtest
  also tests a **wide stop** (3 × ATR, at least 0.30%) with a 1.5R target.
* **Take profit at 2R** (1.5R tested), optional trailing stop, exit on an opposite cross.
* Max **3 positions**, max **2 in the same correlation group and direction**, max **4x
  leverage-adjusted exposure** (Alpaca's intraday buying power), max **15 new trades a day**.
* Costs: half the spread (**never under one cent**, which matters on $9 SQQQ) plus slippage on
  every market or stop fill, and SEC/FINRA fees on sells.

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
  are sized so that the total risk to all stops (including each one's gap buffer and exit costs)
  fits inside the remaining room.
* Breaches are judged on the worst price inside each candle, and a trailing drawdown follows the
  best price inside each candle, like a firm checking every tick.
* **Breach** → cancel orders, close positions, **lock until `python propbot.py reset --yes`**.
* **Target** (+8%) → judged on what the account would hold **after paying to close everything**,
  then close everything and stop. If the exits still land under the target, trading resumes.
* It never increases risk to recover losses or chase the target.
* Missing or invalid challenge settings stop the bot from starting. Set them to your firm's
  exact rules; `confirmed_firm_rules: false` reminds you that ETFs, leveraged ETFs, shorting
  and bots must be checked against your firm before any real challenge.

## Backtesting

`python propbot.py backtest` downloads Alpaca 5-minute candles (IEX by default, the same feed the
paper bot trades on; set `backtest.data_feed: sip` for all exchanges), trims early-close days with
the exchange calendar, caches everything per symbol and month under `data/cache/` (re-downloading
a symbol's months together whenever one is missing, so split/dividend adjustments match), and then:

1. Simulates a fixed set of configurations on the **train** (2023-01 → 2024-06) and
   **validation** (2024-07 → 2025-03) periods: **your config.yaml exactly as the bot will trade
   it**, then per strategy family: EMA (9/21, 10/30, 20/50, 1.5R vs 2R, wide stops, filtered,
   filtered + chop), ORB (15/30-minute range, range/mid stop, hold vs 2R, VWAP side), VWAP fade
   (2.0/2.5 ATR, choppy days only) and ICT (2R, 3R, slower swings). `--strategies orb,ict` limits
   the comparison to some families.
2. Rejects any config that loses money after costs in either period or trades outside **4–15
   times per session**. Of the rest, picks the highest **min(train, validation) t-stat of mean
   R**, minus 0.15 per filter.
3. **Risk sweep** for the chosen config, on train and validation only: challenge pass and fail
   rates at each level in `backtest.risk_grid`, next to a **zero-edge luck baseline** (the same
   trades with their average edge removed). A higher risk is only recommended if it blows up at
   most 10% of challenges *and* beats the luck baseline by 10+ points. Otherwise risk stays put.
   (On random prices, 0.75% risk "passes" ~40% of challenges from luck alone, which is why the
   baseline matters.) If no config has an edge, the sweep is skipped.
4. Runs **only the chosen config at the chosen risk, once**, on the untouched **test** period
   (2025-04 → 2026-09).
5. Writes `reports/<timestamp>/report.md` plus CSVs and `selected_config.json`. The paper bot
   warns at startup if `config.yaml` differs from the last selection, so copy the selected
   settings over if you want to trade what was tested.

Execution model: next-candle-open fills, sizing only from prices known at that open, half the
spread (min one cent) + slippage on every market and stop fill, regulatory fees on sells,
take-profit limits only fill once price trades through by half the spread, stops gapped through
fill at the open, **stop first when a candle touches both**, challenge limits judged on each
candle's worst price, flat every session (early closes included).

The report covers, per config and per instrument: trades and trades per session, win rate,
average win/loss, profit factor after costs, net return, expectancy ($ and R), **cost per trade
in R** (the gross edge has to beat it), max daily and overall drawdown, longest losing streak,
exposure, turnover, share of sessions that hit the daily trade cap, results by month and by
market regime (trending up / sideways / declining, normal / high volatility, using only past
data), simulated challenges (pass %, daily and total breaches, time-outs, average and median
days), and a **Monte Carlo** of reshuffled trading days with the luck baseline and its
assumptions spelled out.

## Paper trading

`python propbot.py paper`:

* Polls every 15 s during market hours; signals only from **completed** candles.
* Sends **bracket orders** (market entry + stop loss + take profit) to the **paper** endpoint
  only. Any other `ALPACA_BASE_URL` is refused before a request is sent. The stop and target
  legs are **GTC**, so if the bot dies before flattening, the position still has its stop at
  the next open.
* Uses the **broker's clock**, not the computer's, for the cutoff, and raises a critical
  `overnight_positions` alert if anything is still open after the close.
* **No duplicate orders**: each order's `client_order_id` is derived from symbol, candle and
  side, the intent is saved before sending, and processed candles survive restarts. If a submit
  fails ambiguously (timeout, 5xx), the record is kept and resolved against the broker, never
  dropped.
* **Reconciles** with the broker every poll (the broker wins): detects positions closed by
  stops/targets, adopts quantity changes and partial fills, closes positions it didn't open
  (configurable). This runs after the risk check, so a broker error can never block it.
* Exits cancel the bracket legs first (Alpaca nests them under the entry order) and retry next
  poll if the broker refuses. Flattening checks every symbol in Alpaca's multi-status reply and
  keeps retrying until the account is really flat.
* Retries rate limits (honoring `Retry-After`), server errors and dropped connections; skips
  symbols with missing data.
* Logs every signal, order, fill, rejection, exit and risk decision to
  `logs/events-YYYYMMDD.jsonl`; writes live P&L, daily and overall drawdown, distance to each
  limit, exposure and trade counts to `state/status.json` (`python propbot.py status`, or the
  dashboard).
* **Emergency stop**: `python propbot.py stop` cancels all orders and closes all positions
  immediately and writes `state/KILL`, which a running bot obeys on its next poll. Trading stays
  locked until `reset --yes` (which refuses while the bot is still running). Ctrl+C, SIGTERM
  or SIGHUP wake the bot immediately, so it flattens (by default) before a process manager
  kills it.

US pattern-day-trader rules apply to margin accounts under $25,000; the default backtest uses
$25,000 and `verify` warns about it.

## Status of results

* **Unit tests: 133 passing** (`python -m pytest`), covering signals and no-look-ahead, filters,
  position sizing, correlation and exposure limits, every drawdown method, daily resets, locks,
  fills/gaps/ties in the simulator, costs, the risk sweep's luck gate, order duplication across
  restarts, session shutdown, the kill switch and reconciliation (against fake brokers, one of
  which mimics Alpaca's nested bracket legs and held-for-orders refusals).
* **Code review:** a multi-agent review found 30 issues (each confirmed by a separate skeptic)
  plus 11 improvement ideas; all are fixed or applied.
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
| `bot/data.py` | Alpaca candles, regular-hours filter, monthly disk cache, synthetic candles for `--synthetic` |
| `bot/indicators.py` | EMA, ATR, efficiency ratio, closed higher-timeframe candles |
| `bot/strategy.py` | the four strategies (`EmaCross`, `OpeningRangeBreakout`, `VwapReversion`, `IctSweepFvg`), shared filters, stop distance |
| `bot/costs.py` | spread, slippage and fee model |
| `bot/risk.py` | challenge risk manager and position sizing |
| `bot/sim.py` | intraday portfolio simulator |
| `bot/pipeline.py`, `bot/report.py` | config comparison, test run, metrics, report |
| `bot/montecarlo.py` | reshuffled-day challenge simulation |
| `bot/broker.py`, `bot/trader.py`, `bot/http.py` | paper broker client, trading loop, retries |
| `app.py` | read-only dashboard (status + latest report) |
| `tests/` | `python -m pytest` |

### Dashboard

`streamlit run app.py` shows the running paper bot's live status (equity, daily and overall
drawdown, exposure, positions, recent events) and the latest backtest report. It only reads
files; trading happens in `python propbot.py paper`.

> Educational project, not financial advice.
