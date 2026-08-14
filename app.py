import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from urllib.parse import unquote
import time
import datetime

# -------------------------------
# APP CONFIG
# -------------------------------
st.set_page_config(page_title="SMT PRO SNIPER", layout="wide")

st.markdown("<h2 style='text-align:center;'>SMT PRO SNIPER TERMINAL</h2><hr>", unsafe_allow_html=True)

# -------------------------------
# TIME FILTER (NO TRADE BEFORE 9:20)
# -------------------------------
current_time = datetime.datetime.now().time()
if current_time < datetime.time(9, 20):
    st.warning("⏳ Wait till 9:20 AM for sniper trades")
    st.stop()

# -------------------------------
# AUTO REFRESH
# -------------------------------
auto_refresh = st.checkbox("Auto Refresh (5 min)", value=False)

if auto_refresh:
    time.sleep(300)
    st.rerun()

# -------------------------------
# STOCK SOURCE
# -------------------------------
source = st.radio("Stock Source", ["Manual CSV", "Chartink LIVE"], horizontal=True)

# ===============================
# CSV MODE
# ===============================
if source == "Manual CSV":

    uploaded_file = st.file_uploader("Upload Stock CSV", type=["csv"])

    if uploaded_file:
        df_symbols = pd.read_csv(uploaded_file)
        df_symbols.columns = df_symbols.columns.str.strip()

        if "Symbol" in df_symbols.columns:
            symbols = [str(s).strip().upper() + ".NS" for s in df_symbols["Symbol"].dropna()]
        else:
            st.error("CSV must contain 'Symbol'")
            st.stop()
    else:
        symbols = []

# ===============================
# CHARTINK MODE
# ===============================
else:

    chartink_cookie = st.text_input("Chartink Cookie", type="password")

    @st.cache_data(ttl=60)
    def get_chartink_symbols(cookie):

        if not cookie:
            return []

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

        try:
            res = session.post("https://chartink.com/screener/process", headers=headers, json=payload)
            data = res.json().get("data", [])
            return [row["nsecode"].upper() + ".NS" for row in data if row.get("nsecode")]
        except:
            return []

    if st.button("Load Chartink Stocks"):
        symbols = get_chartink_symbols(chartink_cookie)
        st.session_state["symbols"] = symbols

    symbols = st.session_state.get("symbols", [])

# -------------------------------
# TIMEFRAME
# -------------------------------
timeframe = st.selectbox("Timeframe", ["5m", "15m"], index=0)

# -------------------------------
# DATA FETCH
# -------------------------------
@st.cache_data(ttl=60)
def get_data(symbol):
    try:
        df = yf.download(symbol, period="5d", interval=timeframe, progress=False)
        if df is None or df.empty:
            return None
        return df.dropna()
    except:
        return None

# -------------------------------
# ATR
# -------------------------------
def calculate_atr(df, period=14):
    df = df.copy()
    df["tr"] = (df["High"] - df["Low"]).combine(
        abs(df["High"] - df["Close"].shift()),
        max
    )
    return df["tr"].rolling(period).mean().iloc[-1]

# -------------------------------
# RISK SETTINGS
# -------------------------------
capital = st.sidebar.number_input("Capital", value=50000)
risk_percent = st.sidebar.slider("Risk %", 0.5, 5.0, 1.0)
risk_amount = capital * (risk_percent / 100)

# -------------------------------
# POSITION SIZE
# -------------------------------
def calculate_position(entry, sl):
    if entry is None or sl is None:
        return 0, 0, 0

    sl_dist = abs(entry - sl)
    if sl_dist == 0:
        return 0, 0, 0

    qty = int(risk_amount / sl_dist)
    capital_used = qty * entry
    risk = qty * sl_dist

    if capital_used > capital:
        qty = int(capital / entry)
        capital_used = qty * entry
        risk = qty * sl_dist

    return qty, capital_used, risk

# -------------------------------
# SNIPER LOGIC
# -------------------------------
def analyze_stock(df):

    if df is None or len(df) < 40:
        return "WAIT", None, None, None, 0

    c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    atr = calculate_atr(df)

    signal = "WAIT"
    entry = sl = target = None
    score = 0

    # Trap detection
    fake_up = c2["High"] > c1["High"] and c3["Close"] < c2["High"]
    fake_down = c2["Low"] < c1["Low"] and c3["Close"] > c2["Low"]

    # Volume
    vol_ok = c3["Volume"] > c2["Volume"] > c1["Volume"]
    if vol_ok:
        score += 30

    # BUY
    if c3["Close"] > c2["High"] and not fake_up:
        signal = "BUY"
        entry = c3["Close"]
        sl = c2["Low"]
        target = entry + (2 * atr)
        score += 40

    # SELL
    elif c3["Close"] < c2["Low"] and not fake_down:
        signal = "SELL"
        entry = c3["Close"]
        sl = c2["High"]
        target = entry - (2 * atr)
        score += 40

    # Sideways filter
    if abs(c2["High"] - c2["Low"]) < atr * 0.5:
        return "WAIT", None, None, None, 0

    if score < 50:
        return "WAIT", None, None, None, score

    return signal, entry, sl, target, score

# -------------------------------
# RUN SCANNER
# -------------------------------
if st.button("Run Sniper Scanner"):

    results = []

    for sym in symbols:
        df = get_data(sym)
        signal, entry, sl, target, score = analyze_stock(df)

        qty, cap, risk = calculate_position(entry, sl)

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

    df_results = pd.DataFrame(results)

    st.dataframe(df_results)

    # Top trades
    best = df_results[
        (df_results["Signal"].isin(["BUY", "SELL"])) &
        (df_results["Score"] >= 60)
    ].sort_values("Score", ascending=False).head(2)

    st.subheader("🔥 TOP SNIPER TRADES")

    for _, row in best.iterrows():
        color = "green" if row["Signal"] == "BUY" else "red"

        st.markdown(f"""
        <div style='padding:15px;background:{color};color:white;border-radius:10px'>
        <b>{row['Stock']}</b> - {row['Signal']}<br>
        Entry: {round(row['Entry'],2)} | SL: {round(row['SL'],2)} | Target: {round(row['Target'],2)}<br>
        Qty: {row['Qty']} | Risk: ₹{round(row['Risk'],0)}
        </div>
        """, unsafe_allow_html=True)

    if best.empty:
        st.warning("No sniper trades found")

# -------------------------------
# FOOTER
# -------------------------------
st.caption("For educational use only")
