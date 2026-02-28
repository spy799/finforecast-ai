import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import requests
from datetime import datetime
from io import BytesIO
from polygon import RESTClient
from edgar import set_identity
from edgar import Company

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


def get_ticker(query):
    if any(x in query.upper() for x in [".SR", ".T", ".L"]) or query.isupper() or query.replace(".", "").isdigit():
        return query.upper()
    try:
        data = yf.utils.get_json(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 1},
            user_agent="Mozilla/5.0"
        )
        return data["quotes"][0]["symbol"]
    except:
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
        except:
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
        except:
            pass

    # Priority 3: EDGAR (US stocks)
    if not ticker.endswith('.SR') and '@' in edgar_email:
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
                # محاولة التعامل مع أسماء الأعمدة المختلفة
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
            st.warning(f"EDGAR failed: {str(e)}")

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
        except:
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
    except:
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
        # ... (انسخ باقي الكود من عند with tabs[1]: لحد النهاية بدون تغيير كبير)

        st.session_state.data_loaded = True

    except Exception as e:
        st.error(f"حدث خطأ: {str(e)}")

st.caption("FinForecast AI – يعتمد على FMP + SAHMK + Polygon + EDGAR + yfinance")