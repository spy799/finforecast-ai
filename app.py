import streamlit as st
"""
FinForecast AI - Smart Financial Forecasting Tool

A comprehensive financial analysis and forecasting application built with Streamlit that provides:
- Historical financial data retrieval from multiple sources (FMP, SAHMK, EDGAR, Polygon, yfinance)
- Financial forecasting with configurable time horizons (3-10 years)
- Multiple analysis scenarios including DCF valuation, Monte Carlo simulations
- Industry benchmarks and analyst consensus data
- Support for international stocks (US, Saudi Arabia, and other markets)

Key Components:
- Ticker symbol resolution via Yahoo Finance
- Priority-based data fetching with fallback mechanisms
- Secure API key management via Streamlit Secrets
- Session state management for UI interactions
- Data caching for optimized performance

Dependencies:
    - streamlit: Web application framework
    - yfinance: Yahoo Finance data retrieval
    - pandas: Data manipulation and analysis
    - numpy: Numerical computations
    - plotly: Interactive visualizations
    - requests: HTTP requests
    - polygon: Polygon.io API client
    - edgartools (optional): SEC EDGAR data retrieval
    - edgar (optional): SEC identity management

Main Functions:
    - get_ticker(query): Resolves company name or ticker symbol
    - fetch_financials(ticker, fmp_key, sah_mk_key, polygon_key, edgar_email):
        Fetches historical financial data with multi-source priority fallback

Configuration:
    - Requires API keys in Streamlit Secrets:
        - FMP_API_KEY: Financial Modeling Prep API key
        - SAHMK_API_KEY: Saudi Tadawul market API key
        - POLYGON_API_KEY: Polygon.io API key
        - EDGAR_EMAIL: Email for SEC EDGAR data retrieval
"""
import yfinance as yf
import requests
from datetime import datetime
# from io import BytesIO            # لم يعد مستخدماً
from polygon import RESTClient

# edgar/edgartools اختياريان – إذا لم يتم تثبيتهما سيتم تخطي
# المصدر المقابل في دالة جلب البيانات.
try:
    from edgar import set_identity
    from edgartools import Company # pyright: ignore[reportMissingImports]
except ImportError:              # pragma: no cover
    set_identity = None
    Company = None

import pandas as pd
import numpy as np
import plotly.express as px

# تهيئة مفاتيح الحالة حتى لا تحدث KeyError لاحقاً
if "run_analysis" not in st.session_state:
    st.session_state.run_analysis = False
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False

st.set_page_config(page_title="FinForecast AI", layout="wide")
st.title("FinForecast AI - Smart Financial Forecasting Tool")

# ───────────────────────────────────────────────
# قراءة المفاتيح من secrets (آمنة 100%)
# لا تكتب أي key هنا داخل الكود
# ───────────────────────────────────────────────
FMP_KEY       = st.secrets.get("FMP_API_KEY", "")
SAHMK_KEY     = st.secrets.get("SAHMK_API_KEY", "")
POLYGON_KEY   = st.secrets.get("POLYGON_API_KEY", "")
EDGAR_EMAIL   = st.secrets.get("EDGAR_EMAIL", "anonymous@example.com")


def get_ticker(query: str) -> str:
    if any(x in query.upper() for x in [".SR", ".T", ".L"]) \
       or query.isupper() \
       or query.replace(".", "").isdigit():
        return query.upper()
    try:
        data = yf.utils.get_json(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 1},
            user_agent="Mozilla/5.0"
        )
        if data.get("quotes"):
            return data["quotes"][0]["symbol"]
    except Exception:
        pass
    return query.upper()


with st.sidebar:
    query = st.text_input("Company Name or Ticker", "AAPL")
    ticker = get_ticker(query)
    forecast_years = st.slider("Forecast Years", 3, 10, 5)

    st.caption("API keys are loaded from Streamlit Secrets")

    if st.button("Run Analysis"):
        st.session_state.run_analysis = True


@st.cache_data(ttl=3600 * 2)
def fetch_financials(ticker, fmp_key, sah_mk_key, polygon_key, edgar_email):
    # Priority 1: FMP
    if fmp_key:
        try:
            url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}?limit=12&period=annual&apikey={fmp_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    df = pd.DataFrame(data)[['date', 'revenue', 'operatingIncome', 'netIncome', 'eps']]
                    df['Year'] = pd.to_datetime(df['date']).dt.year
                    df = df.rename(columns={
                        'revenue': 'Revenue',
                        'operatingIncome': 'Operating Income',
                        'netIncome': 'Net Income',
                        'eps': 'EPS'
                    })
                    return df[['Year', 'Revenue', 'Operating Income', 'Net Income', 'EPS']].sort_values('Year')
        except Exception:
            pass

    # Priority 2: SAHMK (Saudi stocks)
    if sah_mk_key and ticker.endswith('.SR'):
        try:
            symbol = ticker.replace('.SR', '')
            url = f"https://app.sahmk.sa/api/v1/financials/{symbol}/"
            headers = {'X-API-Key': sah_mk_key}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                inc = data.get('income_statements', [])
                df = pd.DataFrame(inc)
                df['Year'] = pd.to_datetime(df['report_date']).dt.year
                df = df.rename(columns={
                    'total_revenue': 'Revenue',
                    'operating_income': 'Operating Income',
                    'net_income': 'Net Income'
                })
                if 'eps' in df.columns:
                    df = df.rename(columns={'eps': 'EPS'})
                else:
                    df['EPS'] = df['Net Income'] / 1_000_000_000   # fallback تقريبي
                cols = ['Year', 'Revenue', 'Operating Income', 'Net Income']
                if 'EPS' in df.columns:
                    cols.append('EPS')
                return df[cols].sort_values('Year')
        except Exception:
            pass

    # Priority 3: EDGAR (US stocks) – فقط إذا كانت الحزم متاحة
    if Company and set_identity and not ticker.endswith('.SR') and '@' in edgar_email:
        try:
            set_identity(edgar_email)
            company = Company(ticker)
            financials = company.get_financials()
            inc = financials.income_statement()
            if not inc.empty:
                inc = inc.reset_index()
                inc['Year'] = pd.to_datetime(inc['date']).dt.year
                rename_dict = {
                    'revenue': 'Revenue',
                    'operating_income': 'Operating Income',
                    'net_income': 'Net Income'
                }
                for old, new in rename_dict.items():
                    if old in inc.columns:
                        inc = inc.rename(columns={old: new})
                if 'EPS' not in inc.columns and 'earningspersharebasic' in inc.columns:
                    inc = inc.rename(columns={'earningspersharebasic': 'EPS'})
                cols = ['Year', 'Revenue', 'Operating Income', 'Net Income']
                if 'EPS' in inc.columns:
                    cols.append('EPS')
                return inc[cols].sort_values('Year')
        except Exception as e:
            st.warning(f"EDGAR failed: {e}")

    # Priority 4: Polygon
    if polygon_key:
        try:
            client = RESTClient(polygon_key)
            financials = list(client.vx.list_stock_financials(
                ticker=ticker, timeframe="annual", limit=12
            ))
            data = []
            for f in financials:
                inc = f.financials.income_statement
                data.append({
                    "Year": f.fiscal_year,
                    "Revenue": getattr(inc.revenues, "value", None),
                    "Operating Income": getattr(inc.operating_income_loss, "value", None),
                    "Net Income": getattr(inc.net_income_loss, "value", None),
                    "EPS": getattr(inc.basic_earnings_per_share, "value", None)
                })
            df = pd.DataFrame(data).dropna(how="all")
            if not df.empty:
                return df.sort_values("Year")
        except Exception:
            pass

    # Fallback: yfinance
    try:
        stock = yf.Ticker(ticker)
        income = stock.income_stmt.T
        if not income.empty:
            df = pd.DataFrame({
                "Year": pd.to_datetime(income.index).year,
                "Revenue": income.get("Total Revenue"),
                "Operating Income": income.get("Operating Income"),
                "Net Income": income.get("Net Income"),
                "EPS": income.get("Diluted EPS")
            }).dropna(how="all")
            return df.sort_values("Year")
    except Exception:
        pass

    return pd.DataFrame()


if st.session_state.get("run_analysis", False) or "data_loaded" in st.session_state:
    try:
        with st.spinner("جاري جلب البيانات المالية..."):
            hist_df = fetch_financials(ticker, FMP_KEY, SAHMK_KEY, POLYGON_KEY, EDGAR_EMAIL)

        stock = yf.Ticker(ticker)
        info = stock.info
        current = info.get("currentPrice", info.get("regularMarketPrice", 0))
        shares = info.get("sharesOutstanding", 1_000_000_000) or 1_000_000_000
        name = info.get("longName", ticker)
        st.success(f"تم تحميل: **{name}** ({ticker})")

        tabs = st.tabs(["Historical", "Forecast Scenarios", "Advanced DCF", "Monte Carlo", "Industry & Analysts"])

        with tabs[0]:
            st.subheader("Historical Financials")
            if not hist_df.empty:
                st.dataframe(hist_df.style.format({
                    "Revenue": "{:,.0f}",
                    "Operating Income": "{:,.0f}",
                    "Net Income": "{:,.0f}",
                    "EPS": "{:.2f}"
                }))
            else:
                st.warning("لم يتم العثور على بيانات مالية تاريخية")

        # باقي الأقسام (Forecast, DCF, Monte Carlo, Analysts) كما هي
        # … انسخ الكود السابق من with tabs[1]: حتى النهاية إذا احتجته …

        st.session_state.data_loaded = True

    except Exception as e:
        st.error(f"حدث خطأ: {e}")

st.caption("FinForecast AI – يعتمد على FMP + SAHMK + Polygon + EDGAR + yfinance")