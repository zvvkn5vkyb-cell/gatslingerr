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
from ingest import sync_all
from executor import check_and_execute

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
    if st.sidebar.button("Sync IBKR → DB", key="sync_ibkr"):
        with st.sidebar.status("Syncing...", expanded=True):
            results = sync_all(ib)
            st.write(f"Trades inserted: {results['trades_inserted']}")
            st.write(f"Positions snapshot: {results['positions_inserted']}")
            st.write(f"P&L synced: {results['pnl_synced']}")
            st.write(f"NAV rolled: {results['nav_rolled']}")
        st.sidebar.success("Sync complete")
        st.rerun()
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
    ["Trading Dashboard", "Strategy Manager", "Cohort Analysis"],
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

if page == "Cohort Analysis":
    st.subheader("Investor Cohort Dispersion")

    # ── Cohort overview table
    cohorts = q("""
        SELECT entry_date,
               COUNT(*) AS investors,
               ROUND(AVG(net_return_pct) * 100, 4) AS avg_return_pct,
               ROUND((MAX(net_return_pct) - MIN(net_return_pct)) * 100, 4) AS spread_pct,
               ROUND(MAX(ABS(fee_drag_component)) * 100, 4) AS max_fee_drag_pct,
               ROUND(MAX(ABS(timing_component)) * 100, 4) AS max_timing_pct
        FROM monitoring.investor_performance
        WHERE fund_name = %s
        GROUP BY entry_date
        ORDER BY entry_date
    """, (active_fund or 'GatSlinger Paper',))

    if not cohorts:
        st.info("No investor performance data.")
        st.stop()

    cohort_df = pd.DataFrame(cohorts)
    st.dataframe(cohort_df, use_container_width=True, hide_index=True)

    # ── Cohort selector
    cohort_dates = [str(c["entry_date"]) for c in cohorts]
    selected_date = st.selectbox("Select cohort to explain", cohort_dates,
                                  index=len(cohort_dates) - 1)

    # ── Explain Dispersion
    st.markdown("---")
    st.subheader(f"Explain Dispersion — {selected_date}")

    investors = q("""
        SELECT investor_id, class, entry_nav, units,
               ROUND(realized_return * 100, 4) AS realized_pct,
               ROUND(nav_return_component * 100, 4) AS nav_return_pct,
               ROUND(fee_drag_component * 100, 4) AS fee_drag_pct,
               ROUND(timing_component * 100, 4) AS timing_pct,
               ROUND(restatement_adjustment * 100, 4) AS restatement_pct,
               ROUND(net_return_pct * 100, 4) AS net_return_pct,
               holding_days
        FROM monitoring.investor_performance
        WHERE fund_name = %s AND entry_date = %s
        ORDER BY net_return_pct DESC
    """, (active_fund or 'GatSlinger Paper', selected_date))

    if not investors:
        st.warning("No investors in this cohort.")
        st.stop()

    inv_df = pd.DataFrame(investors)

    # ── Summary metrics
    spread = float(inv_df["net_return_pct"].max() - inv_df["net_return_pct"].min())
    fee_range = float(inv_df["fee_drag_pct"].max() - inv_df["fee_drag_pct"].min())
    timing_range = float(inv_df["timing_pct"].max() - inv_df["timing_pct"].min())

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("Investors", len(inv_df))
    mc2.metric("Return Spread", f"{spread:.4f}%")
    mc3.metric("Fee Drag Range", f"{fee_range:.4f}%")
    mc4.metric("Timing Range", f"{timing_range:.4f}%")

    # ── Determine primary driver
    drivers = []
    if fee_range > 0.001:
        drivers.append(f"Fee drag ({fee_range:.4f}%)")
    if timing_range > 0.001:
        drivers.append(f"Settlement timing ({timing_range:.4f}%)")
    restatement_range = float(inv_df["restatement_pct"].max() - inv_df["restatement_pct"].min())
    if restatement_range > 0.001:
        drivers.append(f"NAV restatement ({restatement_range:.4f}%)")

    if drivers:
        st.info(f"**Primary drivers:** {' + '.join(drivers)}")
    else:
        st.success("Dispersion is negligible — cohort is fair.")

    # ── Full investor table
    st.markdown("#### Investor Detail")
    st.dataframe(inv_df, use_container_width=True, hide_index=True)

    # ── Return decomposition chart
    st.markdown("#### Return Decomposition")
    chart_df = inv_df.set_index("investor_id")[["nav_return_pct", "fee_drag_pct", "timing_pct", "restatement_pct"]]
    st.bar_chart(chart_df)

    # ── Explainability check
    st.markdown("#### Explainability")
    for _, row in inv_df.iterrows():
        explained = abs(row["nav_return_pct"]) + abs(row["fee_drag_pct"]) + abs(row["timing_pct"]) + abs(row["restatement_pct"])
        residual = abs(row["net_return_pct"]) - explained
        status = "EXPLAINED" if abs(residual) < 0.01 else "RESIDUAL"
        color = "green" if status == "EXPLAINED" else "red"
        st.markdown(
            f'<span style="color:{color};font-size:13px">'
            f'{row["investor_id"]}: net {row["net_return_pct"]:.4f}% = '
            f'NAV {row["nav_return_pct"]:.4f}% + fees {row["fee_drag_pct"]:.4f}% + '
            f'timing {row["timing_pct"]:.4f}% + restate {row["restatement_pct"]:.4f}% '
            f'→ {status} (residual {residual:.4f}%)</span>',
            unsafe_allow_html=True,
        )

    # ── NAV bridge for the cohort period
    st.markdown("---")
    st.markdown("#### NAV Bridge (Cohort Holding Period)")
    bridge = q("""
        SELECT date, starting_nav, pnl, subscriptions, redemptions, fees, distributions, ending_nav
        FROM monitoring.nav_bridge
        WHERE fund_name = %s AND date >= %s
        ORDER BY date
    """, (active_fund or 'GatSlinger Paper', selected_date))

    if bridge:
        bridge_df = pd.DataFrame(bridge)
        st.dataframe(bridge_df, use_container_width=True, hide_index=True)

        # NAV trajectory chart
        st.markdown("#### NAV Trajectory")
        nav_chart = bridge_df[["date", "ending_nav"]].copy()
        nav_chart["date"] = pd.to_datetime(nav_chart["date"])
        st.line_chart(nav_chart.set_index("date")["ending_nav"])

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
ib = st.session_state.get("ib")  # re-read in case session updated
ib_live = ib and ib.isConnected()

if not ib_live:
    st.warning("Connect to IBKR to see live signals.")
elif not active_strategies:
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

        try:
            raw = get_ibkr_bars(ib, symbol=asset, duration="2 D")
        except Exception as _e:
            import traceback
            st.error(f"{name} data fetch error: {_e}")
            st.code(traceback.format_exc())
            continue

        if raw is None or raw.empty:
            st.warning(f"{name}: No IBKR data returned for {asset}.")
            continue

        # ── Session slicing: today only, RTH 09:30–16:00 ET
        eastern = pytz.timezone("US/Eastern")
        data_full = raw.tz_localize("UTC").tz_convert(eastern) if raw.index.tz is None else raw.tz_convert(eastern)

        today = data_full.index.date[-1]
        data = data_full[data_full.index.date == today]

        session_open = params.get("session_open", "09:30")
        session_close = params.get("session_close", "16:00")
        rth = data.between_time(session_open, session_close)

        # ── Prior day high/low (fed to signal generator for confluence filter)
        prior_day_high = prior_day_low = None
        all_dates = sorted(set(data_full.index.date))
        if len(all_dates) >= 2:
            prior_date = all_dates[-2]
            prior_rth = data_full[data_full.index.date == prior_date].between_time(session_open, session_close)
            if not prior_rth.empty:
                prior_day_high = float(prior_rth["high"].max())
                prior_day_low  = float(prior_rth["low"].min())
        params["_prior_day_high"] = prior_day_high
        params["_prior_day_low"]  = prior_day_low

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

        # ── Pull enriched signal values
        atr_value      = float(signal.get("atr_value", abs(rth["high"] - rth["low"]).rolling(14).mean().iloc[-1]))
        atr_percentile = signal.get("atr_percentile")
        orb_range_pct  = signal.get("orb_range_pct_atr", 0)
        skip_reason    = signal.get("skip_reason", None)

        # ── Row 2: ATR filter + trade eligibility
        atr_filter  = params.get("atr_filter", False)
        atr_min     = params.get("atr_min", 0)
        min_orb_pct = params.get("min_orb_range_pct_atr", 0.0)
        min_atr_pct = params.get("min_atr_percentile", 0.0)

        trade_allowed  = True
        reason_blocked = None

        if atr_filter and atr_value < atr_min:
            trade_allowed  = False
            reason_blocked = f"Low volatility (ATR {atr_value:.2f} < {atr_min})"

        if skip_reason == "orb_range_filter":
            trade_allowed  = False
            reason_blocked = f"ORB range too tight ({orb_range_pct:.1f}% of ATR)"

        if skip_reason == "vol_regime_filter":
            trade_allowed  = False
            reason_blocked = f"Low vol regime (ATR pctile {atr_percentile:.1f})"

        if signal_type == "FLAT" and not reason_blocked:
            trade_allowed  = False
            reason_blocked = "No breakout"

        # ── Row 3: Trend context
        ma_fast = float(rth["close"].rolling(10).mean().iloc[-1])
        ma_slow = float(rth["close"].rolling(30).mean().iloc[-1])
        trend   = "UP" if ma_fast > ma_slow else "DOWN" if len(rth) >= 30 else "N/A"

        colA, colB, colC, colD = st.columns(4)
        colA.metric("ATR (14)", round(atr_value, 2))
        colD.metric("ATR Percentile", f"{atr_percentile:.1f}%" if atr_percentile is not None else "N/A")
        with colB:
            if trade_allowed:
                st.success("Trade Allowed")
            else:
                st.error(f"Blocked: {reason_blocked}")
        colC.metric("Trend (10/30 MA)", trend)

        # ── ORB range vs ATR
        st.caption(f"ORB range: {orb_range} pts = {orb_range_pct:.1f}% of ATR")

        # ── Log no-trade days to DB
        if not trade_allowed and db:
            ntl_high  = float(rth["high"].max())
            ntl_low   = float(rth["low"].min())
            ntl_close = float(rth["close"].iloc[-1])
            q("""
                INSERT INTO monitoring.no_trade_log
                    (fund_name, date, strategy, atr_value, orb_range, orb_range_pct_atr,
                     atr_percentile, skip_reason, would_have_signal,
                     session_high, session_low, session_close)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date, strategy) DO UPDATE SET
                    atr_value = EXCLUDED.atr_value,
                    skip_reason = EXCLUDED.skip_reason,
                    would_have_signal = EXCLUDED.would_have_signal,
                    session_high = EXCLUDED.session_high,
                    session_low = EXCLUDED.session_low,
                    session_close = EXCLUDED.session_close
            """, (
                active_fund or "GatSlinger Paper",
                name,
                round(atr_value, 4),
                orb_range,
                orb_range_pct,
                atr_percentile,
                reason_blocked,
                signal_type,
                ntl_high,
                ntl_low,
                ntl_close,
            ))

        st.caption(f"RTH bars: {len(rth)} | Session: {session_open}–{session_close} ET | Date: {today}")

        # ── VWAP + volume display
        vwap         = signal.get("vwap")
        vol_current  = signal.get("current_volume", 0)
        vol_avg      = signal.get("avg_volume", 0)
        volume_ok    = signal.get("volume_ok", True)
        vwap_ok      = (price > vwap if signal_type == "LONG" else price < vwap) if vwap else None

        vx1, vx2, vx3 = st.columns(3)
        vx1.metric("VWAP", round(vwap, 2) if vwap else "N/A",
                   delta="Above" if vwap_ok else "Below" if vwap_ok is not None else None)
        vx2.metric("Volume (current)", f"{int(vol_current):,}")
        vx3.metric("Volume (avg)", f"{int(vol_avg):,}",
                   delta="✓ OK" if volume_ok else "✗ Low")

        # ── Prior day high/low display
        if prior_day_high and prior_day_low:
            pdcol1, pdcol2, pdcol3 = st.columns(3)
            pdcol1.metric("Prior Day High", round(prior_day_high, 2),
                          delta="Above ✓" if price > prior_day_high else "Below")
            pdcol2.metric("Prior Day Low", round(prior_day_low, 2),
                          delta="Below ✓" if price < prior_day_low else "Above")
            pdcol3.metric("Prior Day Confluence", "ON" if params.get("prior_day_confluence") else "OFF")

        # ── Retest state machine (persists across Streamlit reruns)
        state_key = f"trade_state_{name}"
        if state_key not in st.session_state:
            st.session_state[state_key] = TradeState()

        ts = st.session_state[state_key]

        # ── Daily reset (new day detected)
        day_key = f"last_trade_date_{name}"
        if st.session_state.get(day_key) != today:
            ts.reset_daily()
            st.session_state[day_key] = today

        # ── Circuit breaker check
        max_consec_losses = int(params.get("max_consecutive_losses", 2))
        eod_exit_time     = params.get("eod_exit_time", "15:45")

        if ts.circuit_broken:
            st.warning(f"{name}: Circuit breaker active — {ts.consecutive_losses} consecutive losses. Paused for today.")
            st.session_state[state_key] = ts
            st.markdown("---")
            continue

        # ── Max daily trades check
        max_daily = int(params.get("max_daily_trades", 2))
        if ts.daily_trades >= max_daily:
            st.info(f"{name}: Max daily trades reached ({ts.daily_trades}/{max_daily}).")
            st.session_state[state_key] = ts
            st.markdown("---")
            continue

        # Inject live ATR and asset info so the state machine + executor can work
        params["_atr_value"] = atr_value
        params["_asset"] = asset

        prev_state = ts.state
        current_bar_time = rth.index[-1].time() if hasattr(rth.index[-1], "time") else None

        # Only advance if trade is allowed or we're already tracking
        if trade_allowed or ts.state != "WAITING_BREAKOUT":
            ts = update_retest_state(ts, signal_type, price, orb_high, orb_low, params,
                                       bar_count=len(rth), current_time=current_bar_time)
            st.session_state[state_key] = ts

        # ── Auto-execute: submit bracket order when state transitions to IN_POSITION
        if ts.state == "IN_POSITION" and prev_state != "IN_POSITION" and ib_live:
            strategy_mode = cfg.get("mode", "sim")
            executed = check_and_execute(ib, ts, params, mode=strategy_mode)
            if executed:
                ts.daily_trades += 1
                st.success(f"ORDER SUBMITTED: {ts.direction} {asset} @ market | "
                           f"Stop: {ts.stop_price:.2f} | Target: {ts.target_price:.2f}")
            st.session_state[state_key] = ts

        # ── Exit check (stop loss / profit target / EOD)
        if "trade_log" not in st.session_state:
            st.session_state.trade_log = []

        if ts.state == "IN_POSITION":
            exited, exit_reason, exit_pnl = check_exit(ts, price,
                                                        current_time=current_bar_time,
                                                        eod_exit_time=eod_exit_time)
            if exit_reason == "PARTIAL_TP":
                # Half position off — log it, but don't reset (stop already moved to breakeven)
                st.session_state.trade_log.append({
                    "strategy": name,
                    "direction": ts.direction,
                    "entry": round(ts.entry_price, 2),
                    "exit": round(price, 2),
                    "stop": "BREAKEVEN",
                    "target": round(ts.target_price, 2) if ts.target_price else "—",
                    "pnl": round(exit_pnl, 2),
                    "reason": "PARTIAL_TP — 50% off, stop → breakeven",
                })
                st.session_state[state_key] = ts
            elif exited:
                # Full exit (stop, target, or EOD)
                if exit_pnl < 0:
                    ts.consecutive_losses += 1
                    if ts.consecutive_losses >= max_consec_losses:
                        ts.circuit_broken = True
                else:
                    ts.consecutive_losses = 0

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
            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Live P&L (pts)", round(live_pnl, 2))
            pc2.metric("Stop", round(ts.stop_price, 2) if ts.stop_price else "—")
            pc3.metric("Target", round(ts.target_price, 2) if ts.target_price else "—")
            dist_stop = abs(price - ts.stop_price) if ts.stop_price else 0
            dist_target = abs(price - ts.target_price) if ts.target_price else 0
            pc4.metric("R:R from here",
                       f"{dist_target:.1f} / {dist_stop:.1f}" if dist_stop > 0 else "—")
            # ── Partial TP status
            if ts.use_partial_tp:
                if ts.partial_taken:
                    st.caption("✅ Partial TP taken — 50% off, stop at breakeven, riding remainder to full target")
                elif ts.partial_target:
                    dist_partial = abs(price - ts.partial_target)
                    st.caption(f"⏳ Partial TP at {ts.partial_target:.2f} ({dist_partial:.1f} pts away) — will move stop to breakeven")

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
        bridge = q("SELECT * FROM monitoring.nav_bridge WHERE fund_name = %s ORDER BY date DESC LIMIT 1", (active_fund,))
        if bridge:
            b = bridge[0]
            df = pd.DataFrame([
                {"Component": k, "Amount": float(b.get(k.lower().replace("&", "").replace(" ", "_"), 0) or 0)}
                for k in ["Starting NAV", "PnL", "Subscriptions", "Redemptions", "Fees", "Distributions", "Ending NAV"]
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

    with col_c:
        st.markdown("#### NAV History")
        hist = q("SELECT timestamp::date AS date, nav_per_unit FROM monitoring.nav_history WHERE fund_name = %s ORDER BY timestamp DESC LIMIT 90", (active_fund,))
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
        data = q("SELECT * FROM monitoring.pricing_dispersion WHERE fund_name = %s ORDER BY nav_dispersion DESC", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)
        else:
            st.success("Clean")
    with col_fa:
        st.markdown("#### Fee Attribution")
        data = q("SELECT * FROM monitoring.investor_fairness WHERE fund_name = %s ORDER BY fee_drag_spread DESC", (active_fund,))
        if data:
            st.dataframe(pd.DataFrame(data), use_container_width=True, hide_index=True)

    # Cohort + HWM
    st.markdown("---")
    col_co, col_hw = st.columns([7, 5])
    with col_co:
        st.markdown("#### Cohort Fairness")
        data = q("SELECT * FROM monitoring.cohort_fairness WHERE fund_name = %s ORDER BY return_dispersion DESC", (active_fund,))
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
                    drill = q("""SELECT * FROM monitoring.investor_performance
                        WHERE fund_name = %s AND entry_date = %s AND ABS(entry_nav - %s::numeric) < 0.01
                        ORDER BY net_return_pct""",
                        (active_fund, str(c["entry_date"]), float(c["entry_nav"])))
                    if drill:
                        st.dataframe(pd.DataFrame(drill), use_container_width=True, hide_index=True)

    with col_hw:
        st.markdown("#### HWM Tracking")
        data = q("SELECT * FROM monitoring.investor_hwm_audit WHERE fund_name = %s ORDER BY accrued_perf_fee DESC", (active_fund,))
        if data:
            above = len([h for h in data if h.get("hwm_status") == "ABOVE_HWM"])
            below = len([h for h in data if h.get("hwm_status") == "BELOW_HWM"])
            total = sum(float(h.get("accrued_perf_fee", 0)) for h in data)
            m1, m2, m3 = st.columns(3)
            m1.metric("Above", above)
            m2.metric("Below", below)
            m3.metric("Accrued", f"${total:,.0f}")
            st.dataframe(pd.DataFrame(data[:25]), use_container_width=True, hide_index=True)
