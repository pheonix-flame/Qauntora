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
    mode                    VARCHAR2(10)  NOT NULL,
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
CREATE INDEX ix_config_active ON qtx_config_versions (symbol, mode, approved_flag);

CREATE TABLE qtx_signal_state (
    symbol                   VARCHAR2(20)    NOT NULL,
    mode                     VARCHAR2(10)    NOT NULL,
    current_tier             VARCHAR2(15)    NOT NULL,
    direction                VARCHAR2(4),
    score                    NUMBER(4,2),
    indicator_snapshot_json  CLOB,
    notes                    VARCHAR2(400),
    updated_at               TIMESTAMP       DEFAULT SYS_EXTRACT_UTC(SYSTIMESTAMP),
    CONSTRAINT pk_qtx_signal_state PRIMARY KEY (symbol, mode),
    CONSTRAINT ck_snapshot_json CHECK (indicator_snapshot_json IS JSON)
);

CREATE TABLE qtx_signals (
    signal_id                NUMBER          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol                   VARCHAR2(20)    NOT NULL,
    mode                     VARCHAR2(10)    NOT NULL,
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
