"""Together AI client targeting GLM-5 via OpenAI-compatible endpoint."""
import os
import streamlit as st
from openai import OpenAI

_TOGETHER_BASE = "https://api.together.ai/v1"
_MODEL = "zai-org/GLM-5"


@st.cache_resource
def get_glm_client() -> OpenAI:
    api_key = os.environ.get("TOGETHER_API_KEY") or st.secrets.get("TOGETHER_API_KEY", "")
    if not api_key:
        raise ValueError("TOGETHER_API_KEY is not set — add it to your environment or .streamlit/secrets.toml")
    return OpenAI(api_key=api_key, base_url=_TOGETHER_BASE)


def glm_chat(messages: list[dict], temperature: float = 0.3) -> str:
    client = get_glm_client()
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content
