"""Opening Range Breakout (ORB) signal generator + retest state machine.

generate_orb_signal  — stateless, returns LONG/SHORT/FLAT per bar snapshot.
TradeState           — tracks the retest flow across Streamlit reruns.
update_retest_state  — advances the state machine one tick.
check_exit           — evaluates stop loss, profit target, partial TP, and time exit.

Expected DataFrame columns: open, high, low, close
Index: any (positional slice used)
"""


def compute_vwap(data):
    """Compute VWAP from bar data. Requires 'volume' column."""
    if "volume" not in data.columns or data["volume"].sum() == 0:
        return None
    typical = (data["high"] + data["low"] + data["close"]) / 3
    return float((typical * data["volume"]).sum() / data["volume"].sum())


def compute_atr(data, period=14):
    """Compute ATR over the last `period` bars."""
    tr = abs(data["high"] - data["low"])
    return float(tr.rolling(period).mean().iloc[-1])


def compute_atr_percentile(data, atr_value, lookback=50):
    """Return the percentile rank of current ATR vs the last `lookback` bars.
    0 = lowest volatility seen, 100 = highest."""
    if len(data) < lookback:
        return None
    tr = abs(data["high"] - data["low"])
    rolling_atrs = tr.rolling(14).mean().dropna()
    if len(rolling_atrs) < 2:
        return None
    pct = float((rolling_atrs < atr_value).sum() / len(rolling_atrs) * 100)
    return round(pct, 1)


def generate_orb_signal(data, params):
    window = int(params.get("window_minutes", 15))
    breakout_buffer = float(params.get("breakout_buffer", 0.0))

    if data is None or len(data) <= window:
        return {"signal": "FLAT", "reason": "Not enough data"}

    opening_range = data.iloc[:window]
    orb_high = float(opening_range["high"].max())
    orb_low = float(opening_range["low"].min())
    orb_range = round(orb_high - orb_low, 4)
    current_close = float(data.iloc[-1]["close"])

    # ── Compute ATR and volatility regime
    atr_value = compute_atr(data)
    atr_percentile = compute_atr_percentile(data, atr_value)

    # ── ORB range filter: range must be >= min_orb_range_pct_atr % of ATR
    min_orb_pct = float(params.get("min_orb_range_pct_atr", 0.0))
    orb_range_pct_atr = round((orb_range / atr_value * 100), 1) if atr_value > 0 else 0

    # ── Volatility regime filter: ATR must be above min percentile
    min_atr_percentile = float(params.get("min_atr_percentile", 0.0))

    # ── VWAP
    vwap = compute_vwap(data)

    # ── Volume confirmation: current bar volume vs average
    use_volume_filter = bool(params.get("volume_filter", False))
    volume_multiplier = float(params.get("volume_multiplier", 1.5))
    current_volume    = float(data["volume"].iloc[-1]) if "volume" in data.columns else 0
    avg_volume        = float(data["volume"].mean()) if "volume" in data.columns else 0
    volume_ok         = (current_volume >= avg_volume * volume_multiplier) if use_volume_filter else True

    # ── Prior day high/low confluence (injected by caller via params["_prior_day_high"])
    use_prior_confluence = bool(params.get("prior_day_confluence", False))
    prior_day_high = params.get("_prior_day_high")
    prior_day_low  = params.get("_prior_day_low")

    base = {
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_range": orb_range,
        "orb_range_pct_atr": orb_range_pct_atr,
        "atr_value": round(atr_value, 4),
        "atr_percentile": atr_percentile,
        "current_close": current_close,
        "vwap": round(vwap, 4) if vwap else None,
        "current_volume": current_volume,
        "avg_volume": round(avg_volume, 0),
        "volume_ok": volume_ok,
        "prior_day_high": prior_day_high,
        "prior_day_low": prior_day_low,
    }

    # ── ORB range too tight vs ATR
    if min_orb_pct > 0 and orb_range_pct_atr < min_orb_pct:
        return {
            "signal": "FLAT",
            "reason": f"ORB range too tight ({orb_range_pct_atr:.1f}% of ATR < {min_orb_pct:.1f}% required)",
            "skip_reason": "orb_range_filter",
            **base,
        }

    # ── Volatility regime too low
    if min_atr_percentile > 0 and atr_percentile is not None and atr_percentile < min_atr_percentile:
        return {
            "signal": "FLAT",
            "reason": f"Low vol regime (ATR pctile {atr_percentile:.1f} < {min_atr_percentile:.1f} required)",
            "skip_reason": "vol_regime_filter",
            **base,
        }

    # ── LONG: price above ORB high + buffer
    if current_close > orb_high + breakout_buffer:
        if vwap and current_close < vwap:
            return {"signal": "FLAT", "reason": "LONG blocked: price below VWAP",
                    "skip_reason": "vwap_filter", **base}
        if not volume_ok:
            return {"signal": "FLAT", "reason": f"LONG blocked: low volume ({current_volume:.0f} < {avg_volume * volume_multiplier:.0f})",
                    "skip_reason": "volume_filter", **base}
        # ── Prior day high confluence: LONG must clear prior day high
        if use_prior_confluence and prior_day_high and current_close < prior_day_high:
            return {"signal": "FLAT",
                    "reason": f"LONG blocked: below prior day high ({prior_day_high:.2f})",
                    "skip_reason": "prior_day_filter", **base}
        return {"signal": "LONG", "reason": "Close above ORB high + buffer | VWAP ✓ | Vol ✓", **base}

    # ── SHORT: price below ORB low - buffer
    if current_close < orb_low - breakout_buffer:
        if vwap and current_close > vwap:
            return {"signal": "FLAT", "reason": "SHORT blocked: price above VWAP",
                    "skip_reason": "vwap_filter", **base}
        if not volume_ok:
            return {"signal": "FLAT", "reason": f"SHORT blocked: low volume ({current_volume:.0f} < {avg_volume * volume_multiplier:.0f})",
                    "skip_reason": "volume_filter", **base}
        # ── Prior day low confluence: SHORT must break prior day low
        if use_prior_confluence and prior_day_low and current_close > prior_day_low:
            return {"signal": "FLAT",
                    "reason": f"SHORT blocked: above prior day low ({prior_day_low:.2f})",
                    "skip_reason": "prior_day_filter", **base}
        return {"signal": "SHORT", "reason": "Close below ORB low - buffer | VWAP ✓ | Vol ✓", **base}

    return {"signal": "FLAT", "reason": "Inside opening range", **base}


# ── Retest state machine ────────────────────────────────────

class TradeState:
    """Persisted in st.session_state per strategy. Tracks:
    WAITING_BREAKOUT → WAITING_RETEST → READY_TO_ENTER → IN_POSITION
    """

    def __init__(self):
        self.state = "WAITING_BREAKOUT"
        self.breakout_level = None
        self.direction = None
        self.entry_price = None
        self.stop_price = None
        self.target_price = None
        self.retest_confirmed = False
        self.has_traded = False
        self.retest_start_index = None
        self.exit_reason = None
        self.pnl = None
        # Circuit breaker
        self.consecutive_losses = 0
        self.circuit_broken = False
        self.daily_trades = 0
        # Partial TP
        self.partial_taken = False
        self.partial_target = None
        self.use_partial_tp = False

    def reset(self):
        self.state = "WAITING_BREAKOUT"
        self.breakout_level = None
        self.direction = None
        self.entry_price = None
        self.stop_price = None
        self.target_price = None
        self.retest_confirmed = False
        self.has_traded = False
        self.retest_start_index = None
        self.exit_reason = None
        self.pnl = None
        self.partial_taken = False
        self.partial_target = None
        self.use_partial_tp = False
        # Do NOT reset consecutive_losses, circuit_broken, or daily_trades on position reset

    def reset_daily(self):
        """Call at start of each new trading day."""
        self.reset()
        self.consecutive_losses = 0
        self.circuit_broken = False
        self.daily_trades = 0


def update_retest_state(state, signal, price, orb_high, orb_low, params,
                        bar_count=0, current_time=None):
    """Advance the retest state machine one tick.

    bar_count:    current number of RTH bars (len(rth) from caller).
    current_time: datetime.time or None — used for no_entry_after cutoff.
    Returns the same TradeState object (mutated in place).
    """
    buffer = float(params.get("breakout_buffer", 0.0))
    retest_tolerance = float(params.get("retest_tolerance", 1.0))
    min_break_strength = float(params.get("min_break_strength", 0.5))
    max_retest_bars = int(params.get("max_retest_bars", 10))

    # ── Clear exit signal from previous tick
    state.exit_reason = None
    state.pnl = None

    # ── Exit evaluation when IN_POSITION
    if state.state == "IN_POSITION" and state.entry_price is not None:
        exited, reason, pnl = check_exit(state, price)
        if exited:
            _reason = reason
            _pnl = round(pnl, 4)
            state.reset()
            # Restore exit info AFTER reset so caller can read it
            state.exit_reason = _reason
            state.pnl = _pnl
            return state

    # ── Timeout: applies to WAITING_RETEST and READY_TO_ENTER
    if state.state in ("WAITING_RETEST", "READY_TO_ENTER") and state.retest_start_index is not None:
        bars_since = bar_count - state.retest_start_index
        if bars_since > max_retest_bars:
            state.reset()
            return state

    # ── No-entry-after: don't start new trades past this time cutoff
    no_entry_after_str = params.get("no_entry_after", "")
    if (no_entry_after_str
            and current_time is not None
            and state.state == "WAITING_BREAKOUT"):
        from datetime import time as dtime
        try:
            h, m = map(int, no_entry_after_str.split(":"))
            if current_time >= dtime(h, m):
                return state  # Past cutoff — don't start new trades
        except (ValueError, AttributeError):
            pass

    # ── LONG flow
    if state.state == "WAITING_BREAKOUT" and signal == "LONG":
        state.state = "WAITING_RETEST"
        state.breakout_level = orb_high
        state.direction = "LONG"
        state.retest_start_index = bar_count
        return state

    if state.state == "WAITING_RETEST" and state.direction == "LONG":
        if abs(price - state.breakout_level) <= retest_tolerance:
            state.state = "READY_TO_ENTER"
            state.retest_confirmed = True
        elif price < state.breakout_level - buffer:
            state.reset()
        return state

    if state.state == "READY_TO_ENTER" and state.direction == "LONG":
        if price > state.breakout_level + min_break_strength:
            atr = float(params.get("_atr_value", 2.0))
            stop_mult = float(params.get("stop_loss_atr", 1.0))
            tp_mult = float(params.get("take_profit_atr", 2.0))
            state.entry_price = price
            state.stop_price = price - (atr * stop_mult)
            state.target_price = price + (atr * tp_mult)
            state.state = "IN_POSITION"
            state.has_traded = True
            state.retest_confirmed = False
            # ── Partial TP setup
            use_partial = bool(params.get("partial_tp", False))
            state.use_partial_tp = use_partial
            if use_partial:
                stop_dist = abs(price - state.stop_price)
                state.partial_target = price + stop_dist  # 1R profit target
        elif price < state.breakout_level - buffer:
            state.reset()
        return state

    # ── SHORT flow
    if state.state == "WAITING_BREAKOUT" and signal == "SHORT":
        state.state = "WAITING_RETEST"
        state.breakout_level = orb_low
        state.direction = "SHORT"
        state.retest_start_index = bar_count
        return state

    if state.state == "WAITING_RETEST" and state.direction == "SHORT":
        if abs(price - state.breakout_level) <= retest_tolerance:
            state.state = "READY_TO_ENTER"
            state.retest_confirmed = True
        elif price > state.breakout_level + buffer:
            state.reset()
        return state

    if state.state == "READY_TO_ENTER" and state.direction == "SHORT":
        if price < state.breakout_level - min_break_strength:
            atr = float(params.get("_atr_value", 2.0))
            stop_mult = float(params.get("stop_loss_atr", 1.0))
            tp_mult = float(params.get("take_profit_atr", 2.0))
            state.entry_price = price
            state.stop_price = price + (atr * stop_mult)
            state.target_price = price - (atr * tp_mult)
            state.state = "IN_POSITION"
            state.has_traded = True
            state.retest_confirmed = False
            # ── Partial TP setup
            use_partial = bool(params.get("partial_tp", False))
            state.use_partial_tp = use_partial
            if use_partial:
                stop_dist = abs(price - state.stop_price)
                state.partial_target = price - stop_dist  # 1R profit target
        elif price > state.breakout_level + buffer:
            state.reset()
        return state

    return state


def check_exit(state, price, current_time=None, eod_exit_time="15:45"):
    """Check stop loss, profit target, partial TP, and time-of-day exit.

    Returns (exit_triggered: bool, reason: str, pnl: float).

    Special case — partial TP:
      Returns (False, "PARTIAL_TP", half_pnl) when partial is taken.
      State is mutated: partial_taken=True, stop moved to breakeven.
      Caller should log the partial but NOT call reset().

    current_time: datetime.time or None. If provided, forces exit at eod_exit_time.
    eod_exit_time: string "HH:MM" in ET. Default 15:45.
    """
    if state.state != "IN_POSITION" or state.entry_price is None:
        return False, "", 0.0

    # ── Partial take-profit check (before stop/target)
    if state.use_partial_tp and not state.partial_taken and state.partial_target is not None:
        if (state.direction == "LONG" and price >= state.partial_target) or \
           (state.direction == "SHORT" and price <= state.partial_target):
            stop_dist = abs(state.entry_price - state.stop_price)
            state.partial_taken = True
            state.stop_price = state.entry_price  # move stop to breakeven
            return False, "PARTIAL_TP", round(stop_dist * 0.5, 4)  # half-position profit

    # ── Time-of-day forced exit
    if current_time is not None:
        from datetime import time as dtime
        h, m = map(int, eod_exit_time.split(":"))
        eod = dtime(h, m)
        if current_time >= eod:
            if state.direction == "LONG":
                pnl = price - state.entry_price
            else:
                pnl = state.entry_price - price
            return True, "EOD_EXIT", round(pnl, 4)

    if state.direction == "LONG":
        pnl = price - state.entry_price
        if state.stop_price is not None and price <= state.stop_price:
            return True, "STOP", state.stop_price - state.entry_price
        if state.target_price is not None and price >= state.target_price:
            return True, "TARGET", state.target_price - state.entry_price
        return False, "", pnl

    if state.direction == "SHORT":
        pnl = state.entry_price - price
        if state.stop_price is not None and price >= state.stop_price:
            return True, "STOP", state.entry_price - state.stop_price
        if state.target_price is not None and price <= state.target_price:
            return True, "TARGET", state.entry_price - state.target_price
        return False, "", pnl

    return False, "", 0.0
