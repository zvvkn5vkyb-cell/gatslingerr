"""PostgreSQL connection and fund accounting query helpers"""
import streamlit as st
import psycopg2


@st.cache_resource
def get_db():
    try:
        conn = psycopg2.connect(
            dbname="financial_db", user="chadh.", host="localhost", port=5432
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
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        st.error(f"DB: {e}")
        get_db.clear()
        return []
