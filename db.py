"""PostgreSQL connection and fund accounting query helpers"""
import os
import streamlit as st
import psycopg2
from dotenv import load_dotenv

load_dotenv()


@st.cache_resource
def get_db():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME", "financial_db"),
            user=os.getenv("DB_USER", "admin"),
            password=os.getenv("DB_PASSWORD", "admin"),
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", 5432)),
        )
        conn.autocommit = True
        return conn
    except Exception:
        return None


def q(sql, params=None):
    conn = get_db()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []  # INSERT/UPDATE/DELETE — no result set
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        st.error(f"DB: {e}")
        get_db.clear()
        return []
