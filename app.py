from pathlib import Path

app_code = r'''import streamlit as st
import pandas as pd
import numpy as np
import requests
import gzip
import io
import json
import time
from datetime import datetime, time as dtime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

# ============================================================
# SMT PRO SNIPER - INTRADAY
# Chartink universe -> Upstox market data -> ATR/VWAP/RSI
# ============================================================

st.set_page_config(
    page_title="SMT PRO SNIPER",
    layout="wide",
    initial_sidebar_state="expanded",
)

IST = ZoneInfo("Asia/Kolkata")

st.markdown("""
<style>
.block-container {padding-top: 1rem; padding-bottom: 1rem;}
.small-box {
    padding: 8px 12px; border-radius: 8px; background: #f5f5f5;
    font-size: 14px; margin-bottom: 8px;
}
.trade-card {
    padding: 14px; border-radius: 10px; margin: 8px 0;
    color: white; font-size: 15px;
}
.buy {background: #168a45;}
.sell {background: #c0392b;}
.wait {background: #777;}
</style>
""", unsafe_allow_html=True)

# -------------------------
# HEADER / IST CLOCK
# -------------------------
now_ist = datetime.now(IST)
st.markdown(
    f"<div style='text-align:right;font-weight:600;'>IST: "
    f"{now_ist.strftime('%d-%m-%Y %H:%M:%S')}</div>",
    unsafe_allow_html=True,
)
st.markdown(
    "<h2 style='text-align:center;'>SMT PRO SNIPER — INTRADAY</h2>",
    unsafe_allow_html=True,
)

# -------------------------
# SETTINGS
# -------------------------
with st.sidebar:
    st.header("⚙️ Scanner Settings")
    refresh_seconds = st.number_input(
        "Refresh interval (seconds)", min_value=30, max_value=900, value=60, step=30
    )
    timeframe = st.selectbox("Data timeframe", ["1m", "5m"], index=0)

    st.header("💰 Risk Management")
    capital = st.number_input("Capital ₹", min_value=1000.0, value=50000.0, step=1000.0)
    risk_pct = st.number_input(
        "Risk per trade %", min_value=0.1, max_value=5.0, value=1.0, step=0.1
    )
    risk_amount = capital * risk_pct / 100.0

    st.header("🎯 Sniper Filters")
    min_score = st.slider("Minimum score", 40, 100, 60)
    min_volume_ratio = st.number_input(
        "Minimum volume ratio", min_value=0.5, max_value=5.0, value=1.20, step=0.05
    )
    atr_sl_mult = st.number_input(
        "ATR SL multiplier", min_value=0.5, max_value=3.0, value=1.0, step=0.1
    )
    atr_target_mult = st.number_input(
        "ATR target multiplier", min_value=1.0, max_value=5.0, value=2.0, step=0.1
    )
    max_trades = st.number_input(
        "Top trades", min_value=1, max_value=10, value=2, step=1
    )

    st.header("🔄 Data Source")
    data_source = st.radio(
        "Market data",
        ["Upstox", "Yahoo fallback"],
        index=0,
    )

# -------------------------
# AUTO REFRESH
# -------------------------
if st.button("🔄 Refresh Now"):
    st.rerun()

st.caption(
    f"Automatic refresh is intended for the scanner. Current interval: {refresh_seconds}s"
)

# -------------------------
# SAFE NUMERIC HELPERS
# -------------------------
def scalar(x, default=np.nan):
    try:
        if isinstance(x, pd.Series):
            if x.empty:
                return default
            x = x.iloc[0]
        if isinstance(x, (list, tuple, np.ndarray)):
            if len(x) == 0:
                return default
            x = x[0]
        return float(x)
    except Exception:
        return default


def normalize_ohlcv(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        # Prefer the first level when yfinance returns MultiIndex columns.
        df.columns = [str(c[0]) for c in df.columns]

    rename = {}
    for c in df.columns:
        key = str(c).strip().lower()
        if key == "open":
            rename[c] = "Open"
        elif key == "high":
            rename[c] = "High"
        elif key == "low":
            rename[c] = "Low"
        elif key == "close":
            rename[c] = "Close"
        elif key in ("volume", "vol"):
            rename[c] = "Volume"

    df = df.rename(columns=rename)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in df.columns for c in required):
        return None

    for c in required:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df[required].dropna()
    return df if not df.empty else None


# -------------------------
# CHARTINK STOCK SOURCE
# -------------------------
st.subheader("1️⃣ Stock Universe")

source = st.radio(
    "Stock source",
    ["CSV", "Chartink Cookie"],
    horizontal=True,
)

symbols = []

if source == "CSV":
    uploaded = st.file_uploader("Upload Chartink CSV", type=["csv"])

    if uploaded is not None:
        try:
            csv_df = pd.read_csv(uploaded)
            csv_df.columns = [str(c).strip() for c in csv_df.columns]

            symbol_col = None
            for candidate in ["Symbol", "symbol", "NSECODE", "nsecode", "NSE Code"]:
                if candidate in csv_df.columns:
                    symbol_col = candidate
                    break

            if symbol_col is None:
                st.error("CSV must contain Symbol / NSECODE column.")
                st.stop()

            for raw in csv_df[symbol_col].dropna():
                s = str(raw).strip().upper()
                if not s or s == "NAN":
                    continue
                if s.endswith(".NS"):
                    symbols.append(s)
                else:
                    symbols.append(s + ".NS")

            symbols = list(dict.fromkeys(symbols))

            if symbols:
                st.success(f"{len(symbols)} stocks loaded from CSV.")
            else:
                st.warning("CSV contains no usable symbols.")
        except Exception as e:
            st.error(f"CSV error: {e}")
            st.stop()
    else:
        st.info("Upload your Chartink result CSV to scan those stocks.")

else:
    cookie = st.text_input("Chartink Cookie", type="password")
    chartink_clause = st.text_area(
        "Chartink scan clause",
        value="""( {cash} ( ( {cash} ( ( {cash} (
daily close >= daily max(252, daily high)*0.98
and daily volume > daily sma(daily volume,20)*1.5
and daily close > daily open
) ) or ( {cash} (
daily high >= daily max(252, daily high)
and daily close < daily open
and daily volume > daily sma(daily volume,20)*1.5
) ) or ( {cash} (
daily open > 1 day ago close*1.02
and daily volume > daily sma(daily volume,20)*2
and daily close > daily open
) ) ) ) ) )""",
        height=150,
    )

    @st.cache_data(ttl=60, show_spinner=False)
    def chartink_symbols(cookie_text, clause):
        if not cookie_text:
            return [], "Cookie required"

        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/151 Safari/537.36"
                ),
                "Referer": "https://chartink.com/",
            })

            for part in cookie_text.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    session.cookies.set(k, v, domain="chartink.com")

            home = session.get("https://chartink.com/", timeout=15)
            xsrf = unquote(session.cookies.get("XSRF-TOKEN", ""))

            headers = {
                "User-Agent": session.headers["User-Agent"],
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://chartink.com/",
                "Content-Type": "application/json",
            }
            if xsrf:
                headers["X-XSRF-TOKEN"] = xsrf

            payload = {"scan_clause": clause}

            res = session.post(
                "https://chartink.com/screener/process",
                headers=headers,
                json=payload,
                timeout=30,
            )

            if res.status_code != 200:
                return [], f"Chartink HTTP {res.status_code}"

            try:
                data = res.json().get("data", [])
            except Exception:
                return [], "Chartink did not return JSON."

            out = []
            for row in data:
                code = row.get("nsecode")
                if code:
                    out.append(str(code).strip().upper() + ".NS")

            out = list(dict.fromkeys(out))
            if not out:
                return [], "No stocks returned by Chartink."

            return out, ""

        except Exception as e:
            return [], str(e)

    if st.button("📡 Fetch / Refresh Chartink Stocks"):
        loaded, err = chartink_symbols(cookie, chartink_clause)
        if loaded:
            st.session_state["chartink_symbols"] = loaded
            st.success(f"{len(loaded)} Chartink stocks loaded.")
        else:
            st.error(err)

    symbols = st.session_state.get("chartink_symbols", [])

    if symbols:
        st.success(f"Active universe: {len(symbols)} stocks")
    else:
        st.info("Enter the cookie and fetch Chartink stocks.")
        st.stop()

# -------------------------
# UPSTOX SETTINGS
# -------------------------
st.subheader("2️⃣ Upstox Data Engine")

if data_source == "Upstox":
    upstox_token = st.text_input(
        "Upstox Access Token",
        type="password",
        help="Use your current Upstox OAuth access token.",
    )
else:
    upstox_token = ""

# -------------------------
# UPSTOX INSTRUMENT MASTER
# -------------------------
@st.cache_data(ttl=86400, show_spinner=False)
def load_upstox_instruments():
    urls = [
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz",
    ]

    last_error = None

    for url in urls:
        try:
            r = requests.get(url, timeout=40)
            r.raise_for_status()
            raw = r.content

            # Correctly handle gzip regardless of HTTP content-type.
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)

            text = raw.decode("utf-8-sig")

            if url.endswith(".csv.gz"):
                inst = pd.read_csv(io.StringIO(text), low_memory=False)
            else:
                obj = json.loads(text)
                inst = pd.DataFrame(obj)

            inst.columns = [str(c).strip() for c in inst.columns]
            return inst

        except Exception as e:
            last_error = e

    raise RuntimeError(f"Unable to download Upstox instrument master: {last_error}")


@st.cache_data(ttl=86400, show_spinner=False)
def build_instrument_map():
    inst = load_upstox_instruments()

    # Upstox has changed column naming across master versions.
    colmap = {str(c).lower().strip(): c for c in inst.columns}

    def find(*names):
        for n in names:
            if n.lower() in colmap:
                return colmap[n.lower()]
        return None

    instrument_key = find("instrument_key")
    trading_symbol = find("trading_symbol", "tradingsymbol", "symbol")
    exchange = find("exchange")
    segment = find("segment")

    if instrument_key is None:
        raise RuntimeError(
            "Upstox instrument master does not contain instrument_key."
        )

    if trading_symbol is None:
        raise RuntimeError(
            "Upstox master lacks trading_symbol/symbol. "
            "Use a current complete instrument master."
        )

    mp = {}

    for _, row in inst.iterrows():
        ex = str(row[exchange]).upper() if exchange else ""
        seg = str(row[segment]).upper() if segment else ""
        sym = str(row[trading_symbol]).strip().upper()

        # NSE cash equities only.
        if ex not in ("NSE", "NSE_EQ", "NSE_EQ."):
            continue

        if seg and "EQ" not in seg and "EQUITY" not in seg:
            continue

        key = str(row[instrument_key]).strip()
        if sym and key:
            mp[sym] = key

    return mp


# -------------------------
# UPSTOX HISTORICAL DATA
# -------------------------
def upstox_candles(instrument_key, interval="1minute"):
    if not upstox_token or not instrument_key:
        return None

    try:
        # Upstox V3 historical candle endpoint.
        end = datetime.now(IST).strftime("%Y-%m-%d")
        start = (datetime.now(IST).date()).strftime("%Y-%m-%d")

        url = (
            "https://api.upstox.com/v3/historical-candle/"
            f"{requests.utils.quote(instrument_key, safe='')}/"
            f"{interval}/{end}/{start}"
        )

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {upstox_token}",
        }

        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            return None

        payload = r.json()
        candles = payload.get("data", {}).get("candles", [])

        if not candles:
            return None

        # V3 candle:
        # timestamp, open, high, low, close, volume, open_interest
        rows = []
        for c in candles:
            if len(c) < 6:
                continue
            rows.append({
                "Open": c[1],
                "High": c[2],
                "Low": c[3],
                "Close": c[4],
                "Volume": c[5],
            })

        if not rows:
            return None

        df = pd.DataFrame(rows)
        return normalize_ohlcv(df)

    except Exception:
        return None


def upstox_intraday(instrument_key):
    if not upstox_token or not instrument_key:
        return None

    try:
        url = (
            "https://api.upstox.com/v3/historical-candle/intraday/"
            f"{requests.utils.quote(instrument_key, safe='')}/1minute"
        )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {upstox_token}",
        }

        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            return None

        candles = r.json().get("data", {}).get("candles", [])
        if not candles:
            return None

        rows = []
        for c in candles:
            if len(c) < 6:
                continue
            rows.append({
                "Open": c[1],
                "High": c[2],
                "Low": c[3],
                "Close": c[4],
                "Volume": c[5],
            })

        return normalize_ohlcv(pd.DataFrame(rows))

    except Exception:
        return None


# -------------------------
# YAHOO FALLBACK
# -------------------------
@st.cache_data(ttl=45, show_spinner=False)
def yahoo_data(symbol, interval):
    try:
        import yfinance as yf

        period = "5d" if interval == "1m" else "10d"
        df = yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        return normalize_ohlcv(df)
    except Exception:
        return None


# -------------------------
# INDICATORS
# -------------------------
def add_indicators(df):
    df = normalize_ohlcv(df)
    if df is None or len(df) < 20:
        return None

    x = df.copy()

    prev_close = x["Close"].shift(1)
    tr1 = x["High"] - x["Low"]
    tr2 = (x["High"] - prev_close).abs()
    tr3 = (x["Low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    x["ATR"] = tr.rolling(14, min_periods=14).mean()

    # Session VWAP. If timestamp is unavailable, cumulative VWAP is still
    # calculated over the supplied intraday dataset.
    typical = (x["High"] + x["Low"] + x["Close"]) / 3
    vol = x["Volume"].fillna(0)

    if isinstance(x.index, pd.DatetimeIndex):
        idx = x.index
        if idx.tz is None:
            try:
                idx = idx.tz_localize("UTC").tz_convert(IST)
            except Exception:
                pass
        else:
            idx = idx.tz_convert(IST)

        session = pd.Series(idx.date, index=x.index)
        pv = typical * vol
        x["VWAP"] = pv.groupby(session).cumsum() / vol.groupby(session).cumsum().replace(0, np.nan)
    else:
        x["VWAP"] = (typical * vol).cumsum() / vol.cumsum().replace(0, np.nan)

    delta = x["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))
    x["RSI"] = x["RSI"].fillna(50)

    x["VolMA"] = x["Volume"].rolling(20, min_periods=5).mean()
    x["VolRatio"] = x["Volume"] / x["VolMA"].replace(0, np.nan)

    return x.dropna(subset=["ATR", "VWAP"])


# -------------------------
# SNIPER ANALYSIS
# -------------------------
def analyze_intraday(df):
    x = add_indicators(df)

    if x is None or len(x) < 20:
        return {
            "Signal": "WAIT",
            "Score": 0,
            "Entry": np.nan,
            "SL": np.nan,
            "Target": np.nan,
            "ATR": np.nan,
            "VWAP": np.nan,
            "RSI": np.nan,
            "VolRatio": np.nan,
            "Reason": "Insufficient data",
        }

    c1 = x.iloc[-3]
    c2 = x.iloc[-2]
    c3 = x.iloc[-1]

    close = scalar(c3["Close"])
    high = scalar(c3["High"])
    low = scalar(c3["Low"])

    h1, l1, cl1 = scalar(c1["High"]), scalar(c1["Low"]), scalar(c1["Close"])
    h2, l2, cl2 = scalar(c2["High"]), scalar(c2["Low"]), scalar(c2["Close"])

    atr = scalar(c3["ATR"])
    vwap = scalar(c3["VWAP"])
    rsi = scalar(c3["RSI"], 50)
    vol_ratio = scalar(c3["VolRatio"], 0)

    if not np.isfinite(atr) or atr <= 0:
        return {
            "Signal": "WAIT", "Score": 0, "Entry": np.nan,
            "SL": np.nan, "Target": np.nan, "ATR": atr,
            "VWAP": vwap, "RSI": rsi, "VolRatio": vol_ratio,
            "Reason": "ATR unavailable"
        }

    # Candle structure
    bullish = close > scalar(c3["Open"])
    bearish = close < scalar(c3["Open"])

    breakout_up = close > h2
    breakout_down = close < l2

    # Fakeout / contradiction
    fake_up = (h2 > h1) and (cl2 <= h1) and (close < h2)
    fake_down = (l2 < l1) and (cl2 >= l1) and (close > l2)

    score_buy = 0
    score_sell = 0
    buy_reasons = []
    sell_reasons = []

    # Price / breakout
    if breakout_up:
        score_buy += 25
        buy_reasons.append("breakout")
    if breakout_down:
        score_sell += 25
        sell_reasons.append("breakdown")

    # VWAP
    if close > vwap:
        score_buy += 20
        buy_reasons.append("above VWAP")
    elif close < vwap:
        score_sell += 20
        sell_reasons.append("below VWAP")

    # RSI quality zone, avoiding extreme chasing
    if 52 <= rsi <= 70:
        score_buy += 15
        buy_reasons.append("RSI")
    elif 30 <= rsi <= 48:
        score_sell += 15
        sell_reasons.append("RSI")

    # Volume
    if vol_ratio >= min_volume_ratio:
        if bullish:
            score_buy += 20
            buy_reasons.append("volume")
        if bearish:
            score_sell += 20
            sell_reasons.append("volume")

    # Candle confirmation
    if bullish and close > h2:
        score_buy += 10
        buy_reasons.append("candle confirmation")
    if bearish and close < l2:
        score_sell += 10
        sell_reasons.append("candle confirmation")

    # Contradiction penalties
    if fake_up:
        score_buy -= 25
    if fake_down:
        score_sell -= 25

    if close < vwap:
        score_buy -= 20
    if close > vwap:
        score_sell -= 20

    # Avoid overextended RSI
    if rsi > 75:
        score_buy -= 15
    if rsi < 25:
        score_sell -= 15

    signal = "WAIT"
    score = max(score_buy, score_sell)
    entry = sl = target = np.nan
    reason = "No complete sniper confirmation"

    if score_buy >= min_score and score_buy > score_sell:
        signal = "BUY"
        entry = close
        sl = min(l2, entry - atr * atr_sl_mult)
        # Make sure SL remains below entry.
        if sl >= entry:
            sl = entry - atr * atr_sl_mult
        target = entry + atr * atr_target_mult
        reason = ", ".join(buy_reasons)

    elif score_sell >= min_score and score_sell > score_buy:
        signal = "SELL"
        entry = close
        sl = max(h2, entry + atr * atr_sl_mult)
        if sl <= entry:
            sl = entry + atr * atr_sl_mult
        target = entry - atr * atr_target_mult
        reason = ", ".join(sell_reasons)

    return {
        "Signal": signal,
        "Score": max(0, int(score)),
        "Entry": entry,
        "SL": sl,
        "Target": target,
        "ATR": atr,
        "VWAP": vwap,
        "RSI": rsi,
        "VolRatio": vol_ratio,
        "Reason": reason,
    }


# -------------------------
# POSITION SIZING
# -------------------------
def position_size(entry, sl):
    entry = scalar(entry)
    sl = scalar(sl)

    if not np.isfinite(entry) or not np.isfinite(sl):
        return 0, 0.0, 0.0

    distance = abs(entry - sl)
    if distance <= 0:
        return 0, 0.0, 0.0

    qty_by_risk = int(risk_amount // distance)
    qty_by_capital = int(capital // entry)

    qty = max(0, min(qty_by_risk, qty_by_capital))
    capital_used = qty * entry
    actual_risk = qty * distance

    return qty, capital_used, actual_risk


# -------------------------
# FETCH ONE STOCK
# -------------------------
@st.cache_data(ttl=45, show_spinner=False)
def fetch_stock(symbol, source_name, token, interval):
    if source_name == "Upstox":
        try:
            mp = build_instrument_map()
            nse = symbol.replace(".NS", "").upper()
            key = mp.get(nse)

            if key:
                df = upstox_intraday(key)
                if df is not None and len(df) >= 20:
                    return df

                df = upstox_candles(key, "1minute")
                if df is not None and len(df) >= 20:
                    return df
        except Exception:
            pass

        # Deliberate fallback when Upstox master/data is temporarily unavailable.
        return yahoo_data(symbol, interval)

    return yahoo_data(symbol, interval)


# -------------------------
# RUN SCANNER
# -------------------------
st.subheader("3️⃣ Intraday Sniper Scanner")

if not symbols:
    st.warning("No stocks available.")
    st.stop()

st.write(f"Scanning **{len(symbols)}** stocks from the selected universe.")

if st.button("🚀 RUN SNIPER SCANNER", type="primary"):
    results = []
    progress = st.progress(0)

    for i, sym in enumerate(symbols):
        df = fetch_stock(sym, data_source, upstox_token, timeframe)

        try:
            a = analyze_intraday(df)
        except Exception as e:
            a = {
                "Signal": "WAIT",
                "Score": 0,
                "Entry": np.nan,
                "SL": np.nan,
                "Target": np.nan,
                "ATR": np.nan,
                "VWAP": np.nan,
                "RSI": np.nan,
                "VolRatio": np.nan,
                "Reason": f"Analysis error: {str(e)[:80]}",
            }

        qty, cap_used, actual_risk = position_size(a["Entry"], a["SL"])

        results.append({
            "Stock": sym.replace(".NS", ""),
            "Signal": a["Signal"],
            "Score": a["Score"],
            "Entry": round(scalar(a["Entry"]), 2) if np.isfinite(scalar(a["Entry"])) else np.nan,
            "SL": round(scalar(a["SL"]), 2) if np.isfinite(scalar(a["SL"])) else np.nan,
            "Target": round(scalar(a["Target"]), 2) if np.isfinite(scalar(a["Target"])) else np.nan,
            "ATR": round(scalar(a["ATR"]), 2) if np.isfinite(scalar(a["ATR"])) else np.nan,
            "VWAP": round(scalar(a["VWAP"]), 2) if np.isfinite(scalar(a["VWAP"])) else np.nan,
            "RSI": round(scalar(a["RSI"]), 1) if np.isfinite(scalar(a["RSI"])) else np.nan,
            "Vol Ratio": round(scalar(a["VolRatio"]), 2) if np.isfinite(scalar(a["VolRatio"])) else np.nan,
            "Qty": qty,
            "Capital Used ₹": round(cap_used, 0),
            "Risk ₹": round(actual_risk, 0),
            "Reason": a["Reason"],
        })

        progress.progress((i + 1) / len(symbols))

    result_df = pd.DataFrame(results)

    # Score first, then signal, for useful ranking.
    result_df = result_df.sort_values(
        ["Score", "Signal"],
        ascending=[False, True]
    ).reset_index(drop=True)

    st.subheader("📊 Scanner Results")

    # Smaller, readable dataframe rather than oversized cards.
    st.dataframe(
        result_df,
        use_container_width=True,
        height=520,
        hide_index=True,
    )

    qualified = result_df[
        result_df["Signal"].isin(["BUY", "SELL"])
        & (result_df["Score"] >= min_score)
        & (result_df["Qty"] > 0)
    ].head(int(max_trades))

    st.subheader("🔥 TOP SNIPER TRADES")

    if qualified.empty:
        st.warning("No stock passed all sniper filters.")
    else:
        for _, r in qualified.iterrows():
            css = "buy" if r["Signal"] == "BUY" else "sell"

            st.markdown(
                f"""
                <div class="trade-card {css}">
                    <b>{r['Stock']} — {r['Signal']} | Score {r['Score']}/100</b><br>
                    Entry: ₹{r['Entry']} &nbsp; | &nbsp;
                    SL: ₹{r['SL']} &nbsp; | &nbsp;
                    Target: ₹{r['Target']}<br>
                    ATR: ₹{r['ATR']} &nbsp; | &nbsp;
                    VWAP: ₹{r['VWAP']} &nbsp; | &nbsp;
                    RSI: {r['RSI']} &nbsp; | &nbsp;
                    Vol Ratio: {r['Vol Ratio']}<br>
                    <b>Maximum Qty: {r['Qty']}</b> &nbsp; | &nbsp;
                    Capital: ₹{r['Capital Used ₹']} &nbsp; | &nbsp;
                    Risk: ₹{r['Risk ₹']}<br>
                    {r['Reason']}
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Download current scan.
    st.download_button(
        "⬇️ Download Scanner CSV",
        result_df.to_csv(index=False).encode("utf-8"),
        file_name=f"smt_sniper_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
    )

# -------------------------
# AUTOMATIC PAGE REFRESH
# -------------------------
# This does not force a 15:15 cutoff. It simply refreshes the page
# during the session at the user-selected interval.
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(
        interval=int(refresh_seconds) * 1000,
        key="smt_sniper_refresh",
    )
except Exception:
    pass

st.caption(
    "SMT PRO SNIPER is an intraday decision-support scanner. "
    "Entry, SL, target and quantity are calculated from the current intraday data, "
    "ATR, VWAP, RSI and volume. Confirm execution and liquidity before trading."
)
'''

out = Path("/mnt/data/app.py")
req = Path("/mnt/data/requirements.txt")

compile(app_code, str(out), "exec")
out.write_text(app_code, encoding="utf-8")
req.write_text(
    "streamlit>=1.40\n"
    "pandas>=2.0\n"
    "numpy>=1.26\n"
    "requests>=2.31\n"
    "yfinance>=0.2.40\n"
    "streamlit-autorefresh>=1.0.1\n",
    encoding="utf-8",
)

print(f"Created: {out}")
print(f"Created: {req}")
print(f"app.py lines: {len(app_code.splitlines())}")
