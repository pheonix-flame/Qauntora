# Quantora

A patient, multi-asset signal and execution system for Forex, Gold, Synthetic Indices and Crypto on Deriv MT5.
The full design is in [`design_doc/Quantora_Specification.md`](design_doc/Quantora_Specification.md).

**Status:** Build-order steps 1–2 (Feed + schema, backtesting harness) are done. Nothing in this repo places trades yet.

| Engine | Status |
|---|---|
| `sql/`: Oracle schema (SQL Server kept as a documented fallback) | done |
| `quantora_feed/`: price history and economic calendar | done |
| `quantora_backtest/`: purged walk-forward harness, baselines, promotion gate | done |
| Core, Validate, Notify, Sentinel, Calibrate, Dashboard | not started (see spec Section 17) |

---

## What you need (personal use)

All of this is free except an optional VPS.

| # | Thing | Why | Where |
|---|---|---|---|
| 1 | **A Windows machine** | The `MetaTrader5` Python package only runs on Windows and controls a running MT5 desktop terminal. | Your own PC to start. For 24/7 running later, a small Windows VPS. |
| 2 | **Deriv account + MT5 demo account** | Price data now; execution later | [deriv.com](https://deriv.com) → Trader's Hub → switch to **Demo** → add an MT5 account. Pick an account type that includes both synthetics and forex/crypto, or note that you have two logins. |
| 3 | **MT5 desktop terminal** | The Python package drives it | Download from Deriv's MT5 page, then log in with your MT5 login, password and server. |
| 4 | **Python 3.11 or 3.12, 64-bit** | Runtime | python.org. Tick "Add to PATH". |
| 5 | **Oracle AI Database Free** (formerly 23ai Free) | Database | [oracle.com/database/free](https://www.oracle.com/database/free/get-started/) → Windows download → run `setup.exe` and choose a password for SYS/SYSTEM. It's free, runs on the same PC as MT5, and its storage cap is far more than M15 bars need (about 350k rows per year for all 10 symbols). No Oracle Client install is needed: Python connects with `python-oracledb` in thin mode. Optional GUI: SQL Developer, or its VS Code extension. |
| 6 | **Telegram bot token** | Quantora Notify (step 5) | In Telegram, message **@BotFather** → `/newbot` → copy the token. To get your chat id, message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates`. |

### "API keys": what you actually need

- **MT5 / Deriv:** no API key. Feed logs in with your **MT5 account number, password and server name** (e.g. `Deriv-Demo`), all shown when you create the MT5 account. (Deriv's separate `api.deriv.com` token is for Deriv's own WebSocket API, which Quantora doesn't use.)
- **Economic calendar:** none needed. The default `NEWS_PROVIDER=forexfactory` uses a free public weekly feed. Financial Modeling Prep (`fmp`) is optional: sign up at financialmodelingprep.com and copy the key from the dashboard. Check that your plan includes the economic calendar. Finnhub's calendar is premium-only, so it isn't used.
- **Crypto:** none, if you use Deriv's own BTC/ETH CFDs (the default here, one broker, one connection). If you later add a spot exchange via `ccxt`, create a key with **trade permission only, never withdrawal**, and IP-whitelist it.
- **Telegram:** the BotFather token above (needed from step 5 on).

---

## Where you enter passwords and settings

There are only **two files you edit**:

| File | What goes in it | Secret? |
|---|---|---|
| `Qauntora/.env` (copy it from `Qauntora/.env.example`) | MT5 login/password/server, Oracle password, optional API keys. **One file for every engine.** Each line says where to get the value. | yes, gitignored, never committed |
| `quantora_backtest/settings.toml` | Trading costs per symbol (pasted from Feed's `check`), mode settings, gate thresholds | no |

## Setting up Quantora Feed (Windows)

```powershell
git clone -b debug/determined-babbage-63pcgv https://github.com/pheonix-flame/Qauntora.git
cd Qauntora
copy .env.example .env      # then edit .env: fill every line marked >>> REQUIRED
cd quantora_feed
py -3.12 -m venv ..\.venv
..\.venv\Scripts\activate
pip install -r requirements.txt
```

One virtual environment (`Qauntora\.venv`) serves every engine.

Create a `quantora` database user once. Open a Command Prompt and connect to the pluggable database as an admin:

```powershell
sqlplus sys@localhost:1521/FREEPDB1 as sysdba
```

```sql
CREATE USER quantora IDENTIFIED BY "choose-a-password"
  DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO quantora;
EXIT
```

Put that password in `Qauntora/.env` as `ORACLE_PASSWORD` (`ORACLE_DSN` defaults to `localhost:1521/FREEPDB1`). Then:

```powershell
python main.py check                     # MT5 login, symbol names + lot specs, broker clock, DB, news
python main.py init-db                   # creates the 8 qtx_ tables in your quantora schema
python main.py backfill --days 30 --dry-run --symbols V75   # preview as CSV first
python main.py backfill --days 365       # real history into Oracle
python main.py run                       # keep it updated every M15 close (Ctrl+C to stop)
python main.py verify --days 7           # prove the stored data is right (see below)
```

`check` prints the minimum lot, contract size and tick value for every symbol on your account. Use it to confirm the Section 13 capital numbers against your real specs. If a symbol shows `NOT FOUND`, it suggests similar names. Set `QTX_BROKER_NAME_<KEY>` in `.env` to the right one.

Tests run on any OS (MT5 and Oracle are faked):

```bash
cd quantora_feed && python -m pytest -q
```

## What Feed collects

**Only the 10 symbols configured in `quantora_feed/config.py`**, nothing else. Each is stored as **M15 candles only**. Core will build H1/H4/D1/W1 from these with queries. Feed also stores the economic calendar.

| Group | Keys (stored as) | Deriv MT5 name |
|---|---|---|
| Synthetics | `V75`, `V100`, `STEP` | Volatility 75 Index, Volatility 100 Index, Step Index |
| Forex | `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD` | same |
| Gold | `XAUUSD` | same |
| Crypto (Deriv CFDs) | `BTCUSD`, `ETHUSD` | same |

The spec's optional third crypto (e.g. SOL) is not included. To collect fewer symbols, set `QTX_SYMBOLS=V75,EURUSD` in `.env`. To add one, add a line to `_DEFAULT_SYMBOLS` in `config.py`.

## Proving the data is right: `python main.py verify`

The unit tests only prove the code's logic, using a fake broker. On your PC, run this after a backfill:

```powershell
python main.py verify --days 7
```

It checks four independent things. Each catches mistakes the others can't:

| Layer | Needs | What it proves |
|---|---|---|
| **Broker clock** | MT5 | Your clock setting (auto-detected, or `BROKER_TZ`) matches the server clock measured from a live tick right now. A wrong setting shifts *every* symbol's timestamps, and the other MT5 checks can't see that. |
| **Integrity** | DB only | No impossible candles (high below close, negative prices), timestamps on 15-minute boundaries, no candle stored before it closed. It flags gaps (forex weekends excluded), frozen prices, spikes, and a feed that has stopped. |
| **Broker cross-check** | MT5 | Stored M15 equals a fresh pull from MT5, so nothing was lost or rounded on the way into Oracle. Your M15 candles also roll up exactly into MT5's own H1 candles, so none are missing. |
| **Independent source** | Internet | Deriv's BTC/ETH 15-minute moves line up with Coinbase's (a different company, free API, no key) at zero time shift. That proves the stored times are true UTC. If they line up best when shifted 2 h, it says so. |

Results are `ok` / `WARN` (worth a look, often a holiday or Feed not running) / `FAIL` (don't build on this data). The exit code is 1 on any FAIL. Checks that can't run show `skip`, never a false `ok`.

It also prints the last 3 candles per symbol in **UTC and broker time**. Open the MT5 chart on M15 and hover those candles: the prices should match exactly at the broker-time column. That's the quickest check by eye.

## Backtesting: `quantora_backtest/`

Build step 2. It answers one question about any strategy: **is there evidence of an edge, after costs, that beats luck?** It only reads `qtx_ohlcv`; it never writes to the database and never touches MT5. It runs on any OS.

```powershell
cd Qauntora\quantora_backtest
pip install -r requirements.txt          # same .venv as Feed
python main.py demo                      # no DB needed: proves the harness finds no edge in random data
python main.py check                     # history per symbol, which modes have enough data, costs set?
python main.py run --symbol V75 --mode SWING
```

Before `run`, paste the cost lines from Feed's `python main.py check` under `[costs]` in `settings.toml`. A symbol without costs is refused, because a cost-free backtest overstates results.

**History needed:** SWING needs at least 363 days for one walk-forward fold. More history gives more folds and more trust, so backfill 3 years for swing work (`python main.py backfill --days 1095` in Feed; set MT5's "Max bars in chart" to Unlimited first). LONG needs about 2 years minimum, SCALP about 3 months.

What a run does:
- **Triple-barrier trades on the M15 price path.** Entry is the next bar's open after a closed-bar decision. SL/TP are set in ATRs, with a time limit per mode. Long trades pay the spread on entry and short trades on exit, plus slippage. If SL and TP fall in the same bar, it counts SL. A stop gapped through fills at the worse price.
- **Purged walk-forward with embargo.** Parameters are picked on train data only, and only test-window trades are reported. Training trades whose outcome reaches into the test window are purged. Every configuration tried is counted.
- **Baselines.** Random entry (200 runs, same windows, barriers and costs) gives the "beats luck?" percentile. Always-long at the strategy's own times checks whether the direction calls add anything. Buy & hold is shown for context.
- **Metrics (Section 8).** Expectancy (R), win rate, profit factor, Sharpe, Sortino, max drawdown, Calmar and Ulcer, plus in-sample vs out-of-sample (a big drop means overfitting).
- **Promotion gate.** At least 30 trades, positive expectancy, drawdown of 20% or less, and beating 95% of random runs. "Tier 3 beats Tier 2" is listed as not yet checkable (it needs Core).

Reports are saved to `quantora_backtest/backtest_output/` as JSON plus a trades CSV. The included `ema_cross` strategy only exercises the harness and gives Core a simple benchmark to beat. **It is not Quantora's strategy, and on real data it will most likely fail the gate.** That's the harness working.

```
quantora_backtest/
├── main.py            CLI: check / strategies / run / demo
├── settings.toml      costs, modes, gate (you edit this)
├── runner.py          one backtest end to end + report
├── simulator.py       triple-barrier trade simulation with costs
├── walk_forward.py    purged walk-forward + embargo + config counting
├── baselines.py       random entry, always long, buy & hold
├── metrics.py · gate.py · timeframes.py (M15 → H1/H4/D1/W1, UTC-aligned) · indicators.py
├── strategies/        base.py (Strategy) · ema_cross.py (reference) · __init__.py (registry)
├── data_sources/      base.py · oracle.py (read-only) · csv_dir.py (Feed --dry-run CSVs)
└── tests/             incl. "finds no edge in a random walk" and "detects a planted edge"
```

## How Feed is organised

Each concern sits behind a small interface with a registry. Adding a data source, calendar or database is one new file and one registry line. The pipeline doesn't change.

```
quantora_feed/
├── main.py              CLI only: check / init-db / backfill / once / run
├── pipeline.py          orchestration: sync symbols + news; knows only the interfaces
├── config.py            .env → typed config; symbol universe (key, broker name, asset class, source)
├── models.py            qtx_ohlcv / qtx_news_events row shapes
├── resilience.py        retry with exponential backoff
├── price_sources/       base.py (PriceSource) · mt5.py · __init__.py (registry; add ccxt.py here later)
├── news_sources/        base.py (NewsProvider) · forexfactory.py · fmp.py · __init__.py (registry)
├── storage/             base.py (Store) · oracle.py · preview.py (--dry-run CSVs) · __init__.py
├── verification/        `verify`: integrity.py · broker.py (vs MT5) · reference.py (vs Coinbase)
└── tests/               fake MT5 terminal + unit tests
```

This splits the flat `mt5_source.py` / `news_source.py` / `db.py` from spec Section 15 into packages. The cross-engine rule is unchanged: no engine imports another engine's code, and they share only the database.

### Behaviour worth knowing

- **Only closed bars are stored.** The still-forming candle is never written.
- **Timestamps are true UTC.** The broker clock offset is auto-detected from a live V75 tick (synthetics tick 24/7) and logged. If your server observes DST, set `BROKER_TZ` so history across DST changes converts correctly.
- **`price_side = 'BID'`**, because MT5 bars are bid-based. Volume is MT5 tick volume.
- **Inserts are insert-only and idempotent** (`MERGE … WHEN NOT MATCHED THEN INSERT`). Re-running a backfill never edits a stored row, so `loaded_at` stays the true first-insert time.
- **Audit timestamps are UTC.** Schema defaults use `SYS_EXTRACT_UTC(SYSTIMESTAMP)`. Plain `SYSTIMESTAMP` would record the database server's local time.
- **Oracle Cloud instead of local?** An Always Free Autonomous Database also works. Set `ORACLE_DSN` to its service name and `ORACLE_WALLET_DIR` / `ORACLE_WALLET_PASSWORD` to the unzipped wallet. Local is simpler, since MT5 already has to run on your PC.
- **MT5 single-owner rule (Section 10):** until Sentinel exists, Feed is the only MT5 user. When Sentinel is built, Feed's live loop moves behind Sentinel's connection.
