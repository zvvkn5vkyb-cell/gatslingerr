"""Strategy Manager — load, save, display, and control strategies.

Backed by strategies.json. No execution logic here.
This is the control layer only.
"""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

STRATEGY_FILE = Path(__file__).parent / "strategies.json"

MODES = ["backtest", "sim", "paper", "live"]
STATUSES = ["active", "paused", "inactive"]


# ── I/O ─────────────────────────────────────────────────────

def load_strategies() -> dict:
    if not STRATEGY_FILE.exists():
        return {}
    with open(STRATEGY_FILE, "r") as f:
        return json.load(f)


def save_strategies(strategies: dict) -> None:
    with open(STRATEGY_FILE, "w") as f:
        json.dump(strategies, f, indent=2)


# ── Helpers ──────────────────────────────────────────────────

def get_active_strategies() -> dict:
    """Return only enabled + active strategies. Safe to call anywhere."""
    return {
        name: cfg
        for name, cfg in load_strategies().items()
        if cfg.get("enabled", False) and cfg.get("status") == "active"
    }


def strategies_to_df(strategies: dict) -> pd.DataFrame:
    rows = []
    for name, cfg in strategies.items():
        p = cfg.get("params", {})
        rows.append({
            "Strategy": name,
            "Enabled": cfg.get("enabled", False),
            "Status": cfg.get("status", "inactive"),
            "Mode": cfg.get("mode", "sim"),
            "Asset": cfg.get("asset", ""),
            "Type": cfg.get("strategy_type", ""),
            "Risk %": p.get("risk_per_trade_pct", 0.0),
            "Window": p.get("window_minutes", ""),
            "Max Trades": p.get("max_daily_trades", ""),
        })
    return pd.DataFrame(rows)


# ── Render ───────────────────────────────────────────────────

def render_strategy_manager():
    st.header("Strategy Manager")

    strategies = load_strategies()

    if not strategies:
        st.warning("No strategies found — check strategies.json.")
        return

    # ── Inventory table
    st.subheader("Strategy Inventory")
    st.dataframe(strategies_to_df(strategies), use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Edit panel
    selected = st.selectbox("Select strategy to edit", list(strategies.keys()))
    cfg = strategies[selected]
    p = cfg.get("params", {})

    st.subheader(f"Editing: {selected}")

    col1, col2, col3 = st.columns(3)
    with col1:
        enabled = st.checkbox("Enabled", value=cfg.get("enabled", False))
    with col2:
        status = st.selectbox(
            "Status", STATUSES,
            index=STATUSES.index(cfg.get("status", "inactive"))
        )
    with col3:
        mode = st.selectbox(
            "Mode", MODES,
            index=MODES.index(cfg.get("mode", "sim"))
        )

    col4, col5 = st.columns(2)
    with col4:
        asset = st.text_input("Asset", value=cfg.get("asset", ""))
    with col5:
        strategy_type = st.text_input("Strategy Type", value=cfg.get("strategy_type", ""))

    st.markdown("#### Parameters")
    c1, c2, c3 = st.columns(3)
    with c1:
        window_minutes    = st.number_input("Window (min)",     min_value=1,   value=int(p.get("window_minutes", 15)))
        breakout_buffer   = st.number_input("Breakout Buffer",               value=float(p.get("breakout_buffer", 0.25)))
        atr_filter        = st.checkbox(    "ATR Filter",                    value=bool(p.get("atr_filter", True)))
    with c2:
        atr_min           = st.number_input("ATR Min",                       value=float(p.get("atr_min", 8.0)))
        max_daily_trades  = st.number_input("Max Daily Trades", min_value=1,  value=int(p.get("max_daily_trades", 2)))
        risk_per_trade_pct= st.number_input("Risk per Trade %", min_value=0.0, value=float(p.get("risk_per_trade_pct", 0.5)))
    with c3:
        stop_loss_atr     = st.number_input("Stop Loss ATR",      min_value=0.1, value=float(p.get("stop_loss_atr", 1.0)))
        take_profit_atr   = st.number_input("Take Profit ATR",    min_value=0.1, value=float(p.get("take_profit_atr", 2.0)))
        retest_tolerance  = st.number_input("Retest Tolerance",   min_value=0.1, value=float(p.get("retest_tolerance", 1.0)))
        min_break_strength= st.number_input("Min Break Strength", min_value=0.1, value=float(p.get("min_break_strength", 0.5)))
        max_retest_bars   = st.number_input("Max Retest Bars",   min_value=1,   value=int(p.get("max_retest_bars", 10)))
        session_open      = st.text_input(  "Session Open",                      value=p.get("session_open", "09:30"))
        session_close     = st.text_input(  "Session Close",                     value=p.get("session_close", "16:00"))

    # ── Action buttons
    cs, cp, cd = st.columns(3)

    with cs:
        if st.button("Save Changes", use_container_width=True, type="primary"):
            strategies[selected] = {
                "enabled": enabled,
                "status": status,
                "mode": mode,
                "asset": asset,
                "strategy_type": strategy_type,
                "params": {
                    "window_minutes": window_minutes,
                    "breakout_buffer": breakout_buffer,
                    "atr_filter": atr_filter,
                    "atr_min": atr_min,
                    "max_daily_trades": max_daily_trades,
                    "risk_per_trade_pct": risk_per_trade_pct,
                    "stop_loss_atr": stop_loss_atr,
                    "take_profit_atr": take_profit_atr,
                    "retest_tolerance": retest_tolerance,
                    "min_break_strength": min_break_strength,
                    "max_retest_bars": max_retest_bars,
                    "session_open": session_open,
                    "session_close": session_close,
                },
            }
            save_strategies(strategies)
            st.success(f"{selected} saved.")
            st.rerun()

    with cp:
        if st.button("Pause", use_container_width=True):
            strategies[selected]["status"] = "paused"
            save_strategies(strategies)
            st.warning(f"{selected} paused.")
            st.rerun()

    with cd:
        if st.button("Disable", use_container_width=True):
            strategies[selected]["enabled"] = False
            strategies[selected]["status"] = "inactive"
            save_strategies(strategies)
            st.error(f"{selected} disabled.")
            st.rerun()

    # ── Active strategies summary
    st.markdown("---")
    st.subheader("Currently Active")
    active = get_active_strategies()
    if active:
        rows = []
        for name, cfg in active.items():
            rows.append({
                "Strategy": name,
                "Asset": cfg.get("asset", ""),
                "Mode": cfg.get("mode", ""),
                "Status": cfg.get("status", ""),
                "Window": cfg.get("params", {}).get("window_minutes", ""),
                "Risk %": cfg.get("params", {}).get("risk_per_trade_pct", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No active strategies.")
