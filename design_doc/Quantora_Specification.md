# Quantora — Product & Technical Specification

**A patient, multi-asset signal and execution system for Forex, Gold, Synthetic Indices, and Crypto.**

---

## 1. Core Philosophy

Quantora is a **discipline engine**, not a prediction engine. This distinction matters more than it sounds, and it's worth stating precisely because it was wrong in earlier drafts of this document.

**Discipline preserves capital. It does not create alpha.** Patience, consistent risk sizing, no-FOMO rules, and cooldowns do not generate positive expected value on their own — if the underlying signal logic has no real edge, discipline just makes the losing more consistent, which is not the same as safer. The actual edge, if it exists, has to come from the confluence logic itself, and that is an open, unproven question that must be settled empirically — not assumed because the system is well-engineered.

What discipline *does* do, and why it's still the right foundation to build on:

- It's what makes the data trustworthy. A loss only tells you something useful about the signal if it wasn't caused by inconsistent human behavior (revenge trading, moved stops, abandoned rules) contaminating the result.
- It's what lets the system survive long enough for a real edge — however modest — to show up in the numbers, rather than being wiped out by ordinary variance during an otherwise-normal losing streak.
- It's what keeps any single mistake small and bounded, so no single bad decision or bad stretch ends the experiment before it's had a fair chance to prove itself one way or the other.

The tagline: *"Quantora doesn't chase. It waits, watches, and confirms — and it doesn't assume it's right until the data says so."*

Everything below exists to serve two goals at once: **build a genuinely disciplined execution system, and build the honest measurement pipeline that proves or disproves whether it has real edge** — rather than taking either for granted.

---

## 2. Trading Modes

Quantora supports three trade horizons, run as separate configuration profiles per symbol — not separate codebases:

| Mode | Timeframe basis | Typical hold time | Use case |
|---|---|---|---|
| **Scalp** | M15 (base timeframe — no rollup needed) | ~15–30 min, exits on TP hit | Fast, high-frequency setups; duration is TP-driven, not fixed |
| **Swing** | H4 / D1 | Days to ~2 weeks | Faster confluence turnover, tighter stops |
| **Long / Position** | D1 / W1 | Weeks to months | Slower-moving trend confirmation, wider stops, smaller position count |

Same confluence engine, same indicators — just different lookback periods, different confirmation thresholds, and different risk parameters per mode. A symbol can be actively tracked in multiple modes simultaneously with independent signal states — e.g. EURUSD-Scalp and EURUSD-Swing run in parallel, each with its own Watching/Forming/Confirmed state.

**Honest ranking of where edge is most plausible, and why.** Scalp mode competes most directly on speed and information against market makers and algorithms with structural advantages retail access doesn't have — this is genuinely the hardest environment to find edge in. Swing and Long mode are driven by broader, slower forces (sustained trend, macro shifts, sentiment) that aren't primarily won by who's faster — this is where retail systematic approaches have historically had the most legitimate track record (trend-following, specifically). **Practical implication: Swing and Long should be trusted first and sized more generously once proven; Scalp should be the most rigorously tested and the last to receive meaningful capital, if it's kept at all.**

**Two engineering realities specific to Scalp mode:**

1. **The three-tier system trades off differently at this speed.** On Swing/Long timeframes, waiting for full confluence before confirming costs little — the setup is still there minutes later. On a 15–30 min hold, waiting for a closed M15 candle before re-evaluating a Tier 2 setup can burn a meaningful share of the trade window.

   **Fix — lightweight live-price polling, not full tick ingestion.** Storing and processing raw tick data was deliberately ruled out (tens of millions of rows per symbol per year, and unnecessary at this scale). Instead, for symbols currently in **Tier 2 (Setup Forming)** under Scalp mode only, Core polls the live, still-forming candle's current price (`mt5.symbol_info_tick()` — one lightweight call, not a data pipeline) every 1–5 minutes to re-check whether the outstanding confirmation condition has now been met, without waiting for the M15 candle to close. Swing and Long continue evaluating only on closed candles. No new data is stored from this — it's a read-only check purely to decide whether to advance the state machine sooner.

2. **Costs matter more relative to the target.** Spread and slippage are a roughly fixed cost per trade regardless of hold time, but a scalp's typical TP distance is much smaller than a swing trade's — the same spread eats a much bigger share of the intended profit. See Section 12 (Execution Realism) — this is a formal, priced-in part of the design, not an afterthought.

A practical addition for Scalp specifically: an optional **time-stop** (e.g. auto-close if TP hasn't been hit within 30–45 minutes, even without hitting stop loss) — a scalp setup that hasn't resolved quickly has usually stopped being a scalp setup. This lives in Sentinel's execution logic, symbol/mode-specific like everything else.

All accounting is in **USD** — the MT5 account is USD-denominated for forex/synthetics/gold, and crypto pairs are quoted in USDT (or traded as CFDs directly against USD if using Deriv's own crypto offering — see Section 3), treated as USD-equivalent for P&L purposes.

---

## 3. Tradable Universe & Broker

**Broker: Deriv**, via MT5 (`MetaTrader5` Python package). This decision has real consequences for the design that are worth stating explicitly rather than assuming a generic broker:

- **Minimum deposit is $5**; Deriv does not offer Cent/Micro accounts, so position-sizing math has to work against real dollar amounts from the start — there's no intermediate "practice with real money at 1/100th scale" tier.
- **Deriv offers forex, gold, synthetic indices, and crypto CFDs all on one platform.** This raises a real architecture question, addressed below.
- Leverage up to 1:1000 (forex/synthetics) means the margin-based sizing cap (Section 7, Cap 2) is rarely the binding constraint at small account sizes on Deriv specifically — the **minimum lot size** is almost always the real floor, not margin.

### Forex + Gold (5)
| Symbol | Role |
|---|---|
| EURUSD | Deepest liquidity, tightest spreads — the reference pair |
| GBPUSD | High liquidity, more volatile, good for swing setups |
| USDJPY | Different session character (Asia-heavy), diversifies away from EUR/GBP |
| XAUUSD (Gold) | Reacts to macro/risk sentiment rather than pure FX flow — **higher minimum capital requirement, see Section 13; deliberately the last instrument to fund** |
| AUDUSD | Commodity-linked — a genuinely different driver (China demand, risk-on/off) |

### Synthetic Indices (3)
| Symbol | Role |
|---|---|
| Volatility 75 Index (V75) | **Minimum lot size 0.001 — ten times smaller than standard forex micro-lots.** Most beginner-friendly, most capital-efficient instrument on Deriv; the recommended starting instrument for small accounts (Section 13) |
| Volatility 100 Index (V100) | Similar character, cross-checks the confluence logic isn't overfit to one instrument |
| Step Index | Fixed-increment, low-drama — useful for validating risk logic without wild swings |

**Explicitly excluded, and why it matters more than a footnote:** Boom/Crash indices and large-lot indices (Vol 90 (1s), Vol 250 (1s), and similar) are deliberately excluded — not just because their engineered spike behavior doesn't transfer cleanly from standard indicators, but because their larger minimum lot sizes make them structurally dangerous for small accounts specifically. A careless lot-size carryover when switching charts in MT5 between a large-lot index and V75 is a realistic way to accidentally risk 100–200x the intended amount — this is addressed operationally in Section 12.

### Crypto (3)
| Symbol | Role |
|---|---|
| BTCUSDT / BTC CFD | Deepest liquidity — the anchor |
| ETHUSDT / ETH CFD | Second-deepest liquidity, correlated with BTC but not identical |
| One liquid alt (e.g. SOL) | Optional third — adds less true diversification than it appears to, since alts tend to move with BTC during risk-off conditions |

**Open architecture decision — not yet resolved:** crypto can be sourced either via `ccxt` against a spot exchange (Bybit/Binance) or via **Deriv's own crypto CFDs**, which would let Sentinel run everything through the single MT5 connection it already owns (Section 10), removing the `ccxt` dependency entirely for a fully single-broker architecture. Trade-off: Deriv CFDs mean no real coin custody (irrelevant for Quantora's signal/trading purpose) and potentially wider spreads than a spot exchange — worth comparing directly on your actual account before deciding, rather than assuming either way.

### Correlation Groups (feeds Quantora Sentinel's sizing logic)

| Group | Symbols | Why grouped |
|---|---|---|
| USD-majors | EURUSD, GBPUSD, AUDUSD | Share a USD leg — tend to move together on USD-driven news |
| Independent | USDJPY, XAUUSD | Distinct enough drivers to size individually |
| Synthetics | V75, V100, Step | Uncorrelated with FX/crypto, but grouped with each other as a light precaution |
| Crypto | BTC, ETH, alt | High correlation, especially during risk-off — treated as one group |

Sentinel caps total risk *per group*, not just per trade — if EURUSD, GBPUSD, and AUDUSD all confirm a signal at once, position sizes are scaled down so the combined group exposure never exceeds the group risk cap. This only ever adjusts *how much* is risked on a signal — it never overrides or replaces the technical-indicator decision that produced the signal in the first place.

**v1 note:** these groups are hardcoded based on known market structure. A future improvement (Section 14, "adopt later") is computing rolling correlation directly from `qtx_ohlcv`, since real correlation is regime-dependent and can drift from this static table.

---

## 4. The Three-Tier Signal System (the core differentiator)

Instead of a binary buy/sell trigger, every symbol/mode combination sits in one of three states at all times:

### Tier 1 — **Watching**
Some conditions are aligning, but confluence is weak. No notification, no action. Logged internally only.

### Tier 2 — **Setup Forming**
Most conditions align, but one key confirmation hasn't triggered yet. A **heads-up notification**, not a trade signal:

> *"BTCUSD: Approaching Buy Zone — $61,200 support with RSI(14) already oversold. Watching for confirmed bullish close before signaling. No trade yet."*

### Tier 3 — **Confirmed Signal**
Full confluence achieved per the mode's threshold. Produces an actionable suggestion: direction, entry zone, stop loss, take profit, and calculated lot size based on live account balance, risk %, and margin constraints (Section 7).

This state machine runs per symbol, per mode — EURUSD-Swing and EURUSD-Long can be in different tiers at the same time, and that's expected.

**Required validation before this mechanism is trusted, not just assumed to work:** the premise that "waiting for Tier 3 beats acting at Tier 2" is itself a testable hypothesis, not a given. Once `qtx_signal_outcomes` (Section 6) has enough labeled history, Calibrate must explicitly compare outcomes for hypothetical entries at Tier 2 confirmation vs. actual Tier 3 confirmation — win rate, R-multiple, and expectancy for each. **A mode is not promoted toward live capital (Section 15) until this comparison has actually been run and Tier 3 demonstrably outperforms Tier 2** for that symbol/mode. If it doesn't, that's the system correctly telling you the "patience" premise needs revisiting for that specific case, rather than continuing to trust it on faith.

---

## 5. Indicator Selection & Collinearity Control

A confluence score is only meaningful if the indicators feeding it are measuring genuinely different things. Picking five popular indicators isn't the same as having five independent opinions — several standard indicators are derived from price in overlapping ways and will agree or disagree together in the same market regimes, quietly inflating confidence without adding real signal.

### Selection by market dimension, not popularity

| Dimension | What it captures | Default pick |
|---|---|---|
| Trend direction | Is price broadly rising or falling | EMA crossover/slope |
| Trend strength (filter, not direction) | Is there a trend worth trading at all | ADX |
| Momentum | Is the move accelerating or fading | RSI *or* MACD — one, not both, by default |
| Volatility / mean-reversion context | Is price stretched relative to its recent range | Bollinger Bands |
| Volume confirmation (crypto only) | Is the move backed by real participation | OBV |

### Mandatory correlation/VIF check — part of every Calibrate cycle

1. Discretize each indicator's output into a per-bar signal: −1 (bearish), 0 (neutral), +1 (bullish).
2. Compute the pairwise correlation matrix between every indicator's signal series, per symbol, over the backtest window.
3. Flag any pair with |ρ| > 0.6–0.7, or any indicator with a **Variance Inflation Factor (VIF)** above ~5 — either signals redundancy, not independent confirmation.
4. Redundant indicators are dropped or folded into a single combined vote, so the confluence score reflects how many *independent dimensions* agree — not how many indicators happen to.

This result is written into `qtx_config_versions.backtest_metrics_json` alongside performance metrics, so approving a config means seeing "these 5 indicators measure ~4.2 independent things," not taking redundancy on faith.

### Explicitly out of scope, and why

Order Flow Imbalance, L2 order book features, and fractional differentiation of price series are real, legitimate techniques in institutional quant research — but they require order-book depth data retail MT5/exchange access doesn't provide, and infrastructure (tick-level ingestion) this project deliberately avoided for good reasons (Section 12, database sizing). Not adopted, not because they're wrong, but because they're the wrong fit for what Quantora actually has access to.

---

## 6. Signal Validation & Outcome Labeling

Before any retraining or tuning can be trusted, Quantora needs to know whether the signals it already generated were actually *correct* — independent of whether a trade was ever placed on them.

### Quantora Validate — a standalone engine

- Runs on a schedule, independent of every other engine — reads only from `qtx_ohlcv` and `qtx_signals`, writes only to `qtx_signal_outcomes`.
- For every Tier 3 (Confirmed) signal not yet resolved, applies a **Triple Barrier** evaluation (upper barrier = take-profit, lower barrier = stop-loss, vertical barrier = mode-specific max holding window — 45 min for Scalp, 2 weeks for Swing, 3 months for Long): checks whether price hits TP or SL first, or times out.
- Labels the outcome as `WIN`, `LOSS`, or `TIMEOUT`, and computes the realized **R-multiple**.
- **Dynamic barriers, not fixed pip distances:** TP/SL distances should be set relative to recent volatility (e.g. a multiple of ATR) rather than a fixed number of pips/points — a fixed 30-pip stop means something very different in a calm market than a volatile one. This is the direct fix for the position-sizing formula in Section 7 assuming a static SL distance without context.

**Crypto vs. MT5 — an important distinction:** Validate only needs **price history**, not account access — this works identically across forex, synthetics, and crypto because Feed writes both sources into the same `qtx_ohlcv` table. No exchange API key or account connection required for this layer.

Account-level verification (real fills, real slippage, real fees on trades Sentinel actually placed) is a separate, later concern:

| | MT5 (forex/synthetics/gold, and crypto if via Deriv CFDs) | Crypto (via ccxt, if used) |
|---|---|---|
| Trade history | `mt5.history_deals_get()` | `exchange.fetch_my_trades()` |
| Open positions | `mt5.positions_get()` | `exchange.fetch_open_orders()` |
| Account balance | `mt5.account_info().balance` | `exchange.fetch_balance()` |

**Security note:** any crypto exchange API key used here should be created with **trading permissions only — never withdrawal permissions.** If a key ever leaks, the blast radius should be "bad trades," never "drained account."

### What this unlocks

With outcomes labeled and linked back to `signal_id` (and the exact indicator snapshot that produced the signal), Calibrate can ask precise questions: which symbol/mode combinations have real edge? Does Scalp on synthetics actually work, or drag the system down? Which indicator readings show up disproportionately in wins vs. losses? Does Tier 3 actually outperform Tier 2 (Section 4's required check)? This labeled dataset is the actual input Calibrate needs — not just "backtest the price history again."

---

## 7. Position Sizing & Risk Controls

### Cap 1 — Risk-based sizing (scales with account balance)

```
risk_amount = account_balance × risk_percent        # e.g. 1% of balance
lot_size    = risk_amount / (stop_loss_distance × value_per_point)
```

`stop_loss_distance` should be volatility-relative (ATR-based, per Section 6), not a fixed pip count. This scales correctly with account size — bigger balance, bigger position, same % risked.

### Cap 2 — Margin-based sizing (scales with leverage)

Risk-based sizing alone can produce a lot size that's fine relative to the stop-loss distance, but consumes a dangerous share of available margin — meaning a sharp adverse move *before* the stop-loss is even reached could trigger a margin call and force-close the position at a worse price than the intended SL. On Deriv's high leverage this is rarely the binding constraint at small account sizes (the minimum lot floor usually binds first — see Section 13), but it must still be checked on every trade:

```
required_margin = (lot_size × contract_size × price) / leverage

if required_margin > max_margin_utilization_pct × account_equity:
    scale lot_size down to fit within the margin cap
    log that realized risk % is now below target — never silently pretend it hit 1%
```

`leverage` and `contract_size` are pulled live per symbol (`mt5.symbol_info()`), never hardcoded — these vary by instrument and shift with broker updates.

### Cap 3 — Tail-risk buffer (CVaR-aware, not just fixed % risk)

Fixed % risk treats the stop-loss distance as the true worst case — it ignores slippage, gap-through-stop risk, and fat-tailed adverse moves. Once enough live trade history exists, Sentinel should track **Conditional Value-at-Risk (CVaR / Expected Shortfall)** at the portfolio level — the average loss in the worst 1–5% of outcomes, not just the "expected" stop-out — and use it as an additional sanity check alongside the drawdown kill-switch, not a replacement for it. This requires real trade history to compute meaningfully; it's a v1.5 addition once `qtx_trades` and `qtx_signal_outcomes` have real data, not a day-one requirement.

### Portfolio-level exposure cap (the backstop)

Group-level correlation caps (Section 3) limit risk *within* a group, but total exposure *across* all groups — when Scalp, Swing, and Long are all firing simultaneously across multiple symbols — needs its own ceiling. The margin check above is evaluated **cumulatively**: total margin in use across every open position, all modes, all symbols, against total account equity.

---

## 8. Realistic Performance Targets

Stated explicitly because "how do we ensure profit" and "can we get 99% win rate" were real questions worth answering directly, in the document, not just in conversation: **there is no version of this system, or any trading system, that guarantees near-certain wins or eliminates losses.** Systems claiming that are either lying, overfit to history in a way that won't survive live markets, or hiding catastrophic tail risk behind a clean-looking average case.

### The actual target: positive expectancy

```
Expectancy = (Win% × Avg Win) − (Loss% × Avg Loss)
```

A system does not need a high win rate to be profitable — it needs wins, summed, to outweigh losses, summed. A 40% win rate with a 1:2 risk-reward ratio is solidly profitable; a 90% win rate with an oversized rare loss is a blowup waiting to happen. Take-profit and stop-loss distances (Sections 6–7) are the direct lever controlling this trade-off, and they're a tunable, testable design decision — not a fixed assumption.

### Stated numeric targets (the actual promotion bar, referenced in Section 15)

These are starting targets, to be revised once real data exists — but "no target at all" was the actual gap flagged during review, so a concrete starting bar is stated here rather than left implicit:

| Metric | Target |
|---|---|
| Expectancy | Positive, net of realistic spread/slippage/commission |
| Win rate | 40–60% (mode-dependent; not a design failure if lower, as long as R:R compensates) |
| Sharpe Ratio | Meaningfully above zero after costs — a specific threshold TBD once a baseline (Section 8, below) exists |
| Max drawdown | Capped at 15–20% before the kill-switch halts trading entirely |
| Minimum sample size before trusting a config | At least 30–50 resolved trades per symbol/mode before treating win rate/expectancy as meaningful, not just lucky or unlucky variance |

### Baseline comparison — required, not optional

No config is considered validated until its performance is compared against a **null baseline** — random entry with identical risk parameters, or a simple always-long / buy-and-hold benchmark over the same window. Without this comparison, a positive-looking backtest proves nothing: it could just be reflecting a trending period that any strategy would have profited from. This baseline check is a required part of every Calibrate cycle's `backtest_metrics_json`, alongside the collinearity/VIF results (Section 5).

### Guarding against overfitting the tuning process itself

Tuning weights and thresholds across 11 symbols × 3 modes creates a large search space, and the more configurations tried, the more likely one looks good by chance alone. A lightweight guard, without needing the full academic machinery: Calibrate logs **how many distinct configurations were evaluated** before landing on a proposal, and that count is shown alongside the performance numbers when a config comes up for approval — so "this looks great" can be weighed against "how many tries did it take to find something that looks great."

### Validation methodology — purged walk-forward with embargo

Standard walk-forward and plain train/test splits can leak information across the boundary when trade outcomes overlap in time (a Swing trade opened near the end of a training window resolves inside the test window). Calibrate's backtesting should **purge** any training samples whose outcome window overlaps the test period, and apply a small **embargo** buffer after the split — a lightweight, adoptable version of a real methodological fix, without requiring a full combinatorial cross-validation framework this project doesn't need at its current scale.

---

## 9. "Trading Psychology" Layer

Concrete, engineered discipline — the system doesn't feel anything, but it behaves as if it does. This is the mechanism that makes Section 8's measurements trustworthy, per the reframing in Section 1 — not a source of edge on its own.

- **No FOMO rule**: if price moves significantly without a confirmed signal, Quantora logs it as a *missed setup* and does not chase a late entry. Hard rule, not a suggestion.
- **Cool-down after a loss**: a configurable pause after a stopped-out trade, preventing revenge-trading patterns.
- **Daily/weekly trade caps**: hard ceiling on new positions, independent of how many Tier 3 signals fire — quality over volume.
- **Auto-journaling**: every signal (all three tiers) logged with full indicator snapshot and confidence score.
- **Drawdown kill-switch**: account drawdown crossing the threshold (Section 8) halts new entries entirely until manually re-armed. Non-negotiable — no override from within the signal engine itself.
- **Live drift monitoring**: a rolling live win-rate/expectancy tracked per symbol/mode, separate from scheduled Calibrate cycles. If it drops meaningfully below what the last approved backtest predicted, Quantora flags it (does not auto-adjust) — an early signal that market regime has shifted before a full Calibrate cycle would otherwise catch it.

---

## 10. System Components (branded)

| Component | Role |
|---|---|
| **Quantora Feed** | Data engine. Pulls and stores historical + live OHLCV from MT5 (forex/synthetics/gold, and crypto if via Deriv CFDs) and/or `ccxt` (if using a separate spot exchange), plus the economic calendar. Only component that talks to external market data. |
| **Quantora Core** | Confluence/scoring engine. Reads from the DB, runs the three-tier state machine per symbol/mode, applies the news-blackout filter, and (Scalp only) the lightweight live-price polling from Section 2. |
| **Quantora Validate** | Signal-outcome engine. Independently checks every Confirmed signal via Triple Barrier evaluation, labels WIN/LOSS/TIMEOUT with a realized R-multiple, writes to `qtx_signal_outcomes`. The ground-truth dataset every other engine's quality depends on. |
| **Quantora Sentinel** | Risk and execution layer. Converts a Tier 3 signal into a sized order (all three sizing caps, Section 7), sends it to MT5 (or ccxt), and enforces the drawdown kill-switch. The only component that touches money, and the **sole owner of the live MT5 connection** (see Section 10's connection-ownership rule below). |
| **Quantora Calibrate** | Retraining/re-optimization engine. Re-tunes indicator weights and thresholds via purged walk-forward backtesting against `qtx_signal_outcomes`, including the mandatory collinearity/VIF check, baseline comparison, and configuration-count logging (Section 8). Never auto-deploys — every proposed config requires manual approval. |
| **Quantora Notify** | Delivers Tier 2/Tier 3 trading alerts *and* system-health alerts (Section 12) via Telegram — two distinct alert classes. |
| **Quantora Dashboard** | Read-only FastAPI + HTML monitoring UI — tier state per symbol, open trades, signal history, account equity, signal-outcome stats. Queries the database only; never writes to it. |

### MT5 connection ownership (single-owner rule)

`MetaTrader5` is a synchronous wrapper around a running desktop terminal, not an independent API — it isn't built to handle multiple processes opening competing connections at once. With Feed and Sentinel as separate processes both potentially calling `mt5.initialize()`, there's a real risk of connection contention or dropped requests (MT5 error 10021, `TOO_MANY_REQUESTS`).

**Rule: only one process owns the live MT5 connection at any time — Sentinel**, since it's the latency-sensitive one where a dropped or delayed call has real consequences. Feed does not open its own competing MT5 session for live polling; it requests current data through Sentinel's connection, and only opens its own short-lived MT5 session for backfill/historical work when Sentinel isn't actively placing orders. A design rule built in from the start, not a retrofit after a dropped-order bug in production.

---

## 11. Database Schema

Eight tables, all prefixed `qtx_`. Which engine owns which table matters — it's what keeps the "separate but connected" boundary real:

| Table | Written by | Read by | Purpose |
|---|---|---|---|
| `qtx_ohlcv` | Feed only | Core, Validate | Price history — base timeframe (M15) only; higher timeframes rolled up via query |
| `qtx_news_events` | Feed only | Core | Economic calendar — feeds the news-blackout filter |
| `qtx_config_versions` | Calibrate proposes, you approve | Core | Indicator weights, tier thresholds, collinearity/VIF results, baseline comparison, and configuration-count metadata per symbol/mode — `approved_flag` stays `'N'` until manually reviewed |
| `qtx_signal_state` | Core only | Notify, Sentinel, Dashboard | Live three-tier state — one row per symbol/mode, always current |
| `qtx_signals` | Core only | Dashboard, Calibrate, Validate | Append-only audit log of every tier change ever, even ones that never became trades |
| `qtx_signal_outcomes` | Validate only | Calibrate, Dashboard | Triple-Barrier WIN/LOSS/TIMEOUT label and realized R-multiple per Confirmed signal — the ground-truth dataset for retraining |
| `qtx_trades` | Sentinel only | Dashboard | Executed trades — entry, exit, P&L, slippage, fees, status |
| `qtx_account_snapshots` | Sentinel only | Sentinel (kill-switch, CVaR), Dashboard | Balance/equity/margin history |

Audit note across the whole schema: every table carries at least one timestamp column that is **never backfilled or edited by any engine** — set once, on insert, by the database itself in **UTC** (`SYS_EXTRACT_UTC(SYSTIMESTAMP)` on Oracle / `SYSUTCDATETIME()` on SQL Server), not by application code. Note that plain `SYSTIMESTAMP` would record the database server's *local* time, which is why the Oracle defaults wrap it.

**Column naming note:** the trading-mode column is `trade_mode`, not `mode`: `MODE` is a reserved word in Oracle 23ai+, both as a column name and as a bind variable (found by testing against a real Oracle Free instance).

**Current target: Oracle** (Oracle AI Database Free locally, or an Always Free Autonomous Database), accessed with `python-oracledb` in thin mode. SQL Server DDL is kept below as the documented fallback; switching back is one new module in each engine's storage layer, because all SQL is isolated there.

### Oracle (current)

```sql
-- ============================================================
-- QUANTORA — Oracle schema (current target)
--
-- Run as the quantora user (not SYS), e.g. via
--   python quantora_feed/main.py init-db
-- All audit timestamps default to SYS_EXTRACT_UTC(SYSTIMESTAMP): true UTC,
-- set by the database on insert, independent of the server's own time zone.
-- ============================================================

CREATE TABLE qtx_ohlcv (
    symbol          VARCHAR2(20)    NOT NULL,
    asset_class     VARCHAR2(20)    NOT NULL,   -- FOREX | SYNTHETIC | CRYPTO
    timeframe       VARCHAR2(5)     NOT NULL,   -- M15 stored; higher timeframes rolled up by query
    bar_ts          TIMESTAMP       NOT NULL,
    open_price      NUMBER(18,6)    NOT NULL,
    high_price      NUMBER(18,6)    NOT NULL,
    low_price       NUMBER(18,6)    NOT NULL,
    close_price     NUMBER(18,6)    NOT NULL,
    volume          NUMBER(18,2),
    price_side      VARCHAR2(4)     DEFAULT 'MID',   -- BID | ASK | MID (MT5 bars are BID)
    loaded_at       TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    CONSTRAINT pk_qtx_ohlcv PRIMARY KEY (symbol, timeframe, bar_ts)
);
CREATE INDEX ix_ohlcv_ts ON qtx_ohlcv (bar_ts);

CREATE TABLE qtx_news_events (
    event_id        NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_ts        TIMESTAMP       NOT NULL,
    currency        VARCHAR2(5)     NOT NULL,
    impact          VARCHAR2(10)    NOT NULL,
    title           VARCHAR2(200)   NOT NULL,
    loaded_at       TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP)
);
CREATE INDEX ix_news_ts ON qtx_news_events (event_ts, impact);

CREATE TABLE qtx_config_versions (
    version_id              NUMBER        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol                  VARCHAR2(20)  NOT NULL,
    trade_mode                    VARCHAR2(10)  NOT NULL,   -- SCALP | SWING | LONG ("mode" is reserved in Oracle 23ai+)
    weights_json            CLOB          NOT NULL,
    tier2_threshold         NUMBER(4,2)   NOT NULL,
    tier3_threshold         NUMBER(4,2)   NOT NULL,
    configs_evaluated_count NUMBER,
    backtest_metrics_json   CLOB,
    proposed_at             TIMESTAMP     DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    approved_flag           CHAR(1)       DEFAULT 'N' CHECK (approved_flag IN ('Y','N')),
    approved_at             TIMESTAMP,
    CONSTRAINT ck_weights_json CHECK (weights_json IS JSON),
    CONSTRAINT ck_metrics_json CHECK (backtest_metrics_json IS JSON)
);
CREATE INDEX ix_config_active ON qtx_config_versions (symbol, trade_mode, approved_flag);

CREATE TABLE qtx_signal_state (
    symbol                   VARCHAR2(20)    NOT NULL,
    trade_mode                     VARCHAR2(10)    NOT NULL,   -- SCALP | SWING | LONG ("mode" is reserved in Oracle 23ai+)
    current_tier             VARCHAR2(15)    NOT NULL,
    direction                VARCHAR2(4),
    score                    NUMBER(4,2),
    indicator_snapshot_json  CLOB,
    notes                    VARCHAR2(400),
    updated_at               TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    CONSTRAINT pk_qtx_signal_state PRIMARY KEY (symbol, trade_mode),
    CONSTRAINT ck_snapshot_json CHECK (indicator_snapshot_json IS JSON)
);

CREATE TABLE qtx_signals (
    signal_id                NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol                   VARCHAR2(20)    NOT NULL,
    trade_mode                     VARCHAR2(10)    NOT NULL,   -- SCALP | SWING | LONG ("mode" is reserved in Oracle 23ai+)
    tier                     VARCHAR2(15)    NOT NULL,
    direction                VARCHAR2(4),
    score                    NUMBER(4,2),
    entry_price              NUMBER(18,6),
    stop_loss                NUMBER(18,6),
    take_profit               NUMBER(18,6),
    lot_size                 NUMBER(10,4),
    indicator_snapshot_json  CLOB,
    created_at               TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    CONSTRAINT ck_signals_json CHECK (indicator_snapshot_json IS JSON)
);
CREATE INDEX ix_signals_symbol_ts ON qtx_signals (symbol, created_at);

CREATE TABLE qtx_signal_outcomes (
    outcome_id      NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id       NUMBER          NOT NULL REFERENCES qtx_signals(signal_id),
    outcome         VARCHAR2(10)    NOT NULL,
    r_multiple      NUMBER(6,3),
    resolved_price  NUMBER(18,6),
    resolved_at     TIMESTAMP,
    evaluated_at    TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    CONSTRAINT ck_outcome_value CHECK (outcome IN ('WIN','LOSS','TIMEOUT'))
);
CREATE INDEX ix_outcomes_signal ON qtx_signal_outcomes (signal_id);
CREATE INDEX ix_outcomes_outcome ON qtx_signal_outcomes (outcome);

CREATE TABLE qtx_trades (
    trade_id             NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id            NUMBER          REFERENCES qtx_signals(signal_id),
    symbol                VARCHAR2(20)    NOT NULL,
    direction             VARCHAR2(4)     NOT NULL,
    lot_size              NUMBER(10,4)    NOT NULL,
    signal_entry_price    NUMBER(18,6),
    entry_price           NUMBER(18,6)    NOT NULL,
    slippage_points       NUMBER(10,2),
    spread_at_entry       NUMBER(10,4),
    commission_usd        NUMBER(10,2),
    execution_latency_ms  NUMBER,
    stop_loss             NUMBER(18,6)    NOT NULL,
    take_profit            NUMBER(18,6),
    exit_price             NUMBER(18,6),
    pnl_usd                NUMBER(14,2),
    margin_used_usd        NUMBER(14,2),
    status                 VARCHAR2(10)    DEFAULT 'OPEN',
    opened_at              TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    closed_at              TIMESTAMP
);
CREATE INDEX ix_trades_status ON qtx_trades (status);

CREATE TABLE qtx_account_snapshots (
    snapshot_id             NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    balance_usd             NUMBER(14,2)    NOT NULL,
    equity_usd              NUMBER(14,2)    NOT NULL,
    margin_used_usd         NUMBER(14,2),
    margin_utilization_pct  NUMBER(5,2),
    cvar_estimate_usd       NUMBER(14,2),
    taken_at                TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP)
);
CREATE INDEX ix_snapshots_ts ON qtx_account_snapshots (taken_at);
```

### SQL Server (documented fallback)

```sql
-- ============================================================
-- QUANTORA — SQL Server schema (documented fallback; Oracle is current)
-- ============================================================

CREATE TABLE qtx_ohlcv (
    symbol          VARCHAR(20)     NOT NULL,
    asset_class     VARCHAR(20)     NOT NULL,   -- FOREX | SYNTHETIC | CRYPTO
    timeframe       VARCHAR(5)      NOT NULL,   -- M15, H1, H4, D1, W1
    bar_ts          DATETIME2       NOT NULL,
    open_price      DECIMAL(18,6)   NOT NULL,
    high_price      DECIMAL(18,6)   NOT NULL,
    low_price       DECIMAL(18,6)   NOT NULL,
    close_price     DECIMAL(18,6)   NOT NULL,
    volume          DECIMAL(18,2),
    price_side      VARCHAR(4)      DEFAULT 'MID',  -- BID | ASK | MID
    loaded_at       DATETIME2       DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_qtx_ohlcv PRIMARY KEY (symbol, timeframe, bar_ts)
);
CREATE INDEX ix_ohlcv_ts ON qtx_ohlcv (bar_ts);

CREATE TABLE qtx_news_events (
    event_id        INT             IDENTITY(1,1) PRIMARY KEY,
    event_ts        DATETIME2       NOT NULL,
    currency        VARCHAR(5)      NOT NULL,
    impact          VARCHAR(10)     NOT NULL,   -- LOW | MEDIUM | HIGH
    title           VARCHAR(200)    NOT NULL,
    loaded_at       DATETIME2       DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_news_ts ON qtx_news_events (event_ts, impact);

CREATE TABLE qtx_config_versions (
    version_id             INT           IDENTITY(1,1) PRIMARY KEY,
    symbol                 VARCHAR(20)   NOT NULL,
    trade_mode                   VARCHAR(10)   NOT NULL,   -- SCALP | SWING | LONG
    weights_json           NVARCHAR(MAX) NOT NULL,
    tier2_threshold        DECIMAL(4,2)  NOT NULL,
    tier3_threshold        DECIMAL(4,2)  NOT NULL,
    configs_evaluated_count INT,                     -- overfitting guard, Section 8
    backtest_metrics_json  NVARCHAR(MAX),             -- incl. VIF, baseline comparison, Sharpe/Sortino/Calmar
    proposed_at            DATETIME2     DEFAULT SYSUTCDATETIME(),
    approved_flag          CHAR(1)       DEFAULT 'N' CHECK (approved_flag IN ('Y','N')),
    approved_at            DATETIME2,
    CONSTRAINT ck_weights_json CHECK (ISJSON(weights_json) = 1),
    CONSTRAINT ck_metrics_json CHECK (backtest_metrics_json IS NULL OR ISJSON(backtest_metrics_json) = 1)
);
CREATE INDEX ix_config_active ON qtx_config_versions (symbol, trade_mode, approved_flag);

CREATE TABLE qtx_signal_state (
    symbol                   VARCHAR(20)   NOT NULL,
    trade_mode                     VARCHAR(10)   NOT NULL,
    current_tier             VARCHAR(15)   NOT NULL,   -- WATCHING | FORMING | CONFIRMED
    direction                VARCHAR(4),
    score                    DECIMAL(4,2),
    indicator_snapshot_json  NVARCHAR(MAX),
    notes                    VARCHAR(400),
    updated_at               DATETIME2     DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_qtx_signal_state PRIMARY KEY (symbol, trade_mode),
    CONSTRAINT ck_snapshot_json CHECK (indicator_snapshot_json IS NULL OR ISJSON(indicator_snapshot_json) = 1)
);

CREATE TABLE qtx_signals (
    signal_id                INT           IDENTITY(1,1) PRIMARY KEY,
    symbol                   VARCHAR(20)   NOT NULL,
    trade_mode                     VARCHAR(10)   NOT NULL,
    tier                     VARCHAR(15)   NOT NULL,
    direction                VARCHAR(4),
    score                    DECIMAL(4,2),
    entry_price              DECIMAL(18,6),   -- intended/suggested entry
    stop_loss                DECIMAL(18,6),
    take_profit              DECIMAL(18,6),
    lot_size                 DECIMAL(10,4),
    indicator_snapshot_json  NVARCHAR(MAX),
    created_at               DATETIME2     DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_signals_json CHECK (indicator_snapshot_json IS NULL OR ISJSON(indicator_snapshot_json) = 1)
);
CREATE INDEX ix_signals_symbol_ts ON qtx_signals (symbol, created_at);

-- Ground-truth outcome labels, written only by Quantora Validate (Triple Barrier Method)
CREATE TABLE qtx_signal_outcomes (
    outcome_id      INT             IDENTITY(1,1) PRIMARY KEY,
    signal_id       INT             NOT NULL REFERENCES qtx_signals(signal_id),
    outcome         VARCHAR(10)     NOT NULL,      -- WIN | LOSS | TIMEOUT
    r_multiple      DECIMAL(6,3),
    resolved_price  DECIMAL(18,6),
    resolved_at     DATETIME2,
    evaluated_at    DATETIME2       DEFAULT SYSUTCDATETIME(),
    CONSTRAINT ck_outcome_value CHECK (outcome IN ('WIN','LOSS','TIMEOUT'))
);
CREATE INDEX ix_outcomes_signal ON qtx_signal_outcomes (signal_id);
CREATE INDEX ix_outcomes_outcome ON qtx_signal_outcomes (outcome);

CREATE TABLE qtx_trades (
    trade_id            INT             IDENTITY(1,1) PRIMARY KEY,
    signal_id           INT             REFERENCES qtx_signals(signal_id),
    symbol               VARCHAR(20)     NOT NULL,
    direction            VARCHAR(4)      NOT NULL,
    lot_size             DECIMAL(10,4)   NOT NULL,
    signal_entry_price   DECIMAL(18,6),                -- intended price from the Confirmed signal
    entry_price          DECIMAL(18,6)   NOT NULL,     -- actual fill price
    slippage_points      DECIMAL(10,2),                -- entry_price vs signal_entry_price, execution-analytics
    spread_at_entry      DECIMAL(10,4),
    commission_usd       DECIMAL(10,2),
    execution_latency_ms INT,
    stop_loss             DECIMAL(18,6)   NOT NULL,
    take_profit           DECIMAL(18,6),
    exit_price            DECIMAL(18,6),
    pnl_usd               DECIMAL(14,2),
    margin_used_usd        DECIMAL(14,2),
    status                VARCHAR(10)     DEFAULT 'OPEN',  -- OPEN | CLOSED | STOPPED
    opened_at             DATETIME2       DEFAULT SYSUTCDATETIME(),
    closed_at             DATETIME2
);
CREATE INDEX ix_trades_status ON qtx_trades (status);

CREATE TABLE qtx_account_snapshots (
    snapshot_id             INT             IDENTITY(1,1) PRIMARY KEY,
    balance_usd             DECIMAL(14,2)   NOT NULL,
    equity_usd              DECIMAL(14,2)   NOT NULL,
    margin_used_usd         DECIMAL(14,2),
    margin_utilization_pct  DECIMAL(5,2),
    cvar_estimate_usd       DECIMAL(14,2),               -- Section 7 Cap 3, once enough trade history exists
    taken_at                DATETIME2       DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_snapshots_ts ON qtx_account_snapshots (taken_at);
```

---

## 12. Execution Realism & Operational Resilience

A backtest is only trustworthy if it's honest about costs and failure modes — this is where a design quietly lies to itself if left unaddressed.

### Pricing realism

- **Bid/ask vs. mid-price**: `qtx_ohlcv` carries a `price_side` column. A real buy fills at ask, a real sell fills at bid — spread is a modeled cost, not something eyeballed after the fact.
- **Dynamic contract specs**: pip value, minimum lot increment, and contract size are pulled live per symbol (`mt5.symbol_info()`) inside Sentinel's sizing logic — never hardcoded, and Deriv specifically updates synthetic-index specs from time to time (Section 3).
- **Weekend gap risk (forex/gold specifically)**: a stop loss doesn't protect against a weekend gap, it just fills at whatever price is available on reopen. Decide explicitly whether to flatten forex/gold positions before weekend close or accept gap risk as a known, sized cost. Synthetic indices and crypto trade continuously and don't have this issue.
- **Lot-size carryover risk on Deriv specifically**: MT5 does not reset the lot-size field when switching symbols/charts. Moving from a large-lot instrument (e.g. Boom/Crash) to a small-lot one (V75, 0.001 minimum) without manually resetting the lot field is a realistic way to place an order 100–200x larger than intended. Sentinel must explicitly set the lot size per order from calculated values — never rely on whatever value happens to be loaded in the terminal.

### Operational resilience

- **MT5 requires a running, logged-in terminal** — not a pure API. Decide where that machine lives before going live.
- **Reconnect/retry with backoff** required on every external connection (MT5, ccxt if used, news API) — a logged warning and move-on is not sufficient for something meant to run unattended.
- **System-health alerts, separate from trading alerts**: Notify covers two classes — Tier 2/3 trading signals, and system-health warnings (e.g. "Feed hasn't completed a cycle in 2 hours"). A dead system that still *looks* alive in the database is a dangerous silent failure mode.
- **Timezone normalization (resolved)**: MT5 broker servers commonly run on EET/EEST (GMT+2/+3, shifting with EU DST), not UTC — treating the raw epoch timestamp as clean UTC without accounting for the offset can silently misalign candle boundaries, especially across a DST transition. **Fix**: Feed captures the broker's current UTC offset at ingestion and normalizes every MT5 timestamp to true UTC before writing to `qtx_ohlcv` — an explicit, logged conversion, never an assumption. D1/W1 rollups need one further explicit decision: align to UTC midnight, or the broker's own daily rollover time — pick one and document it in Core's rollup query.

### Housekeeping

- **Secrets hygiene**: confirm `.gitignore` excludes `.env` before this ever touches a repository. Crypto API keys (if `ccxt` is used): trading permissions only, never withdrawal.
- **Database backup**: a simple scheduled backup, even for a personal single-instance deployment — losing the machine without one means losing the entire audit trail and signal history.

---

## 13. Getting Started — Capital & Funding Plan

Stated explicitly because "how much do we actually start with" turned out to have a real, calculable answer specific to this broker and instrument set — not a guess.

### Why the minimum lot size, not the deposit minimum, sets the real floor

Deriv's account minimum deposit is $5, but that's not the binding constraint — **the minimum tradeable position size is.** Every instrument enforces a minimum lot, and that minimum risks a fixed dollar amount regardless of account size. The account needs to be large enough that this fixed minimum risk still equals a sane percentage (Section 7's risk %) of the balance.

### Worked numbers — V75 (0.001 minimum lot), the recommended starting instrument

At 0.001 lot on V75, a 1,000-point move equals $1.00 (verified against real contract specs, not estimated):

| Trade style | Typical SL distance | $ risked at min lot | Account needed at 1% risk |
|---|---|---|---|
| Scalp, tight stop | 500 points | $0.50 | **$50** |
| Scalp, wider stop | 1,000 points | $1.00 | **$100** |
| Swing | 3,000 points | $3.00 | **$300** |
| Swing, wider | 5,000 points | $5.00 | **$500** |

### Worked numbers — forex majors (0.01 minimum lot, for comparison)

| Symbol, mode | Typical SL | $ risked at min lot | Account needed at 1% |
|---|---|---|---|
| AUDUSD / USDJPY — Swing | ~45–50 pips | ~$4.50 | **~$450** |
| EURUSD — Swing | 50 pips | $5.00 | **$500** |
| GBPUSD — Swing | 60 pips | $6.00 | **$600** |
| XAUUSD (Gold) — Swing | 300 points | $30.00 | **$1,500–3,000** |

(Illustrative — actual values depend on live spread/contract specs on the specific account; Sentinel is designed to pull these live rather than assume them.)

### Recommended path

1. **Demo first — but at a realistic balance, not the default.** Deriv demo accounts typically start with a large fake balance (e.g. $10,000); reset it to **$50–100** before starting. Trading a $10,000 demo teaches the wrong instincts about position sizing and the psychological weight of a loss — the whole point of demoing "like it's real" is that the numbers have to actually match what you'd fund for real.
2. **Start with V75 (or another `(1s)` low-lot variant) in Scalp or Swing mode** — the most capital-efficient instrument on this broker by a wide margin, and explicitly the one the broker's own lot-size structure favors for small accounts.
3. **Run the demo long enough for a real sample** — Section 8's minimum of 30–50 resolved trades applies here too; a good week is not evidence.
4. **Fund a live account at the same small size the demo used** — $50–100, not a "graduation" jump to something larger. Scaling should be earned by live results (via `qtx_signal_outcomes` and the drift monitoring in Section 9), not triggered by a good demo streak.
5. **Add forex majors once the account has organically grown past ~$450–600**, then Gold last, once past ~$1,500–3,000 — in that order, for the reasons in Section 3.
6. **Never load a large-lot instrument's chart without re-confirming the lot size field** — see Section 12's operational note on this specific, real risk.

---

## 14. External Review Findings — Disposition

This project's specification was run through an external critique exercise (an AI-generated "institutional panel" review, treated with appropriate skepticism about its packaging — invented scores and unverified citations were discounted, but the underlying, well-known quant concepts were evaluated on their own merits). Findings are recorded here with an explicit decision, so the reasoning isn't lost and doesn't need to be re-litigated later.

### Adopted (folded into this document)

- Triple Barrier Method terminology and dynamic (volatility-relative) barriers — Sections 6–7
- Expanded performance metrics (Sortino, Calmar, Profit Factor, Expectancy, Ulcer Index) — Section 8
- Execution/slippage/fee logging — `qtx_trades`, Section 11
- Purged walk-forward with embargo — Section 8
- Lightweight overfitting guard (configuration-count logging) — Section 8, `qtx_config_versions`
- Baseline (null-hypothesis) comparison requirement — Section 8
- CVaR as a portfolio-level tail-risk check, alongside (not replacing) the drawdown kill-switch — Section 7, Cap 3
- MT5 connection single-ownership rule — Section 10
- Broker timezone/DST offset normalization — Section 12
- Lightweight live-price polling for Scalp mode's Tier 2→3 lag — Section 2
- Reframing "discipline" as capital-preservation, not alpha generation — Section 1

### Deliberately deferred (real value, needs more live data or a specific trigger first)

- Rolling, computed correlation groups instead of static ones (Section 3) — once enough `qtx_ohlcv` history exists
- Expected-value/probability-scored signals instead of binary confirmation — once `qtx_signal_outcomes` has enough labeled history per symbol/mode
- Monte Carlo trade reshuffling / bootstrap confidence intervals — once there's a real trade sample to reshuffle
- Lightweight regime detection (ATR-percentile / ADX-threshold based, not a full Hidden Markov Model) — once Calibrate exists and can measure whether it actually improves outcomes
- ATR-percentile-based volatility targeting for position sizing — once Sentinel's basic sizing is live and proven

### Explicitly out of scope (real techniques, wrong fit for this project)

- Order Flow Imbalance / L2 order book features — requires order-book depth data retail access doesn't provide
- Fractional differentiation of price series — built for ML feature engineering; Quantora is a rules-based confluence system, not a model trained on differenced price series
- GARCH/EGARCH volatility modeling — legitimate but heavy; the ATR-percentile approximation (above) gets most of the value at a fraction of the complexity
- Kafka/ZeroMQ/NATS message bus — solves a throughput problem this project doesn't have; engines poll every 15 minutes, not thousands of times per second
- "Remove all standard indicators" — overstated; the real problem (collinearity) is already solved by the VIF/correlation check in Section 5

---

## 15. Project Folder Structure

Each engine lives in its own top-level folder with its **own** `config.py`, `db.py`, and `requirements.txt` — no shared code imports between engines. The only thing they share is the database schema.

```
quantora/
├── README.md
├── design_doc/
│   └── Quantora_Specification.md
├── sql/
│   ├── quantora_oracle_schema.sql      # current target
│   └── quantora_sqlserver_schema.sql   # documented fallback
│
├── quantora_feed/               ← built (modular: each concern behind an interface + registry)
│   ├── main.py                  # CLI: check / init-db / backfill / once / run
│   ├── pipeline.py              # orchestration — knows only the interfaces below
│   ├── config.py
│   ├── models.py
│   ├── resilience.py            # retry with backoff
│   ├── price_sources/           # base.py · mt5.py  (ccxt.py later — pending decision, Section 3)
│   ├── news_sources/            # base.py · forexfactory.py · fmp.py
│   ├── storage/                 # base.py · oracle.py · preview.py (--dry-run CSVs)
│   ├── tests/
│   ├── requirements.txt
│   ├── .env.example
│   └── preview_output/          ← created by --dry-run, gitignored
│
├── quantora_backtest/           ← built (step 2): reads qtx_ohlcv only, never writes
│   ├── main.py · runner.py · settings.toml
│   ├── simulator.py             # triple barrier on the M15 path, costs, pessimistic fills
│   ├── walk_forward.py          # purged walk-forward + embargo, configs counted (Section 8)
│   ├── baselines.py             # random entry, always long, buy & hold (Section 8)
│   ├── metrics.py · gate.py · timeframes.py · indicators.py
│   ├── strategies/              # plugin interface; Core's confluence logic is judged here too
│   └── data_sources/            # oracle.py (read-only) · csv_dir.py
│
├── quantora_core/               ← built (step 3)
│   ├── config.py · settings.toml
│   ├── indicators.py            # dimension-based set, Section 5
│   ├── confluence.py            # conditions, score, tiers, notes, snapshot
│   ├── state_machine.py         # Watching / Forming / Confirmed transitions
│   ├── news_filter.py
│   ├── engine.py                # live evaluation (with catch-up) + replay — same code path
│   ├── diagnose.py              # correlation / VIF of indicator votes, Section 5
│   ├── timeframes.py            # same UTC alignment as the backtester
│   ├── storage/                 # oracle.py · local.py (CSV + JSON, no DB)
│   ├── main.py
│   └── requirements.txt
│   (live_price_poll.py for Scalp Tier 2 — deferred to Sentinel's MT5 gateway, Section 10)
│
├── quantora_validate/           ← built (step 4): reads qtx_ohlcv + qtx_signals, writes qtx_signal_outcomes only
│   ├── config.py · settings.toml
│   ├── triple_barrier.py        # WIN/LOSS/TIMEOUT labeling, Section 6 (same rules as the backtester)
│   ├── validate.py              # labels Tier 3 signals + hypothetical Tier 2 entries (Section 4 check)
│   ├── storage/                 # oracle.py · local.py
│   ├── main.py
│   └── requirements.txt
│
├── integration/                 # end-to-end test: every engine's real CLI, files or real Oracle
│   ├── run_pipeline.py
│   └── fake_mt5_feed.py         # simulated MT5 terminal (MT5 itself is Windows-only)
│
├── quantora_sentinel/
│   ├── config.py
│   ├── db.py
│   ├── mt5_gateway.py           # sole MT5 connection owner, Section 10
│   ├── position_sizing.py       # three-cap sizing, Section 7
│   ├── execution_mt5.py
│   ├── execution_ccxt.py        # if used
│   ├── kill_switch.py
│   ├── cvar.py                  # Section 7 Cap 3
│   ├── main.py
│   └── requirements.txt
│
├── quantora_calibrate/
│   ├── config.py
│   ├── db.py
│   ├── purged_walk_forward.py   # Section 8
│   ├── collinearity_check.py    # Section 5
│   ├── baseline_comparison.py   # Section 8
│   ├── main.py
│   └── requirements.txt
│
├── quantora_notify/             ← built (step 5): reads only; keeps its own small state file
│   ├── config.py · settings.toml
│   ├── channels/                # telegram.py (Bot API) · console.py · base.py
│   ├── health.py                # Feed / Core / Validate / database health checks
│   ├── messages.py              # alert texts
│   ├── notifier.py · state.py   # at-least-once delivery, no floods, no repeats
│   ├── storage/                 # oracle.py · local.py
│   ├── main.py                  # chat-id · test · check · once · run
│   └── requirements.txt
│
└── quantora_dashboard/
    ├── app.py
    ├── routes/
    │   ├── signals.py
    │   ├── outcomes.py
    │   ├── trades.py
    │   └── account.py
    ├── templates/
    ├── static/
    └── requirements.txt
```

**Rules that keep this boundary real:**
- No engine imports another engine's code — ever. If Core needs something Feed knows, it reads it from the database.
- Every engine can be run, tested, and deleted independently.
- `quantora_dashboard` is strictly read-only — it queries, never writes.

---

## 16. Infrastructure Stack

- **Broker**: Deriv, via MT5 (`MetaTrader5` Python package)
- **Crypto** (if not using Deriv CFDs): `ccxt` — decision pending, Section 3
- **Database**: Oracle (current), `python-oracledb` thin mode — SQL Server documented as fallback, Section 11
- **Indicators**: `pandas-ta` or `TA-Lib`
- **Economic calendar**: Finnhub or FinancialModelingPrep free-tier APIs
- **Alerts**: Telegram Bot API — trading alerts and system-health alerts
- **Dashboard**: FastAPI (backend) + HTML/Jinja2 templates, read-only
- **Timeframe alignment (decided)**: H1/H4/D1 buckets aligned to UTC, W1 starts Monday 00:00 UTC — used by the backtester and to be used identically by Core.
- **Scheduling**: cron to start; Airflow only if pipeline complexity later justifies it

---

## 17. Build Order (phased, not all at once)

1. **Quantora Feed + schema** — get historical data flowing and stored reliably.
2. **Backtesting framework** — purged walk-forward harness with baseline comparison built in from the start (Section 8), not retrofitted later.
3. **Quantora Core (confluence engine)** — starting with V75 only (Section 13's recommended instrument), Swing mode, dimension-based indicators (Section 5).
4. **Quantora Validate** — Triple Barrier outcome labeling. A prerequisite for meaningful Calibrate work, not a later add-on.
5. **Quantora Notify (Telegram)** — trading alerts first, system-health alerts as soon as Feed/Core run unattended for any length of time.
6. **Demo trading at realistic balance** ($50–100, Section 13) — validate live behavior against backtest expectations. **Explicit promotion gate**: Section 8's numeric targets (min. 30–50 resolved trades, positive expectancy, Tier 3 outperforming Tier 2 per Section 4) must be met before proceeding — not a vague "long enough."
7. **Quantora Sentinel (execution)** — only after the promotion gate is cleared, with all three sizing caps (Section 7) implemented from day one, and MT5 single-connection ownership enforced.
8. **Fund a live account at the same small size the demo used** (Section 13) — expand instrument coverage (forex majors, then Gold) and modes (Scalp, Long) gradually, gated by the same promotion criteria each time, not by excitement after early results.
9. **Quantora Calibrate** — including collinearity/VIF check, baseline comparison, and configuration-count logging — added once enough live/demo history and labeled outcomes exist to validate against.
10. **Quantora Dashboard** — read-only monitoring UI, added once there's real data worth looking at.

---

## 18. Open Item to Flag Early

If Quantora stays a personal tool, none of this needs anything beyond good engineering. If it becomes a product other people connect their accounts to or invest through, that crosses into regulated financial-services territory in most jurisdictions (Nigeria's SEC now treats digital assets as securities under the ISA 2025 framework, and automated trading/advisory services for third parties typically require licensing). Worth a conversation with a lawyer before that step — separate track from the engineering roadmap, not a blocker to building it for yourself first.
