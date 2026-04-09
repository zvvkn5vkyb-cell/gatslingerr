"""Fund Dashboard API - FastAPI server for monitoring dashboard"""
import asyncio
import json
import os
from datetime import date, datetime
from decimal import Decimal

import asyncpg
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pathlib import Path

DATABASE_URL = "postgresql://admin:admin@localhost:5432/financial_db"

app = FastAPI(title="GatSlinger Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pro.openbb.co",
        "https://openbb.co",
        "http://localhost:1420",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pool = None


def serialize(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def row_to_dict(row):
    return {k: serialize(v) for k, v in dict(row).items()}


async def query(sql, *args):
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [row_to_dict(r) for r in rows]


@app.on_event("startup")
async def startup():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)


@app.on_event("shutdown")
async def shutdown():
    await pool.close()


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "dashboard.html")


@app.get("/api/health")
async def health():
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "db": str(e)}


@app.get("/api/fund-overview")
async def fund_overview():
    return await query("SELECT * FROM monitoring.fund_overview")


@app.get("/api/nav-bridge/{fund_name}")
async def nav_bridge(fund_name: str):
    return await query(
        "SELECT * FROM monitoring.nav_bridge_waterfall WHERE fund_name = $1 ORDER BY date DESC LIMIT 5",
        fund_name,
    )


@app.get("/api/nav-history/{fund_name}")
async def nav_history(fund_name: str, days: int = Query(default=90)):
    return await query(
        "SELECT timestamp::date AS date, nav_per_unit FROM nav_history WHERE fund_name = $1 ORDER BY timestamp DESC LIMIT $2",
        fund_name, days,
    )


@app.get("/api/positions/{fund_name}")
async def positions(fund_name: str):
    return await query(
        "SELECT * FROM monitoring.position_summary WHERE fund_name = $1 ORDER BY market_value DESC",
        fund_name,
    )


@app.get("/api/pnl/{fund_name}")
async def pnl(fund_name: str):
    summary = await query(
        "SELECT * FROM monitoring.daily_pnl_summary WHERE fund_name = $1", fund_name
    )
    positions = await query(
        "SELECT * FROM monitoring.daily_pnl_by_position WHERE fund_name = $1 ORDER BY ABS(daily_pnl) DESC",
        fund_name,
    )
    return {"summary": summary[0] if summary else {}, "positions": positions}


@app.get("/api/fees/{fund_name}")
async def fees(fund_name: str):
    return await query(
        "SELECT * FROM monitoring.fee_summary WHERE fund_name = $1 ORDER BY date DESC", fund_name
    )


@app.get("/api/investors/{fund_name}")
async def investors(fund_name: str):
    return await query(
        "SELECT * FROM monitoring.investor_allocation WHERE fund_name = $1 ORDER BY allocation_value DESC",
        fund_name,
    )


@app.get("/api/alerts")
async def alerts():
    return await query("SELECT * FROM monitoring.active_alerts ORDER BY severity DESC, alert_date DESC")


@app.get("/api/returns/{fund_name}")
async def returns(fund_name: str):
    rows = await query(
        "SELECT * FROM monitoring.rolling_returns WHERE fund_name = $1", fund_name
    )
    return rows[0] if rows else {}


@app.get("/api/cohort-fairness")
async def cohort_fairness():
    return await query(
        "SELECT * FROM cohort_fairness ORDER BY return_dispersion DESC"
    )


@app.get("/api/cohort-fairness/{fund_name}")
async def cohort_fairness_fund(fund_name: str):
    return await query(
        "SELECT * FROM cohort_fairness WHERE fund_name = $1 ORDER BY return_dispersion DESC",
        fund_name,
    )


@app.get("/api/pricing-dispersion/{fund_name}")
async def pricing_dispersion(fund_name: str):
    return await query(
        "SELECT * FROM pricing_dispersion WHERE fund_name = $1 ORDER BY nav_dispersion DESC",
        fund_name,
    )


@app.get("/api/hwm-audit/{fund_name}")
async def hwm_audit(fund_name: str):
    return await query(
        "SELECT * FROM investor_hwm_audit WHERE fund_name = $1 ORDER BY accrued_perf_fee DESC",
        fund_name,
    )


@app.get("/api/investor-fairness/{fund_name}")
async def investor_fairness(fund_name: str):
    return await query(
        "SELECT * FROM investor_fairness WHERE fund_name = $1 ORDER BY fee_drag_spread DESC",
        fund_name,
    )


@app.get("/api/investor-performance/{fund_name}")
async def investor_performance(fund_name: str):
    return await query(
        "SELECT * FROM investor_performance WHERE fund_name = $1 ORDER BY net_return_pct DESC",
        fund_name,
    )


@app.get("/api/cohort-drill/{fund_name}")
async def cohort_drill(
    fund_name: str,
    entry_date: str = Query(...),
    entry_nav: float = Query(...),
):
    return await query(
        """SELECT * FROM investor_performance
           WHERE fund_name = $1
             AND entry_date = $2
             AND ABS(entry_nav - $3::numeric) < 0.01
           ORDER BY net_return_pct""",
        fund_name,
        entry_date,
        entry_nav,
    )


@app.get("/api/cohort-stress/{fund_name}")
async def cohort_stress(fund_name: str, exit_date: str = Query(default=None)):
    d = exit_date or str(date.today())
    return await query("SELECT * FROM run_cohort_stress_test($1, $2::date)", fund_name, d)


@app.get("/api/reconciliation")
async def reconciliation(recon_date: str = Query(default=None)):
    d = recon_date or str(date.today())
    return await query("SELECT * FROM nav_reconciliation($1::date)", d)


@app.get("/api/pnl-stream/{fund_name}")
async def pnl_stream(fund_name: str):
    async def event_generator():
        while True:
            try:
                data = await query(
                    "SELECT * FROM monitoring.daily_pnl_by_position WHERE fund_name = $1",
                    fund_name,
                )
                yield f"data: {json.dumps(data, default=str)}\n\n"
                await asyncio.sleep(5)
            except Exception:
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/apps.json")
def get_apps():
    return JSONResponse(content={})


@app.get("/widgets.json")
def get_widgets():
    widgets = {
        "fund_overview": {
            "name": "Fund Overview",
            "description": "High-level summary of all funds",
            "endpoint": "/api/fund-overview",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                    ],
                },
            },
            "params": [],
        },
        "positions": {
            "name": "Positions",
            "description": "Current position summary for a fund",
            "endpoint": "/api/positions/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                        {"field": "market_value", "headerName": "Market Value", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "pnl": {
            "name": "Daily P&L",
            "description": "Daily profit and loss by position for a fund",
            "endpoint": "/api/pnl/{fund_name}",
            "data": {
                "dataKey": "positions",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                        {"field": "daily_pnl", "headerName": "Daily P&L", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "nav_history": {
            "name": "NAV History",
            "description": "Historical NAV per unit for a fund",
            "endpoint": "/api/nav-history/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "date", "headerName": "Date", "cellDataType": "date"},
                        {"field": "nav_per_unit", "headerName": "NAV Per Unit", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
                {"type": "number", "paramName": "days", "value": 90, "label": "Days"},
            ],
        },
        "investors": {
            "name": "Investor Allocation",
            "description": "Investor allocation breakdown for a fund",
            "endpoint": "/api/investors/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                        {"field": "allocation_value", "headerName": "Allocation Value", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "alerts": {
            "name": "Active Alerts",
            "description": "Active risk and compliance alerts across all funds",
            "endpoint": "/api/alerts",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": False,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "severity", "headerName": "Severity", "cellDataType": "text"},
                        {"field": "alert_date", "headerName": "Date", "cellDataType": "date"},
                    ],
                },
            },
            "params": [],
        },
        "returns": {
            "name": "Rolling Returns",
            "description": "Rolling return metrics for a fund",
            "endpoint": "/api/returns/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "fees": {
            "name": "Fee Summary",
            "description": "Management and performance fee history for a fund",
            "endpoint": "/api/fees/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "date", "headerName": "Date", "cellDataType": "date"},
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "investor_performance": {
            "name": "Investor Performance",
            "description": "Net return per investor for a fund",
            "endpoint": "/api/investor-performance/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": True,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                        {"field": "net_return_pct", "headerName": "Net Return %", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
        "hwm_audit": {
            "name": "High-Water Mark Audit",
            "description": "Per-investor HWM and accrued performance fee audit",
            "endpoint": "/api/hwm-audit/{fund_name}",
            "data": {
                "dataKey": "",
                "table": {
                    "enableCharts": False,
                    "showAll": True,
                    "columnsDefs": [
                        {"field": "fund_name", "headerName": "Fund", "cellDataType": "text"},
                        {"field": "accrued_perf_fee", "headerName": "Accrued Perf Fee", "cellDataType": "number"},
                    ],
                },
            },
            "params": [
                {"type": "text", "paramName": "fund_name", "value": "", "label": "Fund Name"},
            ],
        },
    }
    return JSONResponse(content=widgets)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8050, reload=True)
