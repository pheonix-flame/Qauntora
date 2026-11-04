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
    mode                   VARCHAR(10)   NOT NULL,   -- SCALP | SWING | LONG
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
CREATE INDEX ix_config_active ON qtx_config_versions (symbol, mode, approved_flag);

CREATE TABLE qtx_signal_state (
    symbol                   VARCHAR(20)   NOT NULL,
    mode                     VARCHAR(10)   NOT NULL,
    current_tier             VARCHAR(15)   NOT NULL,   -- WATCHING | FORMING | CONFIRMED
    direction                VARCHAR(4),
    score                    DECIMAL(4,2),
    indicator_snapshot_json  NVARCHAR(MAX),
    notes                    VARCHAR(400),
    updated_at               DATETIME2     DEFAULT SYSUTCDATETIME(),
    CONSTRAINT pk_qtx_signal_state PRIMARY KEY (symbol, mode),
    CONSTRAINT ck_snapshot_json CHECK (indicator_snapshot_json IS NULL OR ISJSON(indicator_snapshot_json) = 1)
);

CREATE TABLE qtx_signals (
    signal_id                INT           IDENTITY(1,1) PRIMARY KEY,
    symbol                   VARCHAR(20)   NOT NULL,
    mode                     VARCHAR(10)   NOT NULL,
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
