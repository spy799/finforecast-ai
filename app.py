import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.express as px
import requests
from datetime import datetime
from polygon import RESTClient
from sec_api import QueryApi

st.set_page_config(page_title="FinForecast AI", layout="wide")
st.title("FinForecast AI - Smart Financial Forecasting Tool")

# ───────────────────────────────────────────────
# قراءة المفاتيح من secrets
# ───────────────────────────────────────────────
FMP_KEY       = st.secrets.get("FMP_API_KEY", "")
SAHMK_KEY     = st.secrets.get("SAHMK_API_KEY", "")
POLYGON_KEY   = st.secrets.get("POLYGON_API_KEY", "")
SEC_API_KEY   = st.secrets.get("SEC_API_KEY", "")

# ───────────────────────────────────────────────
# دالة البحث عن رمز السهم
# ───────────────────────────────────────────────
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

# ───────────────────────────────────────────────
# واجهة الإدخال
# ───────────────────────────────────────────────
with st.sidebar:
    query = st.text_input("Company Name or Ticker", "AAPL")
    ticker = get_ticker(query)
    forecast_years = st.slider("Forecast Years", 3, 10, 5)

    if st.button("Run Analysis"):
        st.session_state.run_analysis = True

# ───────────────────────────────────────────────
# دالة جلب البيانات من SEC API
# ───────────────────────────────────────────────
def fetch_from_sec_api(ticker, api_key):
    try:
        queryApi = QueryApi(api_key=api_key)

        query = {
            "query": {
                "query_string": {
                    "query": f"ticker:{ticker} AND formType:(10-K OR 10-Q)"
                }
            },
            "from": 0,
            "size": 10,
            "sort": [{"filedAt": {"order": "desc"}}]
        }

        filings = queryApi.get_filings(query)

        data = []
        for f in filings["filings"]:
            inc = f.get("financials", {}).get("income_statement", {})
            data.append({
                "Year": int(f["filedAt"][:4]),
                "Revenue": inc.get("revenues"),
                "Operating Income": inc.get("operatingIncome"),
                "Net Income": inc.get("netIncome"),
                "EPS": inc.get("earningsPerShareBasic")
            })

        df = pd.DataFrame(data).dropna(how="all")
        return df.sort_values("Year")

    except Exception as e:
        st.warning(f"SEC API failed: {str(e)}")
        return pd.DataFrame()

# ───────────────────────────────────────────────
# دالة جلب البيانات المالية (FMP → SAHMK → SEC → Polygon → yfinance)
# ───────────────────────────────────────────────
@st.cache_data(ttl=7200)
def fetch_financials(ticker, fmp_key, sah_mk_key, polygon_key, sec_key):

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
                df['EPS'] = df.get('eps', df['Net Income'] / 1_000_000_000)
                return df[['Year', 'Revenue', 'Operating Income', 'Net Income', 'EPS']].sort_values('Year')
        except:
            pass

    # Priority 3: SEC API (US stocks)
    if sec_key and not ticker.endswith('.SR'):
        df = fetch_from_sec_api(ticker, sec_key)
        if not df.empty:
            return df

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

# ───────────────────────────────────────────────
# تشغيل التحليل
# ───────────────────────────────────────────────
if st.session_state.get("run_analysis", False):
    with st.spinner("جاري جلب البيانات المالية..."):
        hist_df = fetch_financials(ticker, FMP_KEY, SAHMK_KEY, POLYGON_KEY, SEC_API_KEY)

    stock = yf.Ticker(ticker)
    info = stock.info
    name = info.get("longName", ticker)

    st.success(f"تم تحميل: **{name}** ({ticker})")

    tabs = st.tabs(["Historical"])

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

st.caption("FinForecast AI – يعتمد على FMP + SAHMK + SEC API + Polygon + yfinance")