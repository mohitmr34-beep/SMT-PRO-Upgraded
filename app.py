import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
from urllib.parse import unquote
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

# ============================================================
# SMT PRO SNIPER
# Daily Quality Filter + Intraday VWAP + Breakout + Volume
# ATR Risk + R:R + Anti-Chase + 20% Move Potential Monitor
# ============================================================

st.set_page_config(
    page_title="SMT PRO SNIPER",
    layout="wide"
)

st.markdown(
    "<h2 style='text-align:center;'>🔥 SMT PRO SNIPER — PRODUCTION</h2>",
    unsafe_allow_html=True
)

# ============================================================
# TIME
# ============================================================

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.now(IST)
MARKET_START = dt_time(9, 15)

if NOW.time() < MARKET_START:
    st.warning(
        f"Scanner starts at 9:15 AM IST. "
        f"Current India time: {NOW.strftime('%H:%M:%S')}"
    )
    st.stop()

st.caption(
    f"🇮🇳 Asia/Kolkata | {NOW.strftime('%d-%m-%Y %H:%M:%S IST')}"
)

# ============================================================
# REFRESH
# ============================================================

auto_refresh = st.checkbox("Auto Refresh (5 min)", value=False)

if auto_refresh:
    time.sleep(300)
    st.rerun()

# ============================================================
# SETTINGS
# ============================================================

st.sidebar.header("⚙️ SNIPER SETTINGS")

timeframe = st.sidebar.selectbox(
    "Intraday Timeframe",
    ["5m", "15m"],
    index=0
)

min_score = st.sidebar.slider(
    "Minimum Sniper Score",
    50,
    90,
    60
)

max_vwap_atr = st.sidebar.slider(
    "Maximum VWAP Distance (ATR)",
    0.5,
    5.0,
    2.0,
    0.1
)

min_rr = st.sidebar.slider(
    "Minimum R:R",
    1.0,
    3.0,
    1.5,
    0.1
)

# ============================================================
# RISK
# ============================================================

st.sidebar.header("💼 RISK MANAGEMENT")

capital = st.sidebar.number_input(
    "Capital (₹)",
    min_value=1000.0,
    value=50000.0,
    step=1000.0
)

risk_pct = st.sidebar.slider(
    "Risk % / trade",
    0.5,
    5.0,
    1.0,
    0.5
)

risk_amount = capital * risk_pct / 100

st.sidebar.info(
    f"Max risk/trade: ₹{risk_amount:,.2f}"
)

# ============================================================
# SAFE NUMBER
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
# CLEAN DATA
# ============================================================

def clean_dataframe(df):

    if df is None or df.empty:
        return None

    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                str(c[0]) if isinstance(c, tuple) else str(c)
                for c in df.columns
            ]

        df = df.loc[:, ~df.columns.duplicated()]

        required = [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume"
        ]

        if not all(c in df.columns for c in required):
            return None

        df = df[required].copy()

        for c in required:
            df[c] = pd.to_numeric(
                df[c],
                errors="coerce"
            )

        df = df.dropna()

        return df if not df.empty else None

    except Exception:
        return None

# ============================================================
# STOCK SOURCE
# ============================================================

source = st.radio(
    "Stock Source",
    ["Manual CSV", "Chartink LIVE"],
    horizontal=True
)

symbols = []

# ============================================================
# CSV
# ============================================================

if source == "Manual CSV":

    uploaded = st.file_uploader(
        "Upload CSV — must contain Symbol column",
        type=["csv"]
    )

    if uploaded is None:
        st.info("Upload your stock CSV to start.")
        st.stop()

    try:
        csv_df = pd.read_csv(uploaded)
        csv_df.columns = (
            csv_df.columns.astype(str).str.strip()
        )

        if "Symbol" not in csv_df.columns:
            st.error("CSV must contain 'Symbol' column.")
            st.stop()

        for s in csv_df["Symbol"].dropna():

            symbol = str(s).strip().upper()

            if not symbol:
                continue

            if symbol.endswith(".NS"):
                symbols.append(symbol)
            else:
                symbols.append(symbol + ".NS")

        symbols = list(dict.fromkeys(symbols))

    except Exception as e:
        st.error(f"CSV error: {e}")
        st.stop()

# ============================================================
# CHARTINK
# ============================================================

else:

    st.subheader("📡 Chartink LIVE")

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

            for part in cookie_value.split(";"):

                part = part.strip()

                if "=" not in part:
                    continue

                key, value = part.split("=", 1)

                session.cookies.set(
                    key,
                    value,
                    domain="chartink.com"
                )

            session.get(
                "https://chartink.com",
                headers={
                    "User-Agent":
                    "Mozilla/5.0"
                },
                timeout=15
            )

            xsrf = unquote(
                session.cookies.get(
                    "XSRF-TOKEN",
                    ""
                )
            )

            headers = {
                "User-Agent":
                "Mozilla/5.0",
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
                " ".join(scan_clause.split())
            }

            response = session.post(
                "https://chartink.com/screener/process",
                headers=headers,
                json=payload,
                timeout=20
            )

            if response.status_code != 200:
                return []

            try:
                data = response.json().get(
                    "data",
                    []
                )
            except Exception:
                return []

            output = []

            for row in data:

                code = row.get("nsecode")

                if not code:
                    continue

                code = str(code).strip().upper()

                if not code.endswith(".NS"):
                    code += ".NS"

                output.append(code)

            return list(dict.fromkeys(output))

        except Exception:
            return []

    if st.button("📡 Load Chartink Stocks"):

        with st.spinner("Fetching Chartink..."):

            loaded = get_chartink_symbols(
                cookie
            )

        if loaded:

            st.session_state[
                "chartink_symbols"
            ] = loaded

            st.success(
                f"{len(loaded)} stocks loaded."
            )

        else:

            st.error(
                "No stocks fetched. "
                "Check cookie / Chartink access."
            )

    symbols = st.session_state.get(
        "chartink_symbols",
        []
    )

    if not symbols:
        st.info(
            "Enter Chartink cookie and load stocks."
        )
        st.stop()

# ============================================================
# DATA
# ============================================================

@st.cache_data(ttl=60)
def get_data(symbol, selected_timeframe):

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
# SESSION VWAP
# IMPORTANT: RESET EVERY TRADING DAY
# ============================================================

def calculate_session_vwap(df):

    if df is None or df.empty:
        return 0.0

    try:

        work = df.copy()

        if not isinstance(
            work.index,
            pd.DatetimeIndex
        ):
            work.index = pd.to_datetime(
                work.index
            )

        idx = work.index

        if idx.tz is None:
            idx = idx.tz_localize(
                "UTC"
            ).tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)

        work.index = idx

        today = datetime.now(IST).date()

        today_df = work[
            work.index.date == today
        ].copy()

        if today_df.empty:
            return 0.0

        typical_price = (
            today_df["High"] +
            today_df["Low"] +
            today_df["Close"]
        ) / 3.0

        volume = today_df["Volume"]

        cumulative_volume = volume.cumsum()

        total_volume = val(
            cumulative_volume.iloc[-1]
        )

        if total_volume <= 0:
            return 0.0

        cumulative_value = (
            typical_price * volume
        ).cumsum()

        vwap = (
            cumulative_value /
            cumulative_volume
        )

        return val(vwap.iloc[-1])

    except Exception:
        return 0.0

# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):

    if df is None or len(df) < period + 1:
        return 0.0

    try:

        high = df["High"]
        low = df["Low"]
        close = df["Close"]

        previous_close = close.shift(1)

        tr1 = high - low
        tr2 = (
            high - previous_close
        ).abs()
        tr3 = (
            low - previous_close
        ).abs()

        tr = pd.concat(
            [tr1, tr2, tr3],
            axis=1
        ).max(axis=1)

        return val(
            tr.rolling(period).mean().iloc[-1]
        )

    except Exception:
        return 0.0

# ============================================================
# RELATIVE VOLUME
# ============================================================

def relative_volume(df, lookback=20):

    if df is None or len(df) < lookback + 1:
        return 0.0

    try:

        current = val(
            df["Volume"].iloc[-1]
        )

        previous = df["Volume"].iloc[
            -lookback-1:-1
        ]

        avg = val(previous.mean())

        if avg <= 0:
            return 0.0

        return current / avg

    except Exception:
        return 0.0

# ============================================================
# OPENING RANGE
# ============================================================

def get_opening_range(df):

    if df is None or df.empty:
        return 0.0, 0.0

    try:

        work = df.copy()

        if not isinstance(
            work.index,
            pd.DatetimeIndex
        ):
            work.index = pd.to_datetime(
                work.index
            )

        idx = work.index

        if idx.tz is None:
            idx = idx.tz_localize(
                "UTC"
            ).tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)

        work.index = idx

        today = datetime.now(IST).date()

        today_df = work[
            work.index.date == today
        ]

        if today_df.empty:
            return 0.0, 0.0

        opening = today_df[
            (today_df.index.time >= dt_time(9, 15))
            &
            (today_df.index.time < dt_time(9, 30))
        ]

        if opening.empty:
            return 0.0, 0.0

        return (
            val(opening["High"].max()),
            val(opening["Low"].min())
        )

    except Exception:
        return 0.0, 0.0

# ============================================================
# POSITION SIZE
# ============================================================

def position_size(entry, sl):

    entry = val(entry)
    sl = val(sl)

    if entry <= 0 or sl <= 0:
        return 0, 0.0, 0.0

    distance = abs(entry - sl)

    if distance <= 0:
        return 0, 0.0, 0.0

    risk_qty = int(
        risk_amount / distance
    )

    capital_qty = int(
        capital / entry
    )

    qty = min(
        risk_qty,
        capital_qty
    )

    if qty <= 0:
        return 0, 0.0, 0.0

    capital_used = qty * entry
    actual_risk = qty * distance

    return (
        qty,
        capital_used,
        actual_risk
    )

# ============================================================
# SNIPER ANALYSIS
# ============================================================

def analyze(df):

    empty = (
        "WAIT",
        None,
        None,
        None,
        0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        "NO DATA"
    )

    if df is None or len(df) < 40:
        return empty

    try:

        c1 = df.iloc[-3]
        c2 = df.iloc[-2]
        c3 = df.iloc[-1]

        h1 = val(c1["High"])
        l1 = val(c1["Low"])
        c1_close = val(c1["Close"])

        h2 = val(c2["High"])
        l2 = val(c2["Low"])
        c2_close = val(c2["Close"])

        o3 = val(c3["Open"])
        h3 = val(c3["High"])
        l3 = val(c3["Low"])
        c3_close = val(c3["Close"])

        v1 = val(c1["Volume"])
        v2 = val(c2["Volume"])
        v3 = val(c3["Volume"])

        atr = calculate_atr(df)
        vwap = calculate_session_vwap(df)
        rvol = relative_volume(df)

        if atr <= 0:
            return empty

        # -----------------------------------------------
        # VWAP DISTANCE
        # -----------------------------------------------

        vwap_distance = (
            abs(c3_close - vwap) / atr
            if vwap > 0
            else 999.0
        )

        # -----------------------------------------------
        # OPENING RANGE
        # -----------------------------------------------

        opening_high, opening_low = (
            get_opening_range(df)
        )

        # -----------------------------------------------
        # CANDLE QUALITY
        # -----------------------------------------------

        c3_range = h3 - l3

        if c3_range <= 0:
            return empty

        bullish_close = (
            c3_close >
            l3 + c3_range * 0.65
        )

        bearish_close = (
            c3_close <
            l3 + c3_range * 0.35
        )

        # -----------------------------------------------
        # BREAKOUT
        # -----------------------------------------------

        breakout_up = (
            c3_close > h2
        )

        breakout_down = (
            c3_close < l2
        )

        # -----------------------------------------------
        # FALSE BREAKOUT PROTECTION
        # -----------------------------------------------

        fake_up = (
            h2 > h1 and
            c3_close < h2
        )

        fake_down = (
            l2 < l1 and
            c3_close > l2
        )

        # -----------------------------------------------
        # VOLUME
        # -----------------------------------------------

        volume_increasing = (
            v3 > v2 and
            v2 >= v1
        )

        # -----------------------------------------------
        # VWAP
        # -----------------------------------------------

        above_vwap = (
            vwap > 0 and
            c3_close > vwap
        )

        below_vwap = (
            vwap > 0 and
            c3_close < vwap
        )

        # -----------------------------------------------
        # SCORE
        # -----------------------------------------------

        buy_score = 0
        sell_score = 0

        buy_reasons = []
        sell_reasons = []

        # Volume trend + relative volume
        if volume_increasing:
            buy_score += 15
            sell_score += 15
            buy_reasons.append("Volume expansion")
            sell_reasons.append("Volume expansion")

        if rvol >= 1.5:
            buy_score += 10
            sell_score += 10
            buy_reasons.append("RVOL >= 1.5")
            sell_reasons.append("RVOL >= 1.5")
        elif rvol >= 1.2:
            buy_score += 5
            sell_score += 5

        # Breakout
        if breakout_up and not fake_up:
            buy_score += 20
            buy_reasons.append("Fresh high breakout")

        if breakout_down and not fake_down:
            sell_score += 20
            sell_reasons.append("Fresh low breakdown")

        # VWAP
        if above_vwap:
            buy_score += 10
            buy_reasons.append("Above session VWAP")

        if below_vwap:
            sell_score += 10
            sell_reasons.append("Below session VWAP")

        # Candle quality
        if bullish_close:
            buy_score += 5
            buy_reasons.append("Strong bullish close")

        if bearish_close:
            sell_score += 5
            sell_reasons.append("Strong bearish close")

        # Opening range
        if (
            opening_high > 0 and
            c3_close > opening_high
        ):
            buy_score += 10
            buy_reasons.append("Opening-range breakout")

        if (
            opening_low > 0 and
            c3_close < opening_low
        ):
            sell_score += 10
            sell_reasons.append("Opening-range breakdown")

        # -----------------------------------------------
        # SELECT DIRECTION
        # -----------------------------------------------

        if buy_score >= sell_score:
            direction = "BUY"
            raw_score = buy_score
            reasons = buy_reasons
        else:
            direction = "SELL"
            raw_score = sell_score
            reasons = sell_reasons

        # -----------------------------------------------
        # STRUCTURAL ENTRY / SL
        # -----------------------------------------------

        if direction == "BUY":

            entry = c3_close

            structural_sl = l2

            # ATR buffer protects against tiny stop-outs
            sl = structural_sl - (
                atr * 0.10
            )

            risk_distance = entry - sl

            target = entry + (
                2.0 * atr
            )

        else:

            entry = c3_close

            structural_sl = h2

            sl = structural_sl + (
                atr * 0.10
            )

            risk_distance = sl - entry

            target = entry - (
                2.0 * atr
            )

        if risk_distance <= 0:
            return empty

        # -----------------------------------------------
        # R:R
        # -----------------------------------------------

        reward = abs(
            target - entry
        )

        rr = (
            reward / risk_distance
            if risk_distance > 0
            else 0.0
        )

        # -----------------------------------------------
        # ANTI-CHASE
        # -----------------------------------------------

        too_extended = (
            vwap <= 0 or
            vwap_distance > max_vwap_atr
        )

        if too_extended:

            reasons.append(
                "Too far from VWAP"
            )

            return (
                "WAIT",
                None,
                None,
                None,
                min(raw_score, 100),
                vwap,
                atr,
                rvol,
                vwap_distance,
                rr,
                opening_high,
                "ANTI-CHASE"
            )

        # -----------------------------------------------
        # R:R QUALITY
        # -----------------------------------------------

        if rr >= min_rr:
            raw_score += 5
            reasons.append(
                f"R:R {rr:.2f}"
            )
        else:
            reasons.append(
                f"Weak R:R {rr:.2f}"
            )

        # -----------------------------------------------
        # FINAL SCORE
        # -----------------------------------------------

        final_score = min(
            raw_score,
            100
        )

        # Need actual breakout + VWAP alignment
        valid_direction = (
            (
                direction == "BUY" and
                breakout_up and
                above_vwap and
                not fake_up
            )
            or
            (
                direction == "SELL" and
                breakout_down and
                below_vwap and
                not fake_down
            )
        )

        if not valid_direction:
            return (
                "WAIT",
                None,
                None,
                None,
                final_score,
                vwap,
                atr,
                rvol,
                vwap_distance,
                rr,
                opening_high,
                "WAITING CONFIRMATION"
            )

        if rr < min_rr:
            return (
                "WAIT",
                None,
                None,
                None,
                final_score,
                vwap,
                atr,
                rvol,
                vwap_distance,
                rr,
                opening_high,
                "LOW R:R"
            )

        if final_score < min_score:
            return (
                "WAIT",
                None,
                None,
                None,
                final_score,
                vwap,
                atr,
                rvol,
                vwap_distance,
                rr,
                opening_high,
                "LOW SCORE"
            )

        return (
            direction,
            entry,
            sl,
            target,
            final_score,
            vwap,
            atr,
            rvol,
            vwap_distance,
            rr,
            opening_high,
            "VALID SNIPER"
        )

    except Exception:
        return empty

# ============================================================
# 20% MOVE POTENTIAL
# This is a monitor, NOT a prediction.
# ============================================================

def move_20_levels(entry, direction):

    entry = val(entry)

    if entry <= 0:
        return None, None

    if direction == "BUY":
        return (
            entry * 1.10,
            entry * 1.20
        )

    if direction == "SELL":
        return (
            entry * 0.90,
            entry * 0.80
        )

    return None, None

# ============================================================
# RUN
# ============================================================

st.write(
    f"📊 Universe: **{len(symbols)} stocks**"
)

if st.button(
    "🚀 RUN SNIPER SCANNER",
    type="primary"
):

    results = []

    progress = st.progress(0)

    total = len(symbols)

    for i, symbol in enumerate(symbols):

        df = get_data(
            symbol,
            timeframe
        )

        (
            signal,
            entry,
            sl,
            target,
            score,
            vwap,
            atr,
            rvol,
            vwap_distance,
            rr,
            opening_high,
            status
        ) = analyze(df)

        qty, cap_used, risk = (
            position_size(
                entry,
                sl
            )
        )

        move10, move20 = (
            move_20_levels(
                entry,
                signal
            )
        )

        results.append({

            "Stock":
                symbol,

            "Signal":
                signal,

            "Score":
                int(score),

            "Status":
                status,

            "VWAP":
                round(vwap, 2)
                if vwap else None,

            "VWAP/ATR":
                round(vwap_distance, 2)
                if vwap_distance
                else None,

            "RVOL":
                round(rvol, 2)
                if rvol else None,

            "ATR":
                round(atr, 2)
                if atr else None,

            "Entry":
                round(val(entry), 2)
                if entry else None,

            "SL":
                round(val(sl), 2)
                if sl else None,

            "Target":
                round(val(target), 2)
                if target else None,

            "R:R":
                round(rr, 2)
                if rr else None,

            "Qty":
                qty,

            "Capital":
                round(cap_used, 2),

            "Risk ₹":
                round(risk, 2),

            "10% Level":
                round(move10, 2)
                if move10 else None,

            "20% Level":
                round(move20, 2)
                if move20 else None
        })

        progress.progress(
            (i + 1) / total
        )

    progress.empty()

    result_df = pd.DataFrame(
        results
    )

    # ========================================================
    # FILTERED SNIPER RESULTS
    # ========================================================

    sniper_df = result_df[
        result_df["Signal"].isin(
            ["BUY", "SELL"]
        )
        &
        (
            result_df["Score"] >= min_score
        )
    ].copy()

    sniper_df = sniper_df.sort_values(
        by=["Score", "R:R"],
        ascending=[False, False]
    )

    st.subheader(
        "🎯 FILTERED SNIPER STOCKS"
    )

    if sniper_df.empty:

        st.warning(
            "No stock passed all sniper filters."
        )

    else:

        display_columns = [
            "Stock",
            "Signal",
            "Score",
            "VWAP",
            "VWAP/ATR",
            "RVOL",
            "Entry",
            "SL",
            "Target",
            "R:R",
            "Qty",
            "Risk ₹"
        ]

        st.dataframe(
            sniper_df[
                display_columns
            ],
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={

                "Stock":
                    st.column_config.TextColumn(
                        "STOCK",
                        width="small"
                    ),

                "Signal":
                    st.column_config.TextColumn(
                        "SIGNAL",
                        width="small"
                    ),

                "Score":
                    st.column_config.NumberColumn(
                        "SCORE",
                        format="%d"
                    ),

                "VWAP":
                    st.column_config.NumberColumn(
                        "VWAP",
                        format="%.2f"
                    ),

                "VWAP/ATR":
                    st.column_config.NumberColumn(
                        "VWAP/ATR",
                        format="%.2f"
                    ),

                "RVOL":
                    st.column_config.NumberColumn(
                        "RVOL",
                        format="%.2f"
                    ),

                "Entry":
                    st.column_config.NumberColumn(
                        "ENTRY",
                        format="%.2f"
                    ),

                "SL":
                    st.column_config.NumberColumn(
                        "SL",
                        format="%.2f"
                    ),

                "Target":
                    st.column_config.NumberColumn(
                        "TARGET",
                        format="%.2f"
                    ),

                "R:R":
                    st.column_config.NumberColumn(
                        "R:R",
                        format="%.2f"
                    ),

                "Qty":
                    st.column_config.NumberColumn(
                        "QTY",
                        format="%d"
                    ),

                "Risk ₹":
                    st.column_config.NumberColumn(
                        "RISK ₹",
                        format="₹%.0f"
                    )
            }
        )

    # ========================================================
    # TOP 2
    # ========================================================

    st.subheader(
        "🔥 TOP 2 SNIPER TRADES"
    )

    top2 = sniper_df.head(2)

    if top2.empty:

        st.info(
            "No qualifying Top 2 trade."
        )

    else:

        cards = st.columns(
            len(top2)
        )

        for card, (_, row) in zip(
            cards,
            top2.iterrows()
        ):

            signal = row["Signal"]

            bg = (
                "#198754"
                if signal == "BUY"
                else "#dc3545"
            )

            with card:

                st.markdown(
                    f"""
                    <div style="
                        padding:18px;
                        border-radius:14px;
                        background:{bg};
                        color:white;
                        min-height:285px;
                        box-shadow:0 4px 12px
                        rgba(0,0,0,0.18);
                    ">

                    <h2 style="margin:0;">
                    {row['Stock']}
                    </h2>

                    <h3>
                    {signal} |
                    Score {int(row['Score'])}/100
                    </h3>

                    <hr>

                    <b>ENTRY:</b>
                    ₹{row['Entry']:.2f}<br>

                    <b>STOP LOSS:</b>
                    ₹{row['SL']:.2f}<br>

                    <b>TARGET:</b>
                    ₹{row['Target']:.2f}<br>

                    <b>VWAP:</b>
                    ₹{row['VWAP']:.2f}<br>

                    <b>VWAP DIST:</b>
                    {row['VWAP/ATR']:.2f} ATR<br>

                    <b>RVOL:</b>
                    {row['RVOL']:.2f}<br>

                    <b>R:R:</b>
                    {row['R:R']:.2f}<br>

                    <b>QTY:</b>
                    {int(row['Qty'])}<br>

                    <b>RISK:</b>
                    ₹{row['Risk ₹']:.0f}<br>

                    <b>20% MONITOR:</b>
                    ₹{row['20% Level']:.2f}

                    </div>
                    """,
                    unsafe_allow_html=True
                )

    # ========================================================
    # 20% POTENTIAL MONITOR
    # ========================================================

    st.subheader(
        "📈 20% MOVE POTENTIAL MONITOR"
    )

    st.caption(
        "The 20% level is a monitoring target, "
        "not a prediction that the stock will move 20%."
    )

    potential = result_df[
        result_df["Signal"].isin(
            ["BUY", "SELL"]
        )
    ].copy()

    if not potential.empty:

        st.dataframe(
            potential[
                [
                    "Stock",
                    "Signal",
                    "Score",
                    "Entry",
                    "10% Level",
                    "20% Level",
                    "VWAP/ATR",
                    "RVOL",
                    "R:R"
                ]
            ],
            use_container_width=True,
            hide_index=True,
            height=300
        )

    # ========================================================
    # ALL RESULTS
    # ========================================================

    with st.expander(
        "🔍 Show All Scanner Results"
    ):

        st.dataframe(
            result_df,
            use_container_width=True,
            hide_index=True,
            height=450
        )

# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "SMT PRO SNIPER | Asia/Kolkata | "
    "Session VWAP resets daily | "
    "Educational use only. Confirm live price, liquidity "
    "and risk before trading."
)
