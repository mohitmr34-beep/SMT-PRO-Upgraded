import streamlit as st
import yfinance as yf
import pandas as pd
import requests
from urllib.parse import unquote
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="SMT PRO SNIPER",
    layout="wide"
)

st.markdown(
    """
    <h2 style='text-align:center;'>SMT PRO SNIPER (VWAP)</h2>
    <hr>
    """,
    unsafe_allow_html=True
)

# ============================================================
# INDIA / KOLKATA TIME
# ============================================================

IST = ZoneInfo("Asia/Kolkata")

india_now = datetime.now(IST)

MARKET_START = dt_time(9, 15)

# Scanner is available from 09:15 IST
if india_now.time() < MARKET_START:

    st.warning(
        "⏰ Scanner starts at 9:15 AM IST "
        f"(Asia/Kolkata). Current India time: "
        f"{india_now.strftime('%H:%M:%S')}"
    )

    st.stop()

st.caption(
    f"🇮🇳 India Time: "
    f"{india_now.strftime('%d-%m-%Y %H:%M:%S IST')}"
)

# ============================================================
# AUTO REFRESH
# ============================================================

auto_refresh = st.checkbox(
    "Auto Refresh (5 min)",
    value=False
)

if auto_refresh:

    time.sleep(300)

    st.rerun()

# ============================================================
# SAFE SCALAR CONVERSION
# ============================================================

def val(x):

    try:

        if isinstance(x, pd.DataFrame):

            if x.empty:
                return 0.0

            x = x.iloc[0]

        if isinstance(x, pd.Series):

            if x.empty:
                return 0.0

            x = x.iloc[0]

        value = float(x)

        if pd.isna(value):
            return 0.0

        return value

    except Exception:

        return 0.0


# ============================================================
# CLEAN YAHOO DATA
# ============================================================

def clean_dataframe(df):

    if df is None:
        return None

    if df.empty:
        return None

    try:

        # Handle MultiIndex from yfinance
        if isinstance(df.columns, pd.MultiIndex):

            # Find OHLCV level
            if "Close" in df.columns.get_level_values(0):

                df.columns = df.columns.get_level_values(0)

            elif "Close" in df.columns.get_level_values(-1):

                df.columns = df.columns.get_level_values(-1)

            else:

                df.columns = [
                    str(c[0]) if isinstance(c, tuple) else str(c)
                    for c in df.columns
                ]

        # Remove duplicate columns
        df = df.loc[:, ~df.columns.duplicated()]

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        for col in required:

            if col not in df.columns:
                return None

        df = df[required].copy()

        for col in required:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna()

        if df.empty:
            return None

        return df

    except Exception:

        return None


# ============================================================
# STOCK SOURCE
# ============================================================

source = st.radio(
    "Stock Source",
    [
        "Manual CSV",
        "Chartink LIVE"
    ],
    horizontal=True
)

symbols = []


# ============================================================
# MANUAL CSV
# ============================================================

if source == "Manual CSV":

    uploaded_file = st.file_uploader(
        "Upload Stock CSV",
        type=["csv"]
    )

    if uploaded_file is not None:

        try:

            df_symbols = pd.read_csv(
                uploaded_file
            )

            df_symbols.columns = (
                df_symbols.columns
                .astype(str)
                .str.strip()
            )

            if "Symbol" not in df_symbols.columns:

                st.error(
                    "CSV must contain a 'Symbol' column."
                )

                st.stop()

            for symbol in df_symbols["Symbol"].dropna():

                symbol = str(symbol).strip().upper()

                if not symbol:
                    continue

                # Avoid duplicate .NS
                if symbol.endswith(".NS"):
                    symbols.append(symbol)
                else:
                    symbols.append(symbol + ".NS")

            symbols = list(dict.fromkeys(symbols))

            if not symbols:

                st.warning(
                    "No valid symbols found in CSV."
                )

                st.stop()

            st.success(
                f"{len(symbols)} stocks loaded from CSV."
            )

        except Exception as e:

            st.error(
                f"CSV error: {str(e)}"
            )

            st.stop()

    else:

        st.info(
            "Upload a CSV containing a 'Symbol' column."
        )

        st.stop()


# ============================================================
# CHARTINK LIVE
# ============================================================

else:

    st.subheader(
        "📡 Chartink LIVE Scanner"
    )

    cookie = st.text_input(
        "Chartink Cookie",
        type="password"
    )

    @st.cache_data(ttl=60)
    def get_chartink_symbols(cookie_value):

        if not cookie_value:
            return []

        try:

            session = requests.Session()

            # ------------------------------------------------
            # Load cookies
            # ------------------------------------------------

            for part in cookie_value.split(";"):

                part = part.strip()

                if "=" not in part:
                    continue

                key, value = part.split(
                    "=",
                    1
                )

                session.cookies.set(
                    key,
                    value,
                    domain="chartink.com"
                )

            # ------------------------------------------------
            # Open Chartink
            # ------------------------------------------------

            home = session.get(
                "https://chartink.com",
                headers={
                    "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/120 Safari/537.36"
                },
                timeout=15
            )

            if home.status_code >= 400:

                return []

            # ------------------------------------------------
            # XSRF
            # ------------------------------------------------

            xsrf = unquote(
                session.cookies.get(
                    "XSRF-TOKEN",
                    ""
                )
            )

            headers = {

                "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120 Safari/537.36",

                "Accept":
                "application/json, text/plain, */*",

                "X-Requested-With":
                "XMLHttpRequest",

                "Referer":
                "https://chartink.com/",

                "Content-Type":
                "application/json"
            }

            if xsrf:

                headers["X-XSRF-TOKEN"] = xsrf

            # ------------------------------------------------
            # CHARTINK FILTER
            # ------------------------------------------------

            scan_clause = """
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

            payload = {
                "scan_clause":
                " ".join(
                    scan_clause.split()
                )
            }

            # ------------------------------------------------
            # REQUEST
            # ------------------------------------------------

            response = session.post(
                "https://chartink.com/screener/process",
                headers=headers,
                json=payload,
                timeout=20
            )

            if response.status_code != 200:

                return []

            # ------------------------------------------------
            # JSON
            # ------------------------------------------------

            try:

                result = response.json()

            except Exception:

                return []

            data = result.get(
                "data",
                []
            )

            if not data:
                return []

            found = []

            for row in data:

                code = row.get(
                    "nsecode"
                )

                if not code:
                    continue

                code = str(
                    code
                ).strip().upper()

                if not code:
                    continue

                if code.endswith(".NS"):
                    found.append(code)
                else:
                    found.append(
                        code + ".NS"
                    )

            return list(
                dict.fromkeys(found)
            )

        except Exception:

            return []

    # --------------------------------------------------------
    # LOAD BUTTON
    # --------------------------------------------------------

    if st.button(
        "📡 Load Chartink Stocks"
    ):

        with st.spinner(
            "Fetching Chartink stocks..."
        ):

            loaded_symbols = (
                get_chartink_symbols(
                    cookie
                )
            )

        if loaded_symbols:

            st.session_state[
                "chartink_symbols"
            ] = loaded_symbols

            st.success(
                f"{len(loaded_symbols)} stocks loaded."
            )

        else:

            st.error(
                "No stocks returned from Chartink. "
                "Check the cookie and scanner access."
            )

    # --------------------------------------------------------
    # KEEP SYMBOLS AFTER RERUN
    # --------------------------------------------------------

    if "chartink_symbols" in st.session_state:

        symbols = st.session_state[
            "chartink_symbols"
        ]

    if not symbols:

        st.info(
            "Enter Chartink cookie and click "
            "'Load Chartink Stocks'."
        )

        st.stop()


# ============================================================
# STOCK COUNT
# ============================================================

st.write(
    f"📊 Stocks to scan: **{len(symbols)}**"
)

# ============================================================
# TIMEFRAME
# ============================================================

timeframe = st.selectbox(
    "Timeframe",
    [
        "5m",
        "15m"
    ],
    index=0
)

# ============================================================
# YAHOO DATA
# ============================================================

@st.cache_data(ttl=60)
def get_data(
    symbol,
    selected_timeframe
):

    try:

        df = yf.download(
            symbol,
            period="5d",
            interval=selected_timeframe,
            progress=False,
            auto_adjust=False,
            threads=False
        )

        return clean_dataframe(df)

    except Exception:

        return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=14
):

    if df is None:
        return 0.0

    if len(df) < period + 1:
        return 0.0

    try:

        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        previous_close = (
            close.shift(1)
        )

        tr1 = high - low

        tr2 = (
            high - previous_close
        ).abs()

        tr3 = (
            low - previous_close
        ).abs()

        tr = pd.concat(
            [
                tr1,
                tr2,
                tr3
            ],
            axis=1
        ).max(axis=1)

        atr = (
            tr
            .rolling(period)
            .mean()
            .iloc[-1]
        )

        return val(atr)

    except Exception:

        return 0.0


# ============================================================
# VWAP
# ============================================================

def calculate_vwap(df):

    if df is None:
        return 0.0

    if df.empty:
        return 0.0

    try:

        high = df["High"]
        low = df["Low"]
        close = df["Close"]
        volume = df["Volume"]

        typical_price = (
            high + low + close
        ) / 3.0

        cumulative_volume = (
            volume.cumsum()
        )

        cumulative_value = (
            typical_price * volume
        ).cumsum()

        if val(
            cumulative_volume.iloc[-1]
        ) <= 0:

            return 0.0

        vwap = (
            cumulative_value
            / cumulative_volume
        )

        return val(
            vwap.iloc[-1]
        )

    except Exception:

        return 0.0


# ============================================================
# RISK MANAGEMENT
# ============================================================

st.sidebar.header(
    "💼 Risk Management"
)

capital = st.sidebar.number_input(
    "Capital (₹)",
    min_value=1000.0,
    value=50000.0,
    step=1000.0
)

risk_pct = st.sidebar.slider(
    "Risk % per trade",
    min_value=0.5,
    max_value=5.0,
    value=1.0,
    step=0.5
)

risk_amt = (
    capital *
    risk_pct /
    100
)

st.sidebar.write(
    f"Maximum risk: **₹{risk_amt:,.2f}**"
)


# ============================================================
# POSITION SIZE
# ============================================================

def calculate_position(
    entry,
    sl
):

    entry = val(entry)
    sl = val(sl)

    if entry <= 0:
        return 0, 0.0, 0.0

    if sl <= 0:
        return 0, 0.0, 0.0

    distance = abs(
        entry - sl
    )

    if distance <= 0:
        return 0, 0.0, 0.0

    qty_by_risk = int(
        risk_amt / distance
    )

    qty_by_capital = int(
        capital / entry
    )

    qty = min(
        qty_by_risk,
        qty_by_capital
    )

    if qty <= 0:
        return 0, 0.0, 0.0

    capital_used = (
        qty * entry
    )

    actual_risk = (
        qty * distance
    )

    return (
        qty,
        capital_used,
        actual_risk
    )


# ============================================================
# SNIPER + VWAP ANALYSIS
# ============================================================

def analyze(df):

    if df is None:

        return (
            "NO DATA",
            None,
            None,
            None,
            0,
            None
        )

    if len(df) < 40:

        return (
            "WAIT",
            None,
            None,
            None,
            0,
            None
        )

    try:

        c1 = df.iloc[-3]
        c2 = df.iloc[-2]
        c3 = df.iloc[-1]

        # ----------------------------------------------------
        # SAFE OHLC
        # ----------------------------------------------------

        h1 = val(c1["High"])
        l1 = val(c1["Low"])
        c1_close = val(c1["Close"])

        h2 = val(c2["High"])
        l2 = val(c2["Low"])
        c2_close = val(c2["Close"])

        h3 = val(c3["High"])
        l3 = val(c3["Low"])
        c3_close = val(c3["Close"])

        v1 = val(c1["Volume"])
        v2 = val(c2["Volume"])
        v3 = val(c3["Volume"])

        # ----------------------------------------------------
        # INDICATORS
        # ----------------------------------------------------

        atr = calculate_atr(df)

        vwap = calculate_vwap(df)

        if atr <= 0:

            return (
                "WAIT",
                None,
                None,
                None,
                0,
                vwap
            )

        if vwap <= 0:

            return (
                "WAIT",
                None,
                None,
                None,
                0,
                vwap
            )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = 0

        # ----------------------------------------------------
        # VOLUME CONFIRMATION
        # ----------------------------------------------------

        volume_confirmation = (
            v3 > v2 and
            v2 > v1
        )

        if volume_confirmation:

            score += 30

        # ----------------------------------------------------
        # VWAP DIRECTION
        # ----------------------------------------------------

        above_vwap = (
            c3_close > vwap
        )

        below_vwap = (
            c3_close < vwap
        )

        # ----------------------------------------------------
        # FALSE BREAKOUT DETECTION
        # ----------------------------------------------------

        fake_up = (
            h2 > h1 and
            c3_close < h2
        )

        fake_down = (
            l2 < l1 and
            c3_close > l2
        )

        # ----------------------------------------------------
        # SIDEWAYS FILTER
        # ----------------------------------------------------

        candle_range = abs(
            h2 - l2
        )

        if candle_range < (
            atr * 0.5
        ):

            return (
                "WAIT",
                None,
                None,
                None,
                0,
                vwap
            )

        # ----------------------------------------------------
        # BUY SETUP
        # ----------------------------------------------------

        buy_breakout = (
            c3_close > h2
        )

        if (
            buy_breakout
            and not fake_up
            and above_vwap
        ):

            signal = "BUY"

            entry = c3_close

            sl = l2

            target = (
                entry +
                (2 * atr)
            )
            
            score += 40

            # Extra VWAP confirmation
            score += 10
            
            if score >= 5
