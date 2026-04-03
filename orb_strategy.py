"""Opening Range Breakout (ORB) signal generator.

Stateless — takes a DataFrame and params dict, returns a signal dict.
No side effects, no I/O. Safe to call from any context.

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
