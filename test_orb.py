import pandas as pd

from orb_strategy import TradeState, update_retest_state


def make_sequence(prices):
    rows = []
    for p in prices:
        rows.append({
            "open": p,
            "high": p + 0.25,
            "low": p - 0.25,
            "close": p
        })
    return pd.DataFrame(rows)


def compute_atr_like(df, window=14):
    tr = (df["high"] - df["low"]).rolling(window).mean()
    if tr.isna().all():
        return 1.0
    return float(tr.iloc[-1]) if pd.notna(tr.iloc[-1]) else 1.0


def run_mock_scenario(name, prices, orb_high, orb_low, params):
    print(f"\n=== {name} ===")

    state = TradeState()
    trade_log = []
    df = make_sequence(prices)

    for i in range(len(df)):
        current = df.iloc[: i + 1].copy()
        price = float(current.iloc[-1]["close"])
        atr_value = compute_atr_like(current)

        signal = "FLAT"
        if price > orb_high + params["breakout_buffer"]:
            signal = "LONG"
        elif price < orb_low - params["breakout_buffer"]:
            signal = "SHORT"

        state = update_retest_state(
            state=state,
            signal=signal,
            price=price,
            orb_high=orb_high,
            orb_low=orb_low,
            params={**params, "_atr_value": atr_value},
            bar_count=len(current)
        )

        print(
            f"bar={i+1:02d} "
            f"price={price:.2f} "
            f"signal={signal:<5} "
            f"state={state.state:<18} "
            f"dir={str(state.direction):<5} "
            f"breakout={state.breakout_level} "
            f"entry={state.entry_price} "
            f"stop={getattr(state, 'stop_price', None)} "
            f"target={getattr(state, 'target_price', None)} "
            f"bars_since={getattr(state, 'bars_since_breakout', None)}"
        )

        if getattr(state, "exit_reason", None):
            trade_log.append({
                "scenario": name,
                "direction": state.direction,
                "entry": state.entry_price,
                "exit": price,
                "stop": getattr(state, "stop_price", None),
                "target": getattr(state, "target_price", None),
                "pnl": getattr(state, "pnl", None),
                "reason": state.exit_reason
            })
            print(f"EXIT -> {state.exit_reason} | pnl={getattr(state, 'pnl', None)}")

    if trade_log:
        print("\nTrade Log:")
        print(pd.DataFrame(trade_log).to_string(index=False))
    else:
        print("\nNo completed trades.")


if __name__ == "__main__":
    params = {
        "breakout_buffer": 0.25,
        "retest_tolerance": 1.0,
        "min_break_strength": 0.75,
        "max_retest_bars": 10,
        "atr_min": 0.0,
        "atr_multiplier": 1.2,
        "orb_stop_fraction": 0.5,
        "rr_multiple": 2.0
    }

    orb_high = 100.0
    orb_low = 95.0

    scenarios = {
        "LONG_WINNER": [
            99.0, 99.4, 99.8,
            100.4, 101.2,
            100.3, 100.1,
            101.1, 101.4, 102.0, 103.0, 104.0, 105.0, 106.0
        ],
        "LONG_FAILS_RETEST": [
            99.0, 99.7, 100.5, 101.1,
            99.4, 99.2, 99.0, 98.8
        ],
        "LONG_TIMEOUT": [
            99.0, 100.5, 101.0,
            100.8, 100.7, 100.9, 100.8, 100.7, 100.6,
            100.7, 100.8, 100.7, 100.8, 100.7, 100.8
        ],
        "SHORT_WINNER": [
            96.2, 95.8, 95.3,
            94.6, 93.9,
            94.8, 94.9,
            94.0, 93.4, 92.8, 92.0, 91.0
        ],
        "SHORT_STOPPED": [
            96.0, 95.5, 94.6, 93.9,
            94.7, 94.9,
            94.0, 95.4, 96.1, 97.0
        ]
    }

    for scenario_name, prices in scenarios.items():
        run_mock_scenario(
            name=scenario_name,
            prices=prices,
            orb_high=orb_high,
            orb_low=orb_low,
            params=params
        )
