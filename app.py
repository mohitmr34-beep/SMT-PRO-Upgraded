import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import time
from urllib.parse import unquote
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

st.set_page_config(page_title='SMT PRO SNIPER', layout='wide')
st.markdown("<h2 style='text-align:center;'>SMT PRO SNIPER (VWAP)</h2><hr>", unsafe_allow_html=True)

# ---------- India time: scanner starts at 09:15 IST ----------
IST = ZoneInfo('Asia/Kolkata')
india_now = datetime.now(IST)
if india_now.time() < dt_time(9, 15):
    st.warning(f"Scanner starts at 9:15 AM IST. Current India time: {india_now.strftime('%H:%M:%S')}")
    st.stop()
st.caption(f"🇮🇳 India Time: {india_now.strftime('%d-%m-%Y %H:%M:%S IST')}")

if st.checkbox('Auto Refresh (5 min)', value=False):
    time.sleep(300)
    st.rerun()

def val(x):
    try:
        if isinstance(x, pd.DataFrame):
            if x.empty: return 0.0
            x = x.iloc[0]
        if isinstance(x, pd.Series):
            if x.empty: return 0.0
            x = x.iloc[0]
        x = float(x)
        return 0.0 if pd.isna(x) else x
    except Exception:
        return 0.0

def clean_dataframe(df):
    if df is None or df.empty:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if 'Close' in df.columns.get_level_values(0):
                df.columns = df.columns.get_level_values(0)
            elif 'Close' in df.columns.get_level_values(-1):
                df.columns = df.columns.get_level_values(-1)
            else:
                df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        cols = ['Open','High','Low','Close','Volume']
        if not all(c in df.columns for c in cols):
            return None
        df = df[cols].copy()
        for c in cols:
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna()
        return df if not df.empty else None
    except Exception:
        return None

# ---------- Stock source ----------
source = st.radio('Stock Source', ['Manual CSV', 'Chartink LIVE'], horizontal=True)
symbols = []

if source == 'Manual CSV':
    uploaded = st.file_uploader('Upload Stock CSV', type=['csv'])
    if uploaded is None:
        st.info("Upload a CSV containing a 'Symbol' column.")
        st.stop()
    try:
        d = pd.read_csv(uploaded)
        d.columns = d.columns.astype(str).str.strip()
        if 'Symbol' not in d.columns:
            st.error("CSV must contain a 'Symbol' column.")
            st.stop()
        for s in d['Symbol'].dropna():
            s = str(s).strip().upper()
            if s:
                symbols.append(s if s.endswith('.NS') else s + '.NS')
        symbols = list(dict.fromkeys(symbols))
        if not symbols:
            st.warning('No valid symbols found in CSV.')
            st.stop()
        st.success(f'{len(symbols)} stocks loaded from CSV.')
    except Exception as e:
        st.error(f'CSV error: {e}')
        st.stop()
else:
    st.subheader('📡 Chartink LIVE Scanner')
    cookie = st.text_input('Chartink Cookie', type='password')

    @st.cache_data(ttl=60)
    def get_chartink_symbols(cookie_value):
        if not cookie_value:
            return []
        try:
            session = requests.Session()
            for part in cookie_value.split(';'):
                part = part.strip()
                if '=' not in part:
                    continue
                k, v = part.split('=', 1)
                session.cookies.set(k, v, domain='chartink.com')
            home = session.get('https://chartink.com', headers={'User-Agent':'Mozilla/5.0'}, timeout=15)
            if home.status_code >= 400:
                return []
            xsrf = unquote(session.cookies.get('XSRF-TOKEN', ''))
            headers = {
                'User-Agent':'Mozilla/5.0',
                'Accept':'application/json, text/plain, */*',
                'X-Requested-With':'XMLHttpRequest',
                'Referer':'https://chartink.com/',
                'Content-Type':'application/json'
            }
            if xsrf:
                headers['X-XSRF-TOKEN'] = xsrf
            scan_clause = '''(
                {cash} (
                    (
                        {cash} (
                            daily close >= daily max(252, daily high)*0.98
                            and daily volume > daily sma(daily volume,20)*1.5
                            and daily close > daily open
                        )
                    ) or (
                        {cash} (
                            daily high >= daily max(252, daily high)
                            and daily close < daily open
                            and daily volume > daily sma(daily volume,20)*1.5
                        )
                    ) or (
                        {cash} (
                            daily open > 1 day ago close*1.02
                            and daily volume > daily sma(daily volume,20)*2
                            and daily close > daily open
                        )
                    )
                )
            )'''
            r = session.post('https://chartink.com/screener/process', headers=headers,
                             json={'scan_clause':' '.join(scan_clause.split())}, timeout=20)
            if r.status_code != 200:
                return []
            try:
                data = r.json().get('data', [])
            except Exception:
                return []
            out = []
            for row in data:
                code = row.get('nsecode')
                if code:
                    code = str(code).strip().upper()
                    out.append(code if code.endswith('.NS') else code + '.NS')
            return list(dict.fromkeys(out))
        except Exception:
            return []

    if st.button('📡 Load Chartink Stocks'):
        with st.spinner('Fetching Chartink stocks...'):
            loaded = get_chartink_symbols(cookie)
        if loaded:
            st.session_state['chartink_symbols'] = loaded
            st.success(f'{len(loaded)} stocks loaded.')
        else:
            st.error('No stocks returned. Check the cookie and scanner access.')
    symbols = st.session_state.get('chartink_symbols', [])
    if not symbols:
        st.info("Enter Chartink cookie and click 'Load Chartink Stocks'.")
        st.stop()

st.write(f'📊 Stocks to scan: **{len(symbols)}**')
timeframe = st.selectbox('Timeframe', ['5m', '15m'], index=0)

@st.cache_data(ttl=60)
def get_data(symbol, selected_timeframe):
    try:
        return clean_dataframe(yf.download(symbol, period='5d', interval=selected_timeframe,
                                            progress=False, auto_adjust=False, threads=False))
    except Exception:
        return None

def calculate_atr(df, period=14):
    if df is None or len(df) < period + 1:
        return 0.0
    try:
        pc = df['Close'].shift(1)
        tr = pd.concat([df['High']-df['Low'], (df['High']-pc).abs(), (df['Low']-pc).abs()], axis=1).max(axis=1)
        return val(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0

def calculate_vwap(df):
    if df is None or df.empty:
        return 0.0
    try:
        tp = (df['High'] + df['Low'] + df['Close']) / 3.0
        cv = df['Volume'].cumsum()
        if val(cv.iloc[-1]) <= 0:
            return 0.0
        return val(((tp * df['Volume']).cumsum() / cv).iloc[-1])
    except Exception:
        return 0.0

st.sidebar.header('💼 Risk Management')
capital = st.sidebar.number_input('Capital (₹)', min_value=1000.0, value=50000.0, step=1000.0)
risk_pct = st.sidebar.slider('Risk % per trade', 0.5, 5.0, 1.0, 0.5)
risk_amt = capital * risk_pct / 100
st.sidebar.write(f'Maximum risk: **₹{risk_amt:,.2f}**')

def calculate_position(entry, sl):
    entry, sl = val(entry), val(sl)
    if entry <= 0 or sl <= 0:
        return 0, 0.0, 0.0
    dist = abs(entry-sl)
    if dist <= 0:
        return 0, 0.0, 0.0
    qty = min(int(risk_amt/dist), int(capital/entry))
    if qty <= 0:
        return 0, 0.0, 0.0
    return qty, qty*entry, qty*dist

def analyze(df):
    if df is None or len(df) < 40:
        return 'WAIT', None, None, None, 0, None
    try:
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        h1, l1 = val(c1['High']), val(c1['Low'])
        h2, l2 = val(c2['High']), val(c2['Low'])
        close = val(c3['Close'])
        v1, v2, v3 = val(c1['Volume']), val(c2['Volume']), val(c3['Volume'])
        atr, vwap = calculate_atr(df), calculate_vwap(df)
        if atr <= 0 or vwap <= 0:
            return 'WAIT', None, None, None, 0, vwap
        score = 30 if (v3 > v2 and v2 > v1) else 0
        fake_up = (h2 > h1) and (close < h2)
        fake_down = (l2 < l1) and (close > l2)
        if abs(h2-l2) < atr*0.5:
            return 'WAIT', None, None, None, 0, vwap
        if close > h2 and not fake_up and close > vwap:
            entry, sl = close, l2
            return 'BUY', entry, sl, entry + 2*atr, min(score+50,100), vwap
        if close < l2 and not fake_down and close < vwap:
            entry, sl = close, h2
            return 'SELL', entry, sl, entry - 2*atr, min(score+50,100), vwap
        return 'WAIT', None, None, None, score, vwap
    except Exception:
        return 'WAIT', None, None, None, 0, None

if st.button('🚀 RUN SNIPER SCANNER', type='primary'):
    results = []
    progress = st.progress(0)
    total = len(symbols)
    for i, sym in enumerate(symbols):
        df = get_data(sym, timeframe)
        signal, entry, sl, target, score, vwap = analyze(df)
        qty, cap_used, risk = calculate_position(entry, sl)
        results.append({
            'Stock': sym, 'Signal': signal, 'Score': score,
            'VWAP': round(vwap,2) if vwap else None,
            'Entry': round(val(entry),2) if entry else None,
            'SL': round(val(sl),2) if sl else None,
            'Target': round(val(target),2) if target else None,
            'Qty': qty, 'Capital Used': round(cap_used,2), 'Risk ₹': round(risk,2)
        })
        progress.progress((i+1)/total)
    progress.empty()

    results_df = pd.DataFrame(results).sort_values('Score', ascending=False).reset_index(drop=True)
    filtered = results_df[results_df['Signal'].isin(['BUY','SELL'])].copy().sort_values('Score', ascending=False)
    filtered = filtered[['Stock','Signal','Score','VWAP','Entry','SL','Target','Qty','Risk ₹']]

    st.subheader('🎯 FILTERED STOCKS')
    if filtered.empty:
        st.warning('No BUY/SELL stocks found.')
    else:
        st.dataframe(filtered, use_container_width=True, hide_index=True, height=420,
                     column_config={
                         'Stock': st.column_config.TextColumn('STOCK', width='small'),
                         'Signal': st.column_config.TextColumn('SIGNAL', width='small'),
                         'Score': st.column_config.NumberColumn('SCORE', format='%d'),
                         'VWAP': st.column_config.NumberColumn('VWAP', format='%.2f'),
                         'Entry': st.column_config.NumberColumn('ENTRY', format='%.2f'),
                         'SL': st.column_config.NumberColumn('SL', format='%.2f'),
                         'Target': st.column_config.NumberColumn('TARGET', format='%.2f'),
                         'Qty': st.column_config.NumberColumn('QTY', format='%d'),
                         'Risk ₹': st.column_config.NumberColumn('RISK ₹', format='₹%.0f')
                     })

    st.subheader('🔥 TOP 2 SNIPER TRADES')
    best = filtered[filtered['Score'] >= 60].head(2)
    if best.empty:
        st.warning('No high-quality setup with score ≥ 60.')
    else:
        cards = st.columns(len(best))
        for col, (_, row) in zip(cards, best.iterrows()):
            bg = '#198754' if row['Signal'] == 'BUY' else '#dc3545'
            with col:
                st.markdown(f'''<div style="padding:18px;border-radius:14px;background:{bg};color:white;min-height:220px;box-shadow:0 4px 12px rgba(0,0,0,.18)">
<h2 style="margin:0">{row['Stock']}</h2>
<h3>{row['Signal']} | Score {row['Score']}/100</h3><hr>
<b>ENTRY:</b> ₹{row['Entry']:.2f}<br>
<b>STOP LOSS:</b> ₹{row['SL']:.2f}<br>
<b>TARGET:</b> ₹{row['Target']:.2f}<br>
<b>VWAP:</b> ₹{row['VWAP']:.2f}<br>
<b>QTY:</b> {row['Qty']}<br>
<b>RISK:</b> ₹{row['Risk ₹']:.0f}
</div>''', unsafe_allow_html=True)

st.markdown('---')
st.caption('SMT PRO SNIPER | Asia/Kolkata | Educational use only. Confirm price, liquidity and risk before trading.')
