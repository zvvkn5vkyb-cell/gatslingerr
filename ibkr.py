"""IBKR connection and account helpers — ib_insync wrapper"""
import asyncio

# Ensure a standard asyncio event loop is set BEFORE importing ib_insync.
# Two problems to solve:
#   1. Streamlit's ScriptRunner thread may have no loop → create one.
#   2. Streamlit on Python 3.12+ / uvloop installs a uvloop.Loop which
#      nest_asyncio cannot patch → replace it with a plain asyncio loop.
try:
    loop = asyncio.get_event_loop()
    # Detect uvloop by name — avoid importing uvloop just to check type.
    if type(loop).__module__.startswith("uvloop"):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import nest_asyncio
nest_asyncio.apply()

from ib_insync import IB, Future, ContFuture, util
import math
import pandas as pd


# ── Utility helpers ──────────────────────────────────────────

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def safe_str(value, default=""):
    return str(value) if value is not None else default


def fmt_money(value):
    return f"${safe_float(value):,.2f}"


def fmt_pct(value):
    return f"{safe_float(value):.2f}%"


# ── Account ──────────────────────────────────────────────────

def get_account_summary_map(ib):
    summary = {}
    for item in ib.accountSummary():
        tag = safe_str(item.tag)
        if tag and tag not in summary:
            summary[tag] = (
                safe_float(item.value)
                if str(item.value).replace(".", "", 1).replace("-", "", 1).isdigit()
                else item.value
            )
    return summary


# ── Contract helpers ─────────────────────────────────────────

def classify_asset_class(contract):
    sec_type = safe_str(getattr(contract, "secType", "")).upper()
    mapping = {
        "STK": "Equity", "OPT": "Option", "FUT": "Future", "CASH": "FX",
        "CFD": "CFD", "BOND": "Bond", "IND": "Index", "CRYPTO": "Crypto",
        "CMDTY": "Commodity", "FOP": "Futures Option",
    }
    return mapping.get(sec_type, f"Other ({sec_type or 'Unknown'})")


def contract_display_symbol(contract):
    sec_type = safe_str(getattr(contract, "secType", "")).upper()
    symbol = safe_str(getattr(contract, "symbol", ""))
    local_symbol = safe_str(getattr(contract, "localSymbol", ""))
    if sec_type == "OPT":
        right = safe_str(getattr(contract, "right", ""))
        strike = getattr(contract, "strike", None)
        expiry = safe_str(getattr(contract, "lastTradeDateOrContractMonth", ""))
        return f"{symbol} {expiry} {right} {strike}".strip()
    return local_symbol or symbol


def get_market_price_from_ticker(ticker, fallback=0.0):
    if ticker is None:
        return safe_float(fallback)
    candidates = [
        getattr(ticker, "marketPrice", lambda: None)()
        if hasattr(ticker, "marketPrice") else None,
        getattr(ticker, "last", None),
        getattr(ticker, "close", None),
        getattr(ticker, "bid", None),
        getattr(ticker, "ask", None),
    ]
    for c in candidates:
        v = safe_float(c, default=None)
        if v is not None and v != 0:
            return v
    return safe_float(fallback)


# ── Historical bars ──────────────────────────────────────────

def get_ibkr_bars(ib, symbol="ES", exchange="CME", currency="USD",
                  duration="1 D", bar_size="1 min"):
    """Fetch historical bars for a futures contract. Returns a DataFrame
    with datetime index and OHLCV columns, or None on failure.

    Uses ContFuture (continuous front-month) for historical data so
    IBKR can resolve the contract without a specific expiry date.
    Falls back to Future with explicit front-month if ContFuture fails.
    """
    # Try continuous front-month first
    contract = ContFuture(symbol, exchange=exchange, currency=currency)
    qualified = ib.qualifyContracts(contract)

    # Fallback: resolve front-month explicitly
    if not qualified:
        contract = Future(symbol, exchange=exchange, currency=currency)
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return None

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow="TRADES",
        useRTH=False,
        formatDate=1,
    )

    if not bars:
        return None

    df = util.df(bars)
    if df is None or df.empty:
        return None

    df = df.rename(columns={"date": "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime")
    return df


# ── Positions ────────────────────────────────────────────────

def get_positions_df(ib):
    positions = ib.positions()
    if not positions:
        return pd.DataFrame(columns=[
            "Account", "Symbol", "Asset Class", "Currency", "Quantity",
            "Avg Cost", "Market Price", "Market Value", "Cost Basis",
            "Unrealized P&L", "Realized P&L", "Exposure %",
        ])

    contracts = [p.contract for p in positions]
    try:
        tickers = ib.reqTickers(*contracts)
    except Exception:
        tickers = [None] * len(contracts)

    acct = get_account_summary_map(ib)
    net_liq = safe_float(acct.get("NetLiquidation", 0.0))

    rows = []
    for idx, p in enumerate(positions):
        contract = p.contract
        ticker = tickers[idx] if idx < len(tickers) else None
        qty = safe_float(p.position)
        avg_cost = safe_float(p.avgCost)
        market_price = get_market_price_from_ticker(ticker, fallback=avg_cost)
        market_value = qty * market_price
        cost_basis = qty * avg_cost
        exposure_pct = (abs(market_value) / net_liq * 100.0) if net_liq else 0.0

        rows.append({
            "Account": safe_str(getattr(p, "account", "")),
            "Symbol": contract_display_symbol(contract),
            "Asset Class": classify_asset_class(contract),
            "Currency": safe_str(getattr(contract, "currency", "")),
            "Quantity": qty,
            "Avg Cost": avg_cost,
            "Market Price": market_price,
            "Market Value": market_value,
            "Cost Basis": cost_basis,
            "Unrealized P&L": market_value - cost_basis,
            "Realized P&L": 0.0,
            "Exposure %": exposure_pct,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Market Value", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    return df
