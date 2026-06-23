import os
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import requests

# ==========================================
# 1. 網頁基本設定與隱藏 Streamlit 原生外框
# ==========================================
st.set_page_config(page_title="ETF 籌碼與即時大數據監控面板", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}
        .block-container {
            padding-top: 0rem !important;
            padding-bottom: 0rem !important;
            padding-left: 0rem !important;
            padding-right: 0rem !important;
        }
        iframe {
            display: block;
            border: none;
        }
        /* 側邊導覽列（如果有的話）或是 Streamlit 基本元件字型與間距優化 */
        .stRadio > div {
            flex-direction: row;
            justify-content: center;
            background-color: #111827;
            padding: 10px;
            border-bottom: 1px solid #1f2937;
        }
        div.row-widget.stRadio > div[data-testid="stMarkdownContainer"] > p {
            color: #f3f4f6 !important;
            font-weight: bold;
        }
    </style>
""", unsafe_allow_html=True)

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"
WORKSHEET_TICKER = "代號"      # 個股代號對照
WORKSHEET_ETF_NAME = "名稱"    # ETF名稱對照

# ==========================================
# 2. 獨立安全的連線與資料載入核心
# ==========================================
def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]

    if creds_json:
        try:
            clean_json = creds_json.strip().strip("'").strip('"')
            return gspread.service_account_from_dict(json.loads(clean_json))
        except:
            pass

    json_path = os.path.join(os.getcwd(), 'credentials.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return gspread.service_account_from_dict(json.load(f))
    return None

@st.cache_resource
def init_gspread():
    try:
        gc = get_sheets_client()
        if gc: return gc.open(SHEET_NAME)
    except:
        pass
    return None

sh = init_gspread()

@st.cache_data(ttl=300)
def fetch_raw_sheet_data():
    if not sh: 
        return None, "無法連線至 Google 試算表，請檢查憑證設定。"
    try:
        ws = sh.worksheet(WORKSHEET_HISTORY)
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return None, f"工作表「{WORKSHEET_HISTORY}」內沒有足夠的數據列。"
        return raw_data, None
    except Exception as e:
        return None, f"讀取工作表「{WORKSHEET_HISTORY}」失敗: {str(e)}"

@st.cache_data(ttl=300)
def fetch_ticker_mapping():
    if not sh: return {}, "無法連線至 Google 試算表"
    try:
        ws = sh.worksheet(WORKSHEET_TICKER)
        raw_ticker = ws.get_all_values()
        if not raw_ticker or len(raw_ticker) < 1: return {}, None
        
        ticker_map = {}
        for row in raw_ticker[1:]:
            if len(row) >= 2:
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                if code: ticker_map[code] = name
        return ticker_map, None
    except Exception as e:
        return {}, f"讀取「{WORKSHEET_TICKER}」工作表失敗: {str(e)}"

@st.cache_data(ttl=300)
def fetch_etf_name_mapping():
    if not sh: return {}, "無法連線至 Google 試算表"
    try:
        ws = sh.worksheet(WORKSHEET_ETF_NAME)
        raw_etf = ws.get_all_values()
        if not raw_etf or len(raw_etf) < 1: return {}, None
        
        etf_name_map = {}
        for row in raw_etf[1:]:
            if len(row) >= 3:
                code = str(row[1]).strip()   
                name = str(row[2]).strip()   
                if code: etf_name_map[code] = name
        return etf_name_map, None
    except Exception as e:
        return {}, f"讀取「{WORKSHEET_ETF_NAME}」工作表失敗: {str(e)}"

# ==========================================
# 3. 證交所/櫃買中心 即時行情 API 整合模組
# ==========================================
def fetch_twse_live_data(etf_list):
    if not etf_list:
        return {}
    
    valid_etfs = []
    for code in etf_list:
        c_clean = str(code).strip()
        if c_clean:
            valid_etfs.append(c_clean)

    if not valid_etfs:
        return {}

    twse_market_data = {}
    ch_elements = []
    for code in valid_etfs:
        ch_elements.append(f"tse_{code}.tw")
        ch_elements.append(f"otc_{code}.tw")
        
    ch_param = "|".join(ch_elements)
    api_url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ch_param}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://mis.twse.com.tw/"
    }
    try:
        res = requests.get(api_url, headers=headers, timeout=10)
        if res.status_code == 200:
            res_json = res.json()
            msg_array = res_json.get("msgArray", [])
            for msg in msg_array:
                code_key = msg.get("c", "").strip() 
                if code_key:
                    # z: 當盤成交價, y: 昨收價, v: 當日累計成交量, n: 股票名稱
                    twse_market_data[code_key] = {
                        "n": msg.get("n", "-"),
                        "d": msg.get("d", ""),  
                        "z": msg.get("z", "-"),  
                        "p": msg.get("p", "-"),  
                        "y": msg.get("y", "-"),  
                        "v": msg.get("v", "0")   
                    }
    except Exception as e:
        print(f"證交所 API 連線異常: {e}")
    return twse_market_data

def process_and_standardize(raw_data, ticker_map=None):
    df = pd.DataFrame(raw_data[1:], columns=raw_data[0])
    df.columns = [str(c).strip() for c in df.columns]
    
    alias_map = {
        "etf": ["ETF代號", "ETF", "ETF碼"],
        "date": ["日期", "時間", "Date"],
        "stock": ["成分股代號", "股票代號", "代號", "商品代號"],
        "name": ["成分股名稱", "股票名稱", "名稱", "商品名稱"],
        "weight": ["持股權重", "權重", "權重(%)", "持股比例"],
        "volume": ["持有數量", "持有數", "張數", "持有張數", "股數", "持有股數"]
    }
    
    rename_dict = {}
    for standard, aliases in alias_map.items():
        for alias in aliases:
            if alias in df.columns:
                rename_dict[alias] = standard
                break
                
    orig_name_col = None
    for alias in alias_map["name"]:
        if alias in df.columns:
            orig_name_col = alias
            break

    df = df.rename(columns=rename_dict)
    
    missing = [k for k in ["etf", "date", "stock", "weight", "volume"] if k not in df.columns]
    if missing:
        return pd.DataFrame(), f"主要欄位對照失敗。缺少對應: {missing}"

    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.
