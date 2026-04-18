"""NAV Summary AI page — drafts a quarterly client summary using GLM-5."""
import json
import streamlit as st
from db import q
from glm_client import glm_chat

_SYSTEM = (
    "You are a senior portfolio manager writing clear, professional investor communications. "
    "When given fund data, produce a concise quarterly NAV summary with: "
    "(1) a one-paragraph performance narrative, "
    "(2) 3–5 key risk bullet points, "
    "(3) a brief outlook sentence. "
    "Use plain language suitable for limited partners. Never hallucinate numbers — use only the data provided."
)


def _build_prompt(fund: dict, positions: list, bridge: dict | None, alerts: list) -> str:
    parts = [f"Fund: {fund.get('fund_name')}"]
    parts.append(f"NAV/unit: {fund.get('nav_per_unit')}  AUM: {fund.get('aum')}  Daily change: {fund.get('daily_change_pct')}%")
    parts.append(f"Investors: {fund.get('num_investors')}")

    if bridge:
        parts.append("\nNAV Bridge:")
        parts.append(json.dumps(bridge, default=str))

    if positions:
        parts.append("\nTop Positions:")
        parts.append(json.dumps(positions[:10], default=str))

    if alerts:
        parts.append("\nActive Alerts:")
        parts.append(json.dumps(alerts[:5], default=str))

    return "\n".join(parts)


def render_nav_summary(active_fund: str, funds: list):
    st.header("NAV Summary — AI Draft")

    if not active_fund or not funds:
        st.info("Select a fund in the sidebar.")
        return

    fund = next((f for f in funds if f["fund_name"] == active_fund), funds[0])

    with st.expander("Source data", expanded=False):
        st.json(fund)

    if st.button("Generate summary with GLM-5", type="primary"):
        with st.spinner("Calling GLM-5 via Together AI…"):
            try:
                positions = q(
                    "SELECT * FROM monitoring.position_summary WHERE fund_name = %s", (active_fund,)
                )
                bridge_rows = q(
                    "SELECT * FROM monitoring.nav_bridge_waterfall WHERE fund_name = %s ORDER BY date DESC LIMIT 1",
                    (active_fund,),
                )
                bridge = bridge_rows[0] if bridge_rows else None
                alerts = q(
                    "SELECT * FROM monitoring.active_alerts ORDER BY alert_date DESC LIMIT 5"
                )

                prompt = _build_prompt(fund, positions, bridge, alerts)
                messages = [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ]
                result = glm_chat(messages)
                st.markdown(result)
                st.download_button(
                    "Download as .txt",
                    data=result,
                    file_name=f"{active_fund}_nav_summary.txt",
                    mime="text/plain",
                )
            except ValueError as e:
                st.error(str(e))
                st.code(
                    "# Add your key to .streamlit/secrets.toml\nTOGETHER_API_KEY = 'tgp_v1_...'"
                )
            except Exception as e:
                st.error(f"GLM-5 call failed: {e}")
