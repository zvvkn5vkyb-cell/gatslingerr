"""
graphify.py — Generate interactive Plotly charts from GatSlinger trading data.

Usage:
    python graphify.py [--type TYPE] [--fund FUND_NAME] [--out OUTPUT_PATH]

Chart types: nav, pnl, exposure, trades, all (default)
"""

import argparse
import sys
import os
import webbrowser
import tempfile
from datetime import datetime

try:
    import psycopg2
except ImportError:
    psycopg2 = None

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly.express as px
except ImportError:
    print("Error: plotly is required. Install it with: pip install plotly")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("Error: pandas is required. Install it with: pip install pandas")
    sys.exit(1)


# ── DB helpers ────────────────────────────────────────────────

def get_conn():
    if psycopg2 is None:
        return None
    try:
        conn = psycopg2.connect(
            dbname="financial_db", user="admin", password="admin",
            host="localhost", port=5432,
            connect_timeout=3,
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


def query(conn, sql, params=None):
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []


# ── Chart builders ────────────────────────────────────────────

def build_nav_chart(conn, fund_name=None):
    """Line chart: NAV per unit history for one or all funds."""
    if fund_name:
        rows = query(conn,
            "SELECT timestamp::date AS date, nav_per_unit, fund_name "
            "FROM nav_history WHERE fund_name = %s ORDER BY timestamp",
            (fund_name,))
    else:
        rows = query(conn,
            "SELECT timestamp::date AS date, nav_per_unit, fund_name "
            "FROM nav_history ORDER BY fund_name, timestamp")

    fig = go.Figure()

    if rows:
        df = pd.DataFrame(rows)
        df["nav_per_unit"] = df["nav_per_unit"].astype(float)
        for fname, grp in df.groupby("fund_name"):
            grp = grp.sort_values("date")
            fig.add_trace(go.Scatter(
                x=grp["date"], y=grp["nav_per_unit"],
                mode="lines+markers", name=fname,
                line=dict(width=2), marker=dict(size=4),
            ))
    else:
        # Placeholder when DB is offline
        fig.add_annotation(
            text="No NAV data available (DB offline or empty)",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=16, color="#94a3b8"),
        )

    title = f"NAV History — {fund_name}" if fund_name else "NAV History (All Funds)"
    fig.update_layout(
        title=title,
        xaxis_title="Date", yaxis_title="NAV per Unit ($)",
        template="plotly_dark", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def build_pnl_chart(conn, fund_name=None):
    """Waterfall + cumulative line: P&L components from NAV bridge."""
    if fund_name:
        rows = query(conn,
            "SELECT date, pnl, starting_nav, ending_nav, fund_name "
            "FROM monitoring.nav_bridge_waterfall WHERE fund_name = %s ORDER BY date",
            (fund_name,))
    else:
        rows = query(conn,
            "SELECT date, pnl, starting_nav, ending_nav, fund_name "
            "FROM monitoring.nav_bridge_waterfall ORDER BY fund_name, date")

    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Daily P&L", "Cumulative P&L"),
        row_heights=[0.5, 0.5],
        shared_xaxes=True,
    )

    if rows:
        df = pd.DataFrame(rows)
        df["pnl"] = df["pnl"].astype(float)
        df["date"] = pd.to_datetime(df["date"])

        for fname, grp in df.groupby("fund_name"):
            grp = grp.sort_values("date")
            colors = ["#22c55e" if v >= 0 else "#ef4444" for v in grp["pnl"]]
            fig.add_trace(go.Bar(
                x=grp["date"], y=grp["pnl"], name=f"{fname} Daily",
                marker_color=colors, showlegend=True,
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=grp["date"], y=grp["pnl"].cumsum(), name=f"{fname} Cumulative",
                mode="lines", line=dict(width=2),
            ), row=2, col=1)
    else:
        for row in (1, 2):
            fig.add_annotation(
                text="No P&L data available (DB offline or empty)",
                xref="paper", yref="paper", x=0.5, y=(0.75 if row == 1 else 0.25),
                showarrow=False, font=dict(size=14, color="#94a3b8"),
            )

    fig.update_layout(
        title="P&L Analysis",
        template="plotly_dark", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(title_text="P&L ($)", row=1, col=1)
    fig.update_yaxes(title_text="Cumulative P&L ($)", row=2, col=1)
    return fig


def build_exposure_chart(conn, fund_name=None):
    """Pie + horizontal bar: portfolio exposure by asset class."""
    if fund_name:
        rows = query(conn,
            "SELECT asset_class, SUM(ABS(market_value)) AS abs_value "
            "FROM monitoring.position_summary "
            "WHERE fund_name = %s GROUP BY asset_class ORDER BY abs_value DESC",
            (fund_name,))
    else:
        rows = query(conn,
            "SELECT asset_class, SUM(ABS(market_value)) AS abs_value "
            "FROM monitoring.position_summary "
            "GROUP BY asset_class ORDER BY abs_value DESC")

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("Exposure by Asset Class (%)", "Gross Exposure ($)"),
        specs=[[{"type": "pie"}, {"type": "xy"}]],
    )

    if rows:
        df = pd.DataFrame(rows)
        df["abs_value"] = df["abs_value"].astype(float)

        fig.add_trace(go.Pie(
            labels=df["asset_class"], values=df["abs_value"],
            hole=0.4, name="Exposure",
            textinfo="label+percent",
        ), row=1, col=1)

        fig.add_trace(go.Bar(
            y=df["asset_class"], x=df["abs_value"],
            orientation="h", name="Gross ($)",
            marker_color="#3b82f6",
        ), row=1, col=2)
    else:
        fig.add_annotation(
            text="No position data available (DB offline or empty)",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=14, color="#94a3b8"),
        )

    title = f"Exposure — {fund_name}" if fund_name else "Portfolio Exposure"
    fig.update_layout(
        title=title, template="plotly_dark",
        showlegend=False,
    )
    return fig


def build_trades_chart(trade_rows=None):
    """Scatter + cumulative line: trade log performance."""
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=("Trade P&L (pts)", "Cumulative P&L (pts)"),
        row_heights=[0.5, 0.5],
        shared_xaxes=True,
    )

    if trade_rows:
        df = pd.DataFrame(trade_rows)
        df["pnl"] = df["pnl"].astype(float)
        df.index = range(1, len(df) + 1)

        colors = ["#22c55e" if v >= 0 else "#ef4444" for v in df["pnl"]]
        fig.add_trace(go.Bar(
            x=df.index, y=df["pnl"],
            marker_color=colors, name="Trade P&L",
            text=[f"{v:+.2f}" for v in df["pnl"]],
            textposition="outside",
        ), row=1, col=1)

        fig.add_trace(go.Scatter(
            x=df.index, y=df["pnl"].cumsum(),
            mode="lines+markers", name="Cumulative",
            line=dict(width=2, color="#3b82f6"),
            marker=dict(size=6),
        ), row=2, col=1)

        wins = (df["pnl"] > 0).sum()
        losses = (df["pnl"] <= 0).sum()
        win_rate = wins / len(df) * 100 if len(df) > 0 else 0
        total = df["pnl"].sum()
        subtitle = (f"  Trades: {len(df)} | Win rate: {win_rate:.0f}% "
                    f"| W/L: {wins}/{losses} | Total: {total:+.2f} pts")
    else:
        for row in (1, 2):
            fig.add_annotation(
                text="No trade log data available",
                xref="paper", yref="paper", x=0.5, y=(0.75 if row == 1 else 0.25),
                showarrow=False, font=dict(size=14, color="#94a3b8"),
            )
        subtitle = ""

    fig.update_layout(
        title="Trade Log Performance" + (subtitle if trade_rows else ""),
        template="plotly_dark", hovermode="x unified",
        showlegend=True,
    )
    fig.update_xaxes(title_text="Trade #", row=2, col=1)
    fig.update_yaxes(title_text="P&L (pts)", row=1, col=1)
    fig.update_yaxes(title_text="Cumulative (pts)", row=2, col=1)
    return fig


# ── HTML assembly ─────────────────────────────────────────────

def render_html(figures, title="GatSlinger Charts"):
    """Combine multiple Plotly figures into a single HTML page."""
    divs = []
    for fig in figures:
        divs.append(fig.to_html(full_html=False, include_plotlyjs="cdn" if not divs else False))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join(f'<div style="margin-bottom:32px">{d}</div>' for d in divs)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{
      background: #0f172a;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      margin: 0;
      padding: 24px;
    }}
    h1 {{ color: #f8fafc; margin-bottom: 4px; }}
    .ts {{ color: #64748b; font-size: 13px; margin-bottom: 32px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="ts">Generated: {timestamp}</div>
  {body}
</body>
</html>"""


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate interactive charts from GatSlinger trading data."
    )
    parser.add_argument(
        "--type", default="all",
        choices=["nav", "pnl", "exposure", "trades", "all"],
        help="Chart type to generate (default: all)",
    )
    parser.add_argument(
        "--fund", default=None,
        help="Filter charts to a specific fund name",
    )
    parser.add_argument(
        "--out", default=None,
        help="Save output HTML to this path instead of opening in browser",
    )
    args = parser.parse_args()

    conn = get_conn()
    if conn is None:
        print("Warning: PostgreSQL unavailable — charts will show empty data.")
    else:
        print("Connected to financial_db.")

    figures = []
    chart_type = args.type

    if chart_type in ("nav", "all"):
        print("Building NAV history chart...")
        figures.append(build_nav_chart(conn, fund_name=args.fund))

    if chart_type in ("pnl", "all"):
        print("Building P&L chart...")
        figures.append(build_pnl_chart(conn, fund_name=args.fund))

    if chart_type in ("exposure", "all"):
        print("Building exposure chart...")
        figures.append(build_exposure_chart(conn, fund_name=args.fund))

    if chart_type in ("trades", "all"):
        print("Building trade log chart...")
        figures.append(build_trades_chart())  # no DB — reads session state at runtime

    if conn:
        conn.close()

    html = render_html(figures, title="GatSlinger — Graphify")

    if args.out:
        out_path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Saved: {out_path}")
    else:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            tmp_path = f.name
        print(f"Opening browser: {tmp_path}")
        webbrowser.open(f"file://{tmp_path}")


if __name__ == "__main__":
    main()
