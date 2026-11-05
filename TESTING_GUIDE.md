# Quantora — Setup & Testing Guide

Everything you need to set up Quantora on your Windows PC, fill in the settings, create the Telegram bot, and test each engine on its own and all of them together. Work through it top to bottom the first time.

> Quantora **suggests** trades and measures them. Nothing here places a trade. Order execution (Sentinel) is a later build step.

**Contents**
1. [What you're setting up](#1-what-youre-setting-up)
2. [Install the software](#2-install-the-software)
3. [The files you fill in](#3-the-files-you-fill-in)
4. [Set up the database](#4-set-up-the-database)
5. [Create the Telegram bot](#5-create-the-telegram-bot)
6. [Test level 1: unit tests (no setup needed)](#6-test-level-1-unit-tests)
7. [Test level 2: all engines together, no database](#7-test-level-2-all-engines-together-no-database)
8. [Test level 3: all engines together, with Oracle](#8-test-level-3-all-engines-together-with-oracle)
9. [Test each engine for real](#9-test-each-engine-for-real)
10. [Run everything together, day to day](#10-run-everything-together-day-to-day)
11. [Troubleshooting](#11-troubleshooting)
12. [Where things are kept](#12-where-things-are-kept)

---

## 1. What you're setting up

Five engines (the "agents"). Each runs on its own and talks to the others **only through the database**.

```
  MT5 (Deriv)                      Telegram on your phone
      │                                       ▲
      ▼                                       │
 ┌─────────┐   prices   ┌─────────┐ signals ┌──────────┐ outcomes ┌─────────┐
 │  FEED   │ ─────────▶ │  CORE   │ ──────▶ │ VALIDATE │ ───────▶ │ NOTIFY  │
 └─────────┘            └─────────┘         └──────────┘          └─────────┘
      │                      │                    │                    ▲
      └──────────────────────┴──── Oracle DB ─────┴────────────────────┘
                                     │
                                ┌──────────┐
                                │ BACKTEST │  (run on demand: "is there an edge?")
                                └──────────┘
```

| Engine | Folder | What it does | Runs |
|---|---|---|---|
| Feed | `quantora_feed` | Pulls 15-minute price bars from MT5 and the economic calendar into Oracle | all the time |
| Core | `quantora_core` | Decides each pair's tier: Watching / Forming / Confirmed | all the time |
| Validate | `quantora_validate` | Labels every signal WIN / LOSS / TIMEOUT once it has played out | all the time |
| Notify | `quantora_notify` | Sends signals, results and health warnings to Telegram | all the time |
| Backtest | `quantora_backtest` | Tests a strategy on history, with costs and against luck | when you ask |

---

## 2. Install the software

All of it is free.

| # | Install | Where | Notes |
|---|---|---|---|
| 1 | **Windows 10/11, 64-bit** | — | MT5's Python package only works on Windows |
| 2 | **Python 3.12, 64-bit** | python.org → Downloads | Tick **"Add python.exe to PATH"** on the first installer screen |
| 3 | **Git** | git-scm.com | Or download the code as a ZIP from GitHub (branch below) |
| 4 | **MT5 desktop app** | Deriv's website (MT5 download) | Log in with your **demo** MT5 account (part 3) |
| 5 | **Oracle AI Database Free** | oracle.com/database/free → Windows | Remember the SYS password you choose during install |
| 6 | **Telegram** | phone app store, or telegram.org | For the alerts (part 5) |

### Get the code and install the Python packages

Open **Command Prompt** (Start → type `cmd`):

```bat
cd %USERPROFILE%
git clone -b debug/determined-babbage-63pcgv https://github.com/pheonix-flame/Qauntora.git
cd Qauntora
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r quantora_feed\requirements.txt
pip install -r quantora_backtest\requirements.txt
pip install -r quantora_core\requirements.txt
pip install -r quantora_validate\requirements.txt
pip install -r quantora_notify\requirements.txt
```

- **One virtual environment (`Qauntora\.venv`) serves all engines.**
- **Every new Command Prompt window needs it switched on first:** `cd %USERPROFILE%\Qauntora` then `.venv\Scripts\activate`. You'll see `(.venv)` at the start of the line.
- If `import MetaTrader5` later fails with a numpy error: `pip install "numpy<2"`.

---

## 3. The files you fill in

There are only **two files you must edit**, plus one optional one:

| File | What goes in it | Required? |
|---|---|---|
| `Qauntora\.env` | Passwords, logins, bot token: **the secrets** | yes |
| `quantora_backtest\settings.toml` | Trading costs per symbol (spread, slippage) | yes, before backtesting |
| `quantora_core\settings.toml` | Which symbols/modes Core watches (default: V75 SWING) | optional |

The other `settings.toml` files (Validate, Notify) work as they are. Read them if you want to change behaviour.

### 3a. Create `.env`

```bat
cd %USERPROFILE%\Qauntora
copy .env.example .env
notepad .env
```

> ⚠ **Rules for `.env`:**
> - Put your value **straight after the `=`**, with no quotes or spaces.
> - **Comments go on their own line,** never after a value.
> - `.env` is never uploaded to GitHub (it's in `.gitignore`). Don't email it or paste it anywhere.

### 3b. Every setting in `.env`: what it is and where to get it

| Setting | Required? | Used by | Where to get it |
|---|---|---|---|
| `MT5_LOGIN` | **yes** | Feed | deriv.com → log in → **Trader's Hub** → switch to **Demo** → your MT5 account → the account **number** |
| `MT5_PASSWORD` | **yes** | Feed | The password you set for that **MT5 account**. Not your deriv.com password. Change it in Trader's Hub → MT5 account → change password |
| `MT5_SERVER` | **yes** | Feed | Shown next to the MT5 account, e.g. `Deriv-Demo`. Copy it exactly |
| `MT5_TERMINAL_PATH` | no | Feed | Only if you have several MT5 installs: full path to `terminal64.exe` |
| `BROKER_TZ` / `BROKER_UTC_OFFSET_HOURS` | no | Feed | **Leave empty.** Feed measures the broker clock itself; `verify` tells you if you ever need to set it |
| `ORACLE_USER` | yes (default `quantora`) | all | Keep `quantora` (created in part 4) |
| `ORACLE_PASSWORD` | **yes** | all | **You choose it** in part 4 (`CREATE USER ... IDENTIFIED BY "..."`). Not the SYS password |
| `ORACLE_DSN` | yes (default ok) | all | `localhost:1521/FREEPDB1` for a local Oracle Free install |
| `ORACLE_WALLET_DIR` / `_PASSWORD` | no | all | Only for Oracle *Cloud*. Leave empty |
| `NEWS_PROVIDER` | yes (default ok) | Feed | `forexfactory`: free, **no key** |
| `FMP_API_KEY` | no | Feed | Only if `NEWS_PROVIDER=fmp`: financialmodelingprep.com → sign up → dashboard |
| `QTX_SYMBOLS` | no | Feed | Empty = all 10 symbols. Or e.g. `V75,BTCUSD` |
| `QTX_BROKER_NAME_<KEY>` | no | Feed | Only if `check` says a symbol is NOT FOUND (part 9) |
| `BACKFILL_DAYS` | no | Feed | Default history to pull, e.g. `1095` for 3 years |
| `TELEGRAM_BOT_TOKEN` | **yes** (for Notify) | Notify | From **@BotFather** (part 5) |
| `TELEGRAM_CHAT_ID` | **yes** (for Notify) | Notify | From `python main.py chat-id` (part 5) |

**There are no paid API keys.** MT5 uses your account login, and the news calendar, Coinbase check and Telegram are all free.

### 3c. What a filled-in `.env` looks like

These are made-up values; use your own:

```ini
MT5_LOGIN=40123456
MT5_PASSWORD=MyMt5Pass!23
MT5_SERVER=Deriv-Demo
MT5_TERMINAL_PATH=
BROKER_TZ=
BROKER_UTC_OFFSET_HOURS=
ORACLE_USER=quantora
ORACLE_PASSWORD=QuantoraDb2026
ORACLE_DSN=localhost:1521/FREEPDB1
ORACLE_WALLET_DIR=
ORACLE_WALLET_PASSWORD=
NEWS_PROVIDER=forexfactory
FMP_API_KEY=
NEWS_REFRESH_HOURS=6
QTX_SYMBOLS=
BACKFILL_DAYS=1095
TELEGRAM_BOT_TOKEN=7234567890:AAHk3xExampleExampleExample123
TELEGRAM_CHAT_ID=123456789
```

### 3d. Trading costs: `quantora_backtest\settings.toml`

The backtester **refuses** to test a symbol without costs, because a backtest without costs flatters the results. You don't have to look anything up. Once Feed is set up (part 9, Feed step 1), its `check` command prints ready-made lines:

```
Trading costs for the backtester — paste under [costs] in quantora_backtest/settings.toml.
V75 = { spread = 12.34, slippage = 6.17 }
```

Open `quantora_backtest\settings.toml` and paste them under the `[costs]` line, e.g.:

```toml
[costs]
V75 = { spread = 12.34, slippage = 6.17 }
```

Run `check` during normal market hours, because spreads widen when the market is quiet.

---

## 4. Set up the database

Do this once, after installing Oracle AI Database Free. In Command Prompt:

```bat
sqlplus sys@localhost:1521/FREEPDB1 as sysdba
```

Enter the **SYS password from the Oracle install**. Then type these lines, replacing `QuantoraDb2026` with the password you chose for `ORACLE_PASSWORD`:

```sql
CREATE USER quantora IDENTIFIED BY "QuantoraDb2026"
  DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO quantora;
EXIT
```

You should see `User created.` and `Grant succeeded.`. Then create Quantora's 8 tables:

```bat
cd %USERPROFILE%\Qauntora\quantora_feed
python main.py init-db
```

Expect `Schema created`. Running it again says `Schema already exists — nothing done`. If it's ever interrupted, run it again: it creates whatever is missing.

*These exact steps were tested against Oracle AI Database Free 23.26.*

---

## 5. Create the Telegram bot

About 5 minutes, free.

**Step 1: create the bot with BotFather.**
1. In Telegram, search for **`@BotFather`**. Open the one with the **blue verified tick** (there are fakes) and press **Start**.
2. Send **`/newbot`**.
3. Give it a **name**, anything: `Quantora Alerts`.
4. Then a **username**: unique, ending in `bot`, e.g. `quantora_austin_bot`. If it's taken, try another.
5. BotFather replies with a **token** like `7234567890:AAHk3x...`. Put it in `.env`:
   ```ini
   TELEGRAM_BOT_TOKEN=7234567890:AAHk3x...
   ```
   The token works like a password. If it ever leaks: BotFather → `/revoke` → put the new token in `.env`.

**Step 2: message your bot first.** Bots can't start a conversation.
- Search for your bot's username, open it, press **Start**, and send `hi`.

**Step 3: find your chat id.**
```bat
cd %USERPROFILE%\Qauntora\quantora_notify
python main.py chat-id
```
It prints `TELEGRAM_CHAT_ID=123456789 (private: Your Name)`. Copy that line into `.env`.
- If it says *"No messages found yet"*, you skipped step 2.

**Step 4: test delivery.**
```bat
python main.py test
```
✅ A *"Quantora test message"* arrives on your phone.

**Optional:**
- For alerts in a Telegram **group**: add the bot to the group, send a message there, and run `chat-id` again. Group ids start with `-`.
- To try Notify *without* a bot: add `--channel console` to any Notify command and alerts print in the window instead.

---

## 6. Test level 1: unit tests

These need **nothing**: no MT5, no database, no bot. They prove each engine's logic with simulated data. Run them first, and again after any change.

```bat
cd %USERPROFILE%\Qauntora\quantora_feed      && python -m pytest -q
cd %USERPROFILE%\Qauntora\quantora_backtest  && python -m pytest -q
cd %USERPROFILE%\Qauntora\quantora_core      && python -m pytest -q
cd %USERPROFILE%\Qauntora\quantora_validate  && python -m pytest -q
cd %USERPROFILE%\Qauntora\quantora_notify    && python -m pytest -q
```

✅ Expected (exact numbers may grow as tests are added):

| Engine | Result | Some of what's proven |
|---|---|---|
| Feed | `60 passed` | Broker clock → UTC, only closed bars stored, no duplicates, `verify` catches bad data and a wrong clock |
| Backtest | `33 passed` | Costs applied, no look-ahead, finds **no** edge in random data, **does** detect a planted edge |
| Core | `34 passed` | Tiers follow the rules, no look-ahead, live run == history replay, news blackout |
| Validate | `11 passed` | WIN/LOSS/TIMEOUT rules, never guesses across data gaps, labels written once |
| Notify | `18 passed` | Real HTTP to a stand-in Telegram, token never leaks, no floods, retries failed sends |

---

## 7. Test level 2: all engines together, no database

This runs **every engine's real command line, in order**, exactly as you would. MT5 is simulated with a realistic price stream, and Telegram with a local stand-in server. Nothing touches your database or your real Telegram.

```bat
cd %USERPROFILE%\Qauntora
python integration\run_pipeline.py --store files
```

Takes about a minute. The steps:
1. Feed backfills 400 days.
2. Feed verifies the data.
3. Notify connects.
4. Core evaluates 120 days back, then now (catching up).
5. Validate labels the outcomes.
6. Notify announces what's new.
7. Core replays history.
8. The backtester tests Core's signals.
9. Everything runs again, then a 5-hour Feed outage is simulated.

✅ It ends with:

```
ALL ENGINES WORK TOGETHER — 25 checks, store: files
```

The cross-engine checks:

| Check | Proves |
|---|---|
| **A** Core live log == Core replay | Running live bar-by-bar makes the same decisions as replaying history, so backtests judge the real live logic |
| **B** Validate labelled Core's signals | Every outcome belongs to a real Tier 3 / Tier 2 entry |
| **C** Validate labels == Backtest outcomes | Two separately written triple-barrier programs agree on every shared trade |
| **D** Re-runs don't duplicate | Running Feed, Core, Validate and Notify again adds nothing twice |
| **E** Notify announced the right things | The right alerts, to the right chat, with the right token |
| **F** Notify health alert + recovery | A Feed outage triggers a warning, then a "Recovered" message |

**The run also prints the backtest gate:** expect **FAIL** lines like `[FAIL] beats random entry`. That's correct: simulated prices contain no real edge, and the gate is supposed to say so.

---

## 8. Test level 3: all engines together, with Oracle

The same test, but with your **real Oracle database** (it reads `ORACLE_*` from `.env`).

> ⚠ **This DELETES everything in the `qtx_` tables.** Run it only **before** you start collecting real data, or on a separate test database. It doesn't touch MT5 or your real Telegram.

```bat
cd %USERPROFILE%\Qauntora
python integration\run_pipeline.py --store oracle
```

✅ `ALL ENGINES WORK TOGETHER — 26 checks, store: oracle`. This proves your Oracle install, user, permissions and every engine's SQL work together.

When it finishes, it **removes all the simulated data and leaves fresh, empty tables**, ready for your real backfill in part 9. There's nothing to clean up by hand.

---

## 9. Test each engine for real

Now with your real MT5, Oracle and bot. Go in this order: each engine needs the one before it. Before you start:
- **MT5** is open and logged in to your **demo** account.
- In MT5: **Tools → Options → Charts → "Max bars in chart" → Unlimited** (needed for long history).
- `.env` is filled in (part 3), the database is set up (part 4), and `.venv` is activated.

### Feed: prices into the database

```bat
cd %USERPROFILE%\Qauntora\quantora_feed
python main.py check
```

✅ It prints your MT5 account (`mode=DEMO`), the broker clock, a table of every symbol (min lot, contract size, tick value, spread), the **cost lines for the backtester** (paste them, part 3d), the database version, and the news count. It ends with `All good.`
- A symbol shows `NOT FOUND — similar: ...`? Set e.g. `QTX_BROKER_NAME_V75=Volatility 75 (1s) Index` in `.env`, using the exact name shown.

```bat
python main.py backfill --days 30 --dry-run --symbols V75
```

✅ This writes CSVs to `quantora_feed\preview_output\` without touching the database. Open `ohlcv_V75_M15.csv` and compare a few candles with the MT5 chart.

```bat
python main.py backfill --days 1095
python main.py verify --days 7
```

✅ The backfill reports `inserted N` per symbol. `verify` should end with `Result: data looks right.` and print each layer's checks:
- **Broker clock:** your clock setting matches the live server clock.
- **Integrity:** no impossible candles; gaps explained.
- **MT5 cross-check:** stored == MT5, and M15 rolls up exactly into MT5's H1.
- **Coinbase:** BTC/ETH timestamps line up at 0 shift, which proves true UTC.

It also prints the last 3 candles in UTC and **broker time**. Hover those candles on the MT5 M15 chart (broker time) and the prices should match exactly.

### Backtest: is there an edge?

```bat
cd %USERPROFILE%\Qauntora\quantora_backtest
python main.py demo
python main.py check
python main.py run --symbol V75 --mode SWING
```

✅
- **`demo`** needs no database and **must FAIL the gate**: it runs on random data with no edge, so the harness is supposed to reject it.
- **`check`** shows each symbol's history and `costs: yes`. SWING needs `ok` (at least 363 days).
- **`run`** tests the simple reference strategy (`ema_cross`). It will probably **fail** the gate. It's only there to exercise the harness, not to trade.

Reports are saved in `quantora_backtest\backtest_output\`.

### Core: signals

```bat
cd %USERPROFILE%\Qauntora\quantora_core
python main.py check
python main.py once
python main.py status
python main.py diagnose --symbol V75 --mode SWING
python main.py replay --symbol V75 --mode SWING
```

✅
- **`check`:** V75 SWING has bars and `settings.toml defaults` as its config.
- **`once`:** prints the current tier with a plain-English note (e.g. *"Setup forming BUY … Watching for: a confirmed bullish close"*). Running it again says `already evaluated`.
- **`status`:** shows the same, read from the database.
- **`diagnose`:** reports how many *independent* opinions the indicators really give (e.g. "4 votes measure ~2.8 independent things"). Flagged pairs mean two indicators say the same thing.
- **`replay`:** writes `core_output\replay_V75_SWING.csv` and prints the exact backtest command for it. Run that command from `quantora_backtest`: it tests Core's real logic with costs and against luck, including the **Tier 3 vs Tier 2** check.

### Validate: outcomes

```bat
cd %USERPROFILE%\Qauntora\quantora_validate
python main.py check
python main.py once
python main.py report
```

✅ On a fresh setup there's usually nothing to label yet (`Labelled 0`), because Core's signals need time to play out: up to 2 weeks for SWING. After a while, `report` shows win rate and expectancy per tier, and *"Tier 3 vs Tier 2"*. It says `not meaningful yet` until each side has 30+ outcomes.

### Notify: alerts

```bat
cd %USERPROFILE%\Qauntora\quantora_notify
python main.py check
python main.py once
```

✅ `check` ends with `All good.`. The first `once` sends **👋 Quantora Notify is connected** to your phone. After that it only messages you when something new happens.

To see the health alerts work: stop Feed for over an hour while Notify runs, and you'll get ⚠️ *"Feed hasn't stored a V75 price bar for …"*. Start Feed again and ✅ *Recovered* follows.

---

## 10. Run everything together, day to day

1. Open **MT5** and log in (demo).
2. Make sure **Oracle** is running (it starts with Windows by default).
3. Double-click **`start_quantora.bat`** in the `Qauntora` folder. It opens four windows: Feed, Core, Validate and Notify.

Or start them by hand, one Command Prompt each (activate `.venv` in each):

```bat
cd %USERPROFILE%\Qauntora\quantora_feed      && python main.py run
cd %USERPROFILE%\Qauntora\quantora_core      && python main.py run
cd %USERPROFILE%\Qauntora\quantora_validate  && python main.py run
cd %USERPROFILE%\Qauntora\quantora_notify    && python main.py run
```

**What happens:**

| Engine | Rhythm |
|---|---|
| Feed | Stores a bar every 15 minutes (20 s after each close) |
| Core | Evaluates 60 s after each close; SWING decisions happen every 4 h |
| Validate | Checks for finished trades every hour |
| Notify | Checks every minute and messages you |

**To stop an engine,** press `Ctrl+C` in its window. If an engine crashes, its window stays open with the error, and Notify warns you on Telegram.

**To keep it running 24/7,** the PC must stay on and awake (Settings → Power → Sleep: Never) with MT5 logged in, or use a small Windows VPS.

**Spec Section 13's advice:** demo-trade at a **realistic $50–100 balance** and wait for **30–50 resolved trades** before trusting anything. A good week is not evidence.

---

## 11. Troubleshooting

| You see | Meaning / fix |
|---|---|
| `ORACLE_PASSWORD is empty — set it in Qauntora/.env` | `.env` missing, in the wrong folder, or the value isn't filled in. It must be `Qauntora\.env` |
| `ORA-01017: invalid username/password` | `ORACLE_PASSWORD` doesn't match the `CREATE USER` password (part 4) |
| `ORA-12541` / `DPY-6005` / `cannot connect` | Oracle isn't running, or `ORACLE_DSN` is wrong. Check Windows Services → `OracleServiceFREE` |
| `ORA-01950: no privileges on tablespace` | The `QUOTA UNLIMITED ON users` part of `CREATE USER` was missed. Run `ALTER USER quantora QUOTA UNLIMITED ON users;` as SYS |
| `mt5.initialize() failed` | MT5 isn't open/logged in, or `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` is wrong |
| `MetaTrader5 package is not installed` | `pip install MetaTrader5`. It's Windows only |
| `numpy` error when importing MetaTrader5 | `pip install "numpy<2"` |
| Symbol `NOT FOUND — similar: ...` | Set `QTX_BROKER_NAME_<KEY>=<exact name>` in `.env` |
| `verify`: `[FAIL] UTC conversion … off by N h` | Feed's clock setting is wrong. Leave `BROKER_TZ` empty, or set what verify suggests, then re-backfill |
| `verify`: gaps `WARN` | Broker maintenance or Feed downtime. Run `backfill` again to fill holes |
| Backtest: `No trading costs for V75` | Paste the cost line from Feed's `check` into `quantora_backtest\settings.toml` (part 3d) |
| Backtest `check`: SWING `short` | Not enough history. `backfill --days 1095` with MT5's "Max bars in chart" set to Unlimited |
| Core: `waiting for Feed` | Normal for a few seconds after a bar closes. If it persists, Feed isn't running |
| Validate: `pending … gap in stored prices` | Feed missed some bars. Run `backfill` again, then Validate labels it |
| Notify: `the bot token is wrong` | Copy the token again from @BotFather |
| Notify: `chat not found` / `press Start` | Wrong `TELEGRAM_CHAT_ID`, or you never pressed Start in the bot's chat |
| Notify: `No messages found yet` (chat-id) | Send your bot a message first (part 5, step 2) |
| Odd characters (`→`, emoji) look broken in an old console | Cosmetic only. Use Windows Terminal for proper display |

---

## 12. Where things are kept

| What | Where |
|---|---|
| Secrets | `Qauntora\.env` (never uploaded) |
| All prices, signals, outcomes | Oracle, `quantora` user, tables `qtx_*` |
| Feed / Core logs | `quantora_feed\logs\feed.log`, `quantora_core\logs\core.log` |
| Backtest reports | `quantora_backtest\backtest_output\` (JSON + trades CSV) |
| Core replay files | `quantora_core\core_output\` |
| Notify's "already sent" position | `quantora_notify\notify_state\` (delete it to make Notify start fresh) |
| `--dry-run` / local-mode files | `quantora_feed\preview_output\`, `core_output\`, `validate_output\` |

Everything above except `.env` and the database can be deleted safely: the engines recreate it.
