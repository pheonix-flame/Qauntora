# Quantora

A patient, multi-asset signal and execution system for Forex, Gold, Synthetic Indices and Crypto on Deriv MT5.
The full design is in [`design_doc/Quantora_Specification.md`](design_doc/Quantora_Specification.md).

**Status:** Build-order step 1 (Quantora Feed + schema) is done. Nothing in this repo places trades yet.

| Engine | Status |
|---|---|
| `sql/`: Oracle schema (SQL Server kept as a documented fallback) | done |
| `quantora_feed/`: price history and economic calendar | done |
| Backtest harness, Core, Validate, Notify, Sentinel, Calibrate, Dashboard | not started (see spec Section 17) |

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

## Setting up Quantora Feed (Windows)

```powershell
git clone https://github.com/pheonix-flame/Qauntora.git
cd Qauntora\quantora_feed
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then edit .env: MT5_LOGIN / MT5_PASSWORD / MT5_SERVER
```

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

Put that password in `.env` as `ORACLE_PASSWORD` (`ORACLE_DSN` defaults to `localhost:1521/FREEPDB1`). Then:

```powershell
python main.py check                     # MT5 login, symbol names + lot specs, broker clock, DB, news
python main.py init-db                   # creates the 8 qtx_ tables in your quantora schema
python main.py backfill --days 30 --dry-run --symbols V75   # preview as CSV first
python main.py backfill --days 365       # real history into Oracle
python main.py run                       # keep it updated every M15 close (Ctrl+C to stop)
```

`check` prints the minimum lot, contract size and tick value for every symbol on your account. Use it to confirm the Section 13 capital numbers against your real specs. If a symbol shows `NOT FOUND`, it suggests similar names. Set `QTX_BROKER_NAME_<KEY>` in `.env` to the right one.

Tests run on any OS (MT5 and Oracle are faked):

```bash
cd quantora_feed && python -m pytest -q
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
