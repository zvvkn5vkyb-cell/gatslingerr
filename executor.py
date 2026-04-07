"""Execution layer — submits orders to IBKR when the strategy state machine
reaches READY_TO_ENTER, and manages stop/target bracket orders.

Only executes in 'paper' or 'live' mode. Sim mode is signal-only.
"""
from ib_insync import (
    IB, Future, ContFuture, MarketOrder, StopOrder, LimitOrder,
    Contract, Order,
)
from ibkr import safe_float, contract_display_symbol, classify_asset_class
from ingest import sync_trades
from datetime import datetime


def build_contract(ib, asset, exchange="CME", currency="USD"):
    """Build and qualify a tradeable front-month futures contract.
    ContFuture is for data only — orders need a real Future with expiry."""
    # Get all available expiries and pick the front month
    contract = Future(asset, exchange=exchange, currency=currency)
    candidates = ib.reqContractDetails(contract)
    if candidates:
        # Sort by expiry, pick the nearest
        candidates.sort(key=lambda c: c.contract.lastTradeDateOrContractMonth)
        front = candidates[0].contract
        ib.qualifyContracts(front)
        return front
    # Fallback
    return Future(asset, exchange=exchange, currency=currency)


def calculate_quantity(ib, risk_pct, stop_distance, contract):
    """Size the position based on account equity and risk percentage.
    risk_pct: e.g. 0.5 means 0.5% of account.
    stop_distance: points from entry to stop.
    Returns number of contracts (minimum 1).
    """
    summary = ib.accountSummary()
    net_liq = 0
    for item in summary:
        if item.tag == "NetLiquidation":
            net_liq = safe_float(item.value)
            break

    if net_liq <= 0 or stop_distance <= 0:
        return 1

    # ES multiplier is 50, MES is 5
    symbol = getattr(contract, 'symbol', '')
    if symbol in ('MES', 'MNQ', 'M2K', 'MYM'):
        multiplier = 5
    elif symbol in ('ES', 'NQ', 'RTY', 'YM'):
        multiplier = 50
    else:
        multiplier = float(getattr(contract, 'multiplier', 50) or 50)

    risk_dollars = net_liq * (risk_pct / 100.0)
    loss_per_contract = stop_distance * multiplier
    qty = int(risk_dollars / loss_per_contract)
    return max(qty, 1)


def execute_entry(ib, trade_state, params, mode="paper"):
    """Submit a bracket order (market entry + stop + target) to IBKR.

    Only fires when trade_state.state == 'IN_POSITION' (just transitioned).
    Returns the parent trade object, or None if not executed.
    """
    if mode not in ("paper", "live"):
        return None

    if trade_state.state != "IN_POSITION":
        return None

    if trade_state.entry_price is None:
        return None

    asset = params.get("_asset", "ES")
    exchange = params.get("_exchange", "CME")
    currency = params.get("_currency", "USD")

    contract = build_contract(ib, asset, exchange, currency)
    if not contract.conId:
        return None

    direction = trade_state.direction
    action = "BUY" if direction == "LONG" else "SELL"
    reverse_action = "SELL" if direction == "LONG" else "BUY"

    stop_distance = abs(trade_state.entry_price - trade_state.stop_price)
    risk_pct = float(params.get("risk_per_trade_pct", 0.5))
    qty = calculate_quantity(ib, risk_pct, stop_distance, contract)

    # Parent: market order for entry
    parent = MarketOrder(action, qty)
    parent.transmit = False

    # Stop loss
    stop = StopOrder(
        reverse_action, qty,
        round(trade_state.stop_price, 2),
    )
    stop.parentId = parent.orderId
    stop.transmit = False

    # Take profit
    take_profit = LimitOrder(
        reverse_action, qty,
        round(trade_state.target_price, 2),
    )
    take_profit.parentId = parent.orderId
    take_profit.transmit = True  # last child transmits all

    # Place bracket
    parent_trade = ib.placeOrder(contract, parent)
    ib.placeOrder(contract, stop)
    ib.placeOrder(contract, take_profit)

    ib.sleep(1)  # allow fills to propagate

    return parent_trade


def check_and_execute(ib, trade_state, params, mode="paper",
                      fund_name="GatSlinger Paper"):
    """Called each tick from app.py. If the state machine just entered
    IN_POSITION, submit the bracket order and sync the trade to DB.

    Returns the trade object if an order was placed, else None.
    """
    # Only execute on fresh entry (not already tracked)
    order_key = "_order_placed"
    if getattr(trade_state, order_key, False):
        return None

    trade = execute_entry(ib, trade_state, params, mode)

    if trade is not None:
        setattr(trade_state, order_key, True)
        # Sync fills to database
        sync_trades(ib, fund_name)

    return trade
