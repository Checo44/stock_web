import os
import re
import json
import requests
import gspread
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timedelta

# ==========================================
# 1. 網頁基本設定與隱藏 Streamlit 原生外框
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide", initial_sidebar_state="collapsed")

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
    </style>
""", unsafe_allow_html=True)

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"
WORKSHEET_TICKER = "代號"      # 個股代號對照工作表
WORKSHEET_ETF_NAME = "名稱"    # ETF名稱對照工作表

# FinMind API 金鑰
FINMIND_TOKEN = st.secrets.get("FINMIND_TOKEN", os.environ.get("FINMIND_TOKEN", ""))

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
        
        headers = [str(h).strip() for h in raw_ticker[0]]
        code_idx, name_idx, industry_idx = None, None, None
        
        for idx, h in enumerate(headers):
            if h in ["股票代號", "代號", "成分股代號", "商品代號"]:
                code_idx = idx
            if h in ["公司名稱", "股票名稱", "名稱", "成分股名稱", "商品名稱"]:
                name_idx = idx
            if h in ["產業別", "產業", "行業別", "行業", "Industry"]:
                industry_idx = idx
                
        if code_idx is None: code_idx = 0
        if name_idx is None: name_idx = 1 if len(headers) > 1 else 0
        
        ticker_map = {}
        for row in raw_ticker[1:]:
            if len(row) > max(code_idx, name_idx):
                code = str(row[code_idx]).strip()
                name = str(row[name_idx]).strip()
                industry = str(row[industry_idx]).strip() if (industry_idx is not None and len(row) > industry_idx) else "未分類"
                if code: 
                    if code.isalpha():
                        code = f"{code} US"
                    ticker_map[code] = {"name": name, "industry": industry}
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
# 3. FinMind PBR/PER 快取與查詢
# ==========================================
@st.cache_data(ttl=3600)  
def fetch_valuation_weights_cached(stock_codes, date_str):
    valid_stocks = []
    for code in stock_codes:
        clean_code = str(code).strip()
        if re.match(r"^\d{4,6}$", clean_code):
            valid_stocks.append(clean_code)
            
    if not valid_stocks:
        return {}

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        start_dt = dt - timedelta(days=7)
        start_date_str = start_dt.strftime("%Y-%m-%d")
    except Exception:
        start_date_str = date_str

    valuation_results = {}
    
    for code in valid_stocks:
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "TaiwanStockPER",  
            "data_id": code,
            "start_date": start_date_str,
            "end_date": date_str,
        }
        if FINMIND_TOKEN:
            params["token"] = FINMIND_TOKEN
            
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    last_record = data[-1]
                    valuation_results[code] = {
                        "pbr": float(last_record.get("PBR", last_record.get("pbr", 0.0)) or 0.0),
                        "per": float(last_record.get("PER", last_record.get("per", 0.0)) or 0.0)
                    }
        except Exception as e:
            print(f"FinMind API 連線失敗 ({code}): {e}")
            
    return valuation_results

# ==========================================
# 4. 外部即時行情 API 整合
# ==========================================
def fetch_twse_live_data(etf_list):
    if not etf_list:
        return {}
    
    valid_etfs = []
    for code in etf_list:
        c_clean = str(code).strip()
        if c_clean and (c_clean.isdigit() or len(c_clean) >= 4):
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
                ex_ch = msg.get("c", "").strip() 
                if ex_ch:
                    twse_market_data[ex_ch] = {
                        "d": msg.get("d", ""),  
                        "z": msg.get("z", "-"),  
                        "p": msg.get("p", "-"),  
                        "y": msg.get("y", "-"),  
                        "v": msg.get("v", "0")   
                    }
    except Exception as e:
        print(f"證交所後端連線異常: {e}")
    return twse_market_data

def process_and_standardize(raw_data, ticker_map=None):
    df = pd.DataFrame(raw_data[1:], columns=raw_data[0])
    df.columns = [str(c).strip() for c in df.columns]
    
    alias_map = {
        "etf": ["ETF代號", "ETF", "ETF碼"],
        "date": ["日期", "時間", "Date"],
        "stock": ["成分股代號", "股票代號", "代號", "商品代號"],
        "name": ["成分股名稱", "股票名稱", "公司名稱", "名稱", "商品名稱"], 
        "weight": ["持股權重", "權重", "權重(%)", "持股比例"],
        "volume": ["持有數量", "持有數", "張數", "持有張數", "股數", "持有股數"],
        "price": ["平均成交價格", "成交價格", "平均價格", "單價", "均價", "價格", "Price"]
    }
    
    rename_dict = {}
    for standard, aliases in alias_map.items():
        for alias in aliases:
            if alias in df.columns:
                rename_dict[alias] = standard
                break
                
    df = df.rename(columns=rename_dict)
    
    missing = [k for k in ["etf", "date", "stock", "weight", "volume"] if k not in df.columns]
    if missing:
        return pd.DataFrame(), f"主要欄位對照失敗。缺少對應: {missing}"

    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.dropna(subset=['date'])
    
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','', regex=False).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)

    if 'price' in df.columns:
        df['price'] = pd.to_numeric(df['price'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    elif len(raw_data[0]) >= 9:
        col_i = str(raw_data[0][8]).strip()
        if col_i in df.columns:
            df['price'] = pd.to_numeric(df[col_i].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
        else:
            df['price'] = 0.0
    else:
        df['price'] = 0.0

    df['stock'] = df['stock'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    is_pure_english = df['stock'].str.match(r'^[A-Za-z]+$')
    df.loc[is_pure_english, 'stock'] = df.loc[is_pure_english, 'stock'] + ' US'
    
    if 'name' not in df.columns:
        df['name'] = ""
    
    if ticker_map:
        df['name'] = df['stock'].apply(lambda x: ticker_map.get(x, {}).get('name', '') if isinstance(ticker_map.get(x), dict) else str(ticker_map.get(x, '')).strip())
        df['industry'] = df['stock'].apply(lambda x: ticker_map.get(x, {}).get('industry', '未分類') if isinstance(ticker_map.get(x), dict) else '未分類')
    else:
        df['name'] = df['name'].astype(str).str.strip()
        df['industry'] = '未分類'
        
    return df, None

# ==========================================
# 5. 主核心資料結構轉換
# ==========================================
def fetch_backend_data_to_json():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg: return "[]", {}, {}, {}, {}
        
    ticker_map, _ = fetch_ticker_mapping()
    etf_name_map, _ = fetch_etf_name_mapping()
    
    df, clean_err = process_and_standardize(raw_data, ticker_map=ticker_map)
    if clean_err or df.empty: return "[]", {}, {}, {}, {}
    
    all_etfs = sorted(list(df['etf'].dropna().unique()))
    twse_live_market = fetch_twse_live_data(all_etfs)
    
    try:
        latest_date = df['date'].max()
        unique_stocks = df['stock'].unique().tolist()
        val_map = fetch_valuation_weights_cached(unique_stocks, latest_date)
        
        df['pbr'] = df['stock'].apply(lambda x: val_map.get(x, {}).get("pbr", 0.0))
        df['per'] = df['stock'].apply(lambda x: val_map.get(x, {}).get("per", 0.0))
    except Exception as e:
        print(f"FinMind 數據併入失敗: {e}")
        df['pbr'] = 0.0
        df['per'] = 0.0
    
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False), {}, twse_live_market, ticker_map, etf_name_map

# ==========================================
# 6. 主渲染邏輯
# ==========================================
def main():
    json_data, wantgoo_market_data, twse_live_market, ticker_map, etf_name_map = fetch_backend_data_to_json()
    twse_json = json.dumps(twse_live_market, ensure_ascii=False)
    ticker_json = json.dumps(ticker_map, ensure_ascii=False)
    etf_name_json = json.dumps(etf_name_map, ensure_ascii=False)

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>ETF 籌碼大數據監控面板</title>
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
      <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
      <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      
      <style>
        body {
          font-family: 'Noto Sans TC', sans-serif;
          background-color: #f4f6f9;
          color: #333;
        }
        .navbar {
          background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
          box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .card {
          border: none;
          border-radius: 12px;
          box-shadow: 0 4px 6px rgba(0,0,0,0.05);
          margin-bottom: 1.5rem;
          background-color: #fff;
        }
        .card-header {
          background-color: #fff;
          border-bottom: 1px solid #edf2f9;
          font-weight: 700;
          font-size: 1.1rem;
          padding: 1rem 1.25rem;
          border-top-left-radius: 12px !important;
          border-top-right-radius: 12px !important;
        }
        .table { margin-bottom: 0; }
        .table th {
          background-color: #f8fafd;
          color: #4a5568;
          font-weight: 600;
        }
        .meta-card {
          background: #ffffff;
          border-left: 4px solid #2a5298;
          padding: 12px;
          border-radius: 8px;
          box-shadow: 0 2px 4px rgba(0,0,0,0.04);
          text-align: center;
        }
        .meta-label {
          font-size: 0.85rem;
          color: #718096;
          margin-bottom: 4px;
        }
        .meta-value {
          font-size: 1.15rem;
          font-weight: 700;
          color: #1a202c;
        }
        .nav-tabs .nav-link {
          border: none;
          color: #4a5568;
          font-weight: 500;
          padding: 0.75rem 1.25rem;
          border-radius: 8px;
          cursor: pointer;
        }
        .nav-tabs .nav-link.active {
          background-color: #e2e8f0;
          color: #1e3c72;
          font-weight: 700;
        }
        .custom-tab-content { display: none; }
        .custom-tab-content.active { display: block; }
        .loading-overlay {
          position: fixed;
          top: 0; left: 0; width: 100%; height: 100%;
          background: rgba(255,255,255,0.75);
          display: flex; justify-content: center; align-items: center;
          z-index: 9999;
        }
        .etf-list-group { max-height: 700px; overflow-y: auto; }
        .etf-item-btn {
          text-align: left;
          border-radius: 8px !important;
          margin-bottom: 4px;
          border: 1px solid #e2e8f0;
          transition: all 0.2s;
        }
        .etf-item-btn:hover { background-color: #f1f5f9; }
        .etf-item-btn.active {
          background-color: #1e3c72 !important;
          border-color: #1e3c72 !important;
          color: #fff !important;
          font-weight: bold;
        }

        /* 異動屬性徽章樣式 (新增/加碼/減持/剔除) */
        .badge-nature-new {
          background: linear-gradient(135deg, #ff8c00, #ff5500);
          color: #ffffff;
          padding: 4px 10px;
          border-radius: 20px;
          font-size: 0.8rem;
          font-weight: 700;
          box-shadow: 0 2px 4px rgba(255,140,0,0.25);
          display: inline-flex;
          align-items: center;
        }
        .badge-nature-up {
          background: linear-gradient(135deg, #ef4444, #dc2626);
          color: #ffffff;
          padding: 4px 10px;
          border-radius: 20px;
          font-size: 0.8rem;
          font-weight: 700;
          box-shadow: 0 2px 4px rgba(239,68,68,0.25);
          display: inline-flex;
          align-items: center;
        }
        .badge-nature-down {
          background: linear-gradient(135deg, #10b981, #059669);
          color: #ffffff;
          padding: 4px 10px;
          border-radius: 20px;
          font-size: 0.8rem;
          font-weight: 700;
          box-shadow: 0 2px 4px rgba(16,185,129,0.25);
          display: inline-flex;
          align-items: center;
        }
        .badge-nature-delete {
          background: linear-gradient(135deg, #475569, #334155);
          color: #ffffff;
          padding: 4px 10px;
          border-radius: 20px;
          font-size: 0.8rem;
          font-weight: 700;
          box-shadow: 0 2px 4px rgba(71,85,105,0.25);
          display: inline-flex;
          align-items: center;
        }

        /* 經理人連續操作動向徽章樣式 */
        .badge-trend-buy {
          background-color: #fef2f2;
          color: #dc2626;
          border: 1px solid #fecaca;
          padding: 5px 12px;
          border-radius: 20px;
          font-weight: 700;
          font-size: 0.85rem;
          display: inline-flex;
          align-items: center;
          box-shadow: 0 1px 2px rgba(220,38,38,0.05);
        }
        .badge-trend-sell {
          background-color: #f0fdf4;
          color: #166534;
          border: 1px solid #bbf7d0;
          padding: 5px 12px;
          border-radius: 20px;
          font-weight: 700;
          font-size: 0.85rem;
          display: inline-flex;
          align-items: center;
          box-shadow: 0 1px 2px rgba(22,101,52,0.05);
        }
        .badge-nature-new-pill {
          background-color: #fff7ed;
          color: #c2410c;
          border: 1px solid #ffedd5;
          padding: 5px 12px;
          border-radius: 20px;
          font-weight: 700;
          font-size: 0.85rem;
          display: inline-flex;
          align-items: center;
        }
        .badge-nature-delete-pill {
          background-color: #f8fafc;
          color: #475569;
          border: 1px solid #e2e8f0;
          padding: 5px 12px;
          border-radius: 20px;
          font-weight: 700;
          font-size: 0.85rem;
          display: inline-flex;
          align-items: center;
        }

        .etf-title-display {
          font-size: 1.5rem;
          font-weight: 700;
          color: #1e3c72;
          margin-bottom: 0.75rem;
          padding-left: 4px;
          display: flex;
          align-items: center;
        }
        .update-date-text { font-size: 0.9rem; font-weight: 400; color: #6c757d; margin-left: 12px; }
        .suggestion-box {
          position: absolute;
          background: white;
          border: 1px solid #ced4da;
          border-top: none;
          z-index: 1000;
          max-height: 200px;
          overflow-y: auto;
          width: 100%;
          border-bottom-left-radius: 8px;
          border-bottom-right-radius: 8px;
          box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .suggestion-item { padding: 10px 15px; cursor: pointer; }
        .suggestion-item:hover { background-color: #f1f5f9; }
        .selected-stock-tag {
          background-color: #e2e8f0;
          color: #1e3c72;
          padding: 4px 10px;
          border-radius: 20px;
          font-weight: 500;
          font-size: 0.9rem;
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .selected-stock-tag i { cursor: pointer; color: #ef4444; }
        .home-table th {
          background-color: #fff !important;
          color: #555 !important;
          font-weight: 500;
          border-bottom: 1px solid #dee2e6;
          padding: 10px;
        }
        .home-table td {
          padding: 10px;
          border-bottom: 1px solid #dee2e6;
          background-color: #fff !important;
        }

        .rank-medal {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 28px;
          height: 28px;
          border-radius: 50%;
          font-weight: 700;
          font-size: 0.85rem;
        }
        .medal-1 { background: linear-gradient(135deg, #ffd700, #ffa500); color: #fff; box-shadow: 0 2px 5px rgba(255,165,0,0.4); }
        .medal-2 { background: linear-gradient(135deg, #c0c0c0, #a9a9a9); color: #fff; box-shadow: 0 2px 5px rgba(169,169,169,0.3); }
        .medal-3 { background: linear-gradient(135deg, #cd7f32, #8b4513); color: #fff; box-shadow: 0 2px 5px rgba(139,69,19,0.4); }
        .medal-other { background-color: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }

        .weight-high { background-color: #1e3c72 !important; color: #ffffff !important; font-weight: 700 !important; font-size: 1.05rem !important; }
        .weight-med { background-color: #bcd2ee !important; color: #1e3c72 !important; font-weight: 700 !important; }
        .weight-low { background-color: #e6f2ff !important; color: #2a5298 !important; font-weight: 600 !important; }
        .weight-none { background-color: #f8fafc !important; color: #94a3b8 !important; }
        
        .summary-card {
          background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
          border-top: 4px solid #1e3c72;
          border-radius: 12px;
          box-shadow: 0 4px 10px rgba(0,0,0,0.06);
          padding: 18px;
          transition: transform 0.2s, box-shadow 0.2s;
        }
        .summary-card:hover { transform: translateY(-3px); box-shadow: 0 6px 15px rgba(0,0,0,0.1); }
      </style>
    </head>
    <body>

      <nav class="navbar navbar-expand-lg navbar-dark sticky-top">
        <div class="container-fluid">
          <a class="navbar-brand" href="#"><i class="bi bi-cpu-fill me-2"></i>ETF 籌碼大數據監控面板</a>
        </div>
      </nav>

      <div id="loading" class="loading-overlay">
        <div class="spinner-border text-primary" style="width: 3rem; height: 3rem;" role="status">
          <span class="visually-hidden">Loading...</span>
        </div>
      </div>

      <div class="container-fluid py-4 px-md-5">
        <ul class="nav nav-tabs mb-4" id="mainTabs">
          <li class="nav-item">
            <button class="nav-link active" id="tab-home" onclick="switchTab('content-home', 'tab-home')"><i class="bi bi-house-door-fill me-2"></i>首頁</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-g" onclick="switchTab('content-g', 'tab-g')"><i class="bi bi-radar text-info me-2"></i>主動型經理人共識雷達</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-a" onclick="switchTab('content-a', 'tab-a')"><i class="bi bi-pie-chart-fill me-2"></i>單檔 ETF 籌碼與持股</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-b" onclick="switchTab('content-b', 'tab-b')"><i class="bi bi-share-fill me-2"></i>個股籌碼分佈</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-f" onclick="switchTab('content-f', 'tab-f')"><i class="bi bi-ui-checks-grid me-2 text-primary"></i>ETF 智能組合篩選</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-c" onclick="switchTab('content-c', 'tab-c')"><i class="bi bi-globe me-2"></i>全市場異動總覽</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-d" onclick="switchTab('content-d', 'tab-d')"><i class="bi bi-fire me-2 text-danger"></i>市場熱度排行</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-e" onclick="switchTab('content-e', 'tab-e')"><i class="bi bi-arrow-left-right me-2"></i>ETF 交叉比較</button>
          </li>
        </ul>

        <div id="tabsContent">
          <!-- 首頁 Tab -->
          <div class="custom-tab-content active" id="content-home">
            <div class="card p-0">
              <div class="table-responsive">
                <table class="table home-table align-middle">
                  <thead>
                    <tr>
                      <th>ETF代號</th>
                      <th>ETF名稱</th>
                      <th>現價</th>
                      <th>漲跌幅</th>
                      <th>加權本益比</th>
                      <th>加權股淨比</th>
                    </tr>
                  </thead>
                  <tbody id="homeTableBody"></tbody>
                </table>
              </div>
            </div>
          </div>

          <!-- 主動型經理人共識雷達 Tab -->
          <div class="custom-tab-content" id="content-g">
            <div class="card p-3 mb-4 bg-light border">
              <div class="d-flex justify-content-between align-items-center mb-2">
                <div class="fw-bold text-dark"><i class="bi bi-check2-square me-1"></i>選取欲納入共識雷達分析範疇的主動式 ETF（預設全不選）：</div>
                <div>
                  <button class="btn btn-sm btn-outline-primary me-2" onclick="selectAllRadar()"><i class="bi bi-check-all me-1"></i>全選</button>
                  <button class="btn btn-sm btn-outline-secondary" onclick="clearAllRadar()"><i class="bi bi-x-square me-1"></i>全不選</button>
                </div>
              </div>
              <div class="d-flex flex-wrap gap-3 p-3 bg-white border rounded" id="radarCheckboxContainer"></div>
              
              <div class="row align-items-center g-3 mt-2">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-secondary"><i class="bi bi-calendar-range me-1"></i>共識觀測時間區間</label>
                  <select id="radarRangeType" class="form-select" onchange="toggleRadarCustomDates()">
                    <option value="1" selected>昨日變動 (1日區間)</option>
                    <option value="5">週變動 (5日區間)</option>
                    <option value="20">月變動 (20日區間 / 月線對齊)</option>
                    <option value="custom">自訂觀測區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="radarCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6">
                      <label class="form-label small text-muted">基準舊日期 (YYYY-MM-DD)</label>
                      <input type="text" id="radarStartDate" class="form-control" placeholder="如: 2024-01-02">
                    </div>
                    <div class="col-6">
                      <label class="form-label small text-muted">比較新日期 (YYYY-MM-DD)</label>
                      <input type="text" id="radarEndDate" class="form-control" placeholder="如: 2024-01-20">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-md-4">
                  <button class="btn btn-primary w-100" onclick="calculateRadarConsensus()"><i class="bi bi-arrow-repeat me-1"></i>重新計算經理人共識</button>
                </div>
              </div>
            </div>
            
            <div class="row g-4">
              <div class="col-md-6">
                <div class="card border-0 shadow-sm rounded-4">
                  <div class="card-header bg-white text-danger py-3 d-flex justify-content-between align-items-center">
                    <span><i class="bi bi-award-fill me-2"></i>🏆 黃金共識股 (最多主動型 ETF 同時加碼)</span>
                    <span class="badge bg-danger-subtle text-danger small">股數淨增動向 > 0</span>
                  </div>
                  <div class="table-responsive" style="max-height: 550px;">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr>
                          <th>股票標的</th>
                          <th class="text-end" style="width: 150px;">共識比例 / 家數</th>
                          <th class="px-4">詳細加碼主要陣容</th>
                        </tr>
                      </thead>
                      <tbody id="radarGoldBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
              
              <div class="col-md-6">
                <div class="card border-0 shadow-sm rounded-4">
                  <div class="card-header bg-white text-muted py-3 d-flex justify-content-between align-items-center">
                    <span><i class="bi bi-exclamation-triangle-fill me-2 text-warning"></i>⚠️ 避險警示股 (最多主動型 ETF 同時減持/剔除)</span>
                    <span class="badge bg-secondary-subtle text-dark small">股數淨減動向 &lt; 0 或移除</span>
                  </div>
                  <div class="table-responsive" style="max-height: 550px;">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr>
                          <th>股票標的</th>
                          <th class="text-end" style="width: 150px;">警示比例 / 家數</th>
                          <th class="px-4">詳細減持主要陣容</th>
                        </tr>
                      </thead>
                      <tbody id="radarWarningBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- 單檔 ETF 籌碼與持股 Tab -->
          <div class="custom-tab-content" id="content-a">
            <div class="row g-4">
              <div class="col-lg-3">
                <div class="card p-3 sticky-top" style="top: 80px;">
                  <div class="fw-bold text-secondary mb-3"><i class="bi bi-search me-1"></i>選取觀測 ETF</div>
                  <div class="list-group etf-list-group" id="etfListGroup"></div>
                </div>
              </div>
              
              <div class="col-lg-9">
                <div id="etfTitleContainer" style="display: none;">
                  <div class="etf-title-display">
                    <span id="txtEtfCode" class="badge bg-primary me-2 font-monospace"></span>
                    <span id="txtEtfName"></span>
                    <span id="txtUpdateDate" class="update-date-text"></span>
                  </div>
                </div>
                
                <div class="row g-3 mb-4">
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #3182ce;">
                      <div class="meta-label">市價</div>
                      <div class="meta-value" id="metaMarketPrice">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #e53e3e;">
                      <div class="meta-label">漲跌</div>
                      <div class="meta-value" id="metaChange">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #dd6b20;">
                      <div class="meta-label">成交量</div>
                      <div class="meta-value" id="metaVolume">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #319795;">
                      <div class="meta-label">台股加權平均本益比</div>
                      <div class="meta-value text-teal" id="metaWeightedPer">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #805ad5;">
                      <div class="meta-label">台股加權平均股淨比</div>
                      <div class="meta-value text-purple" id="metaWeightedPbr">-</div>
                    </div>
                  </div>
                </div>

                <!-- 單檔經理人風格與持股診斷卡片 -->
                <div class="card mb-4 border-start border-primary border-4 shadow-sm" id="diagnosticCard" style="display:none;">
                  <div class="card-header bg-white font-weight-bold text-dark"><i class="bi bi-clipboard-pulse text-primary me-2"></i>經理人投資風格與持股診斷</div>
                  <div class="card-body">
                    <div class="row g-3 text-center mb-3">
                      <div class="col-md-6 border-end">
                        <div class="text-muted small mb-1">本區間組合換股率 (Turnover Rate)</div>
                        <div class="fs-3 fw-bold text-primary font-monospace" id="txtTurnoverRate">0.00%</div>
                        <div class="mt-1"><span class="badge" id="badgeStyleTag">風格讀取中</span></div>
                      </div>
                      <div class="col-md-6">
                        <div class="text-muted small mb-1">診斷時間區間差值</div>
                        <div class="small text-secondary font-monospace fw-bold" id="txtDiagnosticInterval">-</div>
                      </div>
                    </div>
                    
                    <div class="row g-3 mb-3">
                      <div class="col-md-6">
                        <div class="p-2 border rounded bg-light" style="max-height: 200px; overflow-y:auto;">
                          <div class="fw-bold text-danger small border-bottom pb-1 mb-2"><i class="bi bi-shield-lock-fill me-1"></i>核心持股 (最新權重≥4% & 歷史出現率≥80%)</div>
                          <div class="d-flex flex-wrap gap-1" id="boxCoreList"></div>
                        </div>
                      </div>
                      <div class="col-md-6">
                        <div class="p-2 border rounded bg-light" style="max-height: 200px; overflow-y:auto;">
                          <div class="fw-bold text-info small border-bottom pb-1 mb-2"><i class="bi bi-rocket-takeoff-fill me-1"></i>衛星波段持股 (最新權重&lt;2% & 歷史出現率&lt;40%)</div>
                          <div class="d-flex flex-wrap gap-1" id="boxSatelliteList"></div>
                        </div>
                      </div>
                    </div>
                    
                    <div id="diagResultTextContainer" class="border-top pt-3 mt-3"></div>
                    
                    <div class="alert alert-secondary mb-0 py-2 px-3 mt-3 small border-0" style="background-color: #f8fafc; color: #64748b;">
                      <div class="row g-2">
                        <div class="col-md-6"><i class="bi bi-info-circle-fill me-1 text-primary"></i><b>顯著加碼標準：</b>異動股數增加且權重變動大於該規模的 0.5%。</div>
                        <div class="col-md-6"><i class="bi text-warning"></i><b>持倉成本說明：</b>當前公開數據集中不含實際持股成本資料。</div>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div class="row g-3">
                  <div class="col-lg-7">
                    <div class="card h-100">
                      <div class="card-header text-primary d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-list-stars me-2"></i>最新成分股持股明細</span>
                        <div id="selectedIndustryDisplayContainer"></div>
                      </div>
                      <div class="table-responsive" style="max-height: 700px;">
                        <table class="table table-hover align-middle">
                          <thead>
                            <tr>
                              <th>股票代號</th>
                              <th>股票名稱</th>
                              <th class="text-end">持股權重</th>
                              <th class="text-end">持股股數</th>
                              <th class="text-end">本益比</th>
                            </tr>
                          </thead>
                          <tbody id="stockTableBody"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  
                  <div class="col-lg-5">
                    <div class="card mb-3">
                      <div class="card-header text-primary"><i class="bi bi-pie-chart me-2"></i>成分股產業別分佈 (點擊區塊或圖例可篩選)</div>
                      <div class="card-body" style="position: relative; height: 320px;">
                        <canvas id="industryPieChart"></canvas>
                      </div>
                    </div>

                    <div class="card">
                      <div class="card-header text-secondary"><i class="bi bi-cash-coin me-2"></i>非股票資產項目</div>
                      <div class="table-responsive" style="max-height: 350px;">
                        <table class="table table-hover align-middle">
                          <thead>
                            <tr><th>資產代號</th><th>資產項目</th><th class="text-end">權重</th><th>資產價值(股)</th></tr>
                          </thead>
                          <tbody id="assetTableBody"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div class="card p-3 mb-4 mt-4 bg-light border">
                  <div class="row align-items-center g-3">
                    <div class="col-md-4">
                      <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>籌碼比較天數 / 範圍</label>
                      <select id="rangeType" class="form-select" onchange="toggleCustomDates()">
                        <option value="1" selected>昨日比較 (1日變動)</option>
                        <option value="5">週變動比較 (5日變動)</option>
                        <option value="20">月變動比較 (20日區間 / 月線對齊)</option>
                        <option value="custom">自訂指定雙日期區間</option>
                      </select>
                    </div>
                    <div class="col-md-5" id="customDateGroup" style="display: none;">
                      <div class="row">
                        <div class="col-6">
                          <label class="form-label small text-muted">基準舊日期 (YYYY-MM-DD)</label>
                          <input type="text" id="startDateInput" class="form-control" placeholder="如: 2024-01-02">
                        </div>
                        <div class="col-6">
                          <label class="form-label small text-muted">比較新日期 (YYYY-MM-DD)</label>
                          <input type="text" id="endDateInput" class="form-control" placeholder="如: 2024-01-09">
                        </div>
                      </div>
                    </div>
                    <div class="col-md-3 pt-md-4">
                      <button class="btn btn-primary w-100" onclick="refreshCurrentEtf()"><i class="bi bi-arrow-repeat me-1"></i>重新計算籌碼增減</button>
                    </div>
                  </div>
                </div>
                
                <!-- 成分股經理人籌碼異動明細表格 (已修正排序與動向) -->
                <div class="card">
                  <div class="card-header text-dark bg-white d-flex justify-content-between align-items-center py-3">
                    <span class="fw-bold"><i class="bi bi-arrow-left-right me-2 text-primary"></i>成分股經理人籌碼異動明細</span>
                    <span class="badge bg-light text-secondary border fw-normal">依新增 ➔ 加碼 ➔ 減持 ➔ 剔除排序，並依變動股數排列</span>
                  </div>
                  <div class="table-responsive">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr>
                          <th>股票標的</th>
                          <th>異動屬性</th>
                          <th class="text-end">張數 / 股數增減變動 (權重異動)</th>
                          <th class="px-4">經理人連續操作動向</th>
                        </tr>
                      </thead>
                      <tbody id="changeTableBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- 個股籌碼分佈 Tab -->
          <div class="custom-tab-content" id="content-b">
            <div class="card p-4 bg-light mb-4">
              <div class="row align-items-center g-3" style="position: relative;">
                <div class="col-md-6" style="position: relative;">
                  <label class="form-label fw-bold text-dark fs-5"><i class="bi bi-search me-1 text-primary"></i>搜尋單一上市櫃股票 (台股/美股)</label>
                  <input type="text" id="stockSearchInput" class="form-control form-control-lg" placeholder="請輸入股票名稱或代號 (如: NVDA 或 2330)" onkeyup="searchStockSuggestions(this.value, 'searchSuggestions', 'stockSearchInput', false)">
                  <div id="searchSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-md-2 pt-md-4">
                  <button class="btn btn-primary btn-lg w-100" onclick="searchStockDistribution()"><i class="bi bi-pie-chart me-1"></i>分析分佈</button>
                </div>
              </div>
            </div>
            
            <div id="stockResultContainer" style="display: none;">
              <div class="row g-4">
                <div class="col-md-4">
                  <div class="card p-4 text-center mb-4">
                    <h5 class="text-muted mb-2">觀測目標</h5>
                    <h2 class="fw-bold text-primary mb-3" id="resStockTitle">-</h2>
                    <div class="row g-2 mt-2">
                      <div class="col-6">
                        <div class="p-2 border rounded bg-light">
                          <div class="small text-muted">全市場聯動屬性</div>
                          <div class="fw-bold fs-5 mt-1" id="trendStockStatus">-</div>
                        </div>
                      </div>
                      <div class="col-6">
                        <div class="p-2 border rounded bg-light">
                          <div class="small text-muted">區間淨加減持</div>
                          <div class="fw-bold fs-5 mt-1 text-danger" id="trendStockTotalVol">-</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div class="card">
                    <div class="card-header text-dark"><i class="bi bi-layer-forward me-2 text-warning"></i>各大 ETF 基金對此股票之籌碼調整明細</div>
                    <div class="table-responsive">
                      <table class="table table-hover align-middle">
                        <thead>
                          <tr><th>持有之 ETF</th><th>區間籌碼增減變動 (股數)</th></tr>
                        </thead>
                        <tbody id="stockDistBody"></tbody>
                      </table>
                    </div>
                  </div>
                </div>
                
                <div class="col-md-8">
                  <div class="card">
                    <div class="card-header text-primary"><i class="bi bi-grid-3x3-gap-fill me-2"></i>該個股目前被哪些 ETF 所持有？（依持股權重排行）</div>
                    <div class="table-responsive">
                      <table class="table align-middle">
                        <thead>
                          <tr><th>持有之 ETF 代號</th><th>持有之 ETF 名稱</th><th class="text-end">持股權重比例</th><th class="text-end">持有股數</th></tr>
                        </thead>
                        <tbody id="stockDistBody2"></tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- ETF 智能組合篩選 Tab -->
          <div class="custom-tab-content" id="content-f">
            <div class="card p-4 bg-light border-0 shadow-sm rounded-4 mb-4">
              <h4 class="fw-bold text-dark mb-2"><i class="bi bi-cpu-fill text-primary me-2"></i>AI 投資組合回溯目標搜尋器</h4>
              <p class="text-muted small">請任意輸入並挑選多檔全球投資目標公司（如：台積電、聯發科、NVDA），系統將深度回溯大數據，精算出同時重疊包含這群目標公司的精選 ETF 陣容。</p>
              
              <div class="row align-items-center g-3" style="position: relative;">
                <div class="col-md-5" style="position: relative;">
                  <label class="form-label fw-bold text-secondary">請輸入個股名稱或代號（支援模糊搜尋與複選）</label>
                  <input type="text" id="matcherInput" class="form-control" placeholder="輸入台股或美股代號/名稱" onkeyup="searchStockSuggestions(this.value, 'matcherSuggestions', 'matcherInput', true)">
                  <div id="matcherSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-12 mt-3">
                  <div class="fw-bold text-secondary mb-2">目前已選取的投資目標公司：</div>
                  <div id="selectedTargetContainer" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded" style="min-height: 58px;">
                    <span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選組合</span>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="card shadow-sm rounded-4 border-0">
              <div class="card-header bg-white py-3 border-bottom"><i class="bi bi-hand-thumbs-up-fill me-2 text-success"></i>大數據重疊包含回溯分析結果</div>
              <div class="table-responsive">
                <table class="table align-middle">
                  <thead>
                    <tr>
                      <th>精選推薦 ETF</th>
                      <th>ETF 名稱</th>
                      <th class="text-end">所包含您選取目標之總權重(%)</th>
                      <th class="px-4">成分重疊明細對照</th>
                    </tr>
                  </thead>
                  <tbody id="matchResultBody">
                    <tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          
          <!-- 全市場異動總覽 Tab -->
          <div class="custom-tab-content" id="content-c">
            <div class="card p-3 mb-4 bg-light border">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>全市場異動比較天數 / 範圍</label>
                  <select id="globalRangeType" class="form-select" onchange="toggleGlobalChanges()">
                    <option value="1" selected>昨日比較 (1日變動)</option>
                    <option value="5">週變動比較 (5日變動)</option>
                    <option value="20">月變動比較 (20日區間 / 月線對齊)</option>
                    <option value="custom">自訂指定雙日期區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="globalCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6">
                      <label class="form-label small text-muted">基準舊日期 (YYYY-MM-DD)</label>
                      <input type="text" id="globalStartDate" class="form-control" placeholder="如: 2024-01-02">
                    </div>
                    <div class="col-6">
                      <label class="form-label small text-muted">比較新日期 (YYYY-MM-DD)</label>
                      <input type="text" id="globalEndDate" class="form-control" placeholder="如: 2024-01-09">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-md-4">
                  <button class="btn btn-success w-100" onclick="loadGlobalChanges()"><i class="bi bi-arrow-repeat me-1"></i>生成全市場異動報表</button>
                </div>
              </div>
            </div>
            
            <div class="row g-4">
              <div class="col-md-6">
                <div class="card border-0 shadow-sm rounded-4">
                  <div class="card-header bg-white text-danger py-3"><i class="bi bi-plus-circle-fill me-2"></i>全市場 ETF 新增成分股排行</div>
                  <div class="table-responsive" style="max-height: 500px;">
                    <table class="table table-hover align-middle">
                      <thead><tr><th>股票標的</th><th>納入之 ETF 基金清單</th></tr></thead>
                      <tbody id="globalNewBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
              
              <div class="col-md-6">
                <div class="card border-0 shadow-sm rounded-4">
                  <div class="card-header bg-white text-muted py-3"><i class="bi bi-dash-circle-fill me-2"></i>全市場 ETF 剔除成分股排行</div>
                  <div class="table-responsive" style="max-height: 500px;">
                    <table class="table table-hover align-middle">
                      <thead><tr><th>股票標的</th><th>剔除之 ETF 基金清單</th></tr></thead>
                      <tbody id="globalDelBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- 市場熱度排行 Tab -->
          <div class="custom-tab-content" id="content-d">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-secondary">熱度統計比較範圍</label>
                  <select id="heatRangeType" class="form-select" onchange="toggleHeatCustomDates()">
                    <option value="1" selected>日變動</option>
                    <option value="5">週變動</option>
                    <option value="20">月變動 (20日區間)</option>
                    <option value="custom">自訂區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="heatCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6"><input type="text" id="heatStartDate" class="form-control" placeholder="舊日期 YYYY-MM-DD"></div>
                    <div class="col-6"><input type="text" id="heatEndDate" class="form-control" placeholder="新日期 YYYY-MM-DD"></div>
                  </div>
                </div>
                <div class="col-md-3 pt-md-4">
                  <button class="btn btn-danger w-100" onclick="loadMarketHeat()"><i class="bi bi-fire me-1"></i>生成市場熱度分析</button>
                </div>
              </div>
            </div>

            <ul class="nav nav-pills mb-4" id="heatTypeTabs" role="tablist">
              <li class="nav-item" role="presentation">
                <button class="nav-link active fw-bold" id="tab-heat-amt" data-bs-toggle="pill" data-bs-target="#heat-amt-pane" type="button" role="tab"><i class="bi bi-currency-dollar me-1"></i>依買賣超金額排行</button>
              </li>
              <li class="nav-item" role="presentation">
                <button class="nav-link fw-bold" id="tab-heat-vol" data-bs-toggle="pill" data-bs-target="#heat-vol-pane" type="button" role="tab"><i class="bi bi-bar-chart-line-fill me-1"></i>依買賣超張數/股數排行</button>
              </li>
            </ul>

            <div class="tab-content" id="heatTabContent">
              <div class="tab-pane fade show active" id="heat-amt-pane" role="tabpanel">
                <h5 class="fw-bold text-primary mb-3"><i class="bi bi-flag-fill me-2"></i>國內標的 (台股) - 買賣超金額排行</h5>
                <div class="row g-4 mb-4">
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>國內標的 - 買超金額前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">估算買賣超金額</th><th class="text-end">買賣超張數</th></tr></thead>
                          <tbody id="heatBuyAmtBodyDom"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-success"><i class="bi bi-graph-down-arrow me-2"></i>國內標的 - 賣超金額前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">估算買賣超金額</th><th class="text-end">買賣超張數</th></tr></thead>
                          <tbody id="heatSellAmtBodyDom"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>

                <h5 class="fw-bold text-primary mb-3"><i class="bi bi-globe me-2"></i>國外標的 - 買賣超金額排行</h5>
                <div class="row g-4">
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>國外標的 - 買超金額前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">估算買賣超金額</th><th class="text-end">買賣超股數</th></tr></thead>
                          <tbody id="heatBuyAmtBodyFor"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-success"><i class="bi bi-graph-down-arrow me-2"></i>國外標的 - 賣超金額前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">估算買賣超金額</th><th class="text-end">買賣超股數</th></tr></thead>
                          <tbody id="heatSellAmtBodyFor"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="tab-pane fade" id="heat-vol-pane" role="tabpanel">
                <h5 class="fw-bold text-primary mb-3"><i class="bi bi-flag-fill me-2"></i>國內標的 (台股) - 買賣超張數排行</h5>
                <div class="row g-4 mb-4">
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>國內標的 - 買超張數前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">買賣超張數</th><th class="text-end">估算買賣超金額</th></tr></thead>
                          <tbody id="heatBuyVolBodyDom"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-success"><i class="bi bi-graph-down-arrow me-2"></i>國內標的 - 賣超張數前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">買賣超張數</th><th class="text-end">估算買賣超金額</th></tr></thead>
                          <tbody id="heatSellVolBodyDom"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>

                <h5 class="fw-bold text-primary mb-3"><i class="bi bi-globe me-2"></i>國外標的 - 買賣超股數排行</h5>
                <div class="row g-4">
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>國外標的 - 買超股數前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">買賣超股數</th><th class="text-end">估算買賣超金額</th></tr></thead>
                          <tbody id="heatBuyVolBodyFor"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  <div class="col-md-6">
                    <div class="card">
                      <div class="card-header text-success"><i class="bi bi-graph-down-arrow me-2"></i>國外標的 - 賣超股數前 10 大</div>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">買賣超股數</th><th class="text-end">估算買賣超金額</th></tr></thead>
                          <tbody id="heatSellVolBodyFor"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- ETF 交叉比較 Tab -->
          <div class="custom-tab-content" id="content-e">
            <div class="card p-3 mb-4 bg-light">
              <div class="fw-bold text-dark mb-2"><i class="bi bi-check2-square me-1"></i>勾選欲交叉比較的 ETF 基金清單（支援複選多檔進行橫向權重對照與熱力圖分析）</div>
              <div class="d-flex flex-wrap gap-3 p-3 bg-white border rounded" id="compareCheckboxContainer"></div>
            </div>
            
            <div id="compareSummarySection" style="display: none;" class="mb-4">
              <div class="fw-bold text-secondary mb-2"><i class="bi bi-lightning-charge-fill text-warning me-1"></i>交叉比對核心摘要（Top 3 重疊焦點個股）</div>
              <div class="row g-3" id="compareSummaryCards"></div>
            </div>

            <div class="card mb-4" id="coreHoldingsCard" style="display: none;">
              <div class="card-header bg-white text-primary fw-bold d-flex align-items-center">
                <i class="bi bi-shield-heart-fill me-2 text-danger"></i>【英雄所見略同】共同核心持股矩陣（選定之 ETF 皆全數持有）
              </div>
              <div class="table-responsive">
                <table class="table table-bordered align-middle">
                  <thead><tr id="compareCoreTableHeader"><th>股票代號</th><th>股票名稱</th><th>共同持有度</th></tr></thead>
                  <tbody id="compareCoreTableBody"></tbody>
                </table>
              </div>
            </div>

            <div class="card" id="uniqueHoldingsCard" style="display: none;">
              <div class="card-header bg-white text-secondary fw-bold d-flex align-items-center">
                <i class="bi bi-pie-chart-fill me-2 text-warning"></i>【獨門特色持股】個別差異明細矩陣（僅部分 ETF 持有）
              </div>
              <div class="table-responsive">
                <table class="table table-bordered align-middle">
                  <thead><tr id="compareUniqueTableHeader"><th>股票代號</th><th>股票名稱</th><th>共同持有度</th></tr></thead>
                  <tbody id="compareUniqueTableBody"></tbody>
                </table>
              </div>
            </div>

            <div class="card p-5 text-center text-muted" id="comparePlaceholder">
              <i class="bi bi-grid-3x3-gap mb-3" style="font-size: 3rem;"></i>
              <div>請在上方勾選至少一檔以上的 ETF 基金開始進行多方橫向對照。</div>
            </div>
          </div>

        </div>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
      <script>
        const globalRawData = __DATA_PLACEHOLDER__;
        const twseLiveMarketData = __TWSE_PLACEHOLDER__;
        const tickerMappingData = __TICKER_PLACEHOLDER__;
        const etfNameMappingData = __ETF_NAME_PLACEHOLDER__;

        let selectedEtf = null;
        let selectedTargetStocks = [];
        let currentEtfStocks = [];       
        let selectedIndustries = [];     
        let industryChartInstance = null; 

        window.onload = function() {
            document.getElementById('loading').style.display = 'none';
            if (!globalRawData || globalRawData.length === 0) {
                alert("後端未成功載入歷史數據，請確認試算表名稱與結構。");
                return;
            }
            initDashboard();
        };

        function switchTab(contentId, tabId) {
            document.querySelectorAll('.custom-tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('#mainTabs .nav-link').forEach(el => el.classList.remove('active'));
            document.getElementById(contentId).classList.add('active');
            document.getElementById(tabId).classList.add('active');

            if (contentId === 'content-g') {
                calculateRadarConsensus();
            } else if (contentId === 'content-c') {
                loadGlobalChanges();
            } else if (contentId === 'content-d') {
                loadMarketHeat();
            }
        }

        function isNormalStock(code, name) {
            let meta = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL"];
            if (!code || code.trim() === "") return false;
            
            let cleanCode = code.trim();
            let cleanName = name ? name.trim() : "";
            
            if (meta.includes(cleanCode) || (cleanName && meta.includes(cleanName))) return false;
            
            let cashEx = [
                "DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨",
                "RDI", "DR_", "RECEIVABLES", "DIVIDENDS", "DISPOSAL", "INVESTMENTS", "權證", "型購", "型售","買權","賣權","TWSE"
            ];
            
            let upperCode = cleanCode.toUpperCase();
            let upperName = cleanName.toUpperCase();
            if (cashEx.some(k => upperCode.includes(k.toUpperCase()) || upperName.includes(k.toUpperCase()))) return false;
            
            if (/^[GBAHF][A-Z0-9]{5}$/.test(upperCode)) {
                return false;
            }
            return true;
        }

        function initDashboard() {
            let etfSet = new Set();
            globalRawData.forEach(r => { if(r.etf) etfSet.add(r.etf); });
            let sortedEtfs = Array.from(etfSet).sort();

            let listGroup = document.getElementById('etfListGroup');
            let compareContainer = document.getElementById('compareCheckboxContainer');
            let radarContainer = document.getElementById('radarCheckboxContainer');
            
            let listHtml = "";
            let compareHtml = "";
            let radarHtml = "";
            let homeHtml = "";

            sortedEtfs.forEach((etf, index) => {
                let mappedName = etfNameMappingData[etf] || "未知名稱";
                listHtml += `<button class="list-group-item list-group-item-action etf-item-btn font-monospace" id="btn-etf-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-box-se me-2 text-primary"></i><b>${etf}</b> <span class="text-muted small ms-1">${mappedName}</span></button>`;
                compareHtml += `<div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" value="${etf}" id="chk-${etf}" onchange="renderCompareMatrix()"><label class="form-check-label font-monospace" for="chk-${etf}"><b>${etf}</b> <span class="text-muted small">${mappedName}</span></label></div>`;
                
                radarHtml += `<div class="form-check form-check-inline"><input class="form-check-input radar-cb" type="checkbox" value="${etf}" id="radar-chk-${etf}" onchange="calculateRadarConsensus()"><label class="form-check-label font-monospace" for="radar-chk-${etf}"><b>${etf}</b> <span class="text-muted small">${mappedName}</span></label></div>`;

                let price = "-";
                let changePct = "-";
                let twseData = twseLiveMarketData[etf] || null;
                if (twseData) {
                    let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
                    let yesterdayPrice = parseFloat(twseData.y) || 0;
                    if (priceVal > 0) {
                        price = priceVal.toFixed(2);
                        if (yesterdayPrice > 0) {
                            let diff = priceVal - yesterdayPrice;
                            changePct = ((diff / yesterdayPrice) * 100).toFixed(2);
                        }
                    }
                }
                
                let styleColor = "";
                if(parseFloat(changePct) > 0) styleColor = "text-danger fw-bold";
                if(parseFloat(changePct) < 0) styleColor = "text-success fw-bold";
                let displayChange = changePct !== "-" ? (parseFloat(changePct) > 0 ? `+${changePct}%` : `${changePct}%`) : "-";

                let etfData = globalRawData.filter(d => d.etf === etf);
                let dates = etfData.map(d => d.date);
                let sortedDates = [...new Set(dates)].sort((a,b) => new Date(a) - new Date(b));
                let latestDate = sortedDates[sortedDates.length - 1];
                let latestRows = etfData.filter(d => d.date === latestDate);

                let stocks = latestRows.filter(r => isNormalStock(r.stock, r.name));
                let totalTwWeightPer = 0;
                let totalTwWeightPbr = 0;
                let weightedPerSum = 0;
                let weightedPbrSum = 0;

                stocks.forEach(r => {
                    let perVal = r.per ? Number(r.per) : 0;
                    let pbrVal = r.pbr ? Number(r.pbr) : 0;
                    let w = Number(r.weight);
                    
                    if (perVal > 0) {
                        weightedPerSum += perVal * w;
                        totalTwWeightPer += w;
                    }
                    if (pbrVal > 0) {
                        weightedPbrSum += pbrVal * w;
                        totalTwWeightPbr += w;
                    }
                });

                let weightedPer = totalTwWeightPer > 0 ? (weightedPerSum / totalTwWeightPer).toFixed(2) : "-";
                let weightedPbr = totalTwWeightPbr > 0 ? (weightedPbrSum / totalTwWeightPbr).toFixed(2) : "-";

                homeHtml += `<tr>
                    <td class="font-monospace fw-bold">${etf}</td>
                    <td class="fw-bold text-secondary">${mappedName}</td>
                    <td class="font-monospace fw-bold">${price}</td>
                    <td class="font-monospace ${styleColor}">${displayChange}</td>
                    <td class="font-monospace fw-bold text-info">${weightedPer}</td>
                    <td class="font-monospace fw-bold text-teal" style="color: #319795 !important;">${weightedPbr}</td>
                </tr>`;
            });

            listGroup.innerHTML = listHtml;
            compareContainer.innerHTML = compareHtml;
            if(radarContainer) radarContainer.innerHTML = radarHtml;
            document.getElementById('homeTableBody').innerHTML = homeHtml;

            if(sortedEtfs.length > 0) {
                selectEtf(sortedEtfs[0]);
            }
        }

        function selectAllRadar() {
            document.querySelectorAll('.radar-cb').forEach(cb => cb.checked = true);
            calculateRadarConsensus();
        }

        function clearAllRadar() {
            document.querySelectorAll('.radar-cb').forEach(cb => cb.checked = false);
            calculateRadarConsensus();
        }

        function toggleRadarCustomDates() {
            let type = document.getElementById('radarRangeType').value;
            document.getElementById('radarCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function calculateRadarConsensus() {
            let checkedEtfs = Array.from(document.querySelectorAll('.radar-cb:checked')).map(cb => cb.value);
            if (checkedEtfs.length === 0) {
                document.getElementById('radarGoldBody').innerHTML = '<tr><td colspan="3" class="text-center text-muted">請先勾選上方欲納入分析的主動式 ETF 清單</td></tr>';
                document.getElementById('radarWarningBody').innerHTML = '<tr><td colspan="3" class="text-center text-muted">請先勾選上方欲納入分析的主動式 ETF 清單</td></tr>';
                return;
            }

            let type = document.getElementById('radarRangeType').value;
            let goldMap = {}; 
            let warningMap = {}; 

            checkedEtfs.forEach(eCode => {
                let etfData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if(dates.length < 2) return;

                let dOld = null, dNew = dates[dates.length - 1];
                if (type === 'custom') {
                    dOld = document.getElementById('radarStartDate').value;
                    dNew = document.getElementById('radarEndDate').value;
                } else {
                    let offset = parseInt(type);
                    if(dates.length > offset) {
                        dOld = dates[dates.length - 1 - offset];
                    } else {
                        dOld = dates[0];
                    }
                }

                if(!dOld || !dNew) return;

                let oldRows = etfData.filter(d => d.date === dOld);
                let newRows = etfData.filter(d => d.date === dNew);

                let allStocks = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])].filter(s => {
                    let match = newRows.find(x=>x.stock===s) || oldRows.find(x=>x.stock===s);
                    return match ? isNormalStock(match.stock, match.name) : false;
                });

                allStocks.forEach(sCode => {
                    let oRow = oldRows.find(x => x.stock === sCode);
                    let nRow = newRows.find(x => x.stock === sCode);
                    let oVol = oRow ? Number(oRow.volume) : 0;
                    let nVol = nRow ? Number(nRow.volume) : 0;
                    let diff = nVol - oVol;

                    let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知股票");
                    let token = sCode + "||" + sName;

                    if (diff > 0) {
                        if(!goldMap[token]) goldMap[token] = [];
                        goldMap[token].push(eCode);
                    } else if (diff < 0 || (oVol > 0 && nVol === 0)) {
                        if(!warningMap[token]) warningMap[token] = [];
                        warningMap[token].push(eCode);
                    }
                });
            });

            let goldArray = Object.keys(goldMap).map(k => {
                let [code, name] = k.split("||");
                return { code: code, name: name, etfs: goldMap[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            let warningArray = Object.keys(warningMap).map(k => {
                let [code, name] = k.split("||");
                return { code: code, name: name, etfs: warningMap[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            let totalChecked = checkedEtfs.length;

            let goldHtml = goldArray.map(x => {
                let listChips = x.etfs.map(e => `<span class="badge bg-light text-danger border me-1"><b>${e}</b></span>`).join('');
                let strengthPct = Math.round((x.etfs.length / totalChecked) * 100);
                return `<tr>
                    <td class="fw-bold">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                    <td class="text-end">
                      <span class="font-monospace fw-bold text-danger fs-6">${x.etfs.length} / ${totalChecked} 檔</span>
                      <div class="progress mt-1" style="height: 4px; background-color: #fee2e2;">
                        <div class="progress-bar bg-danger" role="progressbar" style="width: ${strengthPct}%"></div>
                      </div>
                    </td>
                    <td class="px-4">${listChips}</td>
                </tr>`;
            }).join('');

            let warningHtml = warningArray.map(x => {
                let listChips = x.etfs.map(e => `<span class="badge bg-light text-secondary border me-1"><b>${e}</b></span>`).join('');
                let strengthPct = Math.round((x.etfs.length / totalChecked) * 100);
                return `<tr>
                    <td class="fw-bold text-secondary">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                    <td class="text-end">
                      <span class="font-monospace fw-bold text-muted fs-6">${x.etfs.length} / ${totalChecked} 檔</span>
                      <div class="progress mt-1" style="height: 4px; background-color: #e2e8f0;">
                        <div class="progress-bar bg-secondary" role="progressbar" style="width: ${strengthPct}%"></div>
                      </div>
                    </td>
                    <td class="px-4">${listChips}</td>
                </tr>`;
            }).join('');

            document.getElementById('radarGoldBody').innerHTML = goldHtml || '<tr><td colspan="3" class="text-center text-muted">目前區間內無重疊加碼共識股</td></tr>';
            document.getElementById('radarWarningBody').innerHTML = warningHtml || '<tr><td colspan="3" class="text-center text-muted">目前區間內無重疊減持避險股</td></tr>';
        }

        function selectEtf(etfCode) {
            selectedEtf = etfCode;
            document.querySelectorAll('.etf-item-btn').forEach(btn => btn.classList.remove('active'));
            let activeBtn = document.getElementById('btn-etf-' + etfCode);
            if (activeBtn) activeBtn.classList.add('active');

            let mappedName = etfNameMappingData[etfCode] || "未知名稱";
            document.getElementById('txtEtfCode').innerText = etfCode;
            document.getElementById('txtEtfName').innerText = mappedName;
            document.getElementById('etfTitleContainer').style.display = 'block';

            let etfData = globalRawData.filter(d => d.etf === etfCode);
            let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            if (dates.length === 0) return;

            let latestDate = dates[dates.length - 1];
            document.getElementById('txtUpdateDate').innerText = `(最新資料日期: ${latestDate})`;

            let latestRows = etfData.filter(d => d.date === latestDate);

            let twseData = twseLiveMarketData[etfCode] || null;
            if (twseData) {
                let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
                let yesterdayPrice = parseFloat(twseData.y) || 0;
                let diff = priceVal - yesterdayPrice;
                let changePct = yesterdayPrice > 0 ? ((diff / yesterdayPrice) * 100).toFixed(2) : "-";
                
                document.getElementById('metaMarketPrice').innerText = priceVal > 0 ? priceVal.toFixed(2) : "-";
                document.getElementById('metaChange').innerText = changePct !== "-" ? (parseFloat(changePct) > 0 ? `+${changePct}%` : `${changePct}%`) : "-";
                document.getElementById('metaVolume').innerText = twseData.v ? parseInt(twseData.v).toLocaleString() : "-";
            } else {
                document.getElementById('metaMarketPrice').innerText = "-";
                document.getElementById('metaChange').innerText = "-";
                document.getElementById('metaVolume').innerText = "-";
            }

            let stocks = latestRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => parseFloat(b.weight) - parseFloat(a.weight));
            let nonStocks = latestRows.filter(r => !isNormalStock(r.stock, r.name)).sort((a,b) => parseFloat(b.weight) - parseFloat(a.weight));

            currentEtfStocks = stocks;
            selectedIndustries = [];
            renderStockTable();

            let assetHtml = nonStocks.map(r => `<tr>
                <td class="font-monospace">${r.stock}</td>
                <td>${r.name || r.stock}</td>
                <td class="text-end font-monospace">${parseFloat(r.weight).toFixed(2)}%</td>
                <td class="text-end font-monospace">${parseInt(r.volume).toLocaleString()}</td>
            </tr>`).join('');
            document.getElementById('assetTableBody').innerHTML = assetHtml || '<tr><td colspan="4" class="text-center text-muted">無非股票資產項目</td></tr>';

            let totalTwWeightPer = 0, totalTwWeightPbr = 0;
            let weightedPerSum = 0, weightedPbrSum = 0;

            stocks.forEach(r => {
                let perVal = r.per ? Number(r.per) : 0;
                let pbrVal = r.pbr ? Number(r.pbr) : 0;
                let w = Number(r.weight);
                if (perVal > 0) { weightedPerSum += perVal * w; totalTwWeightPer += w; }
                if (pbrVal > 0) { weightedPbrSum += pbrVal * w; totalTwWeightPbr += w; }
            });

            document.getElementById('metaWeightedPer').innerText = totalTwWeightPer > 0 ? (weightedPerSum / totalTwWeightPer).toFixed(2) : "-";
            document.getElementById('metaWeightedPbr').innerText = totalTwWeightPbr > 0 ? (weightedPbrSum / totalTwWeightPbr).toFixed(2) : "-";

            renderIndustryPieChart(stocks);
            refreshEtfChanges(etfCode, dates);
        }

        function renderStockTable() {
            let filtered = currentEtfStocks;
            if (selectedIndustries.length > 0) {
                filtered = currentEtfStocks.filter(r => selectedIndustries.includes(r.industry || '未分類'));
            }

            let container = document.getElementById('selectedIndustryDisplayContainer');
            if (selectedIndustries.length > 0) {
                container.innerHTML = `<span class="badge bg-primary me-2">已篩選產業: ${selectedIndustries.join(', ')}</span><button class="btn btn-sm btn-link text-danger p-0" onclick="clearIndustryFilter()">清除篩選</button>`;
            } else {
                container.innerHTML = '';
            }

            let html = filtered.map(r => {
                let perText = r.per && Number(r.per) > 0 ? Number(r.per).toFixed(2) : "-";
                return `<tr>
                    <td class="font-monospace fw-bold">${r.stock}</td>
                    <td class="fw-bold">${r.name || r.stock} <span class="text-muted small">(${r.industry || '未分類'})</span></td>
                    <td class="text-end font-monospace fw-bold text-primary">${parseFloat(r.weight).toFixed(2)}%</td>
                    <td class="text-end font-monospace">${parseInt(r.volume).toLocaleString()}</td>
                    <td class="text-end font-monospace">${perText}</td>
                </tr>`;
            }).join('');
            document.getElementById('stockTableBody').innerHTML = html || '<tr><td colspan="5" class="text-center text-muted">無符合條件之股票明細</td></tr>';
        }

        function clearIndustryFilter() {
            selectedIndustries = [];
            renderStockTable();
        }

        function renderIndustryPieChart(stocks) {
            let industryMap = {};
            stocks.forEach(r => {
                let ind = r.industry || '未分類';
                let w = parseFloat(r.weight) || 0;
                industryMap[ind] = (industryMap[ind] || 0) + w;
            });

            let labels = Object.keys(industryMap);
            let dataVals = Object.values(industryMap);

            let ctx = document.getElementById('industryPieChart').getContext('2d');
            if (industryChartInstance) {
                industryChartInstance.destroy();
            }

            const bgColors = [
                '#1e3c72', '#2a5298', '#3182ce', '#319795', '#38a169',
                '#d69e2e', '#dd6b20', '#e53e3e', '#805ad5', '#d53f8c',
                '#4a5568', '#718096', '#a0aec0', '#cbd5e0', '#e2e8f0'
            ];

            industryChartInstance = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: labels,
                    datasets: [{
                        data: dataVals.map(v => parseFloat(v.toFixed(2))),
                        backgroundColor: bgColors.slice(0, labels.length)
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'right', labels: { boxWidth: 12, font: { size: 11 } } },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return ` ${context.label}: ${context.raw}%`;
                                }
                            }
                        }
                    },
                    onClick: (evt, elements) => {
                        if (elements.length > 0) {
                            let idx = elements[0].index;
                            let clickedIndustry = labels[idx];
                            let pos = selectedIndustries.indexOf(clickedIndustry);
                            if (pos > -1) {
                                selectedIndustries.splice(pos, 1);
                            } else {
                                selectedIndustries.push(clickedIndustry);
                            }
                            renderStockTable();
                        }
                    }
                }
            });
        }

        function toggleCustomDates() {
            let type = document.getElementById('rangeType').value;
            document.getElementById('customDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function refreshCurrentEtf() {
            if (selectedEtf) {
                let etfData = globalRawData.filter(d => d.etf === selectedEtf);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                refreshEtfChanges(selectedEtf, dates);
            }
        }

        // =========================================================================
        // 計算經理人連續操作動向輔助函數 (追溯連續加碼/減持天數與累計量)
        // =========================================================================
        function getConsecutiveTrend(etfData, sortedDates, targetDate, sCode, currentDiffVol, unit) {
            let targetIdx = sortedDates.indexOf(targetDate);
            if (targetIdx <= 0) {
                if (currentDiffVol > 0) return { days: 1, vol: currentDiffVol, type: 'buy' };
                if (currentDiffVol < 0) return { days: 1, vol: currentDiffVol, type: 'sell' };
                return { days: 0, vol: 0, type: 'flat' };
            }

            let dailyVols = [];
            for (let i = 0; i <= targetIdx; i++) {
                let d = sortedDates[i];
                let row = etfData.find(x => x.date === d && x.stock === sCode);
                dailyVols.push(row ? parseFloat(row.volume) || 0 : 0);
            }

            let diffs = [];
            for (let i = 1; i < dailyVols.length; i++) {
                diffs.push(dailyVols[i] - dailyVols[i-1]);
            }

            if (diffs.length === 0) {
                return { days: 1, vol: currentDiffVol, type: currentDiffVol >= 0 ? 'buy' : 'sell' };
            }

            let lastDiff = diffs[diffs.length - 1];
            if (lastDiff === 0) {
                if (currentDiffVol > 0) lastDiff = currentDiffVol;
                else if (currentDiffVol < 0) lastDiff = currentDiffVol;
                else return { days: 0, vol: 0, type: 'flat' };
            }

            let isBuy = lastDiff > 0;
            let days = 0;
            let sumVol = 0;

            for (let i = diffs.length - 1; i >= 0; i--) {
                let diff = diffs[i];
                if (isBuy && diff > 0) {
                    days++;
                    sumVol += diff;
                } else if (!isBuy && diff < 0) {
                    days++;
                    sumVol += diff;
                } else {
                    break;
                }
            }

            return {
                days: days,
                vol: sumVol,
                type: isBuy ? 'buy' : 'sell'
            };
        }

        // =========================================================================
        // 修正成分股經理人籌碼異動明細（依照 新增 -> 加碼 -> 減持 -> 剔除 排序）
        // =========================================================================
        function refreshEtfChanges(etfCode, sortedDates) {
            if (!sortedDates || sortedDates.length < 2) return;
            let type = document.getElementById('rangeType').value;
            let dOld = null, dNew = sortedDates[sortedDates.length - 1];

            if (type === 'custom') {
                dOld = document.getElementById('startDateInput').value;
                dNew = document.getElementById('endDateInput').value;
            } else {
                let offset = parseInt(type);
                if (sortedDates.length > offset) {
                    dOld = sortedDates[sortedDates.length - 1 - offset];
                } else {
                    dOld = sortedDates[0];
                }
            }

            if (!dOld || !dNew) return;

            let etfData = globalRawData.filter(d => d.etf === etfCode);
            let oldRows = etfData.filter(d => d.date === dOld);
            let newRows = etfData.filter(d => d.date === dNew);

            let allStocks = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])];

            let changes = [];
            allStocks.forEach(sCode => {
                let oRow = oldRows.find(x => x.stock === sCode);
                let nRow = newRows.find(x => x.stock === sCode);
                let sName = nRow ? nRow.name : (oRow ? oRow.name : sCode);

                if (!isNormalStock(sCode, sName)) return;

                let oVol = oRow ? parseFloat(oRow.volume) || 0 : 0;
                let nVol = nRow ? parseFloat(nRow.volume) || 0 : 0;
                let diffVol = nVol - oVol;

                let oW = oRow ? parseFloat(oRow.weight) || 0 : 0;
                let nW = nRow ? parseFloat(nRow.weight) || 0 : 0;
                let diffW = nW - oW;

                let natureOrder = 0;
                let natureBadge = "";

                if (oVol === 0 && nVol > 0) {
                    natureOrder = 1; // 1. 新增
                    natureBadge = `<span class="badge-nature-new"><i class="bi bi-plus-lg me-1"></i>新增</span>`;
                } else if (oVol > 0 && nVol > oVol) {
                    natureOrder = 2; // 2. 加碼/增加
                    natureBadge = `<span class="badge-nature-up"><i class="bi bi-arrow-up-right me-1"></i>加碼</span>`;
                } else if (nVol > 0 && nVol < oVol) {
                    natureOrder = 3; // 3. 減持
                    natureBadge = `<span class="badge-nature-down"><i class="bi bi-arrow-down-right me-1"></i>減持</span>`;
                } else if (oVol > 0 && nVol === 0) {
                    natureOrder = 4; // 4. 剔除/刪除
                    natureBadge = `<span class="badge-nature-delete"><i class="bi bi-trash3-fill me-1"></i>剔除</span>`;
                } else {
                    return; // 無變動不列入
                }

                let isDom = /^\d{4,6}$/.test(sCode.trim());
                let unit = isDom ? "張" : "股";

                // 計算經理人連續操作動向
                let trendInfo = getConsecutiveTrend(etfData, sortedDates, dNew, sCode, diffVol, unit);

                changes.push({
                    code: sCode,
                    name: sName,
                    natureOrder: natureOrder,
                    natureBadge: natureBadge,
                    diffVol: diffVol,
                    diffW: diffW,
                    unit: unit,
                    absVol: Math.abs(diffVol),
                    trendInfo: trendInfo
                });
            });

            // 核心排序：1. 新增 -> 2. 加碼 -> 3. 減持 -> 4. 剔除
            // 同類別內依據變動股數 (absVol) 從大到小排列
            changes.sort((a, b) => {
                if (a.natureOrder !== b.natureOrder) {
                    return a.natureOrder - b.natureOrder;
                }
                return b.absVol - a.absVol;
            });

            let html = changes.map(item => {
                let volClass = item.diffVol > 0 ? "text-danger" : "text-success";
                let sign = item.diffVol > 0 ? "+" : "";
                let wSign = item.diffW > 0 ? "+" : "";

                let trendBadge = "";
                let t = item.trendInfo;

                if (t.type === 'buy') {
                    if (t.days > 1) {
                        trendBadge = `<span class="badge-trend-buy"><i class="bi bi-graph-up-arrow me-1"></i>連買 ${t.days} 天 (+${t.vol.toLocaleString()} ${item.unit})</span>`;
                    } else if (item.natureOrder === 1) {
                        trendBadge = `<span class="badge-nature-new-pill"><i class="bi bi-plus-circle-fill me-1"></i>首日建倉 (+${item.diffVol.toLocaleString()} ${item.unit})</span>`;
                    } else {
                        trendBadge = `<span class="badge-trend-buy"><i class="bi bi-cart-plus-fill me-1"></i>加碼買進 1 天 (+${t.vol.toLocaleString()} ${item.unit})</span>`;
                    }
                } else if (t.type === 'sell') {
                    if (t.days > 1 && item.natureOrder === 4) {
                        trendBadge = `<span class="badge-trend-sell"><i class="bi bi-graph-down-arrow me-1"></i>連賣 ${t.days} 天 (累積清倉)</span>`;
                    } else if (t.days > 1) {
                        trendBadge = `<span class="badge-trend-sell"><i class="bi bi-graph-down-arrow me-1"></i>連賣 ${t.days} 天 (${t.vol.toLocaleString()} ${item.unit})</span>`;
                    } else if (item.natureOrder === 4) {
                        trendBadge = `<span class="badge-nature-delete-pill"><i class="bi bi-dash-circle-fill me-1"></i>單日清倉剔除 (${item.diffVol.toLocaleString()} ${item.unit})</span>`;
                    } else {
                        trendBadge = `<span class="badge-trend-sell"><i class="bi bi-cart-dash-fill me-1"></i>減持賣出 1 天 (${t.vol.toLocaleString()} ${item.unit})</span>`;
                    }
                } else {
                    trendBadge = `<span class="badge bg-light text-secondary border"><i class="bi bi-dash me-1"></i>無顯著連續動向</span>`;
                }

                return `<tr>
                    <td class="fw-bold font-monospace fs-6">${item.code} <span class="text-secondary small ms-1 fw-normal">${item.name}</span></td>
                    <td>${item.natureBadge}</td>
                    <td class="text-end font-monospace fw-bold ${volClass} fs-6">${sign}${item.diffVol.toLocaleString()} ${item.unit} <span class="small text-muted fw-normal">(${wSign}${item.diffW.toFixed(2)}%)</span></td>
                    <td class="px-4">${trendBadge}</td>
                </tr>`;
            }).join('');

            document.getElementById('changeTableBody').innerHTML = html || '<tr><td colspan="4" class="text-center text-muted py-3">此雙日期區間內無成分股異動數據</td></tr>';

            runManagerStyleDiagnosis(etfCode, dOld, dNew, sortedDates);
        }

        function runManagerStyleDiagnosis(etfName, dOld, dNew, sortedDates) {
            let etfData = globalRawData.filter(d => d.etf === etfName);
            let oldRows = etfData.filter(d => d.date === dOld);
            let newRows = etfData.filter(d => d.date === dNew);

            if (oldRows.length === 0 || newRows.length === 0) return;

            let allStockTokens = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])];
            let absWeightDiffSum = 0;

            allStockTokens.forEach(s => {
                let oRow = oldRows.find(x => x.stock === s);
                let nRow = newRows.find(x => x.stock === s);
                let oW = oRow ? Number(oRow.weight) : 0;
                let nW = nRow ? Number(nRow.weight) : 0;
                absWeightDiffSum += Math.abs(nW - oW);
            });

            let turnoverRate = absWeightDiffSum / 2;
            document.getElementById('txtTurnoverRate').innerText = turnoverRate.toFixed(2) + "%";
            document.getElementById('txtDiagnosticInterval').innerText = `${dOld} 至 ${dNew}`;

            let styleTagText = "", styleTagClass = "";
            if (turnoverRate < 5) {
                styleTagText = "超低頻價值長抱流派 (周轉率 < 5%)";
                styleTagClass = "bg-success";
            } else if (turnoverRate < 15) {
                styleTagText = "穩健長期價值投資 (周轉率 5%~15%)";
                styleTagClass = "bg-success-subtle text-success border border-success";
            } else if (turnoverRate <= 35) {
                styleTagText = "動態靈活戰術調整 (周轉率 15%~35%)";
                styleTagClass = "bg-primary";
            } else {
                styleTagText = "積極高周轉波段流派 (周轉率 > 35%)";
                styleTagClass = "bg-danger";
            }

            let badge = document.getElementById('badgeStyleTag');
            badge.innerText = styleTagText;
            badge.className = "badge " + styleTagClass + " fs-6 px-3 py-2";

            let latestStocks = newRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);
            let totalObservedDays = sortedDates.length;
            let occurrenceMap = {};

            sortedDates.forEach(d => {
                let dayRows = etfData.filter(x => x.date === d);
                dayRows.forEach(r => {
                    if (r.stock && isNormalStock(r.stock, r.name)) {
                        occurrenceMap[r.stock] = (occurrenceMap[r.stock] || 0) + 1;
                    }
                });
            });

            let coreHtml = "", satelliteHtml = "";
            let allHistoricalStocks = Object.keys(occurrenceMap);

            allHistoricalStocks.forEach(sCode => {
                let appearanceRate = occurrenceMap[sCode] / totalObservedDays;
                let lRow = latestStocks.find(x => x.stock === sCode);
                let currentWeight = lRow ? Number(lRow.weight) : 0;
                let sName = lRow ? lRow.name : (etfData.find(x => x.stock === sCode)?.name || "歷史成分股");

                if (currentWeight >= 4 && appearanceRate >= 0.8) {
                    coreHtml += `<span class="badge bg-danger text-white m-1 p-2"><b>${sCode}</b> ${sName} (${currentWeight.toFixed(1)}%)</span>`;
                } else if (currentWeight < 2 && appearanceRate < 0.4 && currentWeight > 0) {
                    satelliteHtml += `<span class="badge bg-info text-dark m-1 p-2"><b>${sCode}</b> ${sName} (${currentWeight.toFixed(1)}%)</span>`;
                }
            });

            document.getElementById('boxCoreList').innerHTML = coreHtml || '<span class="text-muted small p-2">無符合核心高權重長持股條件標的</span>';
            document.getElementById('boxSatelliteList').innerHTML = satelliteHtml || '<span class="text-muted small p-2">無符合低權重短線衛星股條件標的</span>';

            document.getElementById('diagResultTextContainer').innerHTML = '<p class="text-muted mb-0"><i class="bi bi-lightbulb me-1"></i>經理人調倉趨勢：已完成雙日期權重與週轉率計算。</p>';
            document.getElementById('diagnosticCard').style.display = 'block';
        }

        function searchStockSuggestions(query, targetBoxId, inputId, isMatcher) {
            let box = document.getElementById(targetBoxId);
            if (!query || query.trim() === '') {
                box.style.display = 'none';
                return;
            }

            let q = query.trim().toUpperCase();
            let matches = [];
            let seen = new Set();

            Object.keys(tickerMappingData).forEach(code => {
                let name = tickerMappingData[code].name || '';
                if (code.toUpperCase().includes(q) || name.toUpperCase().includes(q)) {
                    if (!seen.has(code)) {
                        seen.add(code);
                        matches.push({ code: code, name: name });
                    }
                }
            });

            globalRawData.forEach(r => {
                if (r.stock && isNormalStock(r.stock, r.name)) {
                    let code = r.stock;
                    let name = r.name || '';
                    if (code.toUpperCase().includes(q) || name.toUpperCase().includes(q)) {
                        if (!seen.has(code)) {
                            seen.add(code);
                            matches.push({ code: code, name: name });
                        }
                    }
                }
            });

            matches = matches.slice(0, 10);
            if (matches.length === 0) {
                box.style.display = 'none';
                return;
            }

            let html = matches.map(m => `
                <div class="suggestion-item" onclick="selectStockSuggestion('${m.code}', '${m.name}', '${inputId}', '${targetBoxId}', ${isMatcher})">
                    <b>${m.code}</b> <span class="text-muted ms-2">${m.name}</span>
                </div>
            `).join('');

            box.innerHTML = html;
            box.style.display = 'block';
        }

        function selectStockSuggestion(code, name, inputId, targetBoxId, isMatcher) {
            document.getElementById(targetBoxId).style.display = 'none';
            if (isMatcher) {
                document.getElementById(inputId).value = '';
                addTargetStock(code, name);
            } else {
                document.getElementById(inputId).value = code;
                searchStockDistribution();
            }
        }

        function searchStockDistribution() {
            let sCode = document.getElementById('stockSearchInput').value.trim();
            if (!sCode) return;

            let stockRows = globalRawData.filter(d => d.stock.toUpperCase() === sCode.toUpperCase() || (d.name && d.name.toUpperCase().includes(sCode.toUpperCase())));
            if (stockRows.length === 0) {
                alert("在大數據資料庫中找不到該個股紀錄。");
                return;
            }

            let targetCode = stockRows[0].stock;
            let targetName = stockRows[0].name || (tickerMappingData[targetCode] ? tickerMappingData[targetCode].name : targetCode);

            document.getElementById('resStockTitle').innerText = `${targetCode} ${targetName}`;
            document.getElementById('stockResultContainer').style.display = 'block';

            let etfSet = [...new Set(globalRawData.map(d => d.etf))];
            let latestHolders = [];
            let totalVolDiff = 0;

            etfSet.forEach(eCode => {
                let eData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(eData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;

                let latestDate = dates[dates.length - 1];
                let lRow = eData.find(d => d.date === latestDate && d.stock === targetCode);
                if (lRow) {
                    latestHolders.push({
                        etf: eCode,
                        etfName: etfNameMappingData[eCode] || eCode,
                        weight: parseFloat(lRow.weight) || 0,
                        volume: parseFloat(lRow.volume) || 0
                    });
                }

                if (dates.length >= 2) {
                    let oldDate = dates[dates.length - 2];
                    let oRow = eData.find(d => d.date === oldDate && d.stock === targetCode);
                    let oVol = oRow ? parseFloat(oRow.volume) || 0 : 0;
                    let nVol = lRow ? parseFloat(lRow.volume) || 0 : 0;
                    totalVolDiff += (nVol - oVol);
                }
            });

            latestHolders.sort((a,b) => b.weight - a.weight);

            let totalVolStr = totalVolDiff > 0 ? `+${totalVolDiff.toLocaleString()} 股` : `${totalVolDiff.toLocaleString()} 股`;
            document.getElementById('trendStockTotalVol').innerText = totalVolStr;
            document.getElementById('trendStockStatus').innerText = totalVolDiff > 0 ? "淨買超加碼" : (totalVolDiff < 0 ? "淨賣超減持" : "持平");

            let distHtml = latestHolders.map(h => `<tr>
                <td class="fw-bold font-monospace">${h.etf} <span class="text-muted small ms-1">${h.etfName}</span></td>
                <td class="text-end font-monospace">${h.volume.toLocaleString()} 股</td>
            </tr>`).join('');
            document.getElementById('stockDistBody').innerHTML = distHtml || '<tr><td colspan="2" class="text-center text-muted">無持有數據</td></tr>';

            let distHtml2 = latestHolders.map(h => `<tr>
                <td class="fw-bold font-monospace text-primary">${h.etf}</td>
                <td class="fw-bold text-secondary">${h.etfName}</td>
                <td class="text-end font-monospace fw-bold text-primary">${h.weight.toFixed(2)}%</td>
                <td class="text-end font-monospace">${h.volume.toLocaleString()}</td>
            </tr>`).join('');
            document.getElementById('stockDistBody2').innerHTML = distHtml2 || '<tr><td colspan="4" class="text-center text-muted">無持有數據</td></tr>';
        }

        function addTargetStock(code, name) {
            if (selectedTargetStocks.some(s => s.code === code)) return;
            selectedTargetStocks.push({ code: code, name: name });
            renderTargetStockTags();
            calculateStockMatcher();
        }

        function removeTargetStock(code) {
            selectedTargetStocks = selectedTargetStocks.filter(s => s.code !== code);
            renderTargetStockTags();
            calculateStockMatcher();
        }

        function renderTargetStockTags() {
            let container = document.getElementById('selectedTargetContainer');
            if (selectedTargetStocks.length === 0) {
                container.innerHTML = '<span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選組合</span>';
                return;
            }

            let html = selectedTargetStocks.map(s => `
                <span class="selected-stock-tag">
                    <b>${s.code}</b> ${s.name}
                    <i class="bi bi-x-circle-fill" onclick="removeTargetStock('${s.code}')"></i>
                </span>
            `).join('');
            container.innerHTML = html;
        }

        function calculateStockMatcher() {
            if (selectedTargetStocks.length === 0) {
                document.getElementById('matchResultBody').innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>';
                return;
            }

            let etfSet = [...new Set(globalRawData.map(d => d.etf))];
            let results = [];

            etfSet.forEach(eCode => {
                let eData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(eData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;

                let latestDate = dates[dates.length - 1];
                let latestRows = eData.filter(d => d.date === latestDate);

                let matchedHoldings = [];
                let totalMatchWeight = 0;

                selectedTargetStocks.forEach(target => {
                    let match = latestRows.find(r => r.stock === target.code);
                    if (match) {
                        let w = parseFloat(match.weight) || 0;
                        totalMatchWeight += w;
                        matchedHoldings.push({ code: target.code, name: target.name, weight: w });
                    }
                });

                if (matchedHoldings.length > 0) {
                    results.push({
                        etf: eCode,
                        etfName: etfNameMappingData[eCode] || eCode,
                        totalWeight: totalMatchWeight,
                        matchedCount: matchedHoldings.length,
                        matchedHoldings: matchedHoldings
                    });
                }
            });

            results.sort((a,b) => b.totalWeight - a.totalWeight);

            let html = results.map(r => {
                let detailChips = r.matchedHoldings.map(h => `<span class="badge bg-light text-primary border me-1"><b>${h.code}</b> ${h.name} (${h.weight.toFixed(2)}%)</span>`).join('');
                return `<tr>
                    <td class="fw-bold font-monospace text-primary fs-6">${r.etf}</td>
                    <td class="fw-bold text-secondary">${r.etfName}</td>
                    <td class="text-end font-monospace fw-bold text-danger fs-6">${r.totalWeight.toFixed(2)}%</td>
                    <td class="px-4">${detailChips}</td>
                </tr>`;
            }).join('');

            document.getElementById('matchResultBody').innerHTML = html || '<tr><td colspan="4" class="text-center py-4 text-muted">全市場 ETF 中尚無同時包含您選取目標之組合。</td></tr>';
        }

        function toggleGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            document.getElementById('globalCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            if (dates.length < 2) return;

            let dOld = null, dNew = dates[dates.length - 1];
            if (type === 'custom') {
                dOld = document.getElementById('globalStartDate').value;
                dNew = document.getElementById('globalEndDate').value;
            } else {
                let offset = parseInt(type);
                if (dates.length > offset) {
                    dOld = dates[dates.length - 1 - offset];
                } else {
                    dOld = dates[0];
                }
            }

            if (!dOld || !dNew) return;

            let oldRows = globalRawData.filter(d => d.date === dOld);
            let newRows = globalRawData.filter(d => d.date === dNew);

            let newAddedMap = {};
            let deletedMap = {};
            let etfSet = [...new Set(globalRawData.map(d => d.etf))];

            etfSet.forEach(eCode => {
                let eOld = oldRows.filter(r => r.etf === eCode);
                let eNew = newRows.filter(r => r.etf === eCode);

                let oStocks = eOld.map(r => r.stock);
                let nStocks = eNew.map(r => r.stock);

                nStocks.forEach(s => {
                    if (!oStocks.includes(s)) {
                        let sample = eNew.find(r => r.stock === s);
                        if (sample && isNormalStock(s, sample.name)) {
                            let token = s + "||" + (sample.name || s);
                            if (!newAddedMap[token]) newAddedMap[token] = [];
                            newAddedMap[token].push(eCode);
                        }
                    }
                });

                oStocks.forEach(s => {
                    if (!nStocks.includes(s)) {
                        let sample = eOld.find(r => r.stock === s);
                        if (sample && isNormalStock(s, sample.name)) {
                            let token = s + "||" + (sample.name || s);
                            if (!deletedMap[token]) deletedMap[token] = [];
                            deletedMap[token].push(eCode);
                        }
                    }
                });
            });

            let newArr = Object.keys(newAddedMap).map(k => {
                let [code, name] = k.split("||");
                return { code: code, name: name, etfs: newAddedMap[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            let delArr = Object.keys(deletedMap).map(k => {
                let [code, name] = k.split("||");
                return { code: code, name: name, etfs: deletedMap[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            let newHtml = newArr.map(x => {
                let chips = x.etfs.map(e => `<span class="badge bg-light text-danger border me-1"><b>${e}</b></span>`).join('');
                return `<tr>
                    <td class="fw-bold">${x.code} <span class="text-muted small ms-1">${x.name}</span></td>
                    <td>${chips}</td>
                </tr>`;
            }).join('');

            let delHtml = delArr.map(x => {
                let chips = x.etfs.map(e => `<span class="badge bg-light text-secondary border me-1"><b>${e}</b></span>`).join('');
                return `<tr>
                    <td class="fw-bold text-secondary">${x.code} <span class="text-muted small ms-1">${x.name}</span></td>
                    <td>${chips}</td>
                </tr>`;
            }).join('');

            document.getElementById('globalNewBody').innerHTML = newHtml || '<tr><td colspan="2" class="text-center text-muted">無新增成分股紀錄</td></tr>';
            document.getElementById('globalDelBody').innerHTML = delHtml || '<tr><td colspan="2" class="text-center text-muted">無剔除成分股紀錄</td></tr>';
        }

        function toggleHeatCustomDates() {
            let type = document.getElementById('heatRangeType').value;
            document.getElementById('heatCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadMarketHeat() {
            let type = document.getElementById('heatRangeType').value;
            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            if (dates.length < 2) return;

            let dOld = null, dNew = dates[dates.length - 1];
            if (type === 'custom') {
                dOld = document.getElementById('heatStartDate').value;
                dNew = document.getElementById('heatEndDate').value;
            } else {
                let offset = parseInt(type);
                if (dates.length > offset) {
                    dOld = dates[dates.length - 1 - offset];
                } else {
                    dOld = dates[0];
                }
            }

            if (!dOld || !dNew) return;

            let oldRows = globalRawData.filter(d => d.date === dOld);
            let newRows = globalRawData.filter(d => d.date === dNew);

            let stockStats = {};
            let allStocks = [...new Set([...oldRows.map(r => r.stock), ...newRows.map(r => r.stock)])];

            allStocks.forEach(sCode => {
                let sample = newRows.find(x => x.stock === sCode) || oldRows.find(x => x.stock === sCode);
                if (!sample || !isNormalStock(sample.stock, sample.name)) return;

                let sName = sample.name || (tickerMappingData[sCode] ? tickerMappingData[sCode].name : sCode);
                let isDomestic = /^\d{4,6}$/.test(sCode.trim());

                let oldVolSum = oldRows.filter(x => x.stock === sCode).reduce((acc, r) => acc + (parseFloat(r.volume) || 0), 0);
                let newVolSum = newRows.filter(x => x.stock === sCode).reduce((acc, r) => acc + (parseFloat(r.volume) || 0), 0);
                let diffVol = newVolSum - oldVolSum;

                if (diffVol === 0) return;

                let price = parseFloat(sample.price) || 0;
                if (price === 0 && isDomestic && twseLiveMarketData[sCode]) {
                    price = parseFloat(twseLiveMarketData[sCode].z) || parseFloat(twseLiveMarketData[sCode].p) || 0;
                }

                let estAmount = diffVol * price;

                stockStats[sCode] = {
                    code: sCode,
                    name: sName,
                    isDomestic: isDomestic,
                    diffVol: diffVol,
                    price: price,
                    estAmount: estAmount
                };
            });

            let statsArr = Object.values(stockStats);
            let domStats = statsArr.filter(x => x.isDomestic);
            let forStats = statsArr.filter(x => !x.isDomestic);

            // 1. 金額排行
            let domBuyAmt = [...domStats].filter(x => x.diffVol > 0).sort((a,b) => b.estAmount - a.estAmount).slice(0, 10);
            let domSellAmt = [...domStats].filter(x => x.diffVol < 0).sort((a,b) => a.estAmount - b.estAmount).slice(0, 10);
            let forBuyAmt = [...forStats].filter(x => x.diffVol > 0).sort((a,b) => b.estAmount - a.estAmount).slice(0, 10);
            let forSellAmt = [...forStats].filter(x => x.diffVol < 0).sort((a,b) => a.estAmount - b.estAmount).slice(0, 10);

            // 2. 張數/股數排行
            let domBuyVol = [...domStats].filter(x => x.diffVol > 0).sort((a,b) => b.diffVol - a.diffVol).slice(0, 10);
            let domSellVol = [...domStats].filter(x => x.diffVol < 0).sort((a,b) => a.diffVol - b.diffVol).slice(0, 10);
            let forBuyVol = [...forStats].filter(x => x.diffVol > 0).sort((a,b) => b.diffVol - a.diffVol).slice(0, 10);
            let forSellVol = [...forStats].filter(x => x.diffVol < 0).sort((a,b) => a.diffVol - b.diffVol).slice(0, 10);

            let renderHeatTable = (items, isVol, isSell) => {
                if (!items || items.length === 0) return '<tr><td colspan="4" class="text-center text-muted py-3">無排行數據</td></tr>';
                return items.map((item, idx) => {
                    let medalClass = idx === 0 ? "medal-1" : (idx === 1 ? "medal-2" : (idx === 2 ? "medal-3" : "medal-other"));
                    let volText = `${item.diffVol > 0 ? '+' : ''}${item.diffVol.toLocaleString()} ${item.isDomestic ? '張' : '股'}`;
                    let amtText = item.estAmount !== 0 ? `${(item.estAmount / 100000000).toFixed(2)} 億` : '-';
                    let volColor = isSell ? "text-success" : "text-danger";

                    if (isVol) {
                        return `<tr>
                            <td><span class="rank-medal ${medalClass}">${idx+1}</span></td>
                            <td class="fw-bold">${item.code} <span class="text-muted small ms-1">${item.name}</span></td>
                            <td class="text-end font-monospace fw-bold ${volColor}">${volText}</td>
                            <td class="text-end font-monospace">${amtText}</td>
                        </tr>`;
                    } else {
                        return `<tr>
                            <td><span class="rank-medal ${medalClass}">${idx+1}</span></td>
                            <td class="fw-bold">${item.code} <span class="text-muted small ms-1">${item.name}</span></td>
                            <td class="text-end font-monospace fw-bold ${volColor}">${amtText}</td>
                            <td class="text-end font-monospace">${volText}</td>
                        </tr>`;
                    }
                }).join('');
            };

            document.getElementById('heatBuyAmtBodyDom').innerHTML = renderHeatTable(domBuyAmt, false, false);
            document.getElementById('heatSellAmtBodyDom').innerHTML = renderHeatTable(domSellAmt, false, true);
            document.getElementById('heatBuyAmtBodyFor').innerHTML = renderHeatTable(forBuyAmt, false, false);
            document.getElementById('heatSellAmtBodyFor').innerHTML = renderHeatTable(forSellAmt, false, true);

            document.getElementById('heatBuyVolBodyDom').innerHTML = renderHeatTable(domBuyVol, true, false);
            document.getElementById('heatSellVolBodyDom').innerHTML = renderHeatTable(domSellVol, true, true);
            document.getElementById('heatBuyVolBodyFor').innerHTML = renderHeatTable(forBuyVol, true, false);
            document.getElementById('heatSellVolBodyFor').innerHTML = renderHeatTable(forSellVol, true, true);
        }

        function renderCompareMatrix() {
            let checkedEtfs = Array.from(document.querySelectorAll('#compareCheckboxContainer input:checked')).map(cb => cb.value);
            
            let placeholder = document.getElementById('comparePlaceholder');
            let summarySec = document.getElementById('compareSummarySection');
            let coreCard = document.getElementById('coreHoldingsCard');
            let uniqueCard = document.getElementById('uniqueHoldingsCard');

            if (checkedEtfs.length < 1) {
                placeholder.style.display = 'block';
                summarySec.style.display = 'none';
                coreCard.style.display = 'none';
                uniqueCard.style.display = 'none';
                return;
            }

            placeholder.style.display = 'none';
            summarySec.style.display = 'block';
            coreCard.style.display = 'block';
            uniqueCard.style.display = 'block';

            let stockMap = {};

            checkedEtfs.forEach(eCode => {
                let eData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(eData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;

                let latestDate = dates[dates.length - 1];
                let latestRows = eData.filter(d => d.date === latestDate);

                latestRows.forEach(r => {
                    if (!isNormalStock(r.stock, r.name)) return;
                    let sCode = r.stock;
                    let sName = r.name || (tickerMappingData[sCode] ? tickerMappingData[sCode].name : sCode);
                    let w = parseFloat(r.weight) || 0;

                    if (!stockMap[sCode]) {
                        stockMap[sCode] = { code: sCode, name: sName, etfWeights: {} };
                    }
                    stockMap[sCode].etfWeights[eCode] = w;
                });
            });

            let totalSelected = checkedEtfs.length;
            let allStocks = Object.values(stockMap);

            allStocks.forEach(s => {
                s.holdCount = Object.keys(s.etfWeights).length;
                s.sumWeight = Object.values(s.etfWeights).reduce((a,b) => a+b, 0);
                s.avgWeight = s.sumWeight / totalSelected;
            });

            let coreHoldings = allStocks.filter(s => s.holdCount === totalSelected).sort((a,b) => b.sumWeight - a.sumWeight);
            let uniqueHoldings = allStocks.filter(s => s.holdCount < totalSelected).sort((a,b) => b.holdCount - a.holdCount || b.sumWeight - a.sumWeight);

            let topOverlap = [...allStocks].sort((a,b) => b.holdCount - a.holdCount || b.sumWeight - a.sumWeight).slice(0, 3);
            let summaryHtml = topOverlap.map(s => `
                <div class="col-md-4">
                    <div class="summary-card">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <span class="badge bg-primary fs-6">${s.code}</span>
                            <span class="fw-bold text-danger">${s.holdCount} / ${totalSelected} 檔 ETF 持有</span>
                        </div>
                        <div class="fw-bold fs-5 text-dark mb-1">${s.name}</div>
                        <div class="small text-muted">平均持股權重: <b class="text-primary">${s.avgWeight.toFixed(2)}%</b> (總和 ${s.sumWeight.toFixed(2)}%)</div>
                    </div>
                </div>
            `).join('');
            document.getElementById('compareSummaryCards').innerHTML = summaryHtml || '<div class="col-12 text-muted">無重疊焦點個股</div>';

            let buildHeader = (elementId) => {
                let html = `<th>股票代號</th><th>股票名稱</th><th class="text-center">共同持有度</th>`;
                checkedEtfs.forEach(e => {
                    let mappedName = etfNameMappingData[e] || e;
                    html += `<th class="text-end font-monospace">${e}<br><span class="fw-normal small text-muted">${mappedName}</span></th>`;
                });
                document.getElementById(elementId).innerHTML = html;
            };

            buildHeader('compareCoreTableHeader');
            buildHeader('compareUniqueTableHeader');

            let buildRow = (s) => {
                let holdPct = Math.round((s.holdCount / totalSelected) * 100);
                let cells = `<tr>
                    <td class="font-monospace fw-bold">${s.code}</td>
                    <td class="fw-bold text-secondary">${s.name}</td>
                    <td class="text-center">
                        <span class="badge bg-primary-subtle text-primary fw-bold mb-1">${s.holdCount} / ${totalSelected}</span>
                        <div class="progress" style="height: 4px; width: 60px; margin: 0 auto;">
                            <div class="progress-bar bg-primary" style="width: ${holdPct}%"></div>
                        </div>
                    </td>`;

                checkedEtfs.forEach(e => {
                    let w = s.etfWeights[e] || 0;
                    let styleClass = "weight-none";
                    if (w >= 5.0) styleClass = "weight-high";
                    else if (w >= 2.0) styleClass = "weight-med";
                    else if (w > 0) styleClass = "weight-low";

                    cells += `<td class="text-end font-monospace ${styleClass}">${w > 0 ? w.toFixed(2) + '%' : '-'}</td>`;
                });
                cells += `</tr>`;
                return cells;
            };

            document.getElementById('compareCoreTableBody').innerHTML = coreHoldings.map(buildRow).join('') || `<tr><td colspan="${3 + totalSelected}" class="text-center text-muted py-3">選定之 ETF 之間無全數重疊的共同核心持股</td></tr>`;
            document.getElementById('compareUniqueTableBody').innerHTML = uniqueHoldings.map(buildRow).join('') || `<tr><td colspan="${3 + totalSelected}" class="text-center text-muted py-3">選定之 ETF 之間無差異化持股</td></tr>`;
        }
      </script>
    </body>
    </html>
    """

    html_template = html_template.replace("__DATA_PLACEHOLDER__", json_data)\
                                 .replace("__TWSE_PLACEHOLDER__", twse_json)\
                                 .replace("__TICKER_PLACEHOLDER__", ticker_json)\
                                 .replace("__ETF_NAME_PLACEHOLDER__", etf_name_json)

    components.html(html_template, height=1200, scrolling=True)

if __name__ == "__main__":
    main()
