"""Risk analytics and P&L rendering for IBKR positions"""
import streamlit as st
import pandas as pd

from ibkr import (
    safe_float, fmt_money, fmt_pct,
    get_account_summary_map, get_positions_df,
)


# ── Portfolio metrics ────────────────────────────────────────

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


# ── Exposure breakdown ───────────────────────────────────────

def get_exposure_by_asset_class(positions_df):
    if positions_df.empty:
        return pd.DataFrame(columns=["Asset Class", "Market Value", "Exposure %"])
    out = positions_df.groupby("Asset Class", as_index=False).agg({"Market Value": "sum"})
    total_abs = positions_df["Market Value"].abs().sum()
    out["Exposure %"] = out["Market Value"].abs() / total_abs * 100 if total_abs else 0.0
    return out.sort_values("Market Value", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


# ── Risk flags ───────────────────────────────────────────────

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


# ── Render ───────────────────────────────────────────────────

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
