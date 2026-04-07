"""IBKR → PostgreSQL ingestion layer.

Pulls live data from IBKR and writes it into the monitoring schema.
Called on-demand from the dashboard via the Sync button.
"""
from datetime import datetime, date
from db import get_db
from ibkr import (
    safe_float, get_account_summary_map, get_positions_df,
    classify_asset_class, contract_display_symbol,
)


def _exec(sql, params=None):
    conn = get_db()
    if not conn:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        return True
    except Exception as e:
        print(f"ingest SQL error: {e}")
        conn.rollback()
        return False


def _query(sql, params=None):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


# ── Trades ──────────────────────────────────────────────────

def sync_trades(ib, fund_name="GatSlinger Paper"):
    """Ingest completed trades from IBKR into monitoring.trades.
    Only inserts trades not already recorded (by broker_order_id)."""
    fills = ib.fills()
    inserted = 0
    for fill in fills:
        contract = fill.contract
        execution = fill.execution
        order_id = str(execution.orderId)
        exec_id = str(execution.execId)

        # Skip if already recorded
        existing = _query(
            "SELECT id FROM monitoring.trades WHERE broker_order_id = %s AND fund_name = %s",
            (exec_id, fund_name),
        )
        if existing:
            continue

        _exec("""
            INSERT INTO monitoring.trades
                (fund_name, symbol, side, quantity, price, execution_time, strategy, broker_order_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            fund_name,
            contract_display_symbol(contract),
            execution.side,
            safe_float(execution.shares),
            safe_float(execution.price),
            execution.time,
            None,  # strategy can be tagged later
            exec_id,
        ))
        inserted += 1
    return inserted


# ── Positions ───────────────────────────────────────────────

def sync_positions(ib, fund_name="GatSlinger Paper"):
    """Snapshot current IBKR positions into monitoring.positions (append-only)."""
    positions = ib.positions()
    if not positions:
        return 0

    now = datetime.utcnow()
    inserted = 0

    for p in positions:
        contract = p.contract
        qty = safe_float(p.position)
        avg_cost = safe_float(p.avgCost)

        # Request market price
        tickers = ib.reqTickers(contract)
        market_price = avg_cost
        if tickers:
            t = tickers[0]
            for candidate in [
                getattr(t, 'marketPrice', lambda: None)() if hasattr(t, 'marketPrice') else None,
                getattr(t, 'last', None),
                getattr(t, 'close', None),
            ]:
                v = safe_float(candidate, default=None)
                if v is not None and v != 0:
                    market_price = v
                    break

        _exec("""
            INSERT INTO monitoring.positions
                (fund_name, symbol, asset_class, quantity, avg_price, market_price, market_value, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            fund_name,
            contract_display_symbol(contract),
            classify_asset_class(contract),
            qty,
            avg_cost,
            market_price,
            qty * market_price,
            now,
        ))
        inserted += 1
    return inserted


# ── Daily P&L ───────────────────────────────────────────────

def sync_daily_pnl(ib, fund_name="GatSlinger Paper"):
    """Pull today's P&L from IBKR and upsert into daily_pnl_summary."""
    accounts = ib.managedAccounts()
    if not accounts:
        return False

    ib.reqPnL(accounts[0])
    ib.sleep(1)
    pnl_list = ib.pnl()

    if not pnl_list:
        return False

    p = pnl_list[0]
    today = date.today()

    _exec("""
        INSERT INTO monitoring.daily_pnl_summary (fund_name, date, realized_pnl, unrealized_pnl, total_pnl)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (fund_name, date)
        DO UPDATE SET
            realized_pnl = EXCLUDED.realized_pnl,
            unrealized_pnl = EXCLUDED.unrealized_pnl,
            total_pnl = EXCLUDED.total_pnl
    """, (
        fund_name,
        today,
        safe_float(p.realizedPnL),
        safe_float(p.unrealizedPnL),
        safe_float(p.dailyPnL),
    ))
    return True


# ── NAV Bridge Roll-Forward ─────────────────────────────────

def roll_nav_bridge(ib, fund_name="GatSlinger Paper"):
    """Create today's NAV bridge row from IBKR account data + DB state.
    Pulls starting_nav from yesterday's ending_nav, P&L from IBKR,
    subs/redemptions from today's investor_performance entries."""
    today = date.today()

    # Check if today already exists
    existing = _query(
        "SELECT id FROM monitoring.nav_bridge WHERE fund_name = %s AND date = %s",
        (fund_name, today),
    )

    # Get yesterday's ending NAV
    prev = _query("""
        SELECT ending_nav FROM monitoring.nav_bridge
        WHERE fund_name = %s AND date < %s
        ORDER BY date DESC LIMIT 1
    """, (fund_name, today))
    starting_nav = float(prev[0]["ending_nav"]) if prev else 0

    # Get today's P&L from IBKR
    accounts = ib.managedAccounts()
    daily_pnl = 0
    if accounts:
        ib.reqPnL(accounts[0])
        ib.sleep(1)
        pnl_list = ib.pnl()
        if pnl_list:
            daily_pnl = safe_float(pnl_list[0].dailyPnL)

    # Get today's fees from fee_summary
    fee_rows = _query(
        "SELECT COALESCE(SUM(amount), 0) AS total_fees FROM monitoring.fee_summary WHERE fund_name = %s AND date = %s",
        (fund_name, today),
    )
    fees = float(fee_rows[0]["total_fees"]) if fee_rows else 0

    ending_nav = starting_nav + daily_pnl + fees  # fees are negative

    if existing:
        _exec("""
            UPDATE monitoring.nav_bridge
            SET starting_nav = %s, pnl = %s, fees = %s, ending_nav = %s
            WHERE fund_name = %s AND date = %s
        """, (starting_nav, daily_pnl, fees, ending_nav, fund_name, today))
    else:
        _exec("""
            INSERT INTO monitoring.nav_bridge
                (fund_name, date, starting_nav, pnl, subscriptions, redemptions, fees, distributions, ending_nav)
            VALUES (%s, %s, %s, %s, 0, 0, %s, 0, %s)
        """, (fund_name, today, starting_nav, daily_pnl, fees, ending_nav))

    # Update NAV history
    acct = get_account_summary_map(ib)
    net_liq = safe_float(acct.get("NetLiquidation", ending_nav))

    _exec("""
        INSERT INTO monitoring.nav_history (fund_name, nav_per_unit, total_assets, total_liabilities, timestamp)
        VALUES (%s, %s, %s, 0, NOW())
    """, (fund_name, ending_nav / starting_nav if starting_nav > 0 else 1.0, net_liq))

    # Update fund_overview
    _exec("""
        UPDATE monitoring.fund_overview
        SET aum = %s, nav_per_unit = %s, daily_change = %s, daily_change_pct = %s, updated_at = NOW()
        WHERE fund_name = %s
    """, (
        net_liq,
        ending_nav / starting_nav if starting_nav > 0 else 1.0,
        daily_pnl,
        (daily_pnl / starting_nav * 100) if starting_nav > 0 else 0,
        fund_name,
    ))

    return True


# ── Full Sync ───────────────────────────────────────────────

def sync_all(ib, fund_name="GatSlinger Paper"):
    """Run all ingestion steps. Returns a summary dict."""
    results = {}
    results["trades_inserted"] = sync_trades(ib, fund_name)
    results["positions_inserted"] = sync_positions(ib, fund_name)
    results["pnl_synced"] = sync_daily_pnl(ib, fund_name)
    results["nav_rolled"] = roll_nav_bridge(ib, fund_name)
    return results
