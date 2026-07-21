import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os
import requests
import re
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
        code_idx = None
        name_idx = None
        industry_idx = None
        
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
# 3. FinMind PBR/PER 與股價批次查詢與 3 小時快取
# ==========================================
@st.cache_data(ttl=10800)  # 設定 3 小時快取
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
        start_dt = dt - timedelta(days=30)
        start_date_str = start_dt.strftime("%Y-%m-%d")
    except Exception:
        start_date_str = date_str

    valuation_results = {}
    
    for code in valid_stocks:
        url = "https://api.finmindtrade.com/api/v4/data"
        pbr_val = 0.0
        per_val = 0.0
        close_price = 0.0

        # 抓取 PER / PBR
        params_per = {
            "dataset": "TaiwanStockPER",  
            "data_id": code,
            "start_date": start_date_str,
            "end_date": date_str,
        }
        if FINMIND_TOKEN:
            params_per["token"] = FINMIND_TOKEN
            
        try:
            res = requests.get(url, params=params_per, timeout=10)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    last_record = data[-1]
                    pbr_val = float(last_record.get("PBR", last_record.get("pbr", 0.0)) or 0.0)
                    per_val = float(last_record.get("PER", last_record.get("per", 0.0)) or 0.0)
        except Exception as e:
            print(f"FinMind PER API 連線失敗 ({code}): {e}")

        # 抓取台股股價輔助計算損益
        params_price = {
            "dataset": "TaiwanStockPrice",
            "data_id": code,
            "start_date": start_date_str,
            "end_date": date_str,
        }
        if FINMIND_TOKEN:
            params_price["token"] = FINMIND_TOKEN

        try:
            res_p = requests.get(url, params=params_price, timeout=10)
            if res_p.status_code == 200:
                data_p = res_p.json().get("data", [])
                if data_p:
                    last_p = data_p[-1]
                    close_price = float(last_p.get("close", 0.0) or 0.0)
        except Exception as e:
            print(f"FinMind Price API 連線失敗 ({code}): {e}")

        valuation_results[code] = {
            "pbr": pbr_val,
            "per": per_val,
            "close_price": close_price
        }
            
    return valuation_results

# ==========================================
# 4. 外部即時行情 API 整合模組
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
        "amount": ["成交金額", "金額", "Amount", "成交價額", "買賣金額"],
        "price": ["成交價", "單價", "收盤價", "Price", "價格"]
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
    
    if 'amount' in df.columns:
        df['amount'] = pd.to_numeric(df['amount'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    else:
        df['amount'] = 0.0

    if 'price' in df.columns:
        df['price'] = pd.to_numeric(df['price'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
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
# 5. 主核心資料庫結構轉換與打包
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
    
    # 整合 FinMind 估值與股價計算
    try:
        latest_date = df['date'].max()
        unique_stocks = df['stock'].unique().tolist()
        val_map = fetch_valuation_weights_cached(unique_stocks, latest_date)
        
        df['pbr'] = df['stock'].apply(lambda x: val_map.get(x, {}).get("pbr", 0.0))
        df['per'] = df['stock'].apply(lambda x: val_map.get(x, {}).get("per", 0.0))
        df['finmind_price'] = df['stock'].apply(lambda x: val_map.get(x, {}).get("close_price", 0.0))
    except Exception as e:
        print(f"FinMind 數據併入失敗: {e}")
        df['pbr'] = 0.0
        df['per'] = 0.0
        df['finmind_price'] = 0.0
    
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False), {}, twse_live_market, ticker_map, etf_name_map

# ==========================================
# 6. 主渲染邏輯
# ==========================================
def main():
    json_data, wantgoo_market_data, twse_live_market, ticker_map, etf_name_map = fetch_backend_data_to_json()

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
        body { font-family: 'Noto Sans TC', sans-serif; background-color: #f4f6f9; color: #333; }
        .navbar { background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        .card { border: none; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 1.5rem; background-color: #fff; }
        .card-header { background-color: #fff; border-bottom: 1px solid #edf2f9; font-weight: 700; font-size: 1.1rem; padding: 1rem 1.25rem; border-top-left-radius: 12px !important; border-top-right-radius: 12px !important; }
        .table { margin-bottom: 0; }
        .table th { background-color: #f8fafd; color: #4a5568; font-weight: 600; }
        .meta-card { background: #ffffff; border-left: 4px solid #2a5298; padding: 12px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.04); text-align: center; }
        .meta-label { font-size: 0.85rem; color: #718096; margin-bottom: 4px; }
        .meta-value { font-size: 1.15rem; font-weight: 700; color: #1a202c; }
        .nav-tabs .nav-link { border: none; color: #4a5568; font-weight: 500; padding: 0.75rem 1.25rem; border-radius: 8px; cursor: pointer; }
        .nav-tabs .nav-link.active { background-color: #e2e8f0; color: #1e3c72; font-weight: 700; }
        .custom-tab-content { display: none; }
        .custom-tab-content.active { display: block; }
        .loading-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(255,255,255,0.75); display: flex; justify-content: center; align-items: center; z-index: 9999; }
        .etf-list-group { max-height: 700px; overflow-y: auto; }
        .etf-item-btn { text-align: left; border-radius: 8px !important; margin-bottom: 4px; border: 1px solid #e2e8f0; transition: all 0.2s; }
        .etf-item-btn:hover { background-color: #f1f5f9; }
        .etf-item-btn.active { background-color: #1e3c72 !important; border-color: #1e3c72 !important; color: #fff !important; font-weight: bold; }
        .badge-nature-new { background-color: #f97316; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-up { background-color: #dc2626; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-down { background-color: #0f766e; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-delete { background-color: #374151; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-trend-buy { background-color: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; border: 1px solid #bbf7d0; }
        .badge-trend-sell { background-color: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; border: 1px solid #fde68a; }
        .etf-title-display { font-size: 1.5rem; font-weight: 700; color: #1e3c72; margin-bottom: 0.75rem; padding-left: 4px; display: flex; align-items: center; }
        .update-date-text { font-size: 0.9rem; font-weight: 400; color: #6c757d; margin-left: 12px; }
        .suggestion-box { position: absolute; background: white; border: 1px solid #ced4da; border-top: none; z-index: 1000; max-height: 200px; overflow-y: auto; width: 100%; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .suggestion-item { padding: 10px 15px; cursor: pointer; }
        .suggestion-item:hover { background-color: #f1f5f9; }
        .selected-stock-tag { background-color: #e2e8f0; color: #1e3c72; padding: 4px 10px; border-radius: 20px; font-weight: 500; font-size: 0.9rem; display: inline-flex; align-items: center; gap: 6px; }
        .selected-stock-tag i { cursor: pointer; color: #ef4444; }
        .rank-medal { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; font-weight: 700; font-size: 0.85rem; }
        .medal-1 { background: linear-gradient(135deg, #ffd700, #ffa500); color: #fff; }
        .medal-2 { background: linear-gradient(135deg, #c0c0c0, #a9a9a9); color: #fff; }
        .medal-3 { background: linear-gradient(135deg, #cd7f32, #8b4513); color: #fff; }
        .medal-other { background-color: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0; }
        .heat-progress-container { display: flex; align-items: center; justify-content: flex-end; gap: 12px; }
        .heat-bar-wrapper { width: 120px; background-color: #f1f5f9; border-radius: 4px; overflow: hidden; }
        .weight-high { background-color: #1e3c72 !important; color: #ffffff !important; font-weight: 700 !important; }
        .weight-med { background-color: #bcd2ee !important; color: #1e3c72 !important; font-weight: 700 !important; }
        .weight-low { background-color: #e6f2ff !important; color: #2a5298 !important; font-weight: 600 !important; }
        .weight-none { background-color: #f8fafc !important; color: #94a3b8 !important; }
        .summary-card { background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%); border-top: 4px solid #1e3c72; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.06); padding: 18px; }
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
          <!-- 首頁 -->
          <div class="custom-tab-content active" id="content-home">
            <div class="card p-0">
              <div class="table-responsive">
                <table class="table align-middle">
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

          <!-- 📡 主動型經理人共識雷達 -->
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
          
          <!-- 單檔 ETF 籌碼與持股 -->
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
                      <div class="meta-label">加權平均本益比</div>
                      <div class="meta-value text-teal" id="metaWeightedPer">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #805ad5;">
                      <div class="meta-label">加權平均股淨比</div>
                      <div class="meta-value text-purple" id="metaWeightedPbr">-</div>
                    </div>
                  </div>
                </div>

                <!-- 診斷卡片 -->
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
                  </div>
                </div>
                
                <div class="row g-3">
                  <div class="col-lg-8">
                    <div class="card h-100">
                      <div class="card-header text-primary d-flex justify-content-between align-items-center">
                        <span><i class="bi bi-list-stars me-2"></i>最新成分股持股明細 (含台股 FIFO 損益與庫存成本計算)</span>
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
                              <th class="text-end">成交均價</th>
                              <th class="text-end">庫存成本</th>
                              <th class="text-end">帳面未實現損益</th>
                              <th class="text-end">已實現損益</th>
                              <th class="text-end">本益比</th>
                            </tr>
                          </thead>
                          <tbody id="stockTableBody"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  
                  <div class="col-lg-4">
                    <div class="card mb-3">
                      <div class="card-header text-primary"><i class="bi bi-pie-chart me-2"></i>成分股產業別分佈</div>
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
                
                <div class="card">
                  <div class="card-header text-dark bg-white"><i class="bi bi-arrow-left-right me-2 text-primary"></i>成分股經理人籌碼異動明細</div>
                  <div class="table-responsive">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr>
                          <th>股票標的</th>
                          <th>異動屬性</th>
                          <th class="text-end">張數 / 股數增減 (成交均價)</th>
                          <th class="text-end">交易異動金額 / 已實現損益</th>
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
          
          <!-- 個股籌碼分佈 -->
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
                    <div class="card-header text-dark"><i class="bi bi-layer-forward me-2 text-warning"></i>各大 ETF 基金經理人對此股票的區間籌碼調整明細</div>
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
                          <tr><th>持有之 ETF 代號</th><th>持有之 ETF 名稱</th><th class="text-end">持股權重比例</th></tr>
                        </thead>
                        <tbody id="stockDistBody2"></tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- ETF 智能組合篩選 -->
          <div class="custom-tab-content" id="content-f">
            <div class="card p-4 bg-light border-0 shadow-sm rounded-4 mb-4">
              <h4 class="fw-bold text-dark mb-2"><i class="bi bi-cpu-fill text-primary me-2"></i>AI 投資組合回溯目標搜尋器</h4>
              <p class="text-muted small">請任意輸入並挑選多檔全球投資目標公司，系統將深度回溯大數據，為您精算出同時重疊包含這群目標公司的精選 ETF 陣容。</p>
              
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
          
          <!-- 全市場異動總覽 -->
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
          
          <!-- 市場熱度排行 -->
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
            
            <div class="row g-4">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>全市場經理人淨加碼前 10 大股票</div>
                  <div class="table-responsive">
                    <table class="table align-middle">
                      <thead><tr><th>排行</th><th>股票標的</th><th class="text-end" style="min-width: 230px;">區間總加碼股數</th></tr></thead>
                      <tbody id="heatBuyBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header text-success"><i class="bi bi-graph-down-arrow me-2"></i>全市場經理人淨減持前 10 大股票</div>
                  <div class="table-responsive">
                    <table class="table align-middle">
                      <thead><tr><th>排行</th><th>股票標的</th><th class="text-end" style="min-width: 230px;">區間總減持股數</th></tr></thead>
                      <tbody id="heatSellBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <!-- ETF 交叉比較 -->
          <div class="custom-tab-content" id="content-e">
            <div class="card p-3 mb-4 bg-light">
              <div class="fw-bold text-dark mb-2"><i class="bi bi-check2-square me-1"></i>勾選欲交叉比較的 ETF 基金清單</div>
              <div class="d-flex flex-wrap gap-3 p-3 bg-white border rounded" id="compareCheckboxContainer"></div>
            </div>
            
            <div id="compareSummarySection" style="display: none;" class="mb-4">
              <div class="fw-bold text-secondary mb-2"><i class="bi bi-lightning-charge-fill text-warning me-1"></i>交叉比對核心摘要（Top 3 重疊焦點個股）</div>
              <div class="row g-3" id="compareSummaryCards"></div>
            </div>

            <div class="card mb-4" id="coreHoldingsCard" style="display: none;">
              <div class="card-header bg-white text-primary fw-bold d-flex align-items-center">
                <i class="bi bi-shield-heart-fill me-2 text-danger"></i>【共同核心持股矩陣】
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
                <i class="bi bi-pie-chart-fill me-2 text-warning"></i>【獨門特色持股矩陣】
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

        function isTwStock(code) {
            if (!code) return false;
            let cleanCode = String(code).trim();
            return /^\d{4,6}$/.test(cleanCode);
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
            if (/^[GBAHF][A-Z0-9]{5}$/.test(upperCode)) return false;
            return true;
        }

        function getStockPrice(row) {
            if (!row) return 0;
            if (row.price && Number(row.price) > 0) return Number(row.price);
            let amt = Number(row.amount) || 0;
            let vol = Number(row.volume) || 0;
            if (amt > 0 && vol > 0) return amt / vol;
            if (row.finmind_price && Number(row.finmind_price) > 0) return Number(row.finmind_price);
            return 0;
        }

        // ==========================================
        // 核心邏輯：FIFO 計算、均價、庫存成本與已實現損益
        // Formula: 均價 = 總成交金額 / 總成交張數
        // Formula: 庫存成本 = 均價 * 股數 * 1000
        // ==========================================
        function calculateFifoForStock(etfData, sCode, targetDate) {
            if (!isTwStock(sCode)) {
                return { fifoQueue: [], remainingShares: 0, totalBookCost: 0, avgCost: 0, realizedPnl: 0, unrealizedPnl: 0, isTw: false };
            }

            let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            if (targetDate) {
                dates = dates.filter(d => d <= targetDate);
            }

            let fifoQueue = []; // [{ shares, avgPrice, date }]
            let totalRealizedPnl = 0;

            for (let i = 0; i < dates.length; i++) {
                let d = dates[i];
                let row = etfData.find(r => r.date === d && r.stock === sCode);
                let currentShares = row ? Number(row.volume) || 0 : 0;
                let amt = row ? Number(row.amount) || 0 : 0;
                
                // 成交均價 = 總成交金額 / 總成交張數
                let avgPrice = (currentShares > 0 && amt > 0) ? (amt / currentShares) : getStockPrice(row);

                if (i === 0) {
                    if (currentShares > 0) {
                        fifoQueue.push({ shares: currentShares, avgPrice: avgPrice, date: d });
                    }
                } else {
                    let prevRow = etfData.find(r => r.date === dates[i-1] && r.stock === sCode);
                    let prevShares = prevRow ? Number(prevRow.volume) || 0 : 0;
                    let diffShares = currentShares - prevShares;

                    if (diffShares > 0) {
                        fifoQueue.push({ shares: diffShares, avgPrice: avgPrice, date: d });
                    } else if (diffShares < 0) {
                        let sharesToSell = Math.abs(diffShares);
                        while (sharesToSell > 0 && fifoQueue.length > 0) {
                            let lot = fifoQueue[0];
                            let soldQty = Math.min(lot.shares, sharesToSell);
                            
                            // 已實現損益 = (賣出均價 - 買入均價) * 賣出張數/股數 * 1000
                            let pnlForLot = (avgPrice - lot.avgPrice) * soldQty * 1000;
                            totalRealizedPnl += pnlForLot;

                            lot.shares -= soldQty;
                            sharesToSell -= soldQty;

                            if (lot.shares <= 0) {
                                fifoQueue.shift();
                            }
                        }
                    }
                }
            }

            let remainingShares = fifoQueue.reduce((sum, lot) => sum + lot.shares, 0);
            
            // 庫存成本計算公式: 均價 * 股數 * 1000
            let weightedAvgPrice = 0;
            let totalBookCost = 0;
            if (remainingShares > 0) {
                let weightedPriceSum = fifoQueue.reduce((sum, lot) => sum + (lot.shares * lot.avgPrice), 0);
                weightedAvgPrice = weightedPriceSum / remainingShares;
                totalBookCost = weightedAvgPrice * remainingShares * 1000;
            }

            let latestRow = etfData.find(r => r.date === (targetDate || dates[dates.length - 1]) && r.stock === sCode);
            let curPrice = getStockPrice(latestRow);
            let unrealizedPnl = 0;
            if (curPrice > 0 && remainingShares > 0) {
                // 帳面未實現損益 = 現價市值 - 庫存成本
                unrealizedPnl = (curPrice * remainingShares * 1000) - totalBookCost;
            }

            return {
                fifoQueue: fifoQueue,
                remainingShares: remainingShares,
                avgCost: weightedAvgPrice,
                totalBookCost: totalBookCost,
                realizedPnl: totalRealizedPnl,
                unrealizedPnl: unrealizedPnl,
                isTw: true
            };
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

        function selectEtf(etfName) {
            selectedEtf = etfName;
            selectedIndustries = []; 
            
            document.querySelectorAll('.etf-item-btn').forEach(el => el.classList.remove('active'));
            let activeBtn = document.getElementById(`btn-etf-${etfName}`);
            if(activeBtn) activeBtn.classList.add('active');

            let etfData = globalRawData.filter(d => d.etf === etfName);
            let dates = etfData.map(d => d.date);
            let sortedDates = [...new Set(dates)].sort((a,b) => new Date(a) - new Date(b));
            let latestDate = sortedDates[sortedDates.length - 1];
            
            let rangeType = document.getElementById('rangeType').value;
            let dOld = null;
            let dNew = latestDate;
            if (rangeType === 'custom') {
                dOld = document.getElementById('startDateInput').value;
                dNew = document.getElementById('endDateInput').value || latestDate;
            } else {
                let offset = parseInt(rangeType);
                if (sortedDates.length > offset) {
                    dOld = sortedDates[sortedDates.length - 1 - offset];
                } else {
                    dOld = sortedDates[0];
                }
            }

            let latestRows = etfData.filter(d => d.date === dNew);
            let mappedName = etfNameMappingData[etfName] || "未知名稱";

            document.getElementById('txtEtfCode').innerText = etfName;
            document.getElementById('txtEtfName').innerText = mappedName;
            document.getElementById('etfTitleContainer').style.display = 'block';

            let twseData = twseLiveMarketData[etfName] || null;
            if (twseData) {
                let rawD = twseData.d || "";
                if(rawD.length === 8) { rawD = rawD.substring(0,4) + "-" + rawD.substring(4,6) + "-" + rawD.substring(6,8); }
                document.getElementById('txtUpdateDate').innerText = rawD ? `更新日期: ${rawD}` : "";
                let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
                document.getElementById('metaMarketPrice').innerText = priceVal > 0 ? priceVal.toFixed(2) : "-";
                let yPrice = parseFloat(twseData.y) || 0;
                if(priceVal > 0 && yPrice > 0) {
                    let diff = priceVal - yPrice;
                    let pct = ((diff / yPrice) * 100).toFixed(2);
                    document.getElementById('metaChange').innerText = diff > 0 ? `+${diff.toFixed(2)} (+${pct}%)` : `${diff.toFixed(2)} (${pct}%)`;
                    document.getElementById('metaChange').className = `meta-value ${diff > 0 ? 'text-danger' : 'text-success'}`;
                }
                let volVal = parseFloat(twseData.v) || 0;
                document.getElementById('metaVolume').innerText = volVal.toLocaleString() + " 張";
            }

            let aBody = document.getElementById('assetTableBody');
            let aHtml = "";

            let stocks = latestRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);
            currentEtfStocks = stocks; 
            
            let assets = latestRows.filter(r => !isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);

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
            document.getElementById('metaWeightedPer').innerText = weightedPer;
            document.getElementById('metaWeightedPbr').innerText = weightedPbr;

            assets.forEach(r => {
                aHtml += `<tr>
                    <td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td>
                    <td class="fw-bold">${r.name}</td>
                    <td class="text-end font-monospace text-primary fw-bold">${Number(r.weight).toFixed(2)}%</td>
                    <td class="text-end font-monospace text-secondary">${Math.round(r.volume).toLocaleString()}</td>
                </tr>`;
            });
            aBody.innerHTML = aHtml;

            renderIndustryPieChart(stocks);
            updateStockTableDisplay();
            
            calculateStockChanges(etfName, dOld, dNew);
        }

        function renderIndustryPieChart(stocks) {
            let industryWeights = {};
            stocks.forEach(r => {
                let ind = r.industry || "未分類";
                let w = Number(r.weight) || 0;
                industryWeights[ind] = (industryWeights[ind] || 0) + w;
            });

            const labels = Object.keys(industryWeights);
            const data = Object.values(industryWeights).map(v => parseFloat(v.toFixed(2)));

            const ctx = document.getElementById('industryPieChart').getContext('2d');
            if (industryChartInstance) {
                industryChartInstance.destroy();
            }

            const presetColors = [
                '#2a5298', '#319795', '#f97316', '#a855f7', '#10b981', 
                '#ef4444', '#eab308', '#ec4899', '#6366f1', '#64748b'
            ];

            industryChartInstance = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: labels,
                    datasets: [{
                        data: data,
                        backgroundColor: presetColors.slice(0, labels.length)
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'right',
                            labels: { font: { family: 'Noto Sans TC', size: 11 } }
                        }
                    }
                }
            });
        }

        function updateStockTableDisplay() {
            let sBody = document.getElementById('stockTableBody');
            let container = document.getElementById('selectedIndustryDisplayContainer');
            let sHtml = "";

            let etfData = globalRawData.filter(d => d.etf === selectedEtf);
            let filteredStocks = currentEtfStocks;
            if (selectedIndustries.length > 0) {
                filteredStocks = currentEtfStocks.filter(r => selectedIndustries.includes(r.industry || "未分類"));
                let badgesHtml = selectedIndustries.map(ind => 
                    `<span class="badge bg-primary me-1">${ind}</span>`
                ).join('');
                container.innerHTML = `<div class="small text-muted">篩選產業別: ${badgesHtml}</div>`;
            } else {
                container.innerHTML = "";
            }

            filteredStocks.forEach(r => {
                let perVal = r.per ? Number(r.per) : 0;
                let displayPer = (perVal > 0) ? perVal.toFixed(2) : "-";
                
                let isTw = isTwStock(r.stock);
                let avgCostText = "-";
                let inventoryCostText = "-";
                let bookPnLText = "-";
                let bookPnLClass = "";
                let realizedPnLText = "-";
                let realizedPnLClass = "";

                if (!isTw) {
                    avgCostText = "非台股";
                    inventoryCostText = "非台股";
                    bookPnLText = "非台股";
                    realizedPnLText = "非台股";
                    bookPnLClass = "text-muted";
                    realizedPnLClass = "text-muted";
                } else {
                    let fifoRes = calculateFifoForStock(etfData, r.stock, r.date);
                    let avgCost = fifoRes.avgCost;
                    let inventoryCost = fifoRes.totalBookCost;
                    let unrealizedPnL = fifoRes.unrealizedPnl;
                    let realizedPnL = fifoRes.realizedPnl;

                    if (avgCost > 0) {
                        avgCostText = '$' + avgCost.toFixed(2);
                    }
                    if (inventoryCost > 0) {
                        inventoryCostText = '$' + Math.round(inventoryCost).toLocaleString();
                    }

                    if (inventoryCost > 0) {
                        let pnlPct = (unrealizedPnL / inventoryCost) * 100;
                        let sign = unrealizedPnL > 0 ? "+" : "";
                        bookPnLText = `${sign}$${Math.round(unrealizedPnL).toLocaleString()} (${sign}${pnlPct.toFixed(1)}%)`;
                        bookPnLClass = unrealizedPnL > 0 ? "text-danger fw-bold" : (unrealizedPnL < 0 ? "text-success fw-bold" : "text-dark");
                    }

                    if (realizedPnL !== 0) {
                        let rSign = realizedPnL > 0 ? "+" : "";
                        realizedPnLText = `${rSign}$${Math.round(realizedPnL).toLocaleString()}`;
                        realizedPnLClass = realizedPnL > 0 ? "text-danger fw-bold" : "text-success fw-bold";
                    } else {
                        realizedPnLText = "$0";
                        realizedPnLClass = "text-muted";
                    }
                }

                sHtml += `<tr>
                    <td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td>
                    <td class="fw-bold">
                        ${r.name}
                        <span class="badge bg-light text-secondary border fw-normal ms-1" style="font-size:0.75rem;">${r.industry || '未分類'}</span>
                    </td>
                    <td class="text-end font-monospace text-primary fw-bold">${Number(r.weight).toFixed(2)}%</td>
                    <td class="text-end font-monospace text-secondary">${Math.round(r.volume).toLocaleString()}</td>
                    <td class="text-end font-monospace text-muted">${avgCostText}</td>
                    <td class="text-end font-monospace fw-bold text-dark">${inventoryCostText}</td>
                    <td class="text-end font-monospace ${bookPnLClass}">${bookPnLText}</td>
                    <td class="text-end font-monospace ${realizedPnLClass}">${realizedPnLText}</td>
                    <td class="text-end font-monospace text-info fw-bold">${displayPer}</td>
                </tr>`;
            });
            sBody.innerHTML = sHtml || '<tr><td colspan="9" class="text-center text-muted py-4">無符合選定產業別的成分股</td></tr>';
        }

        function toggleCustomDates() {
            let type = document.getElementById('rangeType').value;
            document.getElementById('customDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function refreshCurrentEtf() {
            if (selectedEtf) {
                selectEtf(selectedEtf);
            }
        }

        function calculateStockChanges(etfName, dOld, dNew) {
            let etfData = globalRawData.filter(d => d.etf === etfName);
            let dates = etfData.map(d => d.date);
            let sortedDates = [...new Set(dates)].sort((a,b) => new Date(a) - new Date(b));

            let oldRows = etfData.filter(d => d.date === dOld);
            let newRows = etfData.filter(d => d.date === dNew);

            let allStocks = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])].filter(s => {
                let match = newRows.find(x=>x.stock===s) || oldRows.find(x=>x.stock===s);
                return match ? isNormalStock(match.stock, match.name) : false;
            });

            let changeHtml = "";
            let rowsToRender = [];
            
            allStocks.forEach(sCode => {
                let oRow = oldRows.find(x => x.stock === sCode);
                let nRow = newRows.find(x => x.stock === sCode);
                let oVol = oRow ? Number(oRow.volume) : 0;
                let nVol = nRow ? Number(nRow.volume) : 0;
                let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知股票");

                let diffVol = nVol - oVol;
                if (diffVol === 0) return;

                let priority = 0;
                let actionBadge = "";
                let actionDesc = "";

                let isTw = isTwStock(sCode);
                let targetRow = nRow || oRow;
                let tradePrice = targetRow ? getStockPrice(targetRow) : 0;

                let tradePnlText = "-";
                let tradePnlClass = "";

                if (!isTw) {
                    tradePnlText = "非台股標的";
                    tradePnlClass = "text-muted";
                    if (oVol === 0 && nVol > 0) {
                        priority = 1;
                        actionBadge = `<span class="badge-nature-new"><i class="bi bi-plus-circle me-1"></i>全新納入</span>`;
                        actionDesc = `新增 ${Math.round(nVol).toLocaleString()} 股`;
                    } else if (oVol > 0 && nVol === 0) {
                        priority = 4;
                        actionBadge = `<span class="badge-nature-delete"><i class="bi bi-dash-circle me-1"></i>全數剔除</span>`;
                        actionDesc = `出清 ${Math.round(oVol).toLocaleString()} 股`;
                    } else if (diffVol > 0) {
                        priority = 2;
                        actionBadge = `<span class="badge-nature-up"><i class="bi bi-caret-up-fill me-1"></i>加碼持股</span>`;
                        actionDesc = `增加 ${Math.round(diffVol).toLocaleString()} 股`;
                    } else if (diffVol < 0) {
                        priority = 3;
                        actionBadge = `<span class="badge-nature-down"><i class="bi bi-caret-down-fill me-1"></i>減持股份</span>`;
                        actionDesc = `調節 ${Math.round(Math.abs(diffVol)).toLocaleString()} 股`;
                    }
                } else {
                    if (oVol === 0 && nVol > 0) {
                        priority = 1; 
                        actionBadge = `<span class="badge-nature-new"><i class="bi bi-plus-circle me-1"></i>全新納入</span>`;
                        actionDesc = `新增 ${Math.round(nVol).toLocaleString()} 股 (均價: ${tradePrice > 0 ? '$'+tradePrice.toFixed(2) : '-'})`;
                        tradePnlText = tradePrice > 0 ? `買進總額 $${Math.round(nVol * tradePrice * 1000).toLocaleString()}` : "-";
                        tradePnlClass = "text-muted";
                    } else if (oVol > 0 && nVol === 0) {
                        priority = 4; 
                        actionBadge = `<span class="badge-nature-delete"><i class="bi bi-dash-circle me-1"></i>全數剔除</span>`;
                        actionDesc = `出清 ${Math.round(oVol).toLocaleString()} 股 (均價: ${tradePrice > 0 ? '$'+tradePrice.toFixed(2) : '-'})`;
                        
                        let fifoOld = calculateFifoForStock(etfData, sCode, dOld);
                        if (tradePrice > 0 && fifoOld.avgCost > 0) {
                            let sellProceeds = oVol * tradePrice * 1000;
                            let pnlVal = sellProceeds - fifoOld.totalBookCost;
                            let sign = pnlVal > 0 ? "+" : "";
                            tradePnlText = `${sign}$${Math.round(pnlVal).toLocaleString()}`;
                            tradePnlClass = pnlVal > 0 ? "text-danger fw-bold" : (pnlVal < 0 ? "text-success fw-bold" : "text-dark");
                        }
                    } else if (diffVol > 0) {
                        priority = 2;
                        actionBadge = `<span class="badge-nature-up"><i class="bi bi-caret-up-fill me-1"></i>加碼持股</span>`;
                        actionDesc = `增加 ${Math.round(diffVol).toLocaleString()} 股 (均價: ${tradePrice > 0 ? '$'+tradePrice.toFixed(2) : '-'})`;
                        if (tradePrice > 0) {
                            tradePnlText = `加碼金額 $${Math.round(diffVol * tradePrice * 1000).toLocaleString()}`;
                            tradePnlClass = "text-muted";
                        }
                    } else if (diffVol < 0) {
                        priority = 3;
                        actionBadge = `<span class="badge-nature-down"><i class="bi bi-caret-down-fill me-1"></i>減持股份</span>`;
                        actionDesc = `調節 ${Math.round(Math.abs(diffVol)).toLocaleString()} 股 (均價: ${tradePrice > 0 ? '$'+tradePrice.toFixed(2) : '-'})`;
                        
                        let fifoOld = calculateFifoForStock(etfData, sCode, dOld);
                        if (tradePrice > 0 && fifoOld.avgCost > 0) {
                            let soldQty = Math.abs(diffVol);
                            let pnlVal = (tradePrice - fifoOld.avgCost) * soldQty * 1000;
                            let sign = pnlVal > 0 ? "+" : "";
                            tradePnlText = `${sign}$${Math.round(pnlVal).toLocaleString()}`;
                            tradePnlClass = pnlVal > 0 ? "text-danger fw-bold" : (pnlVal < 0 ? "text-success fw-bold" : "text-dark");
                        }
                    }
                }

                // 連續動向計算
                let streak = 0;
                let streakType = "";
                for (let k = sortedDates.length - 1; k > 0; k--) {
                    let dCurr = sortedDates[k];
                    let dPrev = sortedDates[k-1];
                    let rCurr = etfData.find(x => x.date === dCurr && x.stock === sCode);
                    let rPrev = etfData.find(x => x.date === dPrev && x.stock === sCode);
                    let vCurr = rCurr ? Number(rCurr.volume) || 0 : 0;
                    let vPrev = rPrev ? Number(rPrev.volume) || 0 : 0;
                    let vDiff = vCurr - vPrev;

                    if (vDiff > 0) {
                        if (streakType === "" || streakType === "buy") {
                            streakType = "buy";
                            streak++;
                        } else break;
                    } else if (vDiff < 0) {
                        if (streakType === "" || streakType === "sell") {
                            streakType = "sell";
                            streak++;
                        } else break;
                    } else {
                        break;
                    }
                }

                let streakBadge = "";
                if (streakType === "buy" && streak > 0) {
                    streakBadge = `<span class="badge-trend-buy">連續 ${streak} 日加碼</span>`;
                } else if (streakType === "sell" && streak > 0) {
                    streakBadge = `<span class="badge-trend-sell">連續 ${streak} 日減碼</span>`;
                } else {
                    streakBadge = `<span class="badge bg-light text-muted border">單日波段調整</span>`;
                }

                rowsToRender.push({
                    sCode: sCode,
                    sName: sName,
                    priority: priority,
                    actionBadge: actionBadge,
                    actionDesc: actionDesc,
                    tradePnlText: tradePnlText,
                    tradePnlClass: tradePnlClass,
                    streakBadge: streakBadge,
                    diffVol: Math.abs(diffVol)
                });
            });

            rowsToRender.sort((a,b) => (a.priority - b.priority) || (b.diffVol - a.diffVol));

            rowsToRender.forEach(r => {
                changeHtml += `<tr>
                    <td><span class="badge bg-light text-dark font-monospace border me-1">${r.sCode}</span> <b>${r.sName}</b></td>
                    <td>${r.actionBadge}</td>
                    <td class="text-end font-monospace fw-bold">${r.actionDesc}</td>
                    <td class="text-end font-monospace ${r.tradePnlClass}">${r.tradePnlText}</td>
                    <td class="px-4">${r.streakBadge}</td>
                </tr>`;
            });

            document.getElementById('changeTableBody').innerHTML = changeHtml || '<tr><td colspan="5" class="text-center text-muted py-4">選定雙日期區間內無經理人籌碼異動紀錄</td></tr>';
        }

        // ==========================================
        // 個股搜尋與熱度模組
        // ==========================================
        function searchStockSuggestions(inputVal, suggestionBoxId, inputId, isMatcher) {
            let clean = inputVal.trim().toUpperCase();
            let box = document.getElementById(suggestionBoxId);
            if (!clean) {
                box.style.display = 'none';
                return;
            }

            let allStockMap = {};
            globalRawData.forEach(r => {
                if (r.stock && isNormalStock(r.stock, r.name)) {
                    allStockMap[r.stock] = r.name || tickerMappingData[r.stock]?.name || r.stock;
                }
            });

            let matches = [];
            Object.keys(allStockMap).forEach(code => {
                let name = allStockMap[code];
                if (code.toUpperCase().includes(clean) || name.toUpperCase().includes(clean)) {
                    matches.push({ code: code, name: name });
                }
            });

            if (matches.length === 0) {
                box.style.display = 'none';
                return;
            }

            let html = matches.slice(0, 10).map(m => {
                if (isMatcher) {
                    return `<div class="suggestion-item" onclick="addMatcherTarget('${m.code}', '${m.name.replace(/'/g, "\\'")}')"><b>${m.code}</b> ${m.name}</div>`;
                } else {
                    return `<div class="suggestion-item" onclick="selectStockForDistribution('${m.code}', '${m.name.replace(/'/g, "\\'")}')"><b>${m.code}</b> ${m.name}</div>`;
                }
            }).join('');

            box.innerHTML = html;
            box.style.display = 'block';
        }

        function selectStockForDistribution(code, name) {
            document.getElementById('stockSearchInput').value = code + " " + name;
            document.getElementById('searchSuggestions').style.display = 'none';
            searchStockDistribution();
        }

        function searchStockDistribution() {
            let val = document.getElementById('stockSearchInput').value.trim();
            if (!val) return;
            let sCode = val.split(' ')[0].trim();

            let matches = globalRawData.filter(d => d.stock.toUpperCase() === sCode.toUpperCase());
            if (matches.length === 0) {
                alert("未搜尋到關於「" + sCode + "」的持股紀錄。");
                return;
            }

            let sName = matches[0].name || tickerMappingData[sCode]?.name || sCode;
            document.getElementById('resStockTitle').innerText = `${sCode} ${sName}`;

            let etfDataGroup = {};
            globalRawData.forEach(r => {
                if (!etfDataGroup[r.etf]) etfDataGroup[r.etf] = [];
                etfDataGroup[r.etf].push(r);
            });

            let totalVolChange = 0;
            let etfDistRows = [];
            let etfDistRows2 = [];

            Object.keys(etfDataGroup).forEach(eCode => {
                let eRows = etfDataGroup[eCode];
                let dates = [...new Set(eRows.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length < 1) return;

                let latestDate = dates[dates.length - 1];
                let prevDate = dates.length > 1 ? dates[dates.length - 2] : null;

                let lRow = eRows.find(x => x.date === latestDate && x.stock.toUpperCase() === sCode.toUpperCase());
                let pRow = prevDate ? eRows.find(x => x.date === prevDate && x.stock.toUpperCase() === sCode.toUpperCase()) : null;

                let lVol = lRow ? Number(lRow.volume) || 0 : 0;
                let pVol = pRow ? Number(pRow.volume) || 0 : 0;
                let diffVol = lVol - pVol;

                if (lRow && lVol > 0) {
                    etfDistRows2.push({
                        etfCode: eCode,
                        etfName: etfNameMappingData[eCode] || "未知名稱",
                        weight: Number(lRow.weight) || 0,
                        volume: lVol
                    });
                }

                if (diffVol !== 0) {
                    totalVolChange += diffVol;
                    etfDistRows.push({
                        etfCode: eCode,
                        etfName: etfNameMappingData[eCode] || "未知名稱",
                        diffVol: diffVol
                    });
                }
            });

            let trendStatus = totalVolChange > 0 ? "全市場強勢加碼" : (totalVolChange < 0 ? "全市場減持調節" : "持平觀望");
            let trendClass = totalVolChange > 0 ? "text-danger" : (totalVolChange < 0 ? "text-success" : "text-muted");
            document.getElementById('trendStockStatus').innerText = trendStatus;
            document.getElementById('trendStockStatus').className = "fw-bold fs-5 mt-1 " + trendClass;
            document.getElementById('trendStockTotalVol').innerText = (totalVolChange > 0 ? "+" : "") + Math.round(totalVolChange).toLocaleString() + " 股";

            let html1 = etfDistRows.map(x => {
                let sign = x.diffVol > 0 ? "+" : "";
                let colorClass = x.diffVol > 0 ? "text-danger" : "text-success";
                return `<tr>
                    <td><b class="font-monospace">${x.etfCode}</b> <span class="text-muted small">${x.etfName}</span></td>
                    <td class="font-monospace fw-bold ${colorClass}">${sign}${Math.round(x.diffVol).toLocaleString()} 股</td>
                </tr>`;
            }).join('');

            let html2 = etfDistRows2.sort((a,b) => b.weight - a.weight).map(x => {
                return `<tr>
                    <td class="font-monospace fw-bold">${x.etfCode}</td>
                    <td>${x.etfName}</td>
                    <td class="text-end font-monospace text-primary fw-bold">${x.weight.toFixed(2)}%</td>
                </tr>`;
            }).join('');

            document.getElementById('stockDistBody').innerHTML = html1 || '<tr><td colspan="2" class="text-center text-muted py-3">近一日無 ETF 對此股票進行張數調整</td></tr>';
            document.getElementById('stockDistBody2').innerHTML = html2 || '<tr><td colspan="3" class="text-center text-muted py-3">目前無 ETF 持有此股票</td></tr>';
            document.getElementById('stockResultContainer').style.display = 'block';
        }

        // ==========================================
        // AI 組合篩選模組
        // ==========================================
        function addMatcherTarget(code, name) {
            document.getElementById('matcherSuggestions').style.display = 'none';
            document.getElementById('matcherInput').value = '';

            if (!selectedTargetStocks.some(x => x.code === code)) {
                selectedTargetStocks.push({ code: code, name: name });
                renderSelectedTargets();
                renderMatcherResults();
            }
        }

        function removeMatcherTarget(code) {
            selectedTargetStocks = selectedTargetStocks.filter(x => x.code !== code);
            renderSelectedTargets();
            renderMatcherResults();
        }

        function renderSelectedTargets() {
            let container = document.getElementById('selectedTargetContainer');
            if (selectedTargetStocks.length === 0) {
                container.innerHTML = '<span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選組合</span>';
                return;
            }

            let html = selectedTargetStocks.map(x => {
                return `<span class="selected-stock-tag">
                    <b>${x.code}</b> ${x.name}
                    <i class="bi bi-x-circle-fill" onclick="removeMatcherTarget('${x.code}')"></i>
                </span>`;
            }).join('');

            container.innerHTML = html;
        }

        function renderMatcherResults() {
            let body = document.getElementById('matchResultBody');
            if (selectedTargetStocks.length === 0) {
                body.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>';
                return;
            }

            let etfGroup = {};
            globalRawData.forEach(r => {
                if (!etfGroup[r.etf]) etfGroup[r.etf] = [];
                etfGroup[r.etf].push(r);
            });

            let results = [];
            Object.keys(etfGroup).forEach(eCode => {
                let eRows = etfGroup[eCode];
                let dates = [...new Set(eRows.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;

                let latestDate = dates[dates.length - 1];
                let latestRows = eRows.filter(x => x.date === latestDate);

                let totalMatchWeight = 0;
                let matchedDetails = [];

                selectedTargetStocks.forEach(target => {
                    let row = latestRows.find(x => x.stock.toUpperCase() === target.code.toUpperCase());
                    if (row && Number(row.weight) > 0) {
                        let w = Number(row.weight);
                        totalMatchWeight += w;
                        matchedDetails.push({ code: target.code, name: target.name, weight: w });
                    }
                });

                if (matchedDetails.length > 0) {
                    results.push({
                        etfCode: eCode,
                        etfName: etfNameMappingData[eCode] || "未知名稱",
                        totalWeight: totalMatchWeight,
                        matchedCount: matchedDetails.length,
                        details: matchedDetails
                    });
                }
            });

            results.sort((a,b) => (b.matchedCount - a.matchedCount) || (b.totalWeight - a.totalWeight));

            let html = results.map(r => {
                let chips = r.details.map(d => `<span class="badge bg-light text-primary border me-1"><b>${d.code}</b> ${d.name} (${d.weight.toFixed(1)}%)</span>`).join('');
                return `<tr>
                    <td class="font-monospace fw-bold fs-6">${r.etfCode}</td>
                    <td class="fw-bold text-secondary">${r.etfName}</td>
                    <td class="text-end font-monospace text-primary fw-bold fs-6">${r.totalWeight.toFixed(2)}%</td>
                    <td class="px-4">${chips}</td>
                </tr>`;
            }).join('');

            body.innerHTML = html || '<tr><td colspan="4" class="text-center py-4 text-muted">無 ETF 組合包含目前選定的目標公司。</td></tr>';
        }

        // ==========================================
        // 全市場異動總覽
        // ==========================================
        function toggleGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            document.getElementById('globalCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let newStockMap = {}; 
            let delStockMap = {};

            let etfGroup = {};
            globalRawData.forEach(r => {
                if (!etfGroup[r.etf]) etfGroup[r.etf] = [];
                etfGroup[r.etf].push(r);
            });

            Object.keys(etfGroup).forEach(eCode => {
                let eRows = etfGroup[eCode];
                let dates = [...new Set(eRows.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length < 2) return;

                let dOld = null, dNew = dates[dates.length - 1];
                if (type === 'custom') {
                    dOld = document.getElementById('globalStartDate').value;
                    dNew = document.getElementById('globalEndDate').value;
                } else {
                    let offset = parseInt(type);
                    dOld = dates.length > offset ? dates[dates.length - 1 - offset] : dates[0];
                }

                if (!dOld || !dNew) return;

                let oldRows = eRows.filter(x => x.date === dOld);
                let newRows = eRows.filter(x => x.date === dNew);

                let allStocks = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])].filter(s => {
                    let match = newRows.find(x=>x.stock===s) || oldRows.find(x=>x.stock===s);
                    return match ? isNormalStock(match.stock, match.name) : false;
                });

                allStocks.forEach(sCode => {
                    let oRow = oldRows.find(x => x.stock === sCode);
                    let nRow = newRows.find(x => x.stock === sCode);
                    let oVol = oRow ? Number(oRow.volume) : 0;
                    let nVol = nRow ? Number(nRow.volume) : 0;
                    let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知");
                    let token = sCode + "||" + sName;

                    if (oVol === 0 && nVol > 0) {
                        if (!newStockMap[token]) newStockMap[token] = [];
                        newStockMap[token].push(eCode);
                    } else if (oVol > 0 && nVol === 0) {
                        if (!delStockMap[token]) delStockMap[token] = [];
                        delStockMap[token].push(eCode);
                    }
                });
            });

            let newHtml = Object.keys(newStockMap).map(token => {
                let [code, name] = token.split("||");
                let listChips = newStockMap[token].map(e => `<span class="badge bg-danger-subtle text-danger border border-danger-subtle me-1"><b>${e}</b></span>`).join('');
                return `<tr>
                    <td class="fw-bold">${code} <span class="text-muted small fw-normal ms-1">${name}</span></td>
                    <td>${listChips}</td>
                </tr>`;
            }).join('');

            let delHtml = Object.keys(delStockMap).map(token => {
                let [code, name] = token.split("||");
                let listChips = delStockMap[token].map(e => `<span class="badge bg-secondary-subtle text-dark border me-1"><b>${e}</b></span>`).join('');
                return `<tr>
                    <td class="fw-bold text-secondary">${code} <span class="text-muted small fw-normal ms-1">${name}</span></td>
                    <td>${listChips}</td>
                </tr>`;
            }).join('');

            document.getElementById('globalNewBody').innerHTML = newHtml || '<tr><td colspan="2" class="text-center text-muted py-3">選定區間內無全市場 ETF 新增成分股</td></tr>';
            document.getElementById('globalDelBody').innerHTML = delHtml || '<tr><td colspan="2" class="text-center text-muted py-3">選定區間內無全市場 ETF 剔除成分股</td></tr>';
        }

        // ==========================================
        // 市場熱度排行
        // ==========================================
        function toggleHeatCustomDates() {
            let type = document.getElementById('heatRangeType').value;
            document.getElementById('heatCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadMarketHeat() {
            let type = document.getElementById('heatRangeType').value;
            let netVolMap = {}; 

            let etfGroup = {};
            globalRawData.forEach(r => {
                if (!etfGroup[r.etf]) etfGroup[r.etf] = [];
                etfGroup[r.etf].push(r);
            });

            Object.keys(etfGroup).forEach(eCode => {
                let eRows = etfGroup[eCode];
                let dates = [...new Set(eRows.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length < 2) return;

                let dOld = null, dNew = dates[dates.length - 1];
                if (type === 'custom') {
                    dOld = document.getElementById('heatStartDate').value;
                    dNew = document.getElementById('heatEndDate').value;
                } else {
                    let offset = parseInt(type);
                    dOld = dates.length > offset ? dates[dates.length - 1 - offset] : dates[0];
                }

                if (!dOld || !dNew) return;

                let oldRows = eRows.filter(x => x.date === dOld);
                let newRows = eRows.filter(x => x.date === dNew);

                let allStocks = [...new Set([...oldRows.map(r=>r.stock), ...newRows.map(r=>r.stock)])].filter(s => {
                    let match = newRows.find(x=>x.stock===s) || oldRows.find(x=>x.stock===s);
                    return match ? isNormalStock(match.stock, match.name) : false;
                });

                allStocks.forEach(sCode => {
                    let oRow = oldRows.find(x => x.stock === sCode);
                    let nRow = newRows.find(x => x.stock === sCode);
                    let oVol = oRow ? Number(oRow.volume) : 0;
                    let nVol = nRow ? Number(nRow.volume) : 0;
                    let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知");
                    let token = sCode + "||" + sName;

                    let diff = nVol - oVol;
                    netVolMap[token] = (netVolMap[token] || 0) + diff;
                });
            });

            let buyList = [];
            let sellList = [];

            Object.keys(netVolMap).forEach(token => {
                let v = netVolMap[token];
                let [code, name] = token.split("||");
                if (v > 0) buyList.push({ code: code, name: name, vol: v });
                if (v < 0) sellList.push({ code: code, name: name, vol: Math.abs(v) });
            });

            buyList.sort((a,b) => b.vol - a.vol);
            sellList.sort((a,b) => b.vol - a.vol);

            let maxBuy = buyList.length > 0 ? buyList[0].vol : 1;
            let maxSell = sellList.length > 0 ? sellList[0].vol : 1;

            let buyHtml = buyList.slice(0, 10).map((x, idx) => {
                let medalClass = idx === 0 ? 'medal-1' : (idx === 1 ? 'medal-2' : (idx === 2 ? 'medal-3' : 'medal-other'));
                let pct = Math.round((x.vol / maxBuy) * 100);
                return `<tr>
                    <td><span class="rank-medal ${medalClass}">${idx + 1}</span></td>
                    <td class="fw-bold">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                    <td>
                      <div class="heat-progress-container">
                        <span class="font-monospace fw-bold text-danger">+${Math.round(x.vol).toLocaleString()} 股</span>
                        <div class="heat-bar-wrapper">
                          <div class="progress-bar bg-danger" style="width: ${pct}%; height: 6px; border-radius: 3px;"></div>
                        </div>
                      </div>
                    </td>
                </tr>`;
            }).join('');

            let sellHtml = sellList.slice(0, 10).map((x, idx) => {
                let medalClass = idx === 0 ? 'medal-1' : (idx === 1 ? 'medal-2' : (idx === 2 ? 'medal-3' : 'medal-other'));
                let pct = Math.round((x.vol / maxSell) * 100);
                return `<tr>
                    <td><span class="rank-medal ${medalClass}">${idx + 1}</span></td>
                    <td class="fw-bold text-secondary">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                    <td>
                      <div class="heat-progress-container">
                        <span class="font-monospace fw-bold text-success">-${Math.round(x.vol).toLocaleString()} 股</span>
                        <div class="heat-bar-wrapper">
                          <div class="progress-bar bg-success" style="width: ${pct}%; height: 6px; border-radius: 3px;"></div>
                        </div>
                      </div>
                    </td>
                </tr>`;
            }).join('');

            document.getElementById('heatBuyBody').innerHTML = buyHtml || '<tr><td colspan="3" class="text-center text-muted py-3">選定區間內無經理人淨加碼標的</td></tr>';
            document.getElementById('heatSellBody').innerHTML = sellHtml || '<tr><td colspan="3" class="text-center text-muted py-3">選定區間內無經理人淨減持標的</td></tr>';
        }

        // ==========================================
        // ETF 交叉比較矩陣
        // ==========================================
        function renderCompareMatrix() {
            let checkedEtfs = Array.from(document.querySelectorAll('#compareCheckboxContainer input[type="checkbox"]:checked')).map(cb => cb.value);

            let summarySection = document.getElementById('compareSummarySection');
            let coreCard = document.getElementById('coreHoldingsCard');
            let uniqueCard = document.getElementById('uniqueHoldingsCard');
            let placeholder = document.getElementById('comparePlaceholder');

            if (checkedEtfs.length < 2) {
                summarySection.style.display = 'none';
                coreCard.style.display = 'none';
                uniqueCard.style.display = 'none';
                placeholder.style.display = 'block';
                return;
            }

            placeholder.style.display = 'none';

            let etfLatestStockMap = {}; 
            checkedEtfs.forEach(eCode => {
                let eRows = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(eRows.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;
                let latestDate = dates[dates.length - 1];
                let latestRows = eRows.filter(x => x.date === latestDate && isNormalStock(x.stock, x.name));

                etfLatestStockMap[eCode] = {};
                latestRows.forEach(r => {
                    etfLatestStockMap[eCode][r.stock] = {
                        weight: Number(r.weight) || 0,
                        name: r.name || tickerMappingData[r.stock]?.name || r.stock
                    };
                });
            });

            let allStockTokens = new Set();
            Object.keys(etfLatestStockMap).forEach(eCode => {
                Object.keys(etfLatestStockMap[eCode]).forEach(sCode => {
                    allStockTokens.add(sCode);
                });
            });

            let coreStocks = [];
            let uniqueStocks = [];

            allStockTokens.forEach(sCode => {
                let holdingCount = 0;
                let totalWeight = 0;
                let sName = "";

                checkedEtfs.forEach(eCode => {
                    if (etfLatestStockMap[eCode][sCode]) {
                        holdingCount++;
                        totalWeight += etfLatestStockMap[eCode][sCode].weight;
                        sName = etfLatestStockMap[eCode][sCode].name;
                    }
                });

                let item = {
                    code: sCode,
                    name: sName,
                    count: holdingCount,
                    avgWeight: totalWeight / checkedEtfs.length,
                    weights: {}
                };

                checkedEtfs.forEach(eCode => {
                    item.weights[eCode] = etfLatestStockMap[eCode][sCode] ? etfLatestStockMap[eCode][sCode].weight : 0;
                });

                if (holdingCount === checkedEtfs.length) {
                    coreStocks.push(item);
                } else {
                    uniqueStocks.push(item);
                }
            });

            coreStocks.sort((a,b) => b.avgWeight - a.avgWeight);
            uniqueStocks.sort((a,b) => (b.count - a.count) || (b.avgWeight - a.avgWeight));

            let topOverlap = [...coreStocks, ...uniqueStocks].sort((a,b) => (b.count - a.count) || (b.avgWeight - a.avgWeight)).slice(0, 3);
            let summaryHtml = topOverlap.map(x => {
                return `<div class="col-md-4">
                    <div class="summary-card">
                      <div class="d-flex justify-content-between align-items-center mb-2">
                        <span class="badge bg-primary font-monospace fs-6">${x.code}</span>
                        <span class="badge bg-light text-dark border">覆蓋率: ${x.count} / ${checkedEtfs.length} 檔</span>
                      </div>
                      <div class="fw-bold fs-5 text-dark mb-1">${x.name}</div>
                      <div class="small text-muted">平均交叉對照權重: <b class="text-primary font-monospace">${x.avgWeight.toFixed(2)}%</b></div>
                    </div>
                </div>`;
            }).join('');

            document.getElementById('compareSummaryCards').innerHTML = summaryHtml;
            summarySection.style.display = 'block';

            let thHtml = `<th>股票代號</th><th>股票名稱</th><th>共同持有度</th>` + checkedEtfs.map(e => `<th class="text-end font-monospace">${e} (${etfNameMappingData[e] || ''})</th>`).join('');
            document.getElementById('compareCoreTableHeader').innerHTML = thHtml;
            document.getElementById('compareUniqueTableHeader').innerHTML = thHtml;

            let getWeightClass = (w) => {
                if (w >= 5.0) return "weight-high";
                if (w >= 2.0) return "weight-med";
                if (w > 0) return "weight-low";
                return "weight-none";
            };

            let coreBodyHtml = coreStocks.map(x => {
                let weightCols = checkedEtfs.map(e => {
                    let w = x.weights[e];
                    let cls = getWeightClass(w);
                    return `<td class="text-end font-monospace ${cls}">${w > 0 ? w.toFixed(2) + '%' : '-'}</td>`;
                }).join('');

                return `<tr>
                    <td class="font-monospace fw-bold">${x.code}</td>
                    <td class="fw-bold">${x.name}</td>
                    <td><span class="badge bg-danger">全數一致持有 (100%)</span></td>
                    ${weightCols}
                </tr>`;
            }).join('');

            let uniqueBodyHtml = uniqueStocks.map(x => {
                let weightCols = checkedEtfs.map(e => {
                    let w = x.weights[e];
                    let cls = getWeightClass(w);
                    return `<td class="text-end font-monospace ${cls}">${w > 0 ? w.toFixed(2) + '%' : '-'}</td>`;
                }).join('');

                let pct = Math.round((x.count / checkedEtfs.length) * 100);
                return `<tr>
                    <td class="font-monospace fw-bold">${x.code}</td>
                    <td class="fw-bold text-secondary">${x.name}</td>
                    <td><span class="badge bg-light text-dark border">重疊率: ${pct}% (${x.count}/${checkedEtfs.length})</span></td>
                    ${weightCols}
                </tr>`;
            }).join('');

            document.getElementById('compareCoreTableBody').innerHTML = coreBodyHtml || '<tr><td colspan="' + (3 + checkedEtfs.length) + '" class="text-center text-muted py-3">選定 ETF 無完全一致覆蓋的共同核心持股</td></tr>';
            document.getElementById('compareUniqueTableBody').innerHTML = uniqueBodyHtml || '<tr><td colspan="' + (3 + checkedEtfs.length) + '" class="text-center text-muted py-3">選定 ETF 無獨門特色持股差異</td></tr>';

            coreCard.style.display = 'block';
            uniqueCard.style.display = 'block';
        }
      </script>
    </body>
    </html>
    """

    # 資料替換與元件渲染
    rendered_html = html_template.replace("__DATA_PLACEHOLDER__", json_data) \
                                 .replace("__TWSE_PLACEHOLDER__", json.dumps(twse_live_market, ensure_ascii=False)) \
                                 .replace("__TICKER_PLACEHOLDER__", json.dumps(ticker_map, ensure_ascii=False)) \
                                 .replace("__ETF_NAME_PLACEHOLDER__", json.dumps(etf_name_map, ensure_ascii=False))

    components.html(rendered_html, height=1200, scrolling=True)

if __name__ == '__main__':
    main()
