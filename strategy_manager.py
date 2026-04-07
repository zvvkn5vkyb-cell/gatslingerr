"""Strategy Manager — load, save, display, and control strategies.

Backed by strategies.json. No execution logic here.
This is the control layer only.
"""

import json
from pathlib import Path

import pandas as pd
from db import q
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
        max_retest_bars    = st.number_input("Max Retest Bars",      min_value=1,   value=int(p.get("max_retest_bars", 10)))
        session_open       = st.text_input(  "Session Open",                       value=p.get("session_open", "09:30"))
        session_close      = st.text_input(  "Session Close",                       value=p.get("session_close", "16:00"))
        min_orb_range_pct  = st.number_input("Min ORB % of ATR",  min_value=0.0,  value=float(p.get("min_orb_range_pct_atr", 0.0)),
                                              help="ORB range must be ≥ this % of ATR. 0 = disabled.")
        min_atr_percentile = st.number_input("Min ATR Percentile", min_value=0.0, max_value=100.0,
                                              value=float(p.get("min_atr_percentile", 0.0)),
                                              help="ATR must be above this rolling percentile. 0 = disabled.")
        volume_filter      = st.checkbox("Volume Filter", value=bool(p.get("volume_filter", False)),
                                         help="Require above-average volume on breakout bar.")
        volume_multiplier  = st.number_input("Volume Multiplier", min_value=0.1, value=float(p.get("volume_multiplier", 1.5)),
                                              help="Breakout bar volume must be ≥ this × average volume.")
        eod_exit_time      = st.text_input("EOD Exit Time (ET)", value=p.get("eod_exit_time", "15:45"),
                                            help="Force-close all positions at this time. Format HH:MM.")
        max_consec_losses  = st.number_input("Max Consecutive Losses", min_value=1, value=int(p.get("max_consecutive_losses", 2)),
                                              help="Pause strategy for the day after this many losses in a row.")
        no_entry_after     = st.text_input("No Entry After (ET)", value=p.get("no_entry_after", "11:30"),
                                            help="Block new trade entries after this time. Format HH:MM. Leave blank to disable.")
        prior_day_confluence = st.checkbox("Prior Day H/L Confluence", value=bool(p.get("prior_day_confluence", False)),
                                            help="LONG requires close above prior day high. SHORT requires close below prior day low.")
        partial_tp         = st.checkbox("Partial TP (50% at 1R)", value=bool(p.get("partial_tp", False)),
                                          help="Take 50% profit at 1R and move stop to breakeven. Ride remaining 50% to full target.")
        partial_tp_r       = st.number_input("Partial TP R multiple", min_value=0.5, max_value=2.0,
                                              value=float(p.get("partial_tp_r", 1.0)),
                                              help="Take partial profit at this R multiple (1.0 = 1R).")

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
                    "min_orb_range_pct_atr": min_orb_range_pct,
                    "min_atr_percentile": min_atr_percentile,
                    "volume_filter": volume_filter,
                    "volume_multiplier": volume_multiplier,
                    "eod_exit_time": eod_exit_time,
                    "max_consecutive_losses": max_consec_losses,
                    "no_entry_after": no_entry_after,
                    "prior_day_confluence": prior_day_confluence,
                    "partial_tp": partial_tp,
                    "partial_tp_r": partial_tp_r,
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

    # ── No-Trade Day Log
    st.markdown("---")
    st.subheader("No-Trade Day Log")
    st.caption("Days where the strategy was blocked. Tracks what would have happened.")

    no_trade = q("""
        SELECT date, strategy, atr_value, orb_range,
               orb_range_pct_atr AS "orb_%_atr",
               atr_percentile AS "atr_pctile",
               skip_reason, would_have_signal,
               session_high, session_low, session_close
        FROM monitoring.no_trade_log
        ORDER BY date DESC
        LIMIT 30
    """)

    if no_trade:
        df = pd.DataFrame(no_trade)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Summary stats
        total = len(df)
        atr_blocked  = len(df[df["skip_reason"].str.contains("volatility", na=False)])
        orb_blocked  = len(df[df["skip_reason"].str.contains("tight", na=False)])
        regime_blocked = len(df[df["skip_reason"].str.contains("regime", na=False)])
        would_long   = len(df[df["would_have_signal"] == "LONG"])
        would_short  = len(df[df["would_have_signal"] == "SHORT"])

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total Skipped", total)
        c2.metric("ATR Filter", atr_blocked)
        c3.metric("ORB Range Filter", orb_blocked)
        c4.metric("Vol Regime Filter", regime_blocked)
        c5.metric("Would've Been Long/Short", f"{would_long}/{would_short}")
    else:
        st.info("No skipped days recorded yet. Will populate automatically during market hours.")
