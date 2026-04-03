"""Opening Range Breakout (ORB) signal generator + retest state machine.

generate_orb_signal  — stateless, returns LONG/SHORT/FLAT per bar snapshot.
TradeState           — tracks the retest flow across Streamlit reruns.
update_retest_state  — advances the state machine one tick.
check_exit           — evaluates stop loss and profit target on IN_POSITION.

Expected DataFrame columns: open, high, low, close
Index: any (positional slice used)
"""


def generate_orb_signal(data, params):
    window = int(params.get("window_minutes", 15))
    breakout_buffer = float(params.get("breakout_buffer", 0.0))

    if data is None or len(data) <= window:
        return {"signal": "FLAT", "reason": "Not enough data"}

    opening_range = data.iloc[:window]
    orb_high = float(opening_range["high"].max())
    orb_low = float(opening_range["low"].min())
    current_close = float(data.iloc[-1]["close"])

    base = {
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_range": round(orb_high - orb_low, 4),
        "current_close": current_close,
    }

    if current_close > orb_high + breakout_buffer:
        return {"signal": "LONG", "reason": "Close above ORB high + buffer", **base}

    if current_close < orb_low - breakout_buffer:
        return {"signal": "SHORT", "reason": "Close below ORB low - buffer", **base}

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


def update_retest_state(state, signal, price, orb_high, orb_low, params,
                        bar_count=0):
    """Advance the retest state machine one tick.

    bar_count: current number of RTH bars (len(rth) from caller).
    Used to enforce max_retest_bars timeout.
    Returns the same TradeState object (mutated in place).
    """
    buffer = float(params.get("breakout_buffer", 0.0))
    retest_tolerance = float(params.get("retest_tolerance", 1.0))
    min_break_strength = float(params.get("min_break_strength", 0.5))
    max_retest_bars = int(params.get("max_retest_bars", 10))

    # ── Timeout: applies to WAITING_RETEST and READY_TO_ENTER
    if state.state in ("WAITING_RETEST", "READY_TO_ENTER") and state.retest_start_index is not None:
        bars_since = bar_count - state.retest_start_index
        if bars_since > max_retest_bars:
            state.reset()
            return state

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
        # Require impulse strength — price must clear breakout by min_break_strength
        if price > state.breakout_level + min_break_strength:
            atr = float(params.get("_atr_value", 2.0))  # injected by caller
            stop_mult = float(params.get("stop_loss_atr", 1.0))
            tp_mult = float(params.get("take_profit_atr", 2.0))
            state.entry_price = price
            state.stop_price = price - (atr * stop_mult)
            state.target_price = price + (atr * tp_mult)
            state.state = "IN_POSITION"
            state.has_traded = True
            state.retest_confirmed = False
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
        # Require impulse strength — price must clear breakout by min_break_strength
        if price < state.breakout_level - min_break_strength:
            atr = float(params.get("_atr_value", 2.0))  # injected by caller
            stop_mult = float(params.get("stop_loss_atr", 1.0))
            tp_mult = float(params.get("take_profit_atr", 2.0))
            state.entry_price = price
            state.stop_price = price + (atr * stop_mult)
            state.target_price = price - (atr * tp_mult)
            state.state = "IN_POSITION"
            state.has_traded = True
            state.retest_confirmed = False
        elif price > state.breakout_level + buffer:
            state.reset()
        return state

    return state


def check_exit(state, price):
    """Check stop loss and profit target for an IN_POSITION state.
    Returns (exit_triggered: bool, reason: str, pnl: float)."""
    if state.state != "IN_POSITION" or state.entry_price is None:
        return False, "", 0.0

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
