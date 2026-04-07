-- GatSlinger fund accounting schema
-- Run once against financial_db to initialize all tables.

CREATE SCHEMA IF NOT EXISTS monitoring;

-- Fund registry
CREATE TABLE IF NOT EXISTS monitoring.fund_overview (
    fund_name       TEXT PRIMARY KEY,
    inception_date  DATE,
    strategy        TEXT,
    aum             NUMERIC(18,2) DEFAULT 0,
    nav             NUMERIC(18,6) DEFAULT 1,
    currency        TEXT DEFAULT 'USD',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- NAV bridge (institutional reconciliation — one row per fund per day)
CREATE TABLE IF NOT EXISTS monitoring.nav_bridge (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    date            DATE NOT NULL,
    starting_nav    NUMERIC(18,6),
    pnl             NUMERIC(18,2) DEFAULT 0,
    subscriptions   NUMERIC(18,2) DEFAULT 0,
    redemptions     NUMERIC(18,2) DEFAULT 0,
    fees            NUMERIC(18,2) DEFAULT 0,
    distributions   NUMERIC(18,2) DEFAULT 0,
    ending_nav      NUMERIC(18,6),
    UNIQUE (fund_name, date)
);

-- NAV bridge / waterfall (one row per fund per day)
CREATE TABLE IF NOT EXISTS monitoring.nav_bridge_waterfall (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    date            DATE NOT NULL,
    beginning_nav   NUMERIC(18,6),
    trading_pnl     NUMERIC(18,2) DEFAULT 0,
    fees            NUMERIC(18,2) DEFAULT 0,
    subscriptions   NUMERIC(18,2) DEFAULT 0,
    redemptions     NUMERIC(18,2) DEFAULT 0,
    ending_nav      NUMERIC(18,6),
    UNIQUE (fund_name, date)
);

-- Position snapshot (latest per fund)
CREATE TABLE IF NOT EXISTS monitoring.position_summary (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    symbol          TEXT NOT NULL,
    asset_class     TEXT,
    quantity        NUMERIC(18,4) DEFAULT 0,
    avg_cost        NUMERIC(18,4) DEFAULT 0,
    market_price    NUMERIC(18,4) DEFAULT 0,
    market_value    NUMERIC(18,2) DEFAULT 0,
    unrealized_pnl  NUMERIC(18,2) DEFAULT 0,
    weight_pct      NUMERIC(8,4)  DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Daily P&L rollup per fund
CREATE TABLE IF NOT EXISTS monitoring.daily_pnl_summary (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    date            DATE NOT NULL,
    realized_pnl    NUMERIC(18,2) DEFAULT 0,
    unrealized_pnl  NUMERIC(18,2) DEFAULT 0,
    total_pnl       NUMERIC(18,2) DEFAULT 0,
    UNIQUE (fund_name, date)
);

-- Daily P&L broken out by position
CREATE TABLE IF NOT EXISTS monitoring.daily_pnl_by_position (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    date            DATE NOT NULL,
    symbol          TEXT NOT NULL,
    daily_pnl       NUMERIC(18,2) DEFAULT 0,
    UNIQUE (fund_name, date, symbol)
);

-- Fee ledger
CREATE TABLE IF NOT EXISTS monitoring.fee_summary (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    date            DATE NOT NULL,
    fee_type        TEXT,   -- e.g. 'management', 'performance', 'admin'
    amount          NUMERIC(18,2) DEFAULT 0,
    UNIQUE (fund_name, date, fee_type)
);

-- Investor allocations
CREATE TABLE IF NOT EXISTS monitoring.investor_allocation (
    id                  SERIAL PRIMARY KEY,
    fund_name           TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    investor_name       TEXT NOT NULL,
    allocation_value    NUMERIC(18,2) DEFAULT 0,
    allocation_pct      NUMERIC(8,4)  DEFAULT 0,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fund_name, investor_name)
);

-- Active alerts
CREATE TABLE IF NOT EXISTS monitoring.active_alerts (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT,
    alert_date      TIMESTAMPTZ DEFAULT NOW(),
    severity        TEXT DEFAULT 'info',   -- 'info', 'warning', 'critical'
    alert_type      TEXT,
    message         TEXT,
    resolved        BOOLEAN DEFAULT FALSE
);

-- Rolling return periods
CREATE TABLE IF NOT EXISTS monitoring.rolling_returns (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    period          TEXT NOT NULL,   -- e.g. '1D', '1W', '1M', '3M', 'YTD', '1Y'
    return_pct      NUMERIC(10,6) DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fund_name, period)
);

-- NAV history (time-series, one row per fund per timestamp)
CREATE TABLE IF NOT EXISTS monitoring.nav_history (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT NOT NULL REFERENCES monitoring.fund_overview(fund_name),
    nav_per_unit    NUMERIC(18,6),
    total_assets    NUMERIC(18,2),
    total_liabilities NUMERIC(18,2),
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Positions (live snapshot + historical — append-only)
CREATE TABLE IF NOT EXISTS monitoring.positions (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT REFERENCES monitoring.fund_overview(fund_name),
    symbol          TEXT NOT NULL,
    asset_class     TEXT,
    quantity        NUMERIC(18,4) DEFAULT 0,
    avg_price       NUMERIC(18,4) DEFAULT 0,
    market_price    NUMERIC(18,4) DEFAULT 0,
    market_value    NUMERIC(18,2) DEFAULT 0,
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Trades (execution audit trail — never update, only insert)
CREATE TABLE IF NOT EXISTS monitoring.trades (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT REFERENCES monitoring.fund_overview(fund_name),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,   -- 'BUY' | 'SELL'
    quantity        NUMERIC(18,4),
    price           NUMERIC(18,4),
    execution_time  TIMESTAMPTZ DEFAULT NOW(),
    strategy        TEXT,
    broker_order_id TEXT
);

-- Signals (alpha engine output — one row per signal event)
CREATE TABLE IF NOT EXISTS monitoring.signals (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    signal_type     TEXT NOT NULL,   -- 'LONG' | 'SHORT' | 'FLAT'
    confidence      NUMERIC(5,4),
    source          TEXT,            -- strategy name
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

-- Fund data / KYP (key metrics from external sources)
CREATE TABLE IF NOT EXISTS monitoring.fund_data (
    id              SERIAL PRIMARY KEY,
    fund_name       TEXT REFERENCES monitoring.fund_overview(fund_name),
    metric          TEXT NOT NULL,
    value           NUMERIC(18,6),
    source          TEXT,
    report_date     DATE,
    UNIQUE (fund_name, metric, report_date)
);

-- Seed a default paper fund so the dashboard isn't empty
INSERT INTO monitoring.fund_overview (fund_name, inception_date, strategy, aum, nav, currency)
VALUES ('GatSlinger Paper', CURRENT_DATE, 'ORB / Futures', 250000, 1.0, 'USD')
ON CONFLICT (fund_name) DO NOTHING;
