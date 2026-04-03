import asyncio

# Streamlit runs scripts in its own thread. Ensure that thread has an event loop
# BEFORE importing ib_insync — eventkit grabs the loop at import time.
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

import nest_asyncio
nest_asyncio.apply()

import streamlit as st
from ib_insync import IB
import math
import pandas as pd
import psycopg2
from datetime import datetime

st.set_page_config(page_title="GatSlinger", layout="wide")

# ============================================================
# SESSION STATE
# ============================================================
if "ib" not in st.session_state:
    st.session_state.ib = None

# ============================================================
# SIDEBAR — IBKR Connection
# ============================================================
st.sidebar.header("IBKR Connection")

host = st.sidebar.text_input("Host", "127.0.0.1")
port = st.sidebar.text_input("Port", "7497")
client_id = st.sidebar.text_input("Client ID", "1")

if st.sidebar.button("Connect"):
    try:
        ib = IB()
        ib.connect(host, int(port), clientId=int(client_id), timeout=10)
        st.session_state.ib = ib
        st.sidebar.success("Connected to IBKR")
        st.rerun()
    except Exception as e:
        st.sidebar.error(f"Failed: {e}")

ib = st.session_state.ib

if ib and ib.isConnected():
    st.sidebar.success("Connected")
    if st.sidebar.button("Disconnect", key="disconnect"):
        ib.disconnect()
        st.session_state.ib = None
        st.rerun()

# ============================================================
# SIDEBAR — Fund Accounting (PostgreSQL)
# ============================================================
st.sidebar.divider()
st.sidebar.header("Fund Accounting")

@st.cache_resource
def get_db():
    try:
        conn = psycopg2.connect(dbname="financial_db", user="chadh.", host="localhost", port=5432)
        conn.autocommit = True
        return conn
    except Exception:
        return None

def q(sql, params=None):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        st.error(f"DB: {e}")
        get_db.clear()
        return []

db = get_db()
if db:
    st.sidebar.caption("PostgreSQL: connected")
else:
    st.sidebar.warning("PostgreSQL: offline")

funds = q("SELECT * FROM monitoring.fund_overview") if db else []
fund_names = [f["fund_name"] for f in funds]
active_fund = st.sidebar.selectbox("Fund", fund_names) if fund_names else None

st.sidebar.divider()
if st.sidebar.button("Refresh"):
    st.cache_resource.clear()
    st.rerun()
st.sidebar.caption(f"{datetime.now().strftime('%H:%M:%S')}")

# ============================================================
# RISK + P&L MODULE — Helpers
# ============================================================

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

def get_account_summary_map(ib):
    summary = {}
    for item in ib.accountSummary():
        tag = safe_str(item.tag)
        if tag and tag not in summary:
            summary[tag] = safe_float(item.value) if str(item.value).replace(".", "", 1).replace("-", "", 1).isdigit() else item.value
    return summary

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
        getattr(ticker, "marketPrice", lambda: None)() if hasattr(ticker, "marketPrice") else None,
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

def get_positions_df(ib):
    positions = ib.positions()
    if not positions:
        return pd.DataFrame(columns=[
            "Account", "Symbol", "Asset Class", "Currency", "Quantity",
            "Avg Cost", "Market Price", "Market Value", "Cost Basis",
            "Unrealized P&L", "Realized P&L", "Exposure %"
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

def get_portfolio_metrics(ib, positions_df):
    acct = get_account_summary_map(ib)
    net_liq = safe_float(acct.get("NetLiquidation", 0.0))
    cash = safe_float(acct.get("TotalCashValue", acct.get("CashBalance", 0.0)))
    buying_power = safe_float(acct.get("BuyingPower", 0.0))
    maint_margin = safe_float(acct.get("MaintMarginReq", 0.0))
    init_margin = safe_float(acct.get("InitMarginReq", 0.0))
    realized_pnl = safe_float(acct.get("RealizedPnL", 0.0))
    unrealized_pnl_account = safe_float(acct.get("UnrealizedPnL", 0.0))

    if positions_df.empty:
        long_exp = short_exp = net_exp = gross_exp = 0.0
        largest_pct = 0.0
    else:
        long_exp = safe_float(positions_df.loc[positions_df["Market Value"] > 0, "Market Value"].sum())
        short_exp = safe_float(positions_df.loc[positions_df["Market Value"] < 0, "Market Value"].sum())
        net_exp = long_exp + short_exp
        gross_exp = abs(long_exp) + abs(short_exp)
        largest_pct = safe_float(positions_df["Exposure %"].max())

    gross_leverage = (gross_exp / net_liq) if net_liq else 0.0
    unrealized_pnl = unrealized_pnl_account if unrealized_pnl_account != 0 else (
        safe_float(positions_df["Unrealized P&L"].sum()) if not positions_df.empty else 0.0
    )

    return {
        "Net Liquidation": net_liq, "Cash": cash, "Buying Power": buying_power,
        "Initial Margin": init_margin, "Maintenance Margin": maint_margin,
        "Realized P&L": realized_pnl, "Unrealized P&L": unrealized_pnl,
        "Long Exposure": long_exp, "Short Exposure": short_exp,
        "Net Exposure": net_exp, "Gross Exposure": gross_exp,
        "Net Exposure %": (net_exp / net_liq * 100) if net_liq else 0,
        "Gross Exposure %": (gross_exp / net_liq * 100) if net_liq else 0,
        "Gross Leverage": gross_leverage,
        "Margin Utilization %": (maint_margin / net_liq * 100) if net_liq else 0,
        "Largest Position %": largest_pct,
        "Concentration Flag": largest_pct >= 20.0,
    }

def get_exposure_by_asset_class(positions_df):
    if positions_df.empty:
        return pd.DataFrame(columns=["Asset Class", "Market Value", "Exposure %"])
    out = positions_df.groupby("Asset Class", as_index=False).agg({"Market Value": "sum"})
    total_abs = positions_df["Market Value"].abs().sum()
    out["Exposure %"] = out["Market Value"].abs() / total_abs * 100 if total_abs else 0.0
    return out.sort_values("Market Value", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

def get_risk_flags(positions_df, metrics):
    flags = []
    if metrics["Largest Position %"] >= 20:
        flags.append(f"Largest single position is {metrics['Largest Position %']:.2f}% of net liquidation")
    if metrics["Gross Leverage"] >= 1.5:
        flags.append(f"Gross leverage is elevated at {metrics['Gross Leverage']:.2f}x")
    if metrics["Margin Utilization %"] >= 35:
        flags.append(f"Maintenance margin utilization is {metrics['Margin Utilization %']:.2f}%")
    if not positions_df.empty:
        opt_val = positions_df.loc[positions_df["Asset Class"] == "Option", "Market Value"].abs().sum()
        total_abs = positions_df["Market Value"].abs().sum()
        opt_share = (opt_val / total_abs * 100) if total_abs else 0
        if opt_share >= 25:
            flags.append(f"Options represent {opt_share:.2f}% of gross exposure")
    if not flags:
        flags.append("No major risk flags triggered under current rules")
    return flags

def render_risk_and_pnl_module(ib):
    st.header("Risk and P&L")
    try:
        with st.spinner("Loading IBKR account and position data..."):
            try:
                ib.sleep(0.5)
            except Exception:
                pass
            positions_df = get_positions_df(ib)
            metrics = get_portfolio_metrics(ib, positions_df)
            asset_class_df = get_exposure_by_asset_class(positions_df)
            risk_flags = get_risk_flags(positions_df, metrics)

        # Top metrics row 1
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Net Liquidation", fmt_money(metrics["Net Liquidation"]))
        c2.metric("Unrealized P&L", fmt_money(metrics["Unrealized P&L"]))
        c3.metric("Realized P&L", fmt_money(metrics["Realized P&L"]))
        c4.metric("Buying Power", fmt_money(metrics["Buying Power"]))

        # Top metrics row 2
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Gross Exposure", fmt_money(metrics["Gross Exposure"]), fmt_pct(metrics["Gross Exposure %"]))
        c6.metric("Net Exposure", fmt_money(metrics["Net Exposure"]), fmt_pct(metrics["Net Exposure %"]))
        c7.metric("Gross Leverage", f"{safe_float(metrics['Gross Leverage']):.2f}x")
        c8.metric("Largest Position", fmt_pct(metrics["Largest Position %"]))

        left, right = st.columns([2, 1])

        with left:
            st.subheader("Positions")
            if positions_df.empty:
                st.info("No open positions")
            else:
                display_df = positions_df.copy()
                for col in ["Avg Cost", "Market Price", "Market Value", "Cost Basis", "Unrealized P&L", "Realized P&L"]:
                    display_df[col] = display_df[col].map(lambda x: round(safe_float(x), 2))
                display_df["Exposure %"] = display_df["Exposure %"].map(lambda x: round(safe_float(x), 2))
                st.dataframe(display_df, use_container_width=True, hide_index=True)

            st.subheader("Exposure by Asset Class")
            if asset_class_df.empty:
                st.info("No asset class exposure")
            else:
                ac_display = asset_class_df.copy()
                ac_display["Market Value"] = ac_display["Market Value"].map(lambda x: round(safe_float(x), 2))
                ac_display["Exposure %"] = ac_display["Exposure %"].map(lambda x: round(safe_float(x), 2))
                st.dataframe(ac_display, use_container_width=True, hide_index=True)

        with right:
            st.subheader("Risk Flags")
            for flag in risk_flags:
                st.warning(flag)

            st.subheader("Margin")
            st.write(f"**Initial Margin:** {fmt_money(metrics['Initial Margin'])}")
            st.write(f"**Maintenance Margin:** {fmt_money(metrics['Maintenance Margin'])}")
            st.write(f"**Margin Utilization:** {fmt_pct(metrics['Margin Utilization %'])}")

            st.subheader("Liquidity")
            st.write(f"**Cash:** {fmt_money(metrics['Cash'])}")
            st.write(f"**Buying Power:** {fmt_money(metrics['Buying Power'])}")

    except Exception as e:
        st.error(f"Risk and P&L module failed: {e}")

# ============================================================
# MAIN — IBKR Live Data
# ============================================================
st.title("GatSlinger")

ib = st.session_state.ib

if ib and ib.isConnected():
    port_num = int(port)
    environment = "Paper" if port_num == 7497 else "Live" if port_num == 7496 else f"Custom ({port_num})"
    st.subheader(f"IBKR | {environment}")
    render_risk_and_pnl_module(ib)

    # Open Orders
    st.markdown("### Open Orders")
    try:
        trades = ib.openTrades()
        if trades:
            rows = []
            for t in trades:
                rows.append({
                    "Symbol": t.contract.symbol,
                    "Action": t.order.action,
                    "Qty": float(t.order.totalQuantity),
                    "Type": t.order.orderType,
                    "Limit": float(t.order.lmtPrice) if t.order.lmtPrice else None,
                    "Status": t.orderStatus.status,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No open orders")
    except Exception as e:
        st.error(f"Orders: {e}")

    # Daily P&L
    st.markdown("### Daily P&L")
    try:
        accounts = ib.managedAccounts()
        if accounts:
            ib.reqPnL(accounts[0])
            ib.sleep(0.5)
            pnl_list = ib.pnl()
            if pnl_list:
                for p in pnl_list:
                    pc1, pc2, pc3 = st.columns(3)
                    pc1.metric("Daily P&L", fmt_money(p.dailyPnL))
                    pc2.metric("Unrealized", fmt_money(p.unrealizedPnL))
                    pc3.metric("Realized", fmt_money(p.realizedPnL))
            else:
                st.info("PnL data pending — refresh in a few seconds")
    except Exception as e:
        st.error(f"PnL: {e}")

else:
    st.info("Connect to IBKR via the sidebar.")

# ============================================================
# MAIN — Fund Accounting (PostgreSQL)
# ============================================================
if active_fund and funds:
    fund = next((f for f in funds if f["fund_name"] == active_fund), funds[0])

    st.markdown("---")
    st.markdown(f"## {active_fund}")

    nav = float(fund.get("nav_per_unit", 0))
    aum = float(fund.get("aum", 0))
    daily_pct = float(fund.get("daily_change_pct", 0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV / Unit", f"${nav:,.4f}", f"{daily_pct:+.2f}%")
    c2.metric("AUM", f"${aum:,.2f}")
    c3.metric("Daily", f"${float(fund.get('daily_change', 0)):,.2f}", f"{daily_pct:+.2f}%")
    c4.metric("Investors", fund.get("num_investors", 0))

    # NAV Bridge + Chart
    col_b, col_c = st.columns([4, 8])
    with col_b:
        st.markdown("#### NAV Bridge")
        bridge = q("SELECT * FROM monitoring.nav_bridge_waterfall WHERE fund_name = %s ORDER BY date DESC LIMIT 1", (active_fund,))
        if bridge:
            b = bridge[0]
            df = pd.DataFrame([
                {"Component": k, "Amount": float(b.get(k.lower().replace("&", "").replace(" ", "_"), 0) or 0)}
                for k in ["Starting NAV", "PnL", "Subscriptions", "Redemptions", "Fees", "Adjustments", "Ending NAV"]
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

    with col_c:
        st.markdown("#### NAV History")
        hist = q("SELECT timestamp::date AS date, nav_per_unit FROM nav_history WHERE fund_name = %s ORDER BY timestamp DESC LIMIT 90", (active_fund,))
        if hist:
            df = pd.DataFrame(hist).sort_values("date")
            df["nav_per_unit"] = df["nav_per_unit"].astype(float)
            st.line_chart(df.set_index("date")["nav_per_unit"])

    # Positions + Fees
    col_p, col_f = st.columns(2)
    with col_p:
        st.markdown("#### Positions")
        data = q("SELECT * FROM monitoring.position_summary WHERE fund_name = %s", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
    with col_f:
        st.markdown("#### Fees")
        data = q("SELECT * FROM monitoring.fee_summary WHERE fund_name = %s", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)

    # Investors + Alerts
    col_i, col_a = st.columns(2)
    with col_i:
        st.markdown("#### Investors")
        data = q("SELECT * FROM monitoring.investor_allocation WHERE fund_name = %s ORDER BY allocation_value DESC", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
    with col_a:
        st.markdown("#### Alerts")
        data = q("SELECT * FROM monitoring.active_alerts ORDER BY alert_date DESC LIMIT 15")
        if data:
            for a in data:
                icon = {"critical": ":red_circle:", "warning": ":large_orange_circle:"}.get(a.get("severity", ""), ":blue_circle:")
                st.markdown(f"{icon} **{a.get('alert_type','')}** — {a.get('message','')}")
        else:
            st.success("No alerts")

    # Pricing + Fairness
    st.markdown("---")
    st.markdown("### Pricing & Fairness")
    col_pr, col_fa = st.columns([5, 7])
    with col_pr:
        st.markdown("#### NAV Dispersion")
        data = q("SELECT * FROM pricing_dispersion WHERE fund_name = %s ORDER BY nav_dispersion DESC", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
        else:
            st.success("Clean")
    with col_fa:
        st.markdown("#### Fee Attribution")
        data = q("SELECT * FROM investor_fairness WHERE fund_name = %s ORDER BY fee_drag_spread DESC", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)

    # Cohort + HWM
    st.markdown("---")
    col_co, col_hw = st.columns([7, 5])
    with col_co:
        st.markdown("#### Cohort Fairness")
        data = q("SELECT * FROM cohort_fairness WHERE fund_name = %s ORDER BY return_dispersion DESC", (active_fund,))
        if data:
            multi = [c for c in data if c["cohort_size"] > 1]
            flagged = [c for c in data if c["fairness_status"] in ("INVESTIGATE", "REVIEW", "POSITION_CHANGE")]
            t1, t2, t3 = st.tabs([f"All ({len(data)})", f"Flagged ({len(flagged)})", f"Multi ({len(multi)})"])
            with t1:
                st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
            with t2:
                st.dataframe(pd.DataFrame(flagged), use_container_width=True, hide_index=True) if flagged else st.success("None")
            with t3:
                st.dataframe(pd.DataFrame(multi), use_container_width=True, hide_index=True) if multi else st.info("None")

            st.markdown("""<div style="font-size:11px;color:#94a3b8;margin-top:6px">
            <b>Explained by:</b>
            <span style="background:rgba(255,68,68,0.2);padding:2px 6px;border-radius:4px">INVESTIGATE</span> Fees
            <span style="background:rgba(59,130,246,0.2);padding:2px 6px;border-radius:4px;margin-left:4px">POSITION_CHANGE</span> Flows
            <span style="background:rgba(0,255,136,0.1);padding:2px 6px;border-radius:4px;margin-left:4px">FAIR</span> Clean
            </div>""", unsafe_allow_html=True)

            if multi:
                st.markdown("##### Drill-Down")
                opts = [f"{c['entry_date']} | {c['entry_nav']} | {c['share_class']} ({c['cohort_size']})" for c in multi]
                sel = st.selectbox("Cohort", opts)
                if sel:
                    c = multi[opts.index(sel)]
                    drill = q("""SELECT * FROM investor_performance
                        WHERE fund_name = %s AND entry_date = %s AND ABS(entry_nav - %s::numeric) < 0.01
                        ORDER BY net_return_pct""",
                        (active_fund, str(c["entry_date"]), float(c["entry_nav"])))
                    if drill:
                        st.dataframe(pd.DataFrame(drill), use_container_width=True, hide_index=True)

    with col_hw:
        st.markdown("#### HWM Tracking")
        data = q("SELECT * FROM investor_hwm_audit WHERE fund_name = %s ORDER BY accrued_perf_fee DESC", (active_fund,))
        if data:
            above = len([h for h in data if h.get("hwm_status") == "ABOVE_HWM"])
            below = len([h for h in data if h.get("hwm_status") == "BELOW_HWM"])
            total = sum(float(h.get("accrued_perf_fee", 0)) for h in data)
            m1, m2, m3 = st.columns(3)
            m1.metric("Above", above)
            m2.metric("Below", below)
            m3.metric("Accrued", f"${total:,.0f}")
            st.dataframe(pd.DataFrame(data[:25]), use_container_width=True, hide_index=True)
