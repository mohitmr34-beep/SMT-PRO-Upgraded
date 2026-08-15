import streamlit as st
import pandas as pd
import numpy as np
import requests
import io
import time
from datetime import datetime, time as dtime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

# ============================================================
# CONFIG
# ============================================================
st.set_page_config(
    page_title="SMT PRO SNIPER - NSE ALL STOCKS",
    page_icon="🎯",
    layout="wide",
)

IST = ZoneInfo("Asia/Kolkata")
st.markdown(
    """
    <style>
    .sniper-card {
        padding:18px; border-radius:14px; margin:10px 0;
        color:white; box-shadow:0 3px 12px rgba(0,0,0,.18);
    }
    .big {font-size:28px;font-weight:800;}
    .metric {font-size:17px;margin-top:6px;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🎯 SMT PRO SNIPER")
st.caption(
    "NSE all-equity intraday scanner | Chartink discovery → Upstox data → "
    "VWAP + RSI + RVOL + structure + ATR + contradiction filter"
)

# ============================================================
# SECRETS / UPSTOX
# ============================================================
def get_secret_token():
    for key in ("UPSTOX_ACCESS_TOKEN", "UPSTOX_TOKEN", "UPSTOX_ACCESS"):
        try:
            value = st.secrets.get(key, "")
            if value:
                return str(value).strip()
        except Exception:
            pass
    return ""

UPSTOX_TOKEN = get_secret_token()

# ============================================================
# SETTINGS
# ============================================================
st.sidebar.header("⚙️ Scanner Settings")

capital = st.sidebar.number_input(
    "Capital (₹)", min_value=1000.0, value=50000.0, step=1000.0
)
risk_pct = st.sidebar.slider(
    "Maximum risk / trade (%)", 0.25, 3.0, 1.0, 0.25
)
max_risk = capital * risk_pct / 100.0

min_score = st.sidebar.slider("Minimum sniper score", 50, 90, 70)
max_symbols = st.sidebar.number_input(
    "Maximum Chartink candidates to process",
    min_value=10, max_value=1000, value=300, step=10
)
min_rvol = st.sidebar.slider("Minimum RVOL", 0.8, 3.0, 1.2, 0.1)
min_rr = st.sidebar.slider("Minimum R:R", 1.2, 3.0, 1.5, 0.1)

st.sidebar.subheader("Intraday rules")
market_start = dtime(9, 15)
analysis_start = dtime(9, 16)
avoid_after = dtime(15, 15)
atr_period = st.sidebar.number_input(
    "ATR period (5-min)", min_value=5, max_value=30, value=14
)

auto_refresh = st.sidebar.checkbox("Auto refresh every 60 seconds", True)

# ============================================================
# TIME
# ============================================================
def now_ist():
    return datetime.now(IST)

now = now_ist()
current_time = now.time()

if current_time < market_start:
    st.warning(
        f"Market scanner starts at 09:15 IST. Current time: "
        f"{now:%H:%M:%S} IST"
    )
elif current_time >= avoid_after:
    st.info("New intraday entries are disabled after 15:15 IST.")

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=60_000, key="smt_sniper_60s")
    except ImportError:
        st.warning(
            "Add `streamlit-autorefresh` to requirements.txt for "
            "automatic 60-second refresh."
        )

# ============================================================
# SAFE HELPERS
# ============================================================
def clean_symbol(x):
    s = str(x).strip().upper()
    for suffix in (".NS", "-EQ", ".NSE"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.replace(" ", "")

def fnum(x, default=np.nan):
    try:
        if isinstance(x, pd.Series):
            if len(x) == 0:
                return default
            x = x.iloc[-1]
        if isinstance(x, (list, tuple, np.ndarray)):
            if len(x) == 0:
                return default
            x = x[-1]
        return float(x)
    except Exception:
        return default

def normalize_ohlcv(df):
    if df is None or len(df) == 0:
        return pd.DataFrame()

    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [
            str(c[0] if isinstance(c, tuple) else c)
            for c in out.columns
        ]

    rename = {}
    for c in out.columns:
        k = str(c).strip().lower()
        mapping = {
            "timestamp": "Timestamp",
            "time": "Timestamp",
            "datetime": "Timestamp",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "vol": "Volume",
        }
        if k in mapping:
            rename[c] = mapping[k]

    out = out.rename(columns=rename)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in out.columns for c in needed):
        return pd.DataFrame()

    for c in needed:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out = out.dropna(subset=needed).copy()

    if "Timestamp" in out.columns:
        out["Timestamp"] = pd.to_datetime(
            out["Timestamp"], errors="coerce"
        )
        out = out.dropna(subset=["Timestamp"])
        try:
            if out["Timestamp"].dt.tz is None:
                out["Timestamp"] = out["Timestamp"].dt.tz_localize(IST)
            else:
                out["Timestamp"] = out["Timestamp"].dt.tz_convert(IST)
        except Exception:
            pass

    return out.reset_index(drop=True)

# ============================================================
# CHARTINK DISCOVERY
# ============================================================
st.subheader("1️⃣ Stock Discovery")

discovery_mode = st.radio(
    "Candidate source",
    ["Chartink Cookie", "Manual CSV / Symbols"],
    horizontal=True,
)

def chartink_fetch(cookie):
    if not cookie:
        return []

    session = requests.Session()
    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    try:
        session.get(
            "https://chartink.com/",
            headers=base_headers,
            timeout=15,
        )

        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                session.cookies.set(
                    k.strip(), v.strip(), domain="chartink.com"
                )

        xsrf = unquote(session.cookies.get("XSRF-TOKEN", ""))
        headers = dict(base_headers)
        headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://chartink.com/",
                "Content-Type": "application/json",
            }
        )
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf

        # Candidate discovery only. Final trade decision is NOT based
        # on this daily filter.
        clause = """
        (
          {cash}
          (
            (
              {cash}
              (
                daily close >= daily max(252, daily high)*0.98
                and daily volume > daily sma(daily volume,20)*1.5
                and daily close > daily open
              )
            )
            or
            (
              {cash}
              (
                daily high >= daily max(252, daily high)
                and daily close < daily open
                and daily volume > daily sma(daily volume,20)*1.5
              )
            )
            or
            (
              {cash}
              (
                daily open > 1 day ago close*1.02
                and daily volume > daily sma(daily volume,20)*2
                and daily close > daily open
              )
            )
          )
        )
        """

        r = session.post(
            "https://chartink.com/screener/process",
            headers=headers,
            json={"scan_clause": " ".join(clause.split())},
            timeout=20,
        )

        if r.status_code != 200:
            return []

        payload = r.json()
        data = payload.get("data", [])
        symbols = []

        for row in data:
            code = row.get("nsecode")
            if code:
                code = clean_symbol(code)
                if code and code not in symbols:
                    symbols.append(code)

        return symbols
    except Exception:
        return []

if discovery_mode == "Chartink Cookie":
    cookie = st.text_input(
        "Chartink browser cookie",
        type="password",
        help="Used only to discover candidate stocks.",
    )

    c1, c2 = st.columns(2)
    with c1:
        manual_refresh = st.button(
            "🔄 Refresh Chartink Now",
            use_container_width=True,
        )
    with c2:
        clear_candidates = st.button(
            "🗑️ Clear Candidates",
            use_container_width=True,
        )

    if clear_candidates:
        st.session_state.pop("chartink_symbols", None)
        st.rerun()

    if manual_refresh:
        st.cache_data.clear()

    if cookie:
        with st.spinner("Fetching latest Chartink candidates..."):
            found = chartink_fetch(cookie)

        if found:
            st.session_state["chartink_symbols"] = found
            st.success(f"{len(found)} Chartink candidates loaded.")
        elif "chartink_symbols" not in st.session_state:
            st.error(
                "Chartink returned no candidates. Check cookie/scanner access."
            )

    symbols = st.session_state.get("chartink_symbols", [])

else:
    uploaded = st.file_uploader(
        "Upload CSV containing a Symbol column",
        type=["csv"],
    )
    manual_text = st.text_area(
        "Or enter NSE symbols separated by commas",
        value="RELIANCE,SBIN,INFY,TCS,HDFCBANK,ICICIBANK",
    )

    symbols = []
    if uploaded:
        try:
            csvdf = pd.read_csv(uploaded)
            csvdf.columns = [str(c).strip() for c in csvdf.columns]
            if "Symbol" not in csvdf.columns:
                st.error("CSV must contain a `Symbol` column.")
            else:
                symbols.extend(
                    clean_symbol(x)
                    for x in csvdf["Symbol"].dropna()
                )
        except Exception as exc:
            st.error(f"CSV error: {exc}")

    symbols.extend(
        clean_symbol(x)
        for x in manual_text.split(",")
        if clean_symbol(x)
    )

symbols = list(dict.fromkeys([s for s in symbols if s]))[: int(max_symbols)]

st.info(
    f"Candidates available: {len(symbols)} | "
    f"Universe restriction: NONE — NSE equity candidates"
)

if not symbols:
    st.stop()

# ============================================================
# UPSTOX INSTRUMENT MASTER
# ============================================================
st.subheader("2️⃣ Upstox Data Engine")

@st.cache_data(ttl=86400, show_spinner=False)
def load_instrument_master():
    urls = [
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz",
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.csv.gz",
    ]

    last_error = None
    for url in urls:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return pd.read_csv(io.BytesIO(r.content))
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        f"Unable to download Upstox instrument master: {last_error}"
    )

def build_equity_map(master):
    df = master.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Upstox has used instrument_type / segment naming across versions.
    segment = (
        df.get("segment", pd.Series("", index=df.index))
        .astype(str)
        .str.upper()
    )
    instrument_type = (
        df.get("instrument_type", pd.Series("", index=df.index))
        .astype(str)
        .str.upper()
    )
    exchange = (
        df.get("exchange", pd.Series("", index=df.index))
        .astype(str)
        .str.upper()
    )

    mask = (
        segment.str.contains("NSE_EQ", na=False)
        | (
            exchange.eq("NSE")
            & instrument_type.isin(["EQ", "EQUITY"])
        )
    )
    eq = df[mask].copy()

    if "trading_symbol" not in eq.columns:
        raise RuntimeError("Upstox master lacks trading_symbol.")

    key_col = "instrument_key"
    if key_col not in eq.columns:
        raise RuntimeError("Upstox master lacks instrument_key.")

    result = {}
    for _, row in eq.iterrows():
        sym = clean_symbol(row.get("trading_symbol", ""))
        key = str(row.get(key_col, "")).strip()
        if sym and key:
            result[sym] = key

    return result

try:
    master = load_instrument_master()
    symbol_map = build_equity_map(master)
except Exception as exc:
    st.error(str(exc))
    st.stop()

valid_symbols = [s for s in symbols if s in symbol_map]
missing = [s for s in symbols if s not in symbol_map]

if missing:
    st.caption(
        "Not mapped in Upstox NSE equity master: "
        + ", ".join(missing[:25])
    )

if not valid_symbols:
    st.error("No selected stocks could be mapped to Upstox NSE equity.")
    st.stop()

if not UPSTOX_TOKEN:
    st.error(
        "UPSTOX_ACCESS_TOKEN is missing. Add it to "
        ".streamlit/secrets.toml."
    )
    st.stop()

# ============================================================
# UPSTOX V3 1-MINUTE DATA
# ============================================================
def upstox_headers():
    return {
        "Authorization": f"Bearer {UPSTOX_TOKEN}",
        "Accept": "application/json",
    }

@st.cache_data(ttl=55, show_spinner=False)
def get_intraday_1m(instrument_key):
    url = (
        "https://api.upstox.com/v3/historical-candle/intraday/"
        f"{instrument_key}/1minute"
    )
    try:
        r = requests.get(
            url,
            headers=upstox_headers(),
            timeout=15,
        )
        if r.status_code != 200:
            return pd.DataFrame()

        payload = r.json()
        candles = payload.get("data", {}).get("candles", [])
        if not candles:
            return pd.DataFrame()

        # V3 candle format:
        # [timestamp, open, high, low, close, volume, oi]
        rows = []
        for c in candles:
            if len(c) < 6:
                continue
            rows.append(
                {
                    "Timestamp": c[0],
                    "Open": c[1],
                    "High": c[2],
                    "Low": c[3],
                    "Close": c[4],
                    "Volume": c[5],
                }
            )

        return normalize_ohlcv(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()

def get_5m_from_1m(one):
    if one.empty:
        return pd.DataFrame()

    x = one.copy()
    x["Timestamp"] = pd.to_datetime(
        x["Timestamp"], errors="coerce"
    )
    x = x.dropna(subset=["Timestamp"])

    if x["Timestamp"].dt.tz is None:
        x["Timestamp"] = x["Timestamp"].dt.tz_localize(IST)
    else:
        x["Timestamp"] = x["Timestamp"].dt.tz_convert(IST)

    x = x.set_index("Timestamp").sort_index()

    agg = x.resample("5min", label="left", closed="left").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    return agg.dropna().reset_index()

# ============================================================
# INDICATORS
# ============================================================
def add_indicators(df):
    x = df.copy()
    if x.empty:
        return x

    prev_close = x["Close"].shift(1)
    tr = pd.concat(
        [
            x["High"] - x["Low"],
            (x["High"] - prev_close).abs(),
            (x["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["ATR"] = tr.rolling(int(atr_period), min_periods=int(atr_period)).mean()

    typical = (x["High"] + x["Low"] + x["Close"]) / 3
    volume = x["Volume"].fillna(0)
    cumulative_volume = volume.cumsum()
    x["VWAP"] = (
        (typical * volume).cumsum()
        / cumulative_volume.replace(0, np.nan)
    )

    delta = x["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["RSI"] = 100 - (100 / (1 + rs))

    x["RVOL"] = (
        x["Volume"]
        / x["Volume"].rolling(20, min_periods=5).mean().replace(0, np.nan)
    )

    x["EMA9"] = x["Close"].ewm(span=9, adjust=False).mean()
    x["EMA20"] = x["Close"].ewm(span=20, adjust=False).mean()

    return x

# ============================================================
# CANDLE / STRUCTURE HELPERS
# ============================================================
def candle_features(row):
    o = fnum(row["Open"])
    h = fnum(row["High"])
    l = fnum(row["Low"])
    c = fnum(row["Close"])

    rng = max(h - l, 1e-9)
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l

    return {
        "range": rng,
        "body": body,
        "body_ratio": body / rng,
        "upper_ratio": upper / rng,
        "lower_ratio": lower / rng,
        "bull": c > o,
        "bear": c < o,
        "close_position": (c - l) / rng,
    }

def position_size(entry, sl):
    if not np.isfinite(entry) or not np.isfinite(sl):
        return 0, 0.0, 0.0

    risk_per_share = abs(entry - sl)
    if risk_per_share <= 0:
        return 0, 0.0, 0.0

    qty_by_risk = int(max_risk / risk_per_share)
    qty_by_capital = int(capital / entry)
    qty = max(0, min(qty_by_risk, qty_by_capital))

    return qty, qty * entry, qty * risk_per_share

# ============================================================
# SNIPER ANALYSIS — STOCK SPECIFIC, NO NIFTY DEPENDENCY
# ============================================================
def analyze_stock(symbol, one_min):
    if one_min.empty or len(one_min) < 30:
        return {
            "Stock": symbol, "Signal": "WAIT", "Score": 0,
            "Reason": "Insufficient 1-minute data"
        }

    x = get_5m_from_1m(one_min)
    if len(x) < 25:
        return {
            "Stock": symbol, "Signal": "WAIT", "Score": 0,
            "Reason": "Insufficient 5-minute structure"
        }

    x = add_indicators(x).dropna(subset=["ATR", "VWAP", "RSI"])
    if len(x) < 8:
        return {
            "Stock": symbol, "Signal": "WAIT", "Score": 0,
            "Reason": "Indicators not ready"
        }

    # Use the most recent completed 5-minute candle.
    c3 = x.iloc[-1]
    c2 = x.iloc[-2]
    c1 = x.iloc[-3]

    price = fnum(c3["Close"])
    vwap = fnum(c3["VWAP"])
    rsi = fnum(c3["RSI"])
    atr = fnum(c3["ATR"])
    rvol = fnum(c3["RVOL"])

    cf = candle_features(c3)

    # Recent stock-specific structure.
    prior_high = fnum(x["High"].iloc[-6:-1].max())
    prior_low = fnum(x["Low"].iloc[-6:-1].min())

    ema9 = fnum(c3["EMA9"])
    ema20 = fnum(c3["EMA20"])
    prev_ema9 = fnum(c2["EMA9"])

    vwap_rising = vwap > fnum(c2["VWAP"])
    vwap_falling = vwap < fnum(c2["VWAP"])

    higher_structure = (
        fnum(c3["High"]) >= fnum(c2["High"])
        and fnum(c3["Low"]) >= fnum(c2["Low"])
    )
    lower_structure = (
        fnum(c3["High"]) <= fnum(c2["High"])
        and fnum(c3["Low"]) <= fnum(c2["Low"])
    )

    breakout_up = price > prior_high
    breakout_down = price < prior_low

    bullish = cf["bull"] and cf["close_position"] >= 0.65
    bearish = cf["bear"] and cf["close_position"] <= 0.35

    # Strong rejection wick = contradiction for a fresh breakout.
    bad_upper = cf["upper_ratio"] > 0.45
    bad_lower = cf["lower_ratio"] > 0.45

    # ========================================================
    # BUY
    # ========================================================
    buy_score = 0
    buy_reasons = []

    if price > vwap:
        buy_score += 20
        buy_reasons.append("above VWAP")
    else:
        buy_reasons -= 25

    if vwap_rising:
        buy_score += 10
        buy_reasons.append("VWAP rising")
    else:
        buy_score -= 15

    if ema9 > ema20 and ema9 >= prev_ema9:
        buy_score += 15
        buy_reasons.append("5m trend up")

    if breakout_up:
        buy_score += 15
        buy_reasons.append("level breakout")

    if rvol >= min_rvol:
        buy_score += 15
        buy_reasons.append(f"RVOL {rvol:.1f}x")
    else:
        buy_score -= 10

    if 52 <= rsi <= 68:
        buy_score += 10
        buy_reasons.append(f"RSI {rsi:.0f}")
    elif rsi > 70:
        buy_score -= 25
    elif rsi < 35:
        buy_score -= 10

    if bullish:
        buy_score += 10
        buy_reasons.append("strong candle")

    if higher_structure:
        buy_score += 5

    # ========================================================
    # SELL
    # ========================================================
    sell_score = 0
    sell_reasons = []

    if price < vwap:
        sell_score += 20
        sell_reasons.append("below VWAP")
    else:
        sell_score -= 25

    if vwap_falling:
        sell_score += 10
        sell_reasons.append("VWAP falling")
    else:
        sell_score -= 15

    if ema9 < ema20 and ema9 <= prev_ema9:
        sell_score += 15
        sell_reasons.append("5m trend down")

    if breakout_down:
        sell_score += 15
        sell_reasons.append("level breakdown")

    if rvol >= min_rvol:
        sell_score += 15
        sell_reasons.append(f"RVOL {rvol:.1f}x")
    else:
        sell_score -= 10

    if 32 <= rsi <= 48:
        sell_score += 10
        sell_reasons.append(f"RSI {rsi:.0f}")
    elif rsi < 30:
        sell_score -= 25
    elif rsi > 65:
        sell_score -= 10

    if bearish:
        sell_score += 10
        sell_reasons.append("strong candle")

    if lower_structure:
        sell_score += 5

    # ========================================================
    # CONTRADICTION FILTERS
    # ========================================================
    buy_contradiction = (
        price <= vwap
        or rsi >= 70
        or vwap_falling
        or bad_upper
    )
    sell_contradiction = (
        price >= vwap
        or rsi <= 30
        or vwap_rising
        or bad_lower
    )

    # Avoid very tiny or abnormally huge ATR.
    candle_range = fnum(c3["High"]) - fnum(c3["Low"])
    if atr <= 0 or candle_range <= 0:
        return {
            "Stock": symbol, "Signal": "WAIT", "Score": 0,
            "Reason": "Invalid ATR/range"
        }

    if candle_range > 2.5 * atr:
        buy_score -= 15
        sell_score -= 15

    # ========================================================
    # ATR STRUCTURE SL / TARGET
    # ========================================================
    signal = "WAIT"
    score = max(0, int(max(buy_score, sell_score)))
    entry = sl = target = np.nan
    reason = ""

    if buy_score >= min_score and not buy_contradiction:
        # Structure SL, but reject if it is too wide.
        raw_sl = min(fnum(c2["Low"]), fnum(c1["Low"]))
        risk_distance = price - raw_sl

        if (
            risk_distance >= 0.7 * atr
            and risk_distance <= 1.8 * atr
            and raw_sl < price
        ):
            signal = "BUY"
            entry = price
            sl = raw_sl
            target = entry + 2.0 * risk_distance
            reason = " + ".join(buy_reasons)

    elif sell_score >= min_score and not sell_contradiction:
        raw_sl = max(fnum(c2["High"]), fnum(c1["High"]))
        risk_distance = raw_sl - price

        if (
            risk_distance >= 0.7 * atr
            and risk_distance <= 1.8 * atr
            and raw_sl > price
        ):
            signal = "SELL"
            entry = price
            sl = raw_sl
            target = entry - 2.0 * risk_distance
            reason = " + ".join(sell_reasons)

    if signal == "WAIT":
        if buy_score >= sell_score:
            score = max(0, int(buy_score))
            reason = "BUY rejected by contradiction/ATR-quality filter"
        else:
            score = max(0, int(sell_score))
            reason = "SELL rejected by contradiction/ATR-quality filter"

    risk_share = abs(entry - sl) if signal != "WAIT" else np.nan
    rr = (
        abs(target - entry) / risk_share
        if signal != "WAIT" and risk_share > 0
        else np.nan
    )

    if signal != "WAIT" and rr < min_rr:
        signal = "WAIT"
        reason = "R:R below minimum"
        entry = sl = target = np.nan
        risk_share = rr = np.nan

    qty, cap_used, actual_risk = position_size(entry, sl)

    # If quantity is zero, don't issue a trade.
    if signal != "WAIT" and qty <= 0:
        signal = "WAIT"
        reason = "Quantity is zero under capital/risk limits"
        entry = sl = target = np.nan
        risk_share = rr = np.nan
        cap_used = actual_risk = 0

    return {
        "Stock": symbol,
        "Signal": signal,
        "Score": int(max(0, score)),
        "Reason": reason,
        "LTP": price,
        "VWAP": vwap,
        "RSI": rsi,
        "ATR(5m)": atr,
        "RVOL": rvol,
        "Entry": entry,
        "SL": sl,
        "Target": target,
        "Risk/Share": risk_share,
        "Qty": int(qty),
        "Capital Used": cap_used,
        "Actual Risk": actual_risk,
        "R:R": rr,
    }

# ============================================================
# SCAN
# ============================================================
st.subheader("3️⃣ Intraday Sniper Scan")

scan_button = st.button(
    "🚀 RUN / REFRESH SNIPER",
    type="primary",
    use_container_width=True,
)

if scan_button or auto_refresh:
    results = []

    progress = st.progress(0)
    total = len(valid_symbols)

    for i, symbol in enumerate(valid_symbols, start=1):
        candles = get_intraday_1m(symbol_map[symbol])
        row = analyze_stock(symbol, candles)
        results.append(row)
        progress.progress(i / total)

    progress.empty()

    result_df = pd.DataFrame(results)

    if not result_df.empty:
        result_df = result_df.sort_values(
            ["Signal", "Score"],
            ascending=[True, False],
        )

        show_cols = [
            "Stock", "Signal", "Score", "Reason", "LTP",
            "VWAP", "RSI", "ATR(5m)", "RVOL",
            "Entry", "SL", "Target", "Risk/Share",
            "Qty", "Capital Used", "Actual Risk", "R:R",
        ]
        show_cols = [c for c in show_cols if c in result_df.columns]

        st.dataframe(
            result_df[show_cols],
            use_container_width=True,
            hide_index=True,
        )

        trades = result_df[
            (result_df["Signal"].isin(["BUY", "SELL"]))
            & (result_df["Score"] >= min_score)
        ].sort_values("Score", ascending=False)

        st.subheader("🔥 TOP QUALITY INTRADAY TRADES")

        if trades.empty:
            st.warning(
                "No stock passed all sniper filters. This is intentional: "
                "WAIT is preferred over a low-quality trade."
            )
        else:
            for _, r in trades.head(2).iterrows():
                buy = r["Signal"] == "BUY"
                bg = "#16803c" if buy else "#b42318"

                st.markdown(
                    f"""
                    <div class="sniper-card" style="background:{bg};">
                        <div class="big">
                            {r['Stock']} — {r['Signal']}
                        </div>
                        <div class="metric">
                            Score <b>{int(r['Score'])}/100</b> |
                            Qty <b>{int(r['Qty'])}</b> |
                            Risk <b>₹{fnum(r['Actual Risk']):,.0f}</b>
                        </div>
                        <div class="metric">
                            Entry <b>₹{fnum(r['Entry']):,.2f}</b> |
                            SL <b>₹{fnum(r['SL']):,.2f}</b> |
                            Target <b>₹{fnum(r['Target']):,.2f}</b>
                        </div>
                        <div class="metric">
                            VWAP ₹{fnum(r['VWAP']):,.2f} |
                            RSI {fnum(r['RSI']):.1f} |
                            ATR {fnum(r['ATR(5m)']):,.2f} |
                            RVOL {fnum(r['RVOL']):.1f}x |
                            R:R {fnum(r['R:R']):.2f}
                        </div>
                        <div style="margin-top:8px;">
                            {r['Reason']}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

        # Useful diagnostic: how many failed each broad stage.
        st.subheader("🔎 Scanner Diagnostics")
        diag = {
            "Candidates": len(result_df),
            "BUY signals": int((result_df["Signal"] == "BUY").sum()),
            "SELL signals": int((result_df["Signal"] == "SELL").sum()),
            "WAIT": int((result_df["Signal"] == "WAIT").sum()),
            "Score ≥ minimum": int(
                (result_df["Score"] >= min_score).sum()
            ),
        }
        st.json(diag)

else:
    st.info(
        "Press RUN / REFRESH SNIPER. With auto-refresh enabled, "
        "the page rescans approximately every 60 seconds."
    )

# ============================================================
# IMPORTANT NOTES
# ============================================================
st.divider()
st.caption(
    "No Nifty-50 dependency. Chartink is candidate discovery only. "
    "Final decisions are stock-specific. ATR SL/target and quantity are "
    "calculated from the selected stock's intraday structure. "
    "Educational use only; verify live price and execution before trading."
)
