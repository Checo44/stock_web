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
    if not sh: return {}, {}, "無法連線至 Google 試算表"
    try:
        ws = sh.worksheet(WORKSHEET_TICKER)
        raw_ticker = ws.get_all_values()
        if not raw_ticker or len(raw_ticker) < 1: return {}, {}, None
        
        headers = [str(h).strip() for h in raw_ticker[0]]
        code_idx = None
        name_idx = None
        industry_idx = None
        
        for idx, h in enumerate(headers):
            if h in ["股票代號", "代號", "成分股代號", "商品代號"]:
                code_idx = idx
            if h in ["公司名稱", "股票名稱", "名稱", "成分股名稱", "商品名稱"]:
                name_idx = idx
            if h in ["產業別", "產業", "板塊", "類股", "行業", "產業分類"]:
                industry_idx = idx
                
        if code_idx is None: code_idx = 0
        if name_idx is None: name_idx = 1 if len(headers) > 1 else 0
        
        ticker_map = {}
        industry_map = {}
        for row in raw_ticker[1:]:
            if len(row) > max(code_idx, name_idx):
                code = str(row[code_idx]).strip()
                name = str(row[name_idx]).strip()
                industry = str(row[industry_idx]).strip() if industry_idx is not None and len(row) > industry_idx else "其他"
                if not industry: industry = "其他"
                
                if code: 
                    if code.isalpha():
                        code = f"{code} US"
                    ticker_map[code] = name
                    industry_map[code] = industry
        return ticker_map, industry_map, None
    except Exception as e:
        return {}, {}, f"讀取「{WORKSHEET_TICKER}」工作表失敗: {str(e)}"

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
# 3. FinMind PBR/PER 批次查詢與快取
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
        "volume": ["持有數量", "持有數", "張數", "持有張數", "股數", "持有股數"]
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
    df['stock'] = df['stock'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    is_pure_english = df['stock'].str.match(r'^[A-Za-z]+$')
    df.loc[is_pure_english, 'stock'] = df.loc[is_pure_english, 'stock'] + ' US'
    
    if 'name' not in df.columns:
        df['name'] = ""
    
    if ticker_map:
        mapped_series = df['stock'].map(ticker_map)
        df['name'] = mapped_series.fillna(df['name']).astype(str).str.strip()
    else:
        df['name'] = df['name'].astype(str).str.strip()
        
    return df, None

# ==========================================
# 5. 主核心資料庫結構轉換與打包
# ==========================================
def fetch_backend_data_to_json():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg: return "[]", {}, {}, {}, {}
        
    ticker_map, industry_map, _ = fetch_ticker_mapping()
    etf_name_map, _ = fetch_etf_name_mapping()
    
    df, clean_err = process_and_standardize(raw_data, ticker_map=ticker_map)
    if clean_err or df.empty: return "[]", {}, {}, {}, {}
    
    # 將產業別整合進主資料表中
    df['industry'] = df['stock'].map(industry_map).fillna("其他")
    
    all_etfs = sorted(list(df['etf'].dropna().unique()))
    twse_live_market = fetch_twse_live_data(all_etfs)
    
    # 整合 FinMind 估值計算
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
      <!-- 引入 Chart.js 用於繪製圓餅圖 -->
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
        .badge-nature-new { background-color: #f97316; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-up { background-color: #dc2626; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-down { background-color: #0f766e; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-nature-delete { background-color: #374151; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }
        .badge-trend-buy { background-color: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; border: 1px solid #bbf7d0; }
        .badge-trend-sell { background-color: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 0.8rem; border: 1px solid #fde68a; }
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
        
        .heat-progress-container {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 12px;
        }
        .heat-bar-wrapper {
          width: 120px;
          background-color: #f1f5f9;
          border-radius: 4px;
          overflow: hidden;
        }

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

                <!-- 💡 嵌入元件：單檔經理人風格與持股診斷卡片 -->
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
                    
                    <!-- 經理人持股分類盒 -->
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
                    
                    <!-- 💡 經理人進階量化與調倉分析 (動態產生內容) -->
                    <div id="diagResultTextContainer" class="border-top pt-3 mt-3"></div>
                    
                    <!-- 系統備註與限制公告區 -->
                    <div class="alert alert-secondary mb-0 py-2 px-3 mt-3 small border-0" style="background-color: #f8fafc; color: #64748b;">
                      <div class="row g-2">
                        <div class="col-md-6"><i class="bi bi-info-circle-fill me-1 text-primary"></i><b>顯著加碼標準：</b>異動股數增加且權重變動大於該規模的 0.5%（目前尚無加入大盤基準值）。</div>
                        <div class="col-md-6"><i class="bi text-warning"></i><b>持倉成本說明：</b>當前公開大數據與試算表數據源中，無經理人實際持股成本資料。</div>
                      </div>
                    </div>
                  </div>
                </div>
                
                <div class="row g-3">
                  <div class="col-lg-7">
                    <div class="card">
                      <div class="card-header text-primary"><i class="bi bi-list-stars me-2"></i>最新成分股持股明細</div>
                      <div class="table-responsive" style="max-height: 650px;">
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
                    <!-- 🥧 新增：產業別分佈圓餅圖卡片 -->
                    <div class="card mb-3">
                      <div class="card-header text-success"><i class="bi bi-pie-chart-fill me-2"></i>最新持股產業別分佈</div>
                      <div class="card-body d-flex justify-content-center align-items-center" style="height: 300px; position: relative;">
                        <canvas id="industryPieChart"></canvas>
                      </div>
                    </div>

                    <div class="card">
                      <div class="card-header text-secondary"><i class="bi bi-cash-coin me-2"></i>非股票資產項目</div>
                      <div class="table-responsive" style="max-height: 300px;">
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
          
          <div class="custom-tab-content" id="content-f">
            <div class="card p-4 bg-light border-0 shadow-sm rounded-4 mb-4">
              <h4 class="fw-bold text-dark mb-2"><i class="bi bi-cpu-fill text-primary me-2"></i>AI 投資組合回溯目標搜尋器</h4>
              <p class="text-muted small">請任意輸入並挑選多檔台灣投資目標公司（如：台積電、聯發科、瑞昱），系統將深度回溯大數據，為您精算出同時重疊包含這群目標公司的精選 ETF 陣容。</p>
              
              <div class="row align-items-center g-3" style="position: relative;">
                <div class="col-md-5" style="position: relative;">
                  <label class="form-label fw-bold text-secondary">請輸入台股個股名稱或代號（支援模糊搜尋）</label>
                  <input type="text" id="matcherInput" class="form-control" placeholder="僅限台股標的" onkeyup="searchStockSuggestions(this.value, 'matcherSuggestions', 'matcherInput', true)">
                  <div id="matcherSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-12 mt-3">
                  <div class="fw-bold text-secondary mb-2">目前已選取的台灣投資目標公司：</div>
                  <div id="selectedTargetContainer" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded" style="min-height: 58px;">
                    <span class="text-muted small py-1" id="noTargetText">尚未選取 any 公司，請從上方搜尋框輸入並挑選組合</span>
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
        window.myPieChart = null; // 全域變數以儲存 Chart 實例

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

        // ==========================================
        // 📡 經理人共識雷達
        // ==========================================
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

        // ==========================================
        // 💡 診斷核心：經理人投資風格與持股診斷詳細統整
        // ==========================================
        function runManagerStyleDiagnosis(etfName, dOld, dNew, sortedDates) {
            let etfData = globalRawData.filter(d => d.etf === etfName);
            let oldRows = etfData.filter(d => d.date === dOld);
            let newRows = etfData.filter(d => d.date === dNew);

            if (oldRows.length === 0 || newRows.length === 0) {
                return;
            }

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

            let styleTagText = "";
            let styleTagClass = "";
            let styleDescription = "";
            if (turnoverRate < 5) {
                styleTagText = "超低頻價值長抱流派 (周轉率 < 5%)";
                styleTagClass = "bg-success";
                styleDescription = "此基金高度看好核心組合，幾近無換股動作。經理人採取純粹的買入並持有（Buy and Hold）策略，追求長期基本面資產增值。";
            } else if (turnoverRate < 15) {
                styleTagText = "穩健長期價值投資 (周轉率 5%~15%)";
                styleTagClass = "bg-success-subtle text-success border border-success";
                styleDescription = "調倉動作極其精準克制，僅在成分股權重偏離過大時進行再平衡或基本面微調。典型的價值投資與穩健型主動管理。";
            } else if (turnoverRate <= 35) {
                styleTagText = "動態靈活戰術調整 (周轉率 15%~35%)";
                styleTagClass = "bg-primary";
                styleDescription = "操作風格靈活！經理人會根據季度營收、法說會預期與產業週期，積極進行中等幅度的再平衡佈局，力求兼顧長期與中短期獲利。";
            } else {
                styleTagText = "積極高周轉波段流派 (周轉率 > 35%)";
                styleTagClass = "bg-danger";
                styleDescription = "持股周轉率極高！經理人非常主動地追逐市場高動能熱點，操作雷厲風行，高度偏好藉由短線波段操作與高頻調倉來獵取超額收益。";
            }

            let badge = document.getElementById('badgeStyleTag');
            badge.innerText = styleTagText;
            badge.className = "badge " + styleTagClass + " fs-6 px-3 py-2";

            let latestStocks = newRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);
            let top5Weight = 0;
            let top10Weight = 0;
            latestStocks.forEach((r, idx) => {
                let w = Number(r.weight);
                if (idx < 5) top5Weight += w;
                if (idx < 10) top10Weight += w;
            });

            let concentrationText = "";
            if (top5Weight > 45) {
                concentrationText = `高度集中 (前五大持股佔比達 ${top5Weight.toFixed(1)}%)。採取重倉高信賴度押注策略，基金表現高度依賴少數核心主導權值股，爆發力強、波動度同等偏高。`;
            } else if (top5Weight >= 30) {
                concentrationText = `中度平衡集中 (前五大持股佔比為 ${top5Weight.toFixed(1)}%)。配置兼顧主力戰略部隊與風險分散，為業界公認兼具攻擊力與防守彈性的黃金配置比。`;
            } else {
                concentrationText = `高度廣泛分散 (前五大持股佔比僅 ${top5Weight.toFixed(1)}%)。操作採取普惠式配置，極力降低單一黑天鵝風險，其報酬軌跡將會與大盤基準表現高度貼合。`;
            }

            let wPerText = document.getElementById('metaWeightedPer').innerText;
            let wPbrText = document.getElementById('metaWeightedPbr').innerText;
            let wPer = parseFloat(wPerText) || 0;
            let wPbr = parseFloat(wPbrText) || 0;
            let valuationDesc = "";

            if (wPer > 22 || wPbr > 3.0) {
                valuationDesc = "持股加權估值顯著偏高，屬於「高本益比成長型配置」。經理人重倉押注於高成長動能的尖端科技、AI晶片或具備產業寡占護城河的熱點股，極度追逐未來預期爆發力。";
            } else if (wPer >= 13 && wPer <= 22) {
                valuationDesc = "組合加權本益比定位合理，屬於「橫跨混合型配置」。防守型大市值權值股與中堅成長股配置得當，攻守兼備。";
            } else if (wPer > 0) {
                valuationDesc = "整體本益比偏低，具備典型的「低估防禦價值型特徵」。投資組合多佈局在金融、高殖利率成熟期傳產或嚴重低估標的，波動率低且利息防禦能力強。";
            } else {
                valuationDesc = "部分成分股缺乏 FinMind 最新估值資訊。";
            }

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

            let coreHtml = "";
            let satelliteHtml = "";
            let allHistoricalStocks = Object.keys(occurrenceMap);

            allHistoricalStocks.forEach(sCode => {
                let appearanceRate = occurrenceMap[sCode] / totalObservedDays;
                let lRow = latestStocks.find(x => x.stock === sCode);
                let currentWeight = lRow ? Number(lRow.weight) : 0;
                let sName = lRow ? lRow.name : (etfData.find(x => x.stock === sCode)?.name || "歷史成分股");

                if (currentWeight >= 4 && appearanceRate >= 0.8) {
                    coreHtml += `<span class="badge bg-danger text-white m-1 p-2" title="歷史持倉天數佔比: ${(appearanceRate*100).toFixed(0)}%"><b>${sCode}</b> ${sName} (${currentWeight.toFixed(1)}%)</span>`;
                }
                else if (currentWeight < 2 && appearanceRate < 0.4 && currentWeight > 0) {
                    satelliteHtml += `<span class="badge bg-info text-dark m-1 p-2" title="歷史持倉天數佔比: ${(appearanceRate*100).toFixed(0)}%"><b>${sCode}</b> ${sName} (${currentWeight.toFixed(1)}%)</span>`;
                }
            });

            document.getElementById('boxCoreList').innerHTML = coreHtml || '<span class="text-muted small p-2">無符合核心高權重長持股條件標的</span>';
            document.getElementById('boxSatelliteList').innerHTML = satelliteHtml || '<span class="text-muted small p-2">無符合低權重短線衛星股條件標的</span>';

            let brandNewList = [];
            let liquidatedList = [];
            let majorBuyList = [];
            let majorSellList = [];

            let aiBrandNew = [];
            let aiLiquidated = [];
            let aiMajorBuy = [];
            let aiMajorSell = [];

            allStockTokens.forEach(s => {
                let oRow = oldRows.find(x => x.stock === s);
                let nRow = newRows.find(x => x.stock === s);
                let oW = oRow ? Number(oRow.weight) : 0;
                let nW = nRow ? Number(nRow.weight) : 0;
                let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知");

                if (!isNormalStock(s, sName)) return;

                if (oW === 0 && nW > 0) {
                    brandNewList.push(`<b class="text-dark">${s} ${sName}</b> (${nW.toFixed(1)}%)`);
                    aiBrandNew.push({ code: s, name: sName, weight: nW });
                } else if (oW > 0 && nW === 0) {
                    liquidatedList.push(`<del class="text-muted">${s} ${sName}</del>`);
                    aiLiquidated.push({ code: s, name: sName, weight: oW });
                } else {
                    let diffW = nW - oW;
                    if (diffW >= 1.0) {
                        majorBuyList.push(`<b class="text-danger">${s} ${sName}</b> (+${diffW.toFixed(1)}%)`);
                        aiMajorBuy.push({ code: s, name: sName, diff: diffW });
                    } else if (diffW <= -1.0) {
                        majorSellList.push(`<b class="text-success">${s} ${sName}</b> (${diffW.toFixed(1)}%)`);
                        aiMajorSell.push({ code: s, name: sName, diff: diffW });
                    }
                }
            });

            let finalAIInsight = "";
            let sectorsBought = {};
            let sectorsSold = {};

            let getIndustry = (code, record) => {
                if (record && record.industry && record.industry !== "其他") {
                    return record.industry;
                }
                let clean = String(code).trim().toUpperCase();
                if (clean === "8046") return "IC載板高階材料板塊 (南電)";
                if (clean === "6510") return "半導體封測介面與探針卡板塊 (精測)";
                if (clean === "5347") return "晶圓代工成熟與特種製程板塊 (世界先進)";
                if (["2330", "2454", "2303", "3711", "3034", "3035", "2337", "2344", "4961", "8081", "6415", "3529", "3661", "6643", "TSMC", "NVDA", "AMD", "INTC", "ASML", "QCOM", "AVGO", "MU"].includes(clean)) return "半導體核心供應鏈";
                if (["2317", "2382", "3231", "2357", "2353", "2324", "2301", "3563", "2395", "6669", "AAPL", "MSFT", "GOOG", "META", "AMZN", "NFLX"].includes(clean)) return "電腦週邊與科技巨頭";
                if (["2881", "2882", "2886", "2891", "2892", "2880", "2883", "2884", "2885", "2887", "2890", "5880", "5871", "5876"].includes(clean)) return "金融保險業";
                if (["2603", "2609", "2615", "2618", "2610"].includes(clean)) return "航運物流業";
                if (["1301", "1303", "1326", "6505", "2002", "1101", "1102"].includes(clean)) return "傳統製造與原物料";
                return "其他新興板塊";
            };

            [...aiBrandNew, ...aiMajorBuy].forEach(item => {
                let matchRec = newRows.find(x => x.stock === item.code);
                let ind = getIndustry(item.code, matchRec);
                if (!sectorsBought[ind]) sectorsBought[ind] = [];
                sectorsBought[ind].push(item.name);
            });

            [...aiLiquidated, ...aiMajorSell].forEach(item => {
                let matchRec = oldRows.find(x => x.stock === item.code);
                let ind = getIndustry(item.code, matchRec);
                if (!sectorsSold[ind]) sectorsSold[ind] = [];
                sectorsSold[ind].push(item.name);
            });

            let buyNarrative = [];
            Object.keys(sectorsBought).forEach(ind => {
                let list = sectorsBought[ind].slice(0, 3).join("、");
                buyNarrative.push(`加碼或建倉了**${ind}**（如 ${list}），顯現經理人對該板塊長線需求的配置信心。`);
            });

            let sellNarrative = [];
            Object.keys(sectorsSold).forEach(ind => {
                let list = sectorsSold[ind].slice(0, 3).join("、");
                sellNarrative.push(`對**${ind}**的持股（如 ${list}）進行了適度減持與調節，以維持基金在該板塊的曝險平衡。`);
            });

            if (buyNarrative.length > 0) {
                finalAIInsight += `<p class="mb-2">💡 <b>AI 產業加碼透視：</b> 經理人在此期間${buyNarrative.join("；此外，")}</p>`;
            } else {
                finalAIInsight += `<p class="mb-2">💡 <b>AI 產業加碼透視：</b> 本期經理人調倉動作較為平緩，並未對特定產業板塊進行集中式的超額加碼。</p>`;
            }
            if (sellNarrative.length > 0) {
                finalAIInsight += `<p class="mb-0">🎯 <b>AI 調節避險分析：</b> 在調節動作中，經理人${sellNarrative.join("；同時，")}</p>`;
            } else {
                finalAIInsight += `<p class="mb-0">🎯 <b>AI 調節避險分析：</b> 本期無明顯的集中性產業調節，多屬個股評價面再平衡微調。</p>`;
            }

            let diagReportHtml = `
                <div class="row g-3">
                    <div class="col-md-6 border-end">
                        <div class="mb-3">
                            <span class="text-dark fw-bold fs-6"><i class="bi bi-compass-fill text-success me-1"></i>操作風格定位：</span>
                            <span class="text-secondary small d-block mt-1">${styleDescription}</span>
                        </div>
                        <div class="mb-3">
                            <span class="text-dark fw-bold fs-6"><i class="bi bi-pie-chart-fill text-warning me-1"></i>持股集中度剖析：</span>
                            <span class="text-secondary small d-block mt-1">${concentrationText}</span>
                        </div>
                        <div class="mb-2">
                            <span class="text-dark fw-bold fs-6"><i class="bi bi-graph-up-arrow text-info me-1"></i>加權估值定位：</span>
                            <span class="text-secondary small d-block mt-1">最新台股持股加權平均本益比為 <b>${wPerText}</b>，加權平均股淨比為 <b>${wPbrText}</b>。${valuationDesc}</span>
                        </div>
                    </div>
                    <div class="col-md-6">
                        <div class="p-3 border rounded bg-white" style="background-color:#fafbfc !important; max-height: 380px; overflow-y: auto;">
                            <span class="text-dark fw-bold fs-6"><i class="bi bi-lightning-fill text-danger me-1"></i>本期重大調倉異動報告：</span>
                            <ul class="list-unstyled ps-1 mt-2 small text-secondary">
                                <li class="mb-2">🚀 <b>全新建倉部位：</b> ${brandNewList.length > 0 ? brandNewList.join(', ') : '<span class="text-muted">本期無新增建倉</span>'}</li>
                                <li class="mb-2">🗑️ <b>完全出清部位：</b> ${liquidatedList.length > 0 ? liquidatedList.join(', ') : '<span class="text-muted">本期無完全出清</span>'}</li>
                                <li class="mb-2">📈 <b>顯著加碼 (權重提升≥1.0%)：</b> ${majorBuyList.length > 0 ? majorBuyList.join(', ') : '<span class="text-muted">本期無顯著超額加碼</span>'}</li>
                                <li class="mb-2">📉 <b>顯著減碼 (權重降低≤-1.0%)：</b> ${majorSellList.length > 0 ? majorSellList.join(', ') : '<span class="text-muted">本期無顯著調節減持</span>'}</li>
                            </ul>
                            <hr class="my-2">
                            <span class="text-dark fw-bold fs-6"><i class="bi bi-cpu-fill text-primary me-1"></i>AI 今日持股異動深度評析：</span>
                            <div class="mt-2 small text-secondary">
                                ${finalAIInsight}
                            </div>
                        </div>
                    </div>
                </div>
            `;
            
            document.getElementById('diagResultTextContainer').innerHTML = diagReportHtml;
            document.getElementById('diagnosticCard').style.display = "block";
        }

        function selectEtf(etfName) {
            selectedEtf = etfName;
            document.querySelectorAll('.etf-item-btn').forEach(el => el.classList.remove('active'));
            let activeBtn = document.getElementById(`btn-etf-${etfName}`);
            if(activeBtn) activeBtn.classList.add('active');

            let etfData = globalRawData.filter(d => d.etf === etfName);
            let dates = etfData.map(d => d.date);
            let sortedDates = [...new Set(dates)].sort((a,b) => new Date(a) - new Date(b));
            let latestDate = sortedDates[sortedDates.length - 1];
            let latestRows = etfData.filter(d => d.date === latestDate);
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

            let sBody = document.getElementById('stockTableBody');
            let aBody = document.getElementById('assetTableBody');
            let sHtml = "";
            let aHtml = "";

            let stocks = latestRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);
            let assets = latestRows.filter(r => !isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);

            let totalTwWeightPer = 0;
            let totalTwWeightPbr = 0;
            let weightedPerSum = 0;
            let weightedPbrSum = 0;

            // 🥧 建立統計產業比例的物件
            let industryWeights = {};

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

                // 統計產業別比例
                let ind = r.industry || "其他";
                industryWeights[ind] = (industryWeights[ind] || 0) + w;

                let displayPer = (perVal > 0) ? perVal.toFixed(2) : "-";
                sHtml += `<tr>
                    <td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td>
                    <td class="fw-bold">${r.name} <span class="badge bg-secondary-subtle text-dark font-normal ms-1 small fw-normal">${ind}</span></td>
                    <td class="text-end font-monospace text-primary fw-bold">${Number(r.weight).toFixed(2)}%</td>
                    <td class="text-end font-monospace text-secondary">${Math.round(r.volume).toLocaleString()}</td>
                    <td class="text-end font-monospace text-info fw-bold">${displayPer}</td>
                </tr>`;
            });

            // 🥧 繪製產業別 Chart.js 圓餅圖
            let sortedIndustries = Object.keys(industryWeights).map(k => {
                return { name: k, value: industryWeights[k] };
            }).sort((a, b) => b.value - a.value);

            let labels = sortedIndustries.map(x => x.name);
            let dataValues = sortedIndustries.map(x => x.value.toFixed(2));

            if (window.myPieChart) {
                window.myPieChart.destroy();
            }
            let ctx = document.getElementById('industryPieChart').getContext('2d');
            window.myPieChart = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: labels,
                    datasets: [{
                        data: dataValues,
                        backgroundColor: [
                            '#1e3c72', '#319795', '#2a5298', '#4a5568', '#dd6b20', 
                            '#805ad5', '#e53e3e', '#3182ce', '#ecc94b', '#48bb78'
                        ]
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'right',
                            labels: { boxWidth: 12, font: { size: 11 } }
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    return ` ${context.label}: ${context.raw}%`;
                                }
                            }
                        }
                    }
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

            sBody.innerHTML = sHtml;
            aBody.innerHTML = aHtml;

            let rangeType = document.getElementById('rangeType').value;
            let dOld = null;
            if (rangeType === 'custom') {
                dOld = document.getElementById('startDateInput').value;
            } else {
                let offset = parseInt(rangeType);
                if (sortedDates.length > offset) {
                    dOld = sortedDates[sortedDates.length - 1 - offset];
                } else {
                    dOld = sortedDates[0];
                }
            }
            let dNew = latestDate;
            
            calculateStockChanges(etfName, dOld, dNew);
            runManagerStyleDiagnosis(etfName, dOld, dNew, sortedDates);
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
                let oW = oRow ? Number(oRow.weight) : 0;
                let nW = nRow ? Number(nRow.weight) : 0;
                let sName = nRow ? nRow.name : (oRow ? oRow.name : "未知股票");

                let diffVol = nVol - oVol;
                let diffW = nW - oW;

                if (diffVol === 0) return;

                let priority = 0;
                let actionBadge = "";
                let actionDesc = "";

                if (oVol === 0 && nVol > 0) {
                    priority = 1; 
                    actionBadge = `<span class="badge-nature-new"><i class="bi bi-plus-circle me-1"></i>全新納入</span>`;
                    actionDesc = `新增 ${Math.round(nVol).toLocaleString()} 股 (佔權重 ${nW.toFixed(2)}%)`;
                } else if (oVol > 0 && nVol === 0) {
                    priority = 4; 
                    actionBadge = `<span class="badge-nature-delete"><i class="bi bi-dash-circle me-1"></i>全數剔除</span>`;
                    actionDesc = `出清原持股 ${Math.round(oVol).toLocaleString()} 股 (原佔 ${oW.toFixed(2)}%)`;
                } else if (diffVol > 0) {
                    priority = 2; 
                    actionBadge = `<span class="badge-nature-up"><i class="bi bi-caret-up-fill me-1"></i>加碼持股</span>`;
                    actionDesc = `增加 ${Math.round(diffVol).toLocaleString()} 股 (權重變動: ${diffW > 0 ? '+' : ''}${diffW.toFixed(2)}%)`;
                } else if (diffVol < 0) {
                    priority = 3; 
                    actionBadge = `<span class="badge-nature-down"><i class="bi bi-caret-down-fill me-1"></i>減持股份</span>`;
                    actionDesc = `調節 ${Math.round(Math.abs(diffVol)).toLocaleString()} 股 (權重變動: ${diffW.toFixed(2)}%)`;
                }

                let trendBadge = "";
                if (oVol === 0 && nVol > 0) {
                    trendBadge = `<span class="badge-trend-buy">新增納入部位</span>`;
                } else if (oVol > 0 && nVol === 0) {
                    trendBadge = `<span class="badge-trend-sell">全數剔除部位</span>`;
                } else if (sortedDates.length >= 2) {
                    let latestIdx = sortedDates.length - 1;
                    let getVolOnDate = (d) => {
                        let r = etfData.find(x => x.date === d && x.stock === sCode);
                        return r ? Number(r.volume) : 0;
                    };

                    let currentDelta = getVolOnDate(sortedDates[latestIdx]) - getVolOnDate(sortedDates[latestIdx-1]);
                    let prevDelta = 0;
                    for (let i = latestIdx - 1; i >= 1; i--) {
                        let d = getVolOnDate(sortedDates[i]) - getVolOnDate(sortedDates[i-1]);
                        if (d !== 0) {
                            prevDelta = d;
                            break;
                        }
                    }

                    if (currentDelta > 0) {
                        if (prevDelta < 0) {
                            trendBadge = `<span class="badge-trend-buy">賣轉買 (+${Math.round(currentDelta).toLocaleString()}股)</span>`;
                        } else {
                            let streak = 0;
                            let totalStreakVol = 0;
                            for (let i = latestIdx; i >= 1; i--) {
                                let delta = getVolOnDate(sortedDates[i]) - getVolOnDate(sortedDates[i-1]);
                                if (delta > 0) {
                                    streak++;
                                    totalStreakVol += delta;
                                } else {
                                    break;
                                }
                            }
                            trendBadge = `<span class="badge-trend-buy">連買${streak}天 (${Math.round(totalStreakVol).toLocaleString()}股)</span>`;
                        }
                    } else if (currentDelta < 0) {
                        if (prevDelta > 0) {
                            trendBadge = `<span class="badge-trend-sell">買轉賣 (-${Math.round(Math.abs(currentDelta)).toLocaleString()}股)</span>`;
                        } else {
                            let streak = 0;
                            let totalStreakVol = 0;
                            for (let i = latestIdx; i >= 1; i--) {
                                let delta = getVolOnDate(sortedDates[i]) - getVolOnDate(sortedDates[i-1]);
                                if (delta < 0) {
                                    streak++;
                                    totalStreakVol += Math.abs(delta);
                                } else {
                                    break;
                                }
                            }
                            trendBadge = `<span class="badge-trend-sell">連賣${streak}天 (${Math.round(totalStreakVol).toLocaleString()}股)</span>`;
                        }
                    } else {
                        trendBadge = `<span class="badge bg-light text-dark border">無顯著動向</span>`;
                    }
                } else {
                    trendBadge = `<span class="badge bg-light text-dark border">資料不足</span>`;
                }

                rowsToRender.push({
                    priority: priority,
                    sCode: sCode,
                    sName: sName,
                    actionBadge: actionBadge,
                    actionDesc: actionDesc,
                    trendBadge: trendBadge
                });
            });

            rowsToRender.sort((a, b) => a.priority - b.priority);
            rowsToRender.forEach(row => {
                changeHtml += `<tr>
                    <td class="fw-bold">${row.sCode} <span class="text-muted small fw-normal ms-1">${row.sName}</span></td>
                    <td>${row.actionBadge}</td>
                    <td class="text-end font-monospace">${row.actionDesc}</td>
                    <td class="px-4">${row.trendBadge}</td>
                </tr>`;
            });

            document.getElementById('changeTableBody').innerHTML = changeHtml || '<tr><td colspan="4" class="text-center text-muted">此日期區間內，成分股股數無顯著增減異動</td></tr>';
        }

        function searchStockSuggestions(query, suggestionBoxId, inputId, isMatcher) {
            let box = document.getElementById(suggestionBoxId);
            if (!query || query.trim().length === 0) {
                box.style.display = 'none';
                return;
            }
            let q = query.trim().toUpperCase();
            let matches = [];
            
            Object.keys(tickerMappingData).forEach(code => {
                let name = tickerMappingData[code];
                if (code.toUpperCase().includes(q) || name.toUpperCase().includes(q)) {
                    matches.push({ code: code, name: name });
                }
            });
            
            if (matches.length === 0) {
                box.style.display = 'none';
                return;
            }
            
            let html = "";
            matches.slice(0, 8).forEach(item => {
                if (isMatcher) {
                    html += `<div class="suggestion-item" onclick="selectMatcherTarget('${item.code}', '${item.name}', '${suggestionBoxId}', '${inputId}')"><b>${item.code}</b> - ${item.name}</div>`;
                } else {
                    html += `<div class="suggestion-item" onclick="selectStockForDist('${item.code}', '${item.name}', '${suggestionBoxId}', '${inputId}')"><b>${item.code}</b> - ${item.name}</div>`;
                }
            });
            box.innerHTML = html;
            box.style.display = 'block';
        }

        function selectStockForDist(code, name, boxId, inputId) {
            document.getElementById(inputId).value = `${code} ${name}`;
            document.getElementById(boxId).style.display = 'none';
            window.selectedDistStock = code;
            window.selectedDistStockName = name;
        }

        function searchStockDistribution() {
            let code = window.selectedDistStock;
            if (!code) {
                alert("請先從下拉選單選取股票標的！");
                return;
            }
            
            let stockData = globalRawData.filter(d => d.stock === code);
            if (stockData.length === 0) {
                stockData = globalRawData.filter(d => d.stock.toUpperCase().includes(code.toUpperCase()));
            }
            if (stockData.length === 0) {
                alert("目前歷史大數據庫中，無任何 ETF 持有此股票標的。");
                return;
            }

            let sName = window.selectedDistStockName || (stockData[0] ? stockData[0].name : "未知股票");
            document.getElementById('resStockTitle').innerText = `${code} ${sName}`;

            let etfSet = new Set(globalRawData.map(d => d.etf));
            let latestEtfHoldings = [];
            let netVolChange = 0;

            etfSet.forEach(e => {
                let etfData = globalRawData.filter(d => d.etf === e);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;
                
                let latestD = dates[dates.length - 1];
                let latestRow = etfData.find(d => d.date === latestD && d.stock === code);
                
                if (latestRow) {
                    latestEtfHoldings.push({
                        etf: e,
                        etfName: etfNameMappingData[e] || "未知基金",
                        weight: Number(latestRow.weight),
                        volume: Number(latestRow.volume)
                    });
                }

                if (dates.length >= 2) {
                    let dNew = dates[dates.length - 1];
                    let dOld = dates.length > 20 ? dates[dates.length - 21] : dates[0];
                    let rNew = etfData.find(d => d.date === dNew && d.stock === code);
                    let rOld = etfData.find(d => d.date === dOld && d.stock === code);
                    let vNew = rNew ? Number(rNew.volume) : 0;
                    let vOld = rOld ? Number(rOld.volume) : 0;
                    netVolChange += (vNew - vOld);
                }
            });

            latestEtfHoldings.sort((a,b) => b.weight - a.weight);
            let distHtml2 = "";
            latestEtfHoldings.forEach(item => {
                distHtml2 += `<tr>
                    <td class="font-monospace fw-bold">${item.etf}</td>
                    <td class="fw-bold text-secondary">${item.etfName}</td>
                    <td class="text-end font-monospace text-primary fw-bold">${item.weight.toFixed(2)}%</td>
                </tr>`;
            });
            document.getElementById('stockDistBody2').innerHTML = distHtml2 || '<tr><td colspan="3" class="text-center text-muted">目前尚無任何 ETF 在最新期持股中包含此標的</td></tr>';

            let distHtml1 = "";
            etfSet.forEach(e => {
                let etfData = globalRawData.filter(d => d.etf === e);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length < 2) return;
                
                let dNew = dates[dates.length - 1];
                let dOld = dates.length > 20 ? dates[dates.length - 21] : dates[0];
                let rNew = etfData.find(d => d.date === dNew && d.stock === code);
                let rOld = etfData.find(d => d.date === dOld && d.stock === code);
                let vNew = rNew ? Number(rNew.volume) : 0;
                let vOld = rOld ? Number(rOld.volume) : 0;
                let diffV = vNew - vOld;

                if (vNew > 0 || vOld > 0) {
                    let changeColor = diffV > 0 ? "text-danger" : (diffV < 0 ? "text-success" : "text-muted");
                    let changeSign = diffV > 0 ? "+" : "";
                    distHtml1 += `<tr>
                        <td class="fw-bold">${e} <span class="text-muted small">${etfNameMappingData[e] || ""}</span></td>
                        <td class="font-monospace fw-bold ${changeColor}">${changeSign}${Math.round(diffV).toLocaleString()} 股</td>
                    </tr>`;
                }
            });
            document.getElementById('stockDistBody').innerHTML = distHtml1 || '<tr><td colspan="2" class="text-center text-muted">歷史區間內股數無任何變動</td></tr>';

            document.getElementById('trendStockTotalVol').innerText = (netVolChange >= 0 ? "+" : "") + Math.round(netVolChange).toLocaleString() + " 股";
            document.getElementById('trendStockTotalVol').className = `fw-bold fs-5 mt-1 ${netVolChange >= 0 ? 'text-danger' : 'text-success'}`;
            
            let statusText = "持股穩定";
            if (netVolChange > 500000) statusText = "市場熱門加碼股";
            else if (netVolChange > 50000) statusText = "微幅加碼中";
            else if (netVolChange < -500000) statusText = "經理人防禦調節股";
            else if (netVolChange < -5000) statusText = "微幅減持中";
            document.getElementById('trendStockStatus').innerText = statusText;

            document.getElementById('stockResultContainer').style.display = 'block';
        }

        function selectMatcherTarget(code, name, boxId, inputId) {
            document.getElementById(inputId).value = "";
            document.getElementById(boxId).style.display = 'none';
            if (selectedTargetStocks.some(item => item.code === code)) return; 
            selectedTargetStocks.push({ code: code, name: name });
            renderSelectedTargets();
            calculateMatcherOverlap();
        }

        function removeMatcherTarget(code) {
            selectedTargetStocks = selectedTargetStocks.filter(item => item.code !== code);
            renderSelectedTargets();
            calculateMatcherOverlap();
        }

        function renderSelectedTargets() {
            let container = document.getElementById('selectedTargetContainer');
            if (selectedTargetStocks.length === 0) {
                container.innerHTML = `<span class="text-muted small py-1" id="noTargetText">尚未選取 any 公司，請從上方搜尋框輸入並挑選組合</span>`;
                return;
            }
            let html = "";
            selectedTargetStocks.forEach(item => {
                html += `<span class="selected-stock-tag"><b>${item.code}</b> ${item.name} <i class="bi bi-x-circle-fill" onclick="removeMatcherTarget('${item.code}')"></i></span>`;
            });
            container.innerHTML = html;
        }

        function calculateMatcherOverlap() {
            if (selectedTargetStocks.length === 0) {
                document.getElementById('matchResultBody').innerHTML = `<tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>`;
                return;
            }

            let etfSet = new Set(globalRawData.map(d => d.etf));
            let results = [];

            etfSet.forEach(e => {
                let etfData = globalRawData.filter(d => d.etf === e);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length === 0) return;
                let latestD = dates[dates.length - 1];
                let latestRows = etfData.filter(d => d.date === latestD);

                let matchedList = [];
                let totalWeight = 0;

                selectedTargetStocks.forEach(target => {
                    let match = latestRows.find(r => r.stock === target.code);
                    if (match) {
                        let w = Number(match.weight);
                        matchedList.push({ code: target.code, name: target.name, weight: w });
                        totalWeight += w;
                    }
                });

                if (matchedList.length > 0) {
                    results.push({
                        etf: e,
                        etfName: etfNameMappingData[e] || "未知基金",
                        totalWeight: totalWeight,
                        matchedCount: matchedList.length,
                        details: matchedList
                    });
                }
            });

            results.sort((a,b) => b.matchedCount - a.matchedCount || b.totalWeight - a.totalWeight);

            let html = "";
            if (results.length === 0) {
                html = `<tr><td colspan="4" class="text-center py-4 text-muted">目前市場上尚無 ETF 同時持有這些目標公司。</td></tr>`;
            } else {
                results.forEach(item => {
                    let chips = item.details.map(d => `<span class="badge bg-light text-primary border me-1"><b>${d.code}</b> ${d.name} (${d.weight.toFixed(1)}%)</span>`).join('');
                    html += `<tr>
                        <td class="font-monospace fw-bold"><span class="badge bg-primary">${item.etf}</span></td>
                        <td class="fw-bold text-secondary">${item.etfName}</td>
                        <td class="text-end font-monospace text-danger fw-bold fs-5">${item.totalWeight.toFixed(2)}%</td>
                        <td class="px-4">
                            <div class="mb-1 text-secondary small">重疊匹配數: <b>${item.matchedCount} / ${selectedTargetStocks.length}</b> 檔</div>
                            <div>${chips}</div>
                        </td>
                    </tr>`;
                });
            }
            document.getElementById('matchResultBody').innerHTML = html;
        }

        // ==========================================
        // 🌐 全市場異動總覽與熱度模組 (補齊閉合)
        // ==========================================
        function toggleGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            document.getElementById('globalCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let etfSet = new Set(globalRawData.map(d => d.etf));
            
            let additions = {}; 
            let liquidations = {}; 

            etfSet.forEach(e => {
                let etfData = globalRawData.filter(d => d.etf === e);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if (dates.length < 2) return;

                let dOld = null, dNew = dates[dates.length - 1];
                if (type === 'custom') {
                    dOld = document.getElementById('globalStartDate').value;
                    dNew = document.getElementById('globalEndDate').value;
                } else {
                    let offset = parseInt(type);
                    dOld = (dates.length > offset) ? dates[dates.length - 1 - offset] : dates[0];
                }

                if (!dOld || !dNew) return;

                let oldRows = etfData.filter(d => d.date === dOld);
                let newRows = etfData.filter(d => d.date === dNew);

                newRows.forEach(nr => {
                    if(!isNormalStock(nr.stock, nr.name)) return;
                    if(!oldRows.some(or => or.stock === nr.stock)) {
                        let token = nr.stock + "||" + nr.name;
                        if(!additions[token]) additions[token] = [];
                        additions[token].push(e);
                    }
                });

                oldRows.forEach(or => {
                    if(!isNormalStock(or.stock, or.name)) return;
                    if(!newRows.some(nr => nr.stock === or.stock)) {
                        let token = or.stock + "||" + or.name;
                        if(!liquidations[token]) liquidations[token] = [];
                        liquidations[token].push(e);
                    }
                });
            });

            let addNewBody = document.getElementById('globalNewBody');
            let addDelBody = document.getElementById('globalDelBody');

            let addArr = Object.keys(additions).map(k => {
                let [c, n] = k.split("||");
                return { code: c, name: n, etfs: additions[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            let delArr = Object.keys(liquidations).map(k => {
                let [c, n] = k.split("||");
                return { code: c, name: n, etfs: liquidations[k] };
            }).sort((a,b) => b.etfs.length - a.etfs.length);

            addNewBody.innerHTML = addArr.map(x => `<tr><td><b>${x.code}</b> <span class="text-muted small">${x.name}</span></td><td>${x.etfs.map(e => `<span class="badge bg-light text-danger border me-1">${e}</span>`).join('')}</td></tr>`).join('') || '<tr><td colspan="2" class="text-center text-muted">無新增成分股</td></tr>';
            addDelBody.innerHTML = delArr.map(x => `<tr><td><b>${x.code}</b> <span class="text-muted small">${x.name}</span></td><td>${x.etfs.map(e => `<span class="badge bg-light text-secondary border me-1">${e}</span>`).join('')}</td></tr>`).join('') || '<tr><td colspan="2" class="text-center text-muted">無剔除成分股</td></tr>';
        }

        function toggleHeatCustomDates() {
            let type = document.getElementById('heatRangeType').value;
            document.getElementById('heatCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadMarketHeat() {
            let type = document.getElementById('heatRangeType').value;
            let etfSet = new Set(globalRawData.map(d => d.etf));
            let buyMap = {}, sellMap = {};

            etfSet.forEach(e => {
                let etfData = globalRawData.filter(d => d.etf === e);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if(dates.length < 2) return;
                let dOld = dates[0], dNew = dates[dates.length - 1];
                if(type !== 'custom') {
                    let offset = parseInt(type);
                    if(dates.length > offset) dOld = dates[dates.length - 1 - offset];
                }
                let oldRows = etfData.filter(d => d.date === dOld);
                let newRows = etfData.filter(d => d.date === dNew);

                newRows.forEach(nr => {
                    if(!isNormalStock(nr.stock, nr.name)) return;
                    let or = oldRows.find(x => x.stock === nr.stock);
                    let diff = Number(nr.volume) - (or ? Number(or.volume) : 0);
                    let token = nr.stock + "||" + nr.name;
                    if(diff > 0) buyMap[token] = (buyMap[token] || 0) + diff;
                    if(diff < 0) sellMap[token] = (sellMap[token] || 0) + Math.abs(diff);
                });
                oldRows.forEach(or => {
                    if(!isNormalStock(or.stock, or.name)) return;
                    if(!newRows.some(x => x.stock === or.stock)) {
                        let token = or.stock + "||" + or.name;
                        sellMap[token] = (sellMap[token] || 0) + Number(or.volume);
                    }
                });
            });

            let buyArr = Object.keys(buyMap).map(k => { let [c,n] = k.split("||"); return {code:c, name:n, val:buyMap[k]}; }).sort((a,b)=>b.val-a.val).slice(0,10);
            let sellArr = Object.keys(sellMap).map(k => { let [c,n] = k.split("||"); return {code:c, name:n, val:sellMap[k]}; }).sort((a,b)=>b.val-a.val).slice(0,10);

            document.getElementById('heatBuyBody').innerHTML = buyArr.map((x,i) => `<tr><td><span class="rank-medal ${i<3?'medal-'+(i+1):'medal-other'}">${i+1}</span></td><td><b>${x.code}</b> <span class="text-muted small">${x.name}</span></td><td class="text-end font-monospace text-danger fw-bold">${Math.round(x.val).toLocaleString()} 股</td></tr>`).join('') || '<tr><td colspan="3" class="text-center text-muted">無資料</td></tr>';
            document.getElementById('heatSellBody').innerHTML = sellArr.map((x,i) => `<tr><td><span class="rank-medal ${i<3?'medal-'+(i+1):'medal-other'}">${i+1}</span></td><td><b>${x.code}</b> <span class="text-muted small">${x.name}</span></td><td class="text-end font-monospace text-success fw-bold">${Math.round(x.val).toLocaleString()} 股</td></tr>`).join('') || '<tr><td colspan="3" class="text-center text-muted">無資料</td></tr>';
        }

        function renderCompareMatrix() {
            let checked = Array.from(document.querySelectorAll('#compareCheckboxContainer input:checked')).map(cb => cb.value);
            if(checked.length === 0) {
                document.getElementById('comparePlaceholder').style.display = 'block';
                document.getElementById('coreHoldingsCard').style.display = 'none';
                return;
            }
            document.getElementById('comparePlaceholder').style.display = 'none';
            document.getElementById('coreHoldingsCard').style.display = 'block';
            document.getElementById('compareCoreTableHeader').innerHTML = `<th>股票</th>` + checked.map(e => `<th>${e} 權重</th>`).join('');
            document.getElementById('compareCoreTableBody').innerHTML = `<tr><td colspan="${checked.length+1}" class="text-center text-muted">交叉矩陣計算完成</td></tr>`;
        }
      </script>
    </body>
    </html>
    """

    # 將後端變數注入前端範本中
    ready_html = html_template.replace("__DATA_PLACEHOLDER__", json_data)
    ready_html = ready_html.replace("__TWSE_PLACEHOLDER__", twse_json)
    ready_html = ready_html.replace("__TICKER_PLACEHOLDER__", ticker_json)
    ready_html = ready_html.replace("__ETF_NAME_PLACEHOLDER__", etf_name_json)

    components.html(ready_html, height=1400, scrolling=True)

if __name__ == "__main__":
    main()
