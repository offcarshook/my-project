# Kronos bot v2: selection, sizing and validation results

Protocol: `PROTOCOL_v2.md` (commit 3418940, written before any filter result). Paper/backtest only.

| step | commit | outcome |
|---|---|---|
| pre-registration | 3418940 | splits, grid, criterion, sizing rule, gates fixed |
| selection + sizing code, tests | 21a12fb | 11 tests pass |
| 15:30 thresholds chosen on TRAIN, frozen | 1374e58 | `results/v2_last30_frozen.json` |
| validation reporting (no rule change) | 70638a2 | run-once guards |
| **15:30 VALIDATION gate** | 2b1105a | **FAILED** |
| audits (evaluator vs Nautilus, data quality) | 3b3e990 | evaluator slightly conservative; 2017-18 data gaps |
| 10:00 window | see section 6 | |

## 1. Model and data checks
* Genuine pretrained weights: NeoQuasar/Kronos-mini (4,108,032 parameters, matches the published
  4.1M) + Kronos-Tokenizer-2k from the Hugging Face snapshot (model.safetensors sha256 a7d5f37e...,
  tokenizer b97ec46b...), official code at commit 67b630e. No random or placeholder weights.
* Forecast reproducibility: every forecast re-seeds the sampler (seed 0), so a restart or the
  resumable cache cannot change a value.
* Causality: Kronos receives candles up to and including the decision candle (index = candle
  close); the 20-decision volatility uses strictly earlier decisions; the 50-session trend uses
  the previous close; thresholds, EV slope and volatility reference come from TRAIN rows only
  (unit test: scrambling every post-TRAIN row leaves the selection identical).
* Data quality (CFD15): the 15:30 candle is missing on 38 NYSE days in 2017 and 48 in 2018 while
  15:45/16:00 exist (about 11% of TRAIN decisions lost; those days are not traded by either
  evaluator). 2019-2025 are complete apart from about 8 days in 2020. CFD, not futures; tick volume.

## 2. TRAIN selection, 15:30 -> 16:00 (in-sample, 2016-11-15 .. 2019-12-31, 688 decisions)
Fast evaluator, unit notional, net of 1.35 index points per round trip. Full 20-cell grid:
`results/v2_last30_train_grid.csv` (best cell t = 0.65; the grid's best Sharpe is 0.39).

| variant | rule | trades | net bp/trade (95% CI) | PF | sum of returns | max DD | months + |
|---|---|---|---|---|---|---|---|
| A | every day, sign of mean forecast | 688 | -0.43 (-2.06, +1.19) | 0.94 | -2.97% | -6.08% | 37% |
| B | + confidence >= 3/16 | 431 | +0.22 (-1.78, +2.21) | 1.03 | +0.94% | -4.02% | 49% |
| C | + move >= 2 x cost | 383 | +0.73 (-1.48, +2.93) | 1.10 | +2.79% | -3.19% | 51% |
| C-EV = S | conf 3/16, |0.227 x forecast| >= cost | 234 | +1.87 (-1.44, +5.19) | 1.22 | +4.39% | -2.26% | 49% |
| D | S + volatility sizing | 234 | +0.93 (-1.40, +3.25) | 1.15 | +2.16% | -1.88% | 49% |
| H3 | existing baseline | 688 | -1.79 (-3.42, -0.16) | 0.79 | -12.32% | -13.60% | 34% |

Final variant by the pre-registered rule: S (D's TRAIN Sharpe 0.47 < S's 0.67). Even in-sample
the chosen filter's interval includes zero.

## 3. VALIDATION, 15:30 -> 16:00 (2020-01-01 .. 2021-12-31, 493 decisions, frozen rules, run once)
| variant | trades | net bp/trade (95% CI) | t | PF | sum of returns | max DD | avg month | months + | worst month |
|---|---|---|---|---|---|---|---|---|---|
| A | 493 | +0.58 (-4.51, +5.67) | 0.22 | 1.04 | +2.86% | -9.92% | +0.12% | 42% | -3.44% |
| B | 302 | -1.87 (-8.14, +4.40) | -0.58 | 0.88 | -5.63% | -9.27% | -0.24% | 46% | -2.69% |
| C | 297 | -1.56 (-7.93, +4.80) | -0.48 | 0.90 | -4.64% | -8.30% | -0.19% | 46% | -2.69% |
| **S (final)** | **285** | **-2.08 (-8.69, +4.52)** | **-0.62** | **0.87** | **-5.94%** | **-9.14%** | **-0.25%** | **42%** | **-2.69%** |
| D (S, vol-sized) | 285 | -0.80 (-3.13, +1.53) | -0.67 | 0.89 | -2.27% | -3.64% | -0.10% | 46% | -0.88% |
| H3 | 493 | +1.96 (-3.12, +7.04) | 0.76 | 1.14 | +9.66% | -10.42% | +0.40% | 50% | -2.97% |

By year (net bp/trade): S 2020 -4.11 (139 trades), 2021 -0.16 (146); A 2020 +1.73, 2021 -0.53.

**Gate (expectancy > 0, PF > 1, >= 40 trades): FAILED** (`results/v2_last30_validation_gate.json`).
As pre-registered, the 15:30 strategy stops here: no 2022-2025 evaluation was run (no OOS
forecasts were kept), no NautilusTrader evaluation of the variants, RESERVED untouched, no
threshold changed. Units: the fast evaluator is unit notional (1x exposure, returns summed);
account-level figures would scale with the bot's sizing and were not produced because the
candidate did not pass.

## 4. Diagnostics (TRAIN / VALIDATION)
* Kronos outputs are one signal: mean, median, probability-up and signal-to-noise are 0.90-0.97
  rank-correlated; none adds IC over the mean (incremental IC -0.07 .. -0.00).
* Direction skill is weak and fading: IC +0.106 (TRAIN), +0.074 (VALIDATION). It was positive in
  both, but too small to pay costs; the filters that looked best in TRAIN were the noisiest part
  of it (all filtered variants turned negative in VALIDATION while unfiltered A stayed slightly
  positive), the signature of selection on noise.
* Long bias: Kronos went long on 63% (TRAIN) / 68% (VALIDATION) of days while the window finished
  up on 52% / 49%. Its longs lost in TRAIN (-1.06 bp) and were flat in VALIDATION (-0.14 bp); its
  shorts were mildly positive in both (+0.64, +2.09 bp, t 0.40, 0.53). Always-long lost in both.
* Regimes: in TRAIN the unfiltered edge sat in down-trend markets (+4.52 bp, t 1.48, n 118) and
  S's in the high-volatility tercile (+3.64 bp); in VALIDATION S's high-volatility tercile lost
  (-2.64 bp, n 226). The regime pattern did not repeat.
* Volatility sizing (pre-specified, separate comparison, not used for selection): on the same
  signals it cut VALIDATION drawdown from -9.14% to -3.64% and the loss per trade from -2.08 to
  -0.80 bp, as designed (it only reduces exposure), but it lowered TRAIN Sharpe and does not turn a
  losing signal into a winning one.

## 5. Audits
* Fast evaluator vs NautilusTrader on identical signals (existing guard-free H3 DEV run, 1,115
  trades): direction agreement 100%, per-trade net correlation 0.981, entries and market exits
  each exactly 1 tick from the candle close, Nautilus +0.115 points/contract better (protective
  stops cut some losers: stopped trades -31.4 vs -32.5 if held). The gate's evaluator is slightly
  conservative; the size of the gap (~0.1-0.2 bp) cannot explain S's -2.08 bp.
* Stop/target ambiguity: the bot has no target; a stop touched in the exit candle is filled
  before the time exit (bar replay O->H->L->C, conservative). Ambiguity cannot flatter results.
* Look-ahead: see section 1. No validation or later data entered any choice.

## 6. 10:00 -> 15:45 window
Pending (TRAIN forecasts running; pre-registered rejection rule: unfiltered TRAIN IC <= 0 or best
TRAIN cell Sharpe <= 0).

## 7. Not done, and why
* 2022-2025 out-of-sample and NautilusTrader A-D evaluation for 15:30: gate failed (protocol).
* Portfolio (E): requires two strategies that pass their gates.
* ES / gold: no verified futures data, contract specifications or cost data here.
