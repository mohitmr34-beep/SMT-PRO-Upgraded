import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from urllib.parse import unquote
from datetime import time as dt_time
from zoneinfo import ZoneInfo
import time

# ============================================================
# SMT PRO SNIPER - PRODUCTION APP
# CSV CANDIDATES -> INTRADAY DATA -> VWAP/RVOL/MOMENTUM
# -> BREAKOUT/CANDLE/ATR/R:R -> 0-100 SCORE
# ============================================================

st.set_page_config(
    page_title="SMT PRO SNIPER",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .sniper-card {
        padding: 18px 20px;
        border-radius: 14px;
        margin-bottom: 12px;
        border: 1px solid rgba(255,255,255,.12);
    }
    .sniper-buy {background: linear-gradient(135deg,#087f5b,#12a66f);}
    .sniper-sell {background: linear-gradient(135deg,#a61e4d,#d6336c);}
    .watch-card {background: rgba(255,193,7,.12); border:1px solid rgba(255,193,7,.35);}
    .metric-box {
        padding: 10px;
        border-radius: 10px;
        background: rgba(127,127,127,.10);
        text-align:center;
    }
    .small-note {font-size: 12px; opacity: .75;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    "<h1 style='text-align:center;margin-bottom:0'>🎯 SMT PRO SNIPER</h1>"
    "<p style='text-align:center;opacity:.7'>Cash Segment • 9:15 IST • VWAP + RVOL + Momentum + ATR</p>",
    unsafe_allow_html=True,
)

# ============================================================
# TIME
# ============================================================

now_ist = pd.Timestamp.now(tz=IST)
today_ist = now_ist.date()

c1, c2, c3 = st.columns(3)
c1.metric("IST Time", now_ist.strftime("%H:%M:%S"))
c2.metric("Trading Date", str(today_ist))
c3.metric(
    "Market Status",
    "OPEN" if MARKET_OPEN <= now_ist.time() <= MARKET_CLOSE else "CLOSED",
)

if now_ist.time() < MARKET_OPEN:
    st.warning(
        "Market scan is locked until 9:15 AM Asia/Kolkata. "
        "The app does not use the old 9:20 AM condition."
    )

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Scanner Settings")

timeframe = st.sidebar.selectbox("Intraday timeframe", ["5m", "15m"], index=0)

score_threshold = st.sidebar.slider(
    "Sniper threshold", min_value=60, max_value=90, value=70, step=5
)

rvol_threshold = st.sidebar.slider(
    "RVOL threshold", min_value=1.0, max_value=3.0, value=1.30, step=0.10
)

atr_period = st.sidebar.number_input(
    "ATR period", min_value=5, max_value=30, value=14, step=1
)

min_rr = st.sidebar.slider(
    "Minimum R:R", min_value=1.0, max_value=4.0, value=1.50, step=0.25
)

opening_range_minutes = st.sidebar.selectbox(
    "Opening range", [5, 10, 15], index=0
)

capital = st.sidebar.number_input(
    "Trading capital ₹", min_value=1000.0, value=50000.0, step=5000.0
)

risk_pct = st.sidebar.slider(
    "Risk per trade %", min_value=0.25, max_value=3.0, value=1.0, step=0.25
)

max_trades = st.sidebar.slider(
    "Top trades", min_value=1, max_value=5, value=2
)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Score is a confluence score, not a probability of profit."
)

# ============================================================
# HELPERS
# ============================================================

def scalar(x, default=np.nan):
    """Safely convert pandas/numpy scalar-like values to float."""
    try:
        if isinstance(x, pd.Series):
            if len(x) == 0:
                return default
            x = x.iloc[0]
        if isinstance(x, pd.DataFrame):
            if x.empty:
                return default
            x = x.iloc[0, 0]
        return float(x)
    except Exception:
        return default


def normalize_ohlcv(df):
    """Make yfinance output safe for pandas calculations."""
    if df is None or df.empty:
        return None

    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        # Prefer first level: Open/High/Low/Close/Volume
        out.columns = [str(c[0]) for c in out.columns]

    out.columns = [str(c).strip().title() for c in out.columns]

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in out.columns for c in required):
        return None

    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=required).copy()

    if out.empty:
        return None

    return out


def clean_symbol(symbol):
    s = str(symbol).strip().upper()

    for suffix in [".NS", ".BO"]:
        if s.endswith(suffix):
            return s

    # Remove spaces and common CSV formatting
    s = s.replace(" ", "")

    return s + ".NS"


def unique_symbols(values):
    result = []
    seen = set()

    for value in values:
        if pd.isna(value):
            continue

        s = clean_symbol(value)

        if s not in seen:
            seen.add(s)
            result.append(s)

    return result


# ============================================================
# DATA SOURCE
# ============================================================

source = st.radio(
    "Stock universe",
    ["CSV Upload", "Chartink LIVE", "Manual Symbols"],
    horizontal=True,
)

symbols = []

if source == "CSV Upload":

    uploaded = st.file_uploader(
        "Upload your stock CSV",
        type=["csv"],
        help="CSV should contain a Symbol column.",
    )

    if uploaded is not None:
        try:
            csv_df = pd.read_csv(uploaded)
            csv_df.columns = [str(c).strip() for c in csv_df.columns]

            symbol_col = None
            for candidate in ["Symbol", "symbol", "NSECODE", "NSE Code", "Ticker"]:
                if candidate in csv_df.columns:
                    symbol_col = candidate
                    break

            if symbol_col is None:
                st.error(
                    "CSV must contain a Symbol column "
                    "(or NSECODE/Ticker)."
                )
                st.stop()

            symbols = unique_symbols(csv_df[symbol_col].tolist())

            st.success(f"{len(symbols)} candidate stocks loaded from CSV.")

            with st.expander("CSV preview"):
                st.dataframe(csv_df.head(20), use_container_width=True)

        except Exception as exc:
            st.error(f"CSV error: {exc}")
            st.stop()

    else:
        st.info("Upload the 4-day/master scanner CSV to begin.")
        st.stop()

elif source == "Manual Symbols":

    manual = st.text_area(
        "Enter NSE symbols separated by commas",
        value="RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN",
    )

    symbols = unique_symbols(manual.split(","))

else:
    st.subheader("Chartink LIVE")

    cookie = st.text_input(
        "Chartink Cookie",
        type="password",
        help="Paste the complete browser cookie string.",
    )

    # This logic mirrors the candidate-generation concept from the
    # user's Chartink setup. It is optional; CSV mode is preferred.
    chartink_clause = st.text_area(
        "Chartink scan clause",
        value=(
            "( {cash} ( "
            "( {cash} ( daily close >= daily max(252, daily high)*0.98 "
            "and daily volume > daily sma(daily volume,20)*1.5 "
            "and daily close > daily open ) ) "
            "or "
            "( {cash} ( daily high >= daily max(252, daily high) "
            "and daily close < daily open "
            "and daily volume > daily sma(daily volume,20)*1.5 ) ) "
            "or "
            "( {cash} ( daily open > 1 day ago close*1.02 "
            "and daily volume > daily sma(daily volume,20)*2 "
            "and daily close > daily open ) ) "
            ") )"
        ),
        height=130,
    )

    @st.cache_data(ttl=60, show_spinner=False)
    def fetch_chartink_symbols(cookie_value, clause):
        if not cookie_value:
            return []

        session = requests.Session()

        try:
            for part in cookie_value.split(";"):
                if "=" in part:
                    key, value = part.strip().split("=", 1)
                    session.cookies.set(
                        key,
                        value,
                        domain="chartink.com",
                    )

            session.get(
                "https://chartink.com",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )

            xsrf = unquote(
                session.cookies.get("XSRF-TOKEN", "")
            )

            headers = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://chartink.com/",
                "Content-Type": "application/json",
            }

            if xsrf:
                headers["X-XSRF-TOKEN"] = xsrf

            response = session.post(
                "https://chartink.com/screener/process",
                headers=headers,
                json={"scan_clause": clause},
                timeout=20,
            )

            if response.status_code != 200:
                return []

            payload = response.json()
            data = payload.get("data", [])

            return unique_symbols(
                [
                    row.get("nsecode")
                    for row in data
                    if isinstance(row, dict) and row.get("nsecode")
                ]
            )

        except Exception:
            return []

    if st.button("Load Chartink Stocks", type="primary"):
        loaded = fetch_chartink_symbols(cookie, chartink_clause)

        if loaded:
            st.session_state["chartink_symbols"] = loaded
            st.success(f"{len(loaded)} Chartink candidates loaded.")
        else:
            st.error(
                "No stocks fetched. Check the cookie, clause, or Chartink response."
            )

    symbols = st.session_state.get("chartink_symbols", [])

    if not symbols:
        st.info("Load Chartink stocks first.")
        st.stop()

st.write(f"**Candidate universe:** {len(symbols)} stocks")

# ============================================================
# YFINANCE DATA
# ============================================================

@st.cache_data(ttl=60, show_spinner=False)
def fetch_intraday(symbol, interval):
    try:
        df = yf.download(
            symbol,
            period="5d",
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        return normalize_ohlcv(df)
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily(symbol):
    try:
        df = yf.download(
            symbol,
            period="1y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        return normalize_ohlcv(df)
    except Exception:
        return None


# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(df, period=14):
    if df is None or len(df) < period + 1:
        return np.nan

    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period, min_periods=period).mean()

    return scalar(atr.iloc[-1])


def add_session_vwap(df):
    out = df.copy()

    if out.index.tz is None:
        idx = pd.DatetimeIndex(out.index).tz_localize("UTC")
    else:
        idx = pd.DatetimeIndex(out.index)

    idx_ist = idx.tz_convert(IST)
    out.index = idx_ist

    typical_price = (
        out["High"] + out["Low"] + out["Close"]
    ) / 3.0

    pv = typical_price * out["Volume"]

    session = pd.Series(
        idx_ist.date,
        index=out.index,
    )

    out["VWAP"] = (
        pv.groupby(session).cumsum()
        / out["Volume"].groupby(session).cumsum().replace(0, np.nan)
    )

    return out


def relative_volume(df, lookback=20):
    if df is None or len(df) < 3:
        return np.nan

    volume = pd.to_numeric(df["Volume"], errors="coerce")

    # Compare current bar with preceding bars only.
    baseline = (
        volume.shift(1)
        .rolling(lookback, min_periods=5)
        .mean()
    )

    return scalar(
        volume.iloc[-1] / baseline.iloc[-1]
        if scalar(baseline.iloc[-1], np.nan) not in [0, np.nan]
        else np.nan
    )


def previous_day_levels(daily):
    if daily is None or len(daily) < 2:
        return np.nan, np.nan, np.nan, np.nan

    prev = daily.iloc[-2]

    return (
        scalar(prev["Open"]),
        scalar(prev["High"]),
        scalar(prev["Low"]),
        scalar(prev["Close"]),
    )


# ============================================================
# SCORING
# ============================================================

def analyze_stock(intraday, daily):
    """
    Returns a diagnostic result even when the stock does not qualify.
    This is intentionally NOT an all-filters-or-discard system.
    """

    empty = {
        "Signal": "NO DATA",
        "Score": 0,
        "Entry": np.nan,
        "SL": np.nan,
        "Target": np.nan,
        "VWAP": np.nan,
        "RVOL": np.nan,
        "ATR": np.nan,
        "RR": np.nan,
        "Gap%": np.nan,
        "Reason": "Insufficient data",
        "VWAP Check": "FAIL",
        "RVOL Check": "FAIL",
        "Momentum": "FAIL",
        "Breakout": "FAIL",
        "Candle": "FAIL",
        "ATR Check": "FAIL",
        "RR Check": "FAIL",
    }

    if intraday is None or len(intraday) < 5:
        return empty

    df = add_session_vwap(intraday)

    # Use today's session if available.
    current_dates = pd.DatetimeIndex(df.index).date
    latest_date = current_dates[-1]

    day = df[current_dates == latest_date].copy()

    if len(day) == 0:
        day = df.copy()

    last = day.iloc[-1]

    close = scalar(last["Close"])
    open_price = scalar(last["Open"])
    high = scalar(last["High"])
    low = scalar(last["Low"])
    volume = scalar(last["Volume"])
    vwap = scalar(last["VWAP"])

    atr = calculate_atr(df, atr_period)

    if not np.isfinite(close):
        return empty

    # Opening range
    first_n = max(
        1,
        int(np.ceil(opening_range_minutes / 5))
    )

    opening = day.iloc[:first_n]

    opening_high = scalar(opening["High"].max())
    opening_low = scalar(opening["Low"].min())
    opening_open = scalar(opening.iloc[0]["Open"])

    # Previous day
    prev_open, prev_high, prev_low, prev_close = previous_day_levels(daily)

    gap_pct = np.nan
    if np.isfinite(prev_close) and prev_close != 0:
        gap_pct = (opening_open / prev_close - 1.0) * 100.0

    # RVOL
    volume_series = pd.to_numeric(df["Volume"], errors="coerce")
    baseline = (
        volume_series.shift(1)
        .rolling(20, min_periods=5)
        .mean()
        .iloc[-1]
    )

    rvol = np.nan
    if np.isfinite(baseline) and baseline > 0:
        rvol = volume / baseline

    # Candle body / range
    candle_range = high - low
    body = abs(close - open_price)

    body_ratio = (
        body / candle_range
        if candle_range > 0
        else 0
    )

    bullish = close > open_price
    bearish = close < open_price

    # Breakout tests
    above_opening = (
        np.isfinite(opening_high)
        and close > opening_high
    )
    below_opening = (
        np.isfinite(opening_low)
        and close < opening_low
    )

    above_prev_high = (
        np.isfinite(prev_high)
        and close > prev_high
    )
    below_prev_low = (
        np.isfinite(prev_low)
        and close < prev_low
    )

    buy_momentum = above_opening or above_prev_high
    sell_momentum = below_opening or below_prev_low

    # VWAP direction
    buy_vwap = np.isfinite(vwap) and close > vwap
    sell_vwap = np.isfinite(vwap) and close < vwap

    # Avoid chasing an extreme extension from VWAP.
    vwap_distance_pct = (
        abs(close - vwap) / close * 100
        if np.isfinite(vwap) and close != 0
        else np.nan
    )

    # ========================================================
    # Direction score
    # ========================================================

    buy_score = 0
    sell_score = 0

    # VWAP = 20
    if buy_vwap:
        buy_score += 20
    if sell_vwap:
        sell_score += 20

    # RVOL = 20
    if np.isfinite(rvol):
        if rvol >= rvol_threshold:
            buy_score += 20
            sell_score += 20
        elif rvol >= 1.0:
            buy_score += 10
            sell_score += 10

    # Opening momentum = 15
    if buy_momentum and bullish:
        buy_score += 15
    if sell_momentum and bearish:
        sell_score += 15

    # Breakout = 15
    if above_opening:
        buy_score += 15
    if below_opening:
        sell_score += 15

    # Previous day breakout = 10
    if above_prev_high:
        buy_score += 10
    if below_prev_low:
        sell_score += 10

    # Candle quality = 10
    if body_ratio >= 0.60:
        if bullish:
            buy_score += 10
        elif bearish:
            sell_score += 10
    elif body_ratio >= 0.40:
        if bullish:
            buy_score += 5
        elif bearish:
            sell_score += 5

    # ATR / volatility = 5
    atr_ok = np.isfinite(atr) and atr > 0
    if atr_ok:
        buy_score += 5
        sell_score += 5

    # Gap context = 5
    if np.isfinite(gap_pct):
        if gap_pct >= 1:
            buy_score += 5
        elif gap_pct <= -1:
            sell_score += 5

    # Determine direction.
    if buy_score > sell_score:
        direction = "BUY"
        raw_score = buy_score
    elif sell_score > buy_score:
        direction = "SELL"
        raw_score = sell_score
    else:
        direction = "WAIT"
        raw_score = max(buy_score, sell_score)

    # ========================================================
    # Risk model
    # ========================================================

    entry = close
    sl = np.nan
    target = np.nan
    rr = np.nan

    if direction == "BUY":
        structure_sl = opening_low
        if np.isfinite(prev_low):
            structure_sl = min(structure_sl, prev_low)

        if not np.isfinite(structure_sl) or structure_sl >= entry:
            structure_sl = entry - atr if atr_ok else np.nan

        sl = structure_sl

        if np.isfinite(sl) and sl < entry:
            risk_per_share = entry - sl
            target = entry + (risk_per_share * 2.0)
            rr = (
                (target - entry) / risk_per_share
                if risk_per_share > 0
                else np.nan
            )

    elif direction == "SELL":
        structure_sl = opening_high
        if np.isfinite(prev_high):
            structure_sl = max(structure_sl, prev_high)

        if not np.isfinite(structure_sl) or structure_sl <= entry:
            structure_sl = entry + atr if atr_ok else np.nan

        sl = structure_sl

        if np.isfinite(sl) and sl > entry:
            risk_per_share = sl - entry
            target = entry - (risk_per_share * 2.0)
            rr = (
                (entry - target) / risk_per_share
                if risk_per_share > 0
                else np.nan
            )

    # Risk/reward score is a gate, but not a reason to erase the stock.
    rr_ok = np.isfinite(rr) and rr >= min_rr

    if rr_ok:
        raw_score += 5

    # Penalize extreme VWAP chase.
    if np.isfinite(vwap_distance_pct) and vwap_distance_pct > 5:
        raw_score -= 10

    final_score = int(max(0, min(100, raw_score)))

    # ========================================================
    # Diagnostic labels
    # ========================================================

    vwap_check = (
        "PASS" if (buy_vwap if direction == "BUY"
                   else sell_vwap if direction == "SELL"
                   else False)
        else "FAIL"
    )

    rvol_check = (
        "PASS" if np.isfinite(rvol) and rvol >= rvol_threshold
        else "WATCH"
    )

    momentum_check = (
        "PASS"
        if (
            (direction == "BUY" and buy_momentum)
            or (direction == "SELL" and sell_momentum)
        )
        else "FAIL"
    )

    breakout_check = (
        "PASS"
        if (
            (direction == "BUY" and above_opening)
            or (direction == "SELL" and below_opening)
        )
        else "WATCH"
    )

    candle_check = "PASS" if body_ratio >= 0.60 else "WATCH"

    atr_check = "PASS" if atr_ok else "FAIL"
    rr_check = "PASS" if rr_ok else "FAIL"

    # Status
    if final_score >= 80 and rr_ok and vwap_check == "PASS":
        status = "🔥 SNIPER"
    elif final_score >= 70 and rr_ok:
        status = "🟢 HIGH QUALITY"
    elif final_score >= 60:
        status = "🟡 WATCH"
    else:
        status = "⚪ WAIT"

    reasons = []

    if vwap_check == "FAIL":
        reasons.append("VWAP")
    if rvol_check == "FAIL":
        reasons.append("RVOL")
    if momentum_check == "FAIL":
        reasons.append("Momentum")
    if breakout_check == "FAIL":
        reasons.append("Breakout")
    if candle_check == "FAIL":
        reasons.append("Candle")
    if rr_check == "FAIL":
        reasons.append("R:R")

    reason = (
        "All major checks aligned"
        if not reasons
        else "Weak: " + ", ".join(reasons)
    )

    return {
        "Signal": direction,
        "Score": final_score,
        "Status": status,
        "Entry": entry,
        "SL": sl,
        "Target": target,
        "VWAP": vwap,
        "RVOL": rvol,
        "ATR": atr,
        "RR": rr,
        "Gap%": gap_pct,
        "VWAP Check": vwap_check,
        "RVOL Check": rvol_check,
        "Momentum": momentum_check,
        "Breakout": breakout_check,
        "Candle": candle_check,
        "ATR Check": atr_check,
        "RR Check": rr_check,
        "Reason": reason,
    }


# ============================================================
# POSITION SIZE
# ============================================================

def calculate_position(entry, sl):
    if not np.isfinite(entry) or not np.isfinite(sl):
        return 0, 0.0, 0.0

    distance = abs(entry - sl)

    if distance <= 0:
        return 0, 0.0, 0.0

    risk_amount = capital * risk_pct / 100.0

    qty_by_risk = int(risk_amount / distance)
    qty_by_capital = int(capital / entry)

    qty = max(0, min(qty_by_risk, qty_by_capital))

    capital_used = qty * entry
    actual_risk = qty * distance

    return qty, capital_used, actual_risk


# ============================================================
# SCANNER
# ============================================================

run = st.button(
    "🚀 RUN SNIPER SCANNER",
    type="primary",
    use_container_width=True,
)

if run:

    if now_ist.time() < MARKET_OPEN:
        st.warning(
            "The scanner is configured for 9:15 AM Asia/Kolkata. "
            "Run it at/after 9:15 AM."
        )

    results = []
    progress = st.progress(0)
    status_text = st.empty()

    total = len(symbols)

    for i, symbol in enumerate(symbols, start=1):

        status_text.text(
            f"Scanning {symbol}  |  {i}/{total}"
        )

        intraday = fetch_intraday(symbol, timeframe)
        daily = fetch_daily(symbol)

        analysis = analyze_stock(intraday, daily)

        qty, capital_used, actual_risk = calculate_position(
            analysis["Entry"],
            analysis["SL"],
        )

        analysis["Stock"] = symbol
        analysis["Qty"] = qty
        analysis["Capital Used"] = capital_used
        analysis["Risk ₹"] = actual_risk

        # 10% / 20% monitoring levels
        entry = analysis["Entry"]
        if np.isfinite(entry):
            if analysis["Signal"] == "BUY":
                analysis["+10%"] = entry * 1.10
                analysis["+20%"] = entry * 1.20
                analysis["-10%"] = entry * 0.90
                analysis["-20%"] = entry * 0.80
            elif analysis["Signal"] == "SELL":
                analysis["+10%"] = entry * 0.90
                analysis["+20%"] = entry * 0.80
                analysis["-10%"] = entry * 1.10
                analysis["-20%"] = entry * 1.20
            else:
                analysis["+10%"] = np.nan
                analysis["+20%"] = np.nan
                analysis["-10%"] = np.nan
                analysis["-20%"] = np.nan
        else:
            analysis["+10%"] = np.nan
            analysis["+20%"] = np.nan
            analysis["-10%"] = np.nan
            analysis["-20%"] = np.nan

        results.append(analysis)

        progress.progress(i / total)

    status_text.empty()
    progress.empty()

    result_df = pd.DataFrame(results)

    # Store in session so refresh doesn't erase the last scan.
    st.session_state["last_results"] = result_df

# ============================================================
# DISPLAY
# ============================================================

if "last_results" in st.session_state:

    result_df = st.session_state["last_results"].copy()

    # Sort by score first.
    result_df = result_df.sort_values(
        ["Score", "RR"],
        ascending=[False, False],
        na_position="last",
    )

    st.markdown("## 📊 Scanner Diagnostics")

    st.caption(
        "Every candidate is displayed. This prevents the old problem where "
        "strict filters silently removed the entire universe."
    )

    display_cols = [
        "Stock",
        "Signal",
        "Score",
        "Status",
        "Entry",
        "SL",
        "Target",
        "VWAP",
        "RVOL",
        "ATR",
        "RR",
        "Gap%",
        "VWAP Check",
        "RVOL Check",
        "Momentum",
        "Breakout",
        "Candle",
        "ATR Check",
        "RR Check",
        "Reason",
        "Qty",
        "Capital Used",
        "Risk ₹",
    ]

    available_cols = [
        c for c in display_cols if c in result_df.columns
    ]

    formatted = result_df[available_cols].copy()

    numeric_cols = [
        "Entry",
        "SL",
        "Target",
        "VWAP",
        "RVOL",
        "ATR",
        "RR",
        "Gap%",
        "+10%",
        "+20%",
        "-10%",
        "-20%",
        "Capital Used",
        "Risk ₹",
    ]

    for col in numeric_cols:
        if col in formatted.columns:
            formatted[col] = pd.to_numeric(
                formatted[col],
                errors="coerce",
            ).round(2)

    st.dataframe(
        formatted,
        use_container_width=True,
        height=520,
        hide_index=True,
    )

    # ========================================================
    # TOP SNIPER TRADES
    # ========================================================

    eligible = result_df[
        (result_df["Signal"].isin(["BUY", "SELL"]))
        & (result_df["Score"] >= score_threshold)
        & (result_df["RR"] >= min_rr)
        & (result_df["VWAP Check"] == "PASS")
    ].copy()

    eligible = eligible.sort_values(
        ["Score", "RR", "RVOL"],
        ascending=[False, False, False],
        na_position="last",
    ).head(max_trades)

    st.markdown("## 🔥 TOP SNIPER TRADES")

    if eligible.empty:

        best_watch = result_df[
            result_df["Signal"].isin(["BUY", "SELL"])
        ].head(5)

        st.warning(
            "No stock passed the final Sniper execution gate. "
            "This does NOT mean no stock has momentum. "
            "See the diagnostic table below and the Watchlist."
        )

        if not best_watch.empty:
            st.markdown("### 🟡 Best Watchlist Candidates")

            for _, row in best_watch.iterrows():
                st.markdown(
                    f"""
                    <div class="sniper-card watch-card">
                    <b>{row['Stock']}</b>
                    &nbsp; | &nbsp; {row['Signal']}
                    &nbsp; | &nbsp; Score {int(row['Score'])}/100
                    <br><br>
                    VWAP: {row['VWAP Check']}
                    &nbsp; | &nbsp;
                    RVOL: {row['RVOL Check']}
                    &nbsp; | &nbsp;
                    Breakout: {row['Breakout']}
                    &nbsp; | &nbsp;
                    R:R: {row['RR Check']}
                    <br>
                    Reason: {row['Reason']}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    else:

        cols = st.columns(len(eligible))

        for col, (_, row) in zip(cols, eligible.iterrows()):

            direction = row["Signal"]

            css_class = (
                "sniper-buy"
                if direction == "BUY"
                else "sniper-sell"
            )

            def money(value):
                return (
                    f"₹{value:,.2f}"
                    if np.isfinite(scalar(value))
                    else "-"
                )

            entry = scalar(row["Entry"])
            sl = scalar(row["SL"])
            target = scalar(row["Target"])
            vwap = scalar(row["VWAP"])
            qty = int(row["Qty"])
            risk = scalar(row["Risk ₹"])

            col.markdown(
                f"""
                <div class="sniper-card {css_class}">
                <h2 style="margin:0">{row['Stock']}</h2>
                <h3>{direction} | Score {int(row['Score'])}/100</h3>
                <hr>
                <b>ENTRY:</b> {money(entry)}<br>
                <b>STOP LOSS:</b> {money(sl)}<br>
                <b>TARGET:</b> {money(target)}<br>
                <b>VWAP:</b> {money(vwap)}<br>
                <b>RVOL:</b> {scalar(row['RVOL'], 0):.2f}x<br>
                <b>R:R:</b> {scalar(row['RR'], 0):.2f}<br>
                <b>QTY:</b> {qty}<br>
                <b>RISK:</b> ₹{risk:,.0f}
                </div>
                """,
                unsafe_allow_html=True,
            )

    # ========================================================
    # WHY EACH STOCK PASSED / FAILED
    # ========================================================

    with st.expander("🔍 Filter-by-filter diagnosis", expanded=False):

        diag_cols = [
            "Stock",
            "Signal",
            "Score",
            "VWAP Check",
            "RVOL Check",
            "Momentum",
            "Breakout",
            "Candle",
            "ATR Check",
            "RR Check",
            "Reason",
        ]

        st.dataframe(
            result_df[diag_cols],
            use_container_width=True,
            hide_index=True,
        )

    # ========================================================
    # 20% MONITORING
    # ========================================================

    with st.expander("📈 10% / 20% movement levels", expanded=False):

        move_cols = [
            "Stock",
            "Signal",
            "Entry",
            "+10%",
            "+20%",
            "-10%",
            "-20%",
            "Score",
        ]

        st.dataframe(
            result_df[move_cols],
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "10%/20% levels are monitoring/reference levels, not guaranteed targets."
        )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    csv_output = result_df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "⬇️ Download Full Scanner Results",
        data=csv_output,
        file_name=f"smt_sniper_{today_ist}.csv",
        mime="text/csv",
        use_container_width=True,
    )

else:
    st.info(
        "Upload your CSV, then click **RUN SNIPER SCANNER**. "
        "The app will fetch current intraday data for the CSV symbols."
    )

# ============================================================
# AUTO REFRESH
# ============================================================

auto_refresh = st.sidebar.checkbox(
    "Auto refresh every 5 minutes",
    value=False,
)

if auto_refresh:
    time.sleep(300)
    st.rerun()

st.markdown("---")
st.caption(
    "SMT PRO SNIPER | Asia/Kolkata | Cash segment | "
    "Educational use only. Confirm live price, liquidity and risk before trading."
)
