Generate visual charts from GatSlinger trading and fund accounting data.

Run the graphify script with an optional chart type argument:

```bash
python graphify.py [--type TYPE] [--fund FUND_NAME] [--out OUTPUT_PATH]
```

**Available chart types** (default: `all`):
- `nav` — NAV per unit history line chart for a fund
- `pnl` — Cumulative P&L waterfall / bar chart
- `exposure` — Portfolio exposure by asset class (pie + bar)
- `trades` — Trade log win/loss scatter and cumulative P&L curve
- `all` — Generate all of the above

**Examples:**
```bash
# Generate all charts and open in browser
python graphify.py

# NAV history for a specific fund
python graphify.py --type nav --fund "Alpha Fund"

# Trade performance charts saved to a file
python graphify.py --type trades --out charts/trades.html

# Exposure breakdown
python graphify.py --type exposure
```

**What it does:**
1. Connects to the local PostgreSQL database (financial-db on localhost:5432)
2. Queries the relevant monitoring views (nav_history, position_summary, fund_overview, etc.)
3. Falls back to session trade log data from `st.session_state` if the DB is unavailable
4. Generates interactive Plotly charts and opens them in the default browser (or saves to `--out`)

**Output:**
- Opens an HTML file in your default browser with interactive Plotly charts
- Or writes to the path specified by `--out`
- If the DB is offline, shows sample/empty charts with a warning

Run `python graphify.py --help` to see all options.
