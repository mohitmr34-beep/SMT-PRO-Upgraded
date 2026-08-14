import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from urllib.parse import unquote
import time
import datetime

# -------------------------------
# CONFIG
# -------------------------------
st.set_page_config(page_title="SMT PRO SNIPER", layout="wide")
st.markdown("<h2 style='text-align:center;'>SMT PRO SNIPER</h2><hr>", unsafe_allow_html=True)

# -------------------------------
# TIME FILTER
# -------------------------------
if datetime.datetime.now().time() < datetime.time(9, 20):
    st.warning("Wait till 9:20 AM")
    st.stop()

# -------------------------------
# AUTO REFRESH
# -------------------------------
if st.checkbox("Auto Refresh (5 min)"):
    time.sleep(300)
    st.rerun()

# -------------------------------
# SAFE VALUE EXTRACTOR
# -------------------------------
def val(x):
    try:
        if isinstance(x, pd.Series):
            return float(x.iloc[0])
        return float(x)
    except:
        return 0.0

# -------------------------------
# SOURCE
# -------------------------------
source = st.radio("Stock Source", ["Manual CSV", "Chartink LIVE"], horizontal=True)
symbols = []

# ===============================
# CSV MODE
# ===============================
if source == "Manual CSV":

    file = st.file_uploader("Upload CSV", type="csv")

    if file:
        df = pd.read_csv(file)
        df.columns = df.columns.str.strip()

        if "Symbol" not in df.columns:
            st.error("CSV must contain 'Symbol'")
            st.stop()

        symbols = [s.strip().upper() + ".NS" for s in df["Symbol"].dropna()]

# ===============================
# CHARTINK MODE
# ===============================
else:

    cookie = st.text_input("Chartink Cookie", type="password")

    @st.cache_data(ttl=60)
    def get_symbols(cookie):

        if not cookie:
            return []

        try:
            session = requests.Session()

            for part in cookie.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    session.cookies.set(k, v, domain="chartink.com")

            session.get("https://chartink.com")

            xsrf = unquote(session.cookies.get("XSRF-TOKEN", ""))

            headers = {
                "X-XSRF-TOKEN": xsrf,
                "Content-Type": "application/json"
            }

            payload = {
                "scan_clause": "( {cash} ( ( {cash} ( ( {cash} ( daily close >= daily max(252, daily high)*0.98 and daily volume > daily sma(daily volume,20)*1.5 and daily close > daily open ) ) or ( {cash} ( daily high >= daily max(252, daily high) and daily close < daily open and daily volume > daily sma(daily volume,20)*1.5 ) ) or ( {cash} ( daily open > 1 day ago close*1.02 and daily volume > daily sma(daily volume,20)*2 and daily close > daily open ) ) ) ) ) )"
            }

            res = session.post("https://chartink.com/screener/process", headers=headers, json=payload)
            data = res.json().get("data", [])

            if not data:
                return []

            return [row["nsecode"].upper() + ".NS" for row in data if row.get("nsecode")]

        except:
            return []

    if st.button("Load Chartink Stocks"):

        symbols = get_symbols(cookie)

        if symbols:
            st.session_state["symbols"] = symbols
            st.success(f"{len(symbols)} stocks loaded")
        else:
            st.error("No stocks fetched. Check cookie.")
            st.stop()

    if "symbols" in st.session_state:
        symbols = st.session_state["symbols"]

    if not symbols:
        st.warning("Load stocks first")
        st.stop()

# -------------------------------
# TIMEFRAME
# -------------------------------
timeframe = st.selectbox("Timeframe", ["5m", "15m"], index=0)

# -------------------------------
# DATA FETCH
# -------------------------------
@st.cache_data(ttl=60)
def get_data(sym):
    try:
        df = yf.download(sym, period="5d", interval=timeframe, progress=False)

        if df is None or df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna()

    except:
        return None

# -------------------------------
# ATR
# -------------------------------
def calculate_atr(df, period=14):

    df = df.copy()

    df["H-L"] = df["High"] - df["Low"]
    df["H-PC"] = abs(df["High"] - df["Close"].shift(1))
    df["L-PC"] = abs(df["Low"] - df["Close"].shift(1))

    df["TR"] = df[["H-L", "H-PC", "L-PC"]].max(axis=1)

    atr = df["TR"].rolling(period).mean()

    return atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0

# -------------------------------
# RISK
# -------------------------------
capital = st.sidebar.number_input("Capital", value=50000)
risk_pct = st.sidebar.slider("Risk %", 0.5, 5.0, 1.0)
risk_amt = capital * (risk_pct / 100)

def position(entry, sl):

    if entry is None or sl is None:
        return 0, 0, 0

    dist = abs(entry - sl)
    if dist == 0:
        return 0, 0, 0

    qty = int(risk_amt / dist)
    cap = qty * entry
    risk = qty * dist

    if cap > capital:
        qty = int(capital / entry)
        cap = qty * entry
        risk = qty * dist

    return qty, cap, risk

# -------------------------------
# SNIPER LOGIC
# -------------------------------
def analyze(df):

    if df is None or len(df) < 40:
        return "WAIT", None, None, None, 0

    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    # SAFE VALUES
    h1, l1, c1c = val(c1["High"]), val(c1["Low"]), val(c1["Close"])
    h2, l2, c2c = val(c2["High"]), val(c2["Low"]), val(c2["Close"])
    h3, l3, c3c = val(c3["High"]), val(c3["Low"]), val(c3["Close"])

    v1, v2, v3 = val(c1["Volume"]), val(c2["Volume"]), val(c3["Volume"])

    atr = calculate_atr(df)

    signal = "WAIT"
    entry = sl = target = None
    score = 0

    # TRAPS
    fake_up = (h2 > h1) and (c3c < h2)
    fake_down = (l2 < l1) and (c3c > l2)

    # VOLUME
    if (v3 > v2) and (v2 > v1):
        score += 30

    # BUY
    if (c3c > h2) and not fake_up:
        signal = "BUY"
        entry = c3c
        sl = l2
        target = entry + (2 * atr)
        score += 40

    # SELL
    elif (c3c < l2) and not fake_down:
        signal = "SELL"
        entry = c3c
        sl = h2
        target = entry - (2 * atr)
        score += 40

    # SIDEWAYS FILTER
    if abs(h2 - l2) < atr * 0.5:
        return "WAIT", None, None, None, 0

    if score < 50:
        return "WAIT", None, None, None, score

    return signal, entry, sl, target, score

# -------------------------------
# RUN
# -------------------------------
if st.button("Run Sniper"):

    results = []

    for sym in symbols:
        df = get_data(sym)
        signal, entry, sl, target, score = analyze(df)

        qty, cap, risk = position(entry, sl)

        results.append({
            "Stock": sym,
            "Signal": signal,
            "Score": score,
            "Entry": entry,
            "SL": sl,
            "Target": target,
            "Qty": qty,
            "Capital": cap,
            "Risk": risk
        })

    res_df = pd.DataFrame(results)

    st.dataframe(res_df, use_container_width=True)

    best = res_df[
        (res_df["Signal"].isin(["BUY", "SELL"])) &
        (res_df["Score"] >= 60)
    ].sort_values("Score", ascending=False).head(2)

    st.subheader("🔥 TOP TRADES")

    for _, r in best.iterrows():
        color = "green" if r["Signal"] == "BUY" else "red"

        st.markdown(f"""
        <div style='padding:15px;background:{color};color:white;border-radius:10px'>
        <b>{r['Stock']}</b> - {r['Signal']}<br>
        Entry: {round(r['Entry'],2)} | SL: {round(r['SL'],2)} | Target: {round(r['Target'],2)}<br>
        Qty: {r['Qty']} | Risk: ₹{round(r['Risk'],0)}
        </div>
        """, unsafe_allow_html=True)

    if best.empty:
        st.warning("No trades found")

# -------------------------------
# FOOTER
# -------------------------------
st.caption("Educational use only")
