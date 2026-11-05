# Quantora

A patient, multi-asset signal and execution system for Forex, Gold, Synthetic Indices and Crypto on Deriv MT5.
The full design is in [`design_doc/Quantora_Specification.md`](design_doc/Quantora_Specification.md).

**Status:** Build-order steps 1–5 (Feed + schema, backtesting harness, Core, Validate, Notify) are done, and tested together end-to-end against a real Oracle database. Nothing in this repo places trades yet: Core *suggests*, and execution (Sentinel) is step 7.

| Engine | Status |
|---|---|
| `sql/`: Oracle schema (SQL Server kept as a documented fallback) | done |
| `quantora_feed/`: price history and economic calendar | done |
| `quantora_backtest/`: purged walk-forward harness, baselines, promotion gate | done |
| `quantora_core/`: three-tier confluence engine (Watching / Forming / Confirmed) | done |
| `quantora_validate/`: Triple Barrier outcome labelling (WIN / LOSS / TIMEOUT + R) | done |
| `integration/`: end-to-end test of all engines together | done |
| `quantora_notify/`: Telegram trading alerts + system-health alerts | done |
| Sentinel, Calibrate, Dashboard | not started (see spec Section 17) |

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
| 6 | **A Telegram bot** (free, 5 minutes) | Quantora Notify alerts on your phone | See **Create your Telegram bot** below. |

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
| `quantora_core/settings.toml` (optional) | Which symbols/modes Core watches (default: V75 SWING), indicator settings | no |

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

These exact steps were tested against Oracle AI Database Free 23.26. If `init-db` is ever interrupted, run it again: it creates whatever is missing and skips what already exists.

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

## Signals: `quantora_core/`

Build step 3, the three-tier engine from spec Section 4. It runs on any OS, reads the prices Feed stored, and needs no MT5.

```powershell
cd Qauntora\quantora_core
pip install -r requirements.txt          # same .venv
python main.py check                     # data per pair, which config each uses
python main.py once                      # evaluate the latest closed bar, update state, log any tier change
python main.py status                    # current tier + human summary per pair
python main.py run                       # keep going: wakes 60 s after every M15 close
python main.py replay --symbol V75 --mode SWING     # history -> signals CSV for the backtester
python main.py diagnose --symbol V75 --mode SWING   # are the indicators independent? (Section 5)
```

**How a tier is decided** (once per closed bar of the mode's timeframe, H4 for SWING):
- **Direction:** EMA20 vs EMA50.
- **Conditions,** checked for that direction, one per market dimension (Section 5):
  - trend: close on the right side of EMA50
  - strength: ADX ≥ 20 (a filter, not a direction)
  - momentum: RSI between 50 and 70 for longs (30 to 50 for shorts), or MACD; one, not both
  - location: price inside the Bollinger Bands (no-FOMO: it never confirms while stretched)
  - volume: OBV with the trend (crypto only)
- **Score:** the weighted share of conditions met, from 0 to 1.
- **Tier 2, FORMING:** score ≥ 0.6. The notes say exactly what it's waiting for, e.g. *"Setup forming BUY (score 0.75). Watching for: a confirmed bullish close. No trade yet."*
- **Tier 3, CONFIRMED:** score ≥ 0.8, plus a **confirming close** (the bar closes in the trade direction beyond the previous close), plus the trigger bar isn't a chase (range ≤ 2×ATR), plus **no news blackout**. A blackout is 30 min before to 15 min after a high-impact event for the symbol's currencies; synthetics are exempt. It records entry, SL and TP using the same ATR multiples as the backtester. Lot size is left for Sentinel, which knows the live balance.
- **Tier 1, WATCHING:** everything else, including indicator warm-up.

**What it writes:** only `qtx_signal_state` (one current row per pair) and `qtx_signals` (an append-only row for every tier change, with the full indicator snapshot as JSON). A confirmed state that lasts several bars is one signal, not one per bar. If Core was offline, the next run catches up and logs the changes it missed. If the latest bar isn't fully stored yet, Core waits for Feed instead of evaluating a half-built candle.

**Config:** the `[defaults]` in `quantora_core/settings.toml` are used until a row in `qtx_config_versions` for that symbol/mode has `approved_flag = 'Y'`. Calibrate (step 9) will propose those rows and you approve them. `check` and every snapshot say which config was used.

**Testing Core honestly:** `replay` runs the *same* code over history and writes every tier change to `core_output/replay_V75_SWING.csv`. Then, from `quantora_backtest`:

```powershell
python main.py run --symbol V75 --mode SWING --strategy core_signals --signals ..\quantora_core\core_output\replay_V75_SWING.csv
```

That applies the same costs, walk-forward, random baseline and gate as everything else. It also fills the gate's **"Tier 3 beats Tier 2"** rule (Section 4): hypothetical entries at Tier 2 versus actual Tier 3 entries. If waiting doesn't pay for a symbol, the gate says so.

**Not in this step:** Scalp mode's live-price polling between M15 closes (Section 2). It needs live prices from MT5, and spec Section 10 makes Sentinel the only MT5 connection, so it comes with Sentinel. Scalp still works on closed M15 bars.

## Outcomes: `quantora_validate/`

Build step 4 (spec Section 6). It answers "was the signal right?" for every signal Core logged. It reads `qtx_ohlcv` and `qtx_signals` and writes only `qtx_signal_outcomes`. It's insert-only: a label, once written, never changes.

```powershell
cd Qauntora\quantora_validate
pip install -r requirements.txt          # same .venv
python main.py check                     # how many signals wait for a label
python main.py once                      # label everything whose outcome is now known
python main.py report                    # live results per symbol/mode/tier, incl. Tier 3 vs Tier 2
python main.py run                       # keep labelling every hour
```

- **Triple barrier.** Take-profit and stop-loss are the ones Core stored with the signal. The time limit is per mode: 45 min for SCALP, 2 weeks for SWING, 3 months for LONG. It walks the stored M15 path from the signal bar's close and applies the same pessimistic rules as the backtester (if SL and TP fall in the same bar, it counts a LOSS; a gap past the stop fills at the worse price). The result is WIN, LOSS or TIMEOUT plus the R-multiple. Trading costs are not applied: this labels whether the *signal* was right.
- **Tier 2 too.** Every FORMING entry is also labelled as a hypothetical "what if we'd acted early" trade, so `report` can run spec Section 4's *Tier 3 vs Tier 2* check on live history. It says "not meaningful yet" until each side has 30+ outcomes.
- **Never guesses.** A trade that hasn't finished stays *pending*. If a 24/7 symbol has a hole in its stored prices along the path, it also stays pending: a barrier might have been hit inside the missing bars. Re-run Feed's backfill to fill the hole.

## Testing everything together: `integration/run_pipeline.py`

This runs every engine through its **real command line**, in order, exactly as you would. Feed is fed by a simulated MT5 terminal, since MT5 itself only runs on Windows with a broker login.

```powershell
cd Qauntora
python integration\run_pipeline.py --store files     # no database needed
python integration\run_pipeline.py --store oracle    # your Oracle (DROPS and recreates all qtx_ tables!)
```

The steps: Feed backfills 400 days, Feed verifies the data, Core runs 120 days back then now (catching up), then Core status, Validate labels and reports, Core replays history, and the backtester runs Core's signals. Then it checks the engines against each other:
- **A.** Core's live log equals Core's replay, so live and history make identical decisions.
- **B.** Every Validate label belongs to a real Tier 3 / Tier 2 entry.
- **C.** Validate's labels equal the backtester's outcomes. These are two separately written triple-barrier implementations.
- **D.** Running Feed, Core, Validate and Notify again duplicates nothing.
- **E.** Notify sends exactly the right alerts, to the right chat, over real HTTP to a stand-in Telegram server.
- **F.** Notify sends a health alert when Feed goes quiet, then a recovery message.

⚠ Use `--store oracle` only on a test database, or before you've collected real data.

## Create your Telegram bot

You need two values in `Qauntora\.env`: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. It takes about 5 minutes and costs nothing.

**1. Create the bot with BotFather.** BotFather is Telegram's official bot for making bots.
1. Install Telegram on your phone or PC and sign in.
2. Search for **`@BotFather`**. Open the one with the **blue verified tick** (there are fakes) and press **Start**.
3. Send **`/newbot`**.
4. BotFather asks for a **name**: anything, e.g. `Quantora Alerts`.
5. Then a **username**: it must be unique and end in `bot`, e.g. `quantora_austin_bot`. If it's taken, try another.
6. BotFather replies with a **token** like `7234567890:AAHk3x...`. Copy it into `Qauntora\.env`:
   ```
   TELEGRAM_BOT_TOKEN=7234567890:AAHk3x...
   ```
   Treat it like a password: anyone with it can send messages as your bot. If it ever leaks, send BotFather `/revoke` and paste the new one in.

**2. Say hello to your bot.** A bot can't message you until you message it first.
1. In Telegram, search for your bot's username, open it, and press **Start**.
2. Send it any message, e.g. `hi`.

**3. Find your chat id.** Notify does this for you:
```powershell
cd Qauntora\quantora_notify
pip install -r requirements.txt          # same .venv
python main.py chat-id
```
It prints a line like `TELEGRAM_CHAT_ID=123456789 (private: Austin)`. Copy that line into `Qauntora\.env`.

**4. Test it:**
```powershell
python main.py test       # a test message should arrive on your phone
python main.py check      # token, chat, database, what's being watched
```

**Optional:**
- Run `/setdescription` and `/setuserpic` in BotFather to give the bot a description and picture.
- To get alerts in a group: add the bot to the group, send a message there, and re-run `chat-id`. Group ids are negative numbers.
- If you'd rather try Notify before making the bot, add `--channel console` to print alerts in the terminal instead.

## Alerts: `quantora_notify/`

Build step 5 (spec Section 10), with two classes of alert:

| Trading | When |
|---|---|
| 🟡 Setup forming | A setup reached Tier 2. Says exactly what it's still waiting for. *No trade yet.* |
| 🟢 Confirmed | Tier 3, with entry, stop, target and reward:risk |
| ⚪ No longer confirmed | A confirmed signal dropped back |
| ✅ ❌ ⏱ Result | Validate labelled a Tier 3 signal: WIN / LOSS / TIMEOUT, with its R |

| Health ⚠️ | When |
|---|---|
| Feed stopped | No new price bar for 60 min (24/7 symbols; forex only while the FX week is open) |
| Core stopped | A pair not evaluated for 2 bars + 30 min |
| Validate stopped | Tier 3 signals past their time limit + 6 h with no outcome |
| Database down | Oracle unreachable |

A health alert repeats at most every 3 hours while the problem lasts, and you get a ✅ *Recovered* message when it clears.

```powershell
python main.py once       # one pass
python main.py run        # keep going (every 60 s) — run it alongside Feed, Core and Validate
```

- **It never floods you.** The first run just says hello and starts from "now". Tier changes that happened while Core was catching up aren't announced as new; you get one short note instead. At most 30 messages per pass.
- **It never loses one.** It only moves past a signal after Telegram accepted the message. If your internet drops, the alert is retried on the next pass.
- **It writes nothing to the database.** It keeps its place in `quantora_notify\notify_state\`.
- **Your bot token never appears** in logs or error messages.
- **To change which alerts you get,** edit `quantora_notify\settings.toml`.

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
