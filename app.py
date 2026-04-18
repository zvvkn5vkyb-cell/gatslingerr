"""GatSlinger — IBKR + Fund Accounting Dashboard"""

# ibkr.py handles the critical event-loop-before-import dance.
# Import it first so ib_insync gets a valid loop.
from ibkr import IB, safe_float, fmt_money, fmt_pct, get_ibkr_bars

import streamlit as st
import pandas as pd
from datetime import datetime

from db import get_db, q
from risk import render_risk_and_pnl_module
from strategy_manager import render_strategy_manager, get_active_strategies
from orb_strategy import generate_orb_signal, TradeState, update_retest_state, check_exit
from nav_summary import render_nav_summary

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

db = get_db()
if db:
    st.sidebar.caption("PostgreSQL: connected")
else:
    st.sidebar.warning("PostgreSQL: offline")

funds = q("SELECT * FROM monitoring.fund_overview") if db else []
fund_names = [f["fund_name"] for f in funds]
active_fund = st.sidebar.selectbox("Fund", fund_names) if fund_names else None

st.sidebar.divider()
page = st.sidebar.radio(
    "Section",
    ["Trading Dashboard", "Strategy Manager", "NAV Summary (AI)"],
    label_visibility="collapsed",
)

if st.sidebar.button("Refresh"):
    st.cache_resource.clear()
    st.rerun()
st.sidebar.caption(f"{datetime.now().strftime('%H:%M:%S')}")

# ============================================================
# MAIN — Route by page
# ============================================================
st.title("GatSlinger")

if page == "Strategy Manager":
    render_strategy_manager()
    st.stop()

if page == "NAV Summary (AI)":
    render_nav_summary(active_fund, funds)
    st.stop()

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

# ── Strategies In Play ───────────────────────────────────────
st.markdown("---")
st.subheader("Strategies In Play")
active_strategies = get_active_strategies()
if active_strategies:
    rows = []
    for name, cfg in active_strategies.items():
        rows.append({
            "Strategy": name,
            "Asset": cfg.get("asset", ""),
            "Mode": cfg.get("mode", ""),
            "Window": cfg.get("params", {}).get("window_minutes", ""),
            "Risk %": cfg.get("params", {}).get("risk_per_trade_pct", ""),
            "Max Trades": cfg.get("params", {}).get("max_daily_trades", ""),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("No active strategies. Enable one in Strategy Manager.")

# ── Strategy Signals ─────────────────────────────────────────
import pytz

st.subheader("Strategy Signals")

active_strategies = get_active_strategies()
ib_live = ib and ib.isConnected()

if not active_strategies:
    st.info("No active strategies to evaluate.")
else:
    for name, cfg in active_strategies.items():
        st.markdown(f"### {name}")

        params = cfg.get("params", {})
        asset = cfg.get("asset", "")

        # ── Get data: live IBKR bars when connected, else skip
        if not ib_live:
            st.warning(f"{name}: Connect to IBKR for live signals.")
            continue

        raw = get_ibkr_bars(ib, symbol=asset)
        if raw is None or raw.empty:
            st.warning(f"{name}: No IBKR data returned for {asset}.")
            continue

        # ── Session slicing: today only, RTH 09:30–16:00 ET
        eastern = pytz.timezone("US/Eastern")
        data = raw.tz_localize("UTC").tz_convert(eastern) if raw.index.tz is None else raw.tz_convert(eastern)

        today = data.index.date[-1]
        data = data[data.index.date == today]

        session_open = params.get("session_open", "09:30")
        session_close = params.get("session_close", "16:00")
        rth = data.between_time(session_open, session_close)

        window = int(params.get("window_minutes", 15))

        if rth.empty:
            st.info(f"{name}: No RTH data for {today}. Market may be closed.")
            continue

        if len(rth) < window:
            st.info(f"{name}: Opening range forming — {len(rth)}/{window} bars.")
            continue

        # ── Build ORB input: opening range + intraday
        orb_data = rth.copy()

        signal = generate_orb_signal(orb_data, params)
        signal_type = signal.get("signal", "FLAT")
        price = signal.get("current_close", 0)
        orb_high = signal.get("orb_high", 0)
        orb_low = signal.get("orb_low", 0)
        orb_range = signal.get("orb_range", 0)

        distance_to_high = price - orb_high
        distance_to_low = orb_low - price

        # ── Row 1: Signal + levels + distance
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Signal", signal_type)
        col2.metric("Price", round(price, 2))
        col3.metric("ORB High", round(orb_high, 2))
        col4.metric("ORB Low", round(orb_low, 2))
        with col5:
            if signal_type == "FLAT":
                st.metric("Dist to Breakout", round(min(abs(distance_to_high), abs(distance_to_low)), 2))
            elif signal_type == "LONG":
                st.metric("Breakout Above", round(distance_to_high, 2))
            elif signal_type == "SHORT":
                st.metric("Breakout Below", round(distance_to_low, 2))

        # ── Row 2: ATR filter + trade eligibility
        atr_filter = params.get("atr_filter", False)
        atr_min = params.get("atr_min", 0)
        atr_value = float(abs(rth["high"] - rth["low"]).rolling(14).mean().iloc[-1])

        trade_allowed = True
        reason_blocked = None

        if atr_filter and atr_value < atr_min:
            trade_allowed = False
            reason_blocked = f"Low volatility (ATR {atr_value:.2f} < {atr_min})"

        if signal_type == "FLAT":
            trade_allowed = False
            reason_blocked = reason_blocked or "No breakout"

        # ── Row 3: Trend context
        ma_fast = float(rth["close"].rolling(10).mean().iloc[-1])
        ma_slow = float(rth["close"].rolling(30).mean().iloc[-1])
        trend = "UP" if ma_fast > ma_slow else "DOWN" if len(rth) >= 30 else "N/A"

        colA, colB, colC = st.columns(3)
        colA.metric("ATR (14)", round(atr_value, 2))
        with colB:
            if trade_allowed:
                st.success("Trade Allowed")
            else:
                st.error(f"Blocked: {reason_blocked}")
        colC.metric("Trend (10/30 MA)", trend)

        st.caption(f"RTH bars: {len(rth)} | Session: {session_open}–{session_close} ET | Date: {today}")

        # ── Retest state machine (persists across Streamlit reruns)
        state_key = f"trade_state_{name}"
        if state_key not in st.session_state:
            st.session_state[state_key] = TradeState()

        ts = st.session_state[state_key]

        # Inject live ATR so the state machine can compute stop/target
        params["_atr_value"] = atr_value

        # Only advance if trade is allowed or we're already tracking
        if trade_allowed or ts.state != "WAITING_BREAKOUT":
            ts = update_retest_state(ts, signal_type, price, orb_high, orb_low, params,
                                       bar_count=len(rth))
            st.session_state[state_key] = ts

        # ── Exit check (stop loss / profit target)
        if "trade_log" not in st.session_state:
            st.session_state.trade_log = []

        if ts.state == "IN_POSITION":
            exited, exit_reason, exit_pnl = check_exit(ts, price)
            if exited:
                st.session_state.trade_log.append({
                    "strategy": name,
                    "direction": ts.direction,
                    "entry": round(ts.entry_price, 2),
                    "exit": round(price, 2),
                    "stop": round(ts.stop_price, 2) if ts.stop_price else "—",
                    "target": round(ts.target_price, 2) if ts.target_price else "—",
                    "pnl": round(exit_pnl, 2),
                    "reason": exit_reason,
                })
                ts.reset()
                st.session_state[state_key] = ts

        # ── Row 4: Retest state display
        state_colors = {
            "WAITING_BREAKOUT": "#94a3b8",
            "WAITING_RETEST":   "#f59e0b",
            "READY_TO_ENTER":   "#22c55e",
            "IN_POSITION":      "#3b82f6",
        }
        sc = state_colors.get(ts.state, "#94a3b8")

        bars_since = (len(rth) - ts.retest_start_index) if ts.retest_start_index is not None else 0
        max_retest_bars = int(params.get("max_retest_bars", 10))

        rs1, rs2, rs3, rs4, rs5 = st.columns(5)
        rs1.markdown(
            f'<div style="background:{sc}22;border:1px solid {sc};border-radius:6px;'
            f'padding:8px 12px;text-align:center;font-weight:600;color:{sc}">'
            f'{ts.state.replace("_", " ")}</div>',
            unsafe_allow_html=True
        )
        rs2.metric("Direction", ts.direction or "—")
        rs3.metric("Breakout Level", round(ts.breakout_level, 2) if ts.breakout_level else "—")
        rs4.metric("Entry Price", round(ts.entry_price, 2) if ts.entry_price else "—")
        rs5.metric("Bars Since Breakout", f"{bars_since}/{max_retest_bars}" if ts.retest_start_index else "—")

        # ── Row 5: Live P&L when in position
        if ts.state == "IN_POSITION" and ts.entry_price is not None:
            _, _, live_pnl = check_exit(ts, price)
            pnl_color = "green" if live_pnl >= 0 else "red"
            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Live P&L (pts)", round(live_pnl, 2))
            pc2.metric("Stop", round(ts.stop_price, 2) if ts.stop_price else "—")
            pc3.metric("Target", round(ts.target_price, 2) if ts.target_price else "—")
            dist_stop = abs(price - ts.stop_price) if ts.stop_price else 0
            dist_target = abs(price - ts.target_price) if ts.target_price else 0
            pc4.metric("R:R from here",
                       f"{dist_target:.1f} / {dist_stop:.1f}" if dist_stop > 0 else "—")

        # ── Reason line
        reason = signal.get("reason", "")
        color = {"LONG": "green", "SHORT": "red"}.get(signal_type, "#94a3b8")
        st.markdown(
            f'<span style="color:{color};font-size:13px">▶ {reason}</span>'
            f'<span style="color:#94a3b8;font-size:12px;margin-left:16px">ORB range: {orb_range}</span>',
            unsafe_allow_html=True
        )
        st.markdown("---")

# ── Trade Log ────────────────────────────────────────────────
if "trade_log" not in st.session_state:
    st.session_state.trade_log = []

if st.session_state.trade_log:
    st.subheader("Trade Log")
    log_df = pd.DataFrame(st.session_state.trade_log)
    total_pnl = log_df["pnl"].sum()
    wins = (log_df["pnl"] > 0).sum()
    losses = (log_df["pnl"] <= 0).sum()
    win_rate = (wins / len(log_df) * 100) if len(log_df) > 0 else 0

    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("Trades", len(log_df))
    lc2.metric("Total P&L (pts)", round(total_pnl, 2))
    lc3.metric("Win Rate", f"{win_rate:.0f}%")
    lc4.metric("W / L", f"{wins} / {losses}")

    st.dataframe(log_df, use_container_width=True, hide_index=True)

    if st.button("Clear Trade Log"):
        st.session_state.trade_log = []
        st.rerun()

    st.markdown("---")

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
