import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os
import requests

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
# 3. 外部即時行情 API 整合模組
# ==========================================
def fetch_wantgoo_etf_data():
    api_url = "https://www.wantgoo.com/api/etf/nav-and-discount-premium"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.wantgoo.com/stock/etf/net-value"
    }
    try:
        res = requests.get(api_url, headers=headers, timeout=10)
        if res.status_code == 200:
            market_data = {}
            for item in res.json():
                stock_no = str(item.get("stockNo", "")).strip()
                if stock_no:
                    market_data[stock_no] = {
                        "price": item.get("price", "-"),
                        "change": item.get("changeValue", "-"), 
                        "premium": item.get("discountPremiumRate", "-"), 
                        "volume": item.get("volume", "-") 
                    }
            return market_data
    except Exception as e:
        print(f"玩股網爬蟲異常: {e}")
    return {}

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
    df = df.dropna(subset=['date'])
    
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','', regex=False).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    if ticker_map:
        mapped_series = df['stock'].map(ticker_map)
        backup_col = orig_name_col if (orig_name_col and orig_name_col in df.columns) else 'name'
        df['name'] = mapped_series.fillna(df[backup_col].astype(str).str.strip())
    else:
        df['name'] = df['name'].astype(str).str.strip()
        
    return df, None

# ==========================================
# 4. 主核心資料庫結構轉換與打包
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
    
    wantgoo_data = fetch_wantgoo_etf_data()
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False), wantgoo_data, twse_live_market, ticker_map, etf_name_map

# ==========================================
# 5. 主渲染邏輯
# ==========================================
def main():
    json_data, wantgoo_market_data, twse_live_market, ticker_map, etf_name_map = fetch_backend_data_to_json()
    wantgoo_json = json.dumps(wantgoo_market_data, ensure_ascii=False)
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
        .table {
          margin-bottom: 0;
        }
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
        
        .custom-tab-content {
          display: none;
        }
        .custom-tab-content.active {
          display: block;
        }

        .loading-overlay {
          position: fixed;
          top: 0; left: 0; width: 100%; height: 100%;
          background: rgba(255,255,255,0.7);
          display: flex; justify-content: center; align-items: center;
          z-index: 9999; display: flex;
        }
        .etf-list-group {
          max-height: 700px;
          overflow-y: auto;
        }
        .etf-item-btn {
          text-align: left;
          border-radius: 8px !important;
          margin-bottom: 4px;
          border: 1px solid #e2e8f0;
          transition: all 0.2s;
        }
        .etf-item-btn:hover {
          background-color: #f1f5f9;
        }
        .etf-item-btn.active {
          background-color: #1e3c72 !important;
          border-color: #1e3c72 !important;
          color: #fff !important;
          font-weight: bold;
        }
        .rank-badge {
          width: 24px;
          height: 24px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 50%;
          font-weight: bold;
          font-size: 0.85rem;
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
        .update-date-text {
          font-size: 0.9rem;
          font-weight: 400;
          color: #6c757d;
          margin-left: 12px;
        }
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
        .suggestion-item {
          padding: 10px 15px;
          cursor: pointer;
        }
        .suggestion-item:hover {
          background-color: #f1f5f9;
        }
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
        .selected-stock-tag i {
          cursor: pointer;
          color: #ef4444;
        }

        /* 仿照圖片樣式的首頁表格自訂優化樣式 */
        .home-table th {
          background-color: #f8fafc !important;
          color: #334155 !important;
          font-weight: 700;
          border-bottom: 2px solid #e2e8f0;
          padding: 14px 16px;
          font-size: 1rem;
        }
        .home-table td {
          padding: 14px 16px;
          border-bottom: 1px solid #f1f5f9;
          background-color: #fff !important;
          font-size: 0.95rem;
        }
        .home-table tbody tr:hover td {
          background-color: #f8fafc !important;
        }
        .text-up {
          color: #dc2626 !important; /* 台灣股市 漲為紅色 */
          font-weight: 600;
        }
        .text-down {
          color: #16a34a !important; /* 台灣股市 跌為綠色 */
          font-weight: 600;
        }
        .text-flat {
          color: #64748b !important; /* 平盤為灰色 */
        }
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
          
          <!-- 新增的首頁分頁內容 -->
          <div class="custom-tab-content active" id="content-home">
            <div class="card p-0 overflow-hidden shadow-sm">
              <div class="table-responsive">
                <table class="table home-table align-middle">
                  <thead>
                    <tr>
                      <th>ETF代號</th>
                      <th>ETF名稱</th>
                      <th>現價</th>
                      <th>漲跌幅</th>
                    </tr>
                  </thead>
                  <tbody id="homeTableBody">
                    <!-- 由 JavaScript 動態填入 -->
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          
          <div class="custom-tab-content" id="content-a">
            <div class="row g-4">
              
              <div class="col-lg-3">
                <div class="card p-3 sticky-top" style="top: 80px; z-index: 10;">
                  <label class="form-label fw-bold text-secondary mb-3"><i class="bi bi-list-ul me-1"></i>請選擇 ETF 代號</label>
                  <input type="text" id="etfSearchInput" class="form-control mb-3" placeholder="輸入關鍵字篩選..." onkeyup="filterEtfList()">
                  <div id="etfButtonList" class="list-group etf-list-group">
                    <div class="text-muted text-center py-3">載入中...</div>
                  </div>
                </div>
              </div>

              <div class="col-lg-9">
                <div id="etfTitleContainer" class="etf-title-display" style="display: none;">
                  <i class="bi bi-bookmark-star-fill me-2 text-warning"></i>
                  <span id="txtEtfCode"></span>&nbsp;&nbsp;<span id="txtEtfName" class="text-dark"></span>
                  <span id="txtUpdateDate" class="update-date-text"></span>
                </div>

                <div id="metaContainer" class="row g-2 mb-4" style="display: none;">
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
                    <div class="meta-card" style="border-left-color: #319795;">
                      <div class="meta-label">折溢價</div>
                      <div class="meta-value" id="metaPremium">-%</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #805ad5;">
                      <div class="meta-label">規模</div>
                      <div class="meta-value" id="metaSize">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md">
                    <div class="meta-card" style="border-left-color: #dd6b20;">
                      <div class="meta-label">成交量</div>
                      <div class="meta-value" id="metaVolume">-</div>
                    </div>
                  </div>
                </div>

                <div class="row g-3">
                  <div class="col-lg-7">
                    <div class="card">
                      <div class="card-header text-primary"><i class="bi bi-list-stars me-2"></i>最新成分股持股明細</div>
                      <div class="table-responsive" style="max-height: 450px;">
                        <table class="table table-hover align-middle">
                          <thead>
                            <tr><th>股票代號</th><th>股票名稱</th><th class="text-end">持股權重</th><th class="text-end">最新持股(股)</th></tr>
                          </thead>
                          <tbody id="stockTableBody"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  
                  <div class="col-lg-5">
                    <div class="card">
                      <div class="card-header text-secondary"><i class="bi bi-cash-coin me-2"></i>非股票資產項目</div>
                      <div class="table-responsive" style="max-height: 450px;">
                        <table class="table table-hover align-middle">
                          <thead>
                            <tr><th>資產代號</th><th>資產項目</th><th class="text-end">權重</th><th class="text-end">資產價值(股)</th></tr>
                          </thead>
                          <tbody id="assetTableBody"></tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="card p-3 mb-4 bg-light border">
                  <div class="row align-items-center g-3">
                    <div class="col-md-4">
                      <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>籌碼比較天數 / 範圍</label>
                      <select id="rangeType" class="form-select" onchange="toggleCustomDates()">
                        <option value="1">與前 1 筆紀錄比較 (日變動)</option>
                        <option value="5">與前 5 筆紀錄比較 (週變動)</option>
                        <option value="10">與前 10 筆紀錄比較</option>
                        <option value="custom">自訂特定兩日期區間</option>
                      </select>
                    </div>
                    <div class="col-md-5" id="customDateGroup" style="display: none;">
                      <div class="row">
                        <div class="col-6">
                          <label class="form-label fw-bold text-secondary">舊日期 (YYYY-MM-DD)</label>
                          <input type="text" id="startDate" class="form-control" placeholder="YYYY-MM-DD">
                        </div>
                        <div class="col-6">
                          <label class="form-label fw-bold text-secondary">新日期 (YYYY-MM-DD)</label>
                          <input type="text" id="endDate" class="form-control" placeholder="YYYY-MM-DD">
                        </div>
                      </div>
                    </div>
                    <div class="col-md-3 pt-4">
                      <button class="btn btn-outline-dark w-100" onclick="refreshCurrentEtf()"><i class="bi bi-calculator me-1"></i>重新計算籌碼</button>
                    </div>
                  </div>
                  <div class="mt-2 text-muted small px-1" id="dateDisplayInfo"></div>
                </div>

                <div class="card">
                  <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                    <span><i class="bi bi-lightning-charge-fill me-2 text-warning"></i>動態籌碼異動計算與連續狀態追蹤</span>
                    <span class="badge bg-secondary" id="compareDateBadge"></span>
                  </div>
                  <div class="table-responsive">
                    <table class="table table-striped table-hover align-middle">
                      <thead>
                        <tr>
                          <th>成分股</th>
                          <th>異動性質</th>
                          <th class="text-end">區間增減股數</th>
                          <th class="px-4">核心歷史連續買賣狀態</th>
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
            <div class="card p-3 mb-4" style="position: relative;">
              <div class="row align-items-center g-3">
                <div class="col-md-4" style="position: relative;">
                  <label class="form-label fw-bold text-secondary">請輸入個股代號 或 名稱 (支援模糊關鍵字查詢)</label>
                  <input type="text" id="stockInput" class="form-control form-control-lg" placeholder="例如: 聯 或 2330" onkeyup="searchStockSuggestions(this.value, 'stockSuggestions', 'stockInput')">
                  <div id="stockSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-md-3">
                  <label class="form-label fw-bold text-secondary">比較天數 / 範圍</label>
                  <select id="stockRangeType" class="form-select form-select-lg">
                    <option value="1">日變動 (與前 1 筆比較)</option>
                    <option value="5">週變動 (與前 5 筆比較)</option>
                    <option value="10">與前 10 筆比較</option>
                  </select>
                </div>
                <div class="col-md-3 pt-4">
                  <button class="btn btn-success btn-lg w-100" onclick="searchStockDistribution()"><i class="bi bi-search me-1"></i>查詢籌碼明細</button>
                </div>
              </div>
            </div>

            <div id="stockTrendCard" class="card mb-4" style="display: none; border-left: 5px solid #f97316;">
              <div class="card-body py-3 px-4">
                <div class="row align-items-center">
                  <div class="col-md-3">
                    <div class="text-muted small mb-1"><i class="bi bi-hash"></i> 查詢標的</div>
                    <h3 class="fw-bold mb-0" id="trendStockHeader">-</h3>
                  </div>
                  <div class="col-md-3">
                    <div class="text-muted small mb-1"><i class="bi bi-speedometer2"></i> 跨市場加減碼趨勢</div>
                    <h4 class="fw-bold mb-0" id="trendStockStatus">-</h4>
                  </div>
                  <div class="col-md-3">
                    <div class="text-muted small mb-1"><i class="bi bi-diagram-3"></i> 涉及 ETF 檔數</div>
                    <h4 class="fw-bold mb-0 text-dark" id="trendStockCount">-</h4>
                  </div>
                  <div class="col-md-3 text-md-end">
                    <div class="text-muted small mb-1">區間跨市場總變動股數</div>
                    <h3 class="fw-bold font-monospace mb-0" id="trendStockTotalVol">-</h3>
                  </div>
                </div>
              </div>
            </div>

            <div class="row g-4">
              <div class="col-lg-7">
                <div id="stockResultCard" class="card" style="display: none;">
                  <div class="card-header bg-success text-white fw-bold d-flex justify-content-between align-items-center">
                    <span id="stockResultTitle"><i class="bi bi-arrow-left-right me-2"></i>各 ETF 區間增減股數明細</span>
                    <span class="badge bg-light text-success font-monospace" id="stockRangeBadge"></span>
                  </div>
                  <div class="table-responsive">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr><th>變動 ETF</th><th class="text-end">增減股數</th></tr>
                      </thead>
                      <tbody id="stockDistBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="col-lg-5">
                <div id="stockWeightCard" class="card" style="display: none;">
                  <div class="card-header bg-dark text-white fw-bold"><i class="bi bi-pie-chart me-2"></i>最新持有該股之 ETF 權重占比</div>
                  <div class="table-responsive">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr><th>ETF</th><th class="text-end">持股權重占比</th><th class="text-end">持有股數</th></tr>
                      </thead>
                      <tbody id="stockDistBody2"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="custom-tab-content" id="content-f">
            <div class="card p-4 mb-4">
              <h5 class="fw-bold text-primary mb-3"><i class="bi bi-ui-checks-grid me-2"></i>依多檔成分股公司 ➔ 逆向精準篩選適合的 ETF</h5>
              <p class="text-muted small">請輸入您想要投資的核心公司（可連續新增多檔全球股票代號與名稱），系統將即時為您篩選出「同時具備」這些公司的全球/台股精選 ETF 陣容。</p>
              
              <div class="row align-items-center g-3" style="position: relative;">
                <div class="col-md-5" style="position: relative;">
                  <label class="form-label fw-bold text-secondary">請輸入全球/台股個股名稱或代號（支援模糊搜尋）</label>
                  <input type="text" id="matcherInput" class="form-control" placeholder="例如: 台積電、AAPL、NVDA、鴻海..." onkeyup="searchStockSuggestions(this.value, 'matcherSuggestions', 'matcherInput', true)">
                  <div id="matcherSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-12 mt-3">
                  <div class="fw-bold text-secondary mb-2">目前已選取的全球投資目標公司：</div>
                  <div id="selectedTargetContainer" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded min-height" style="min-height: 58px;">
                    <span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-header bg-primary text-white fw-bold d-flex justify-content-between align-items-center">
                <span><i class="bi bi-shield-check me-2"></i>完美符合複合條件之 ETF 篩選結果清單</span>
                <span class="badge bg-light text-primary fw-bold" id="matchedCountBadge">共 0 檔符合</span>
              </div>
              <div class="table-responsive">
                <table class="table table-hover align-middle table-striped">
                  <thead>
                    <tr id="matcherTableHeader">
                      <th>ETF 代號</th>
                      <th>ETF 名稱</th>
                      <th>符合之核心成分股、權重與持股數明細</th>
                    </tr>
                  </thead>
                  <tbody id="matcherTableBody">
                    <tr><td colspan="3" class="text-center text-muted py-4">請先在上方新增目標公司，系統將自動進行大數據 analysis。</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="custom-tab-content" id="content-c">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-secondary">全市場異動比較範圍</label>
                  <select id="globalRangeType" class="form-select" onchange="toggleGlobalChanges()">
                    <option value="1">日變動</option>
                    <option value="5">週變動</option>
                    <option value="10">月變動 (10筆)</option>
                    <option value="custom">自訂區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="globalCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6"><input type="text" id="globalStartDate" class="form-control" placeholder="舊日期 YYYY-MM-DD"></div>
                    <div class="col-6"><input type="text" id="globalEndDate" class="form-control" placeholder="新日期 YYYY-MM-DD"></div>
                  </div>
                </div>
                <div class="col-md-3 pt-2"><button class="btn btn-dark w-100 btn-lg" onclick="loadGlobalChanges()"><i class="bi bi-globe2 me-1"></i>生成異動總覽</button></div>
              </div>
            </div>
            <div class="card">
              <div class="card-header bg-danger text-white fw-bold" id="globalTitle">全市場 ETF 成分股異動排行追蹤</div>
              <div class="table-responsive">
                <table class="table table-hover table-striped align-middle">
                  <thead><tr><th>ETF</th><th>成分股</th><th>異動性質</th><th class="text-end">增減股數</th><th>連續買賣狀態</th></tr></thead>
                  <tbody id="globalTableBody"></tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="custom-tab-content" id="content-d">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-secondary">熱度統計比較範圍</label>
                  <select id="heatRangeType" class="form-select" onchange="toggleHeatCustomDates()">
                    <option value="1">日變動</option>
                    <option value="5">週變動</option>
                    <option value="10">月變動 (10筆)</option>
                    <option value="custom">自訂區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="heatCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6"><input type="text" id="heatStartDate" class="form-control" placeholder="舊日期 YYYY-MM-DD"></div>
                    <div class="col-6"><input type="text" id="heatEndDate" class="form-control" placeholder="新日期 YYYY-MM-DD"></div>
                  </div>
                </div>
                <div class="col-md-3 pt-2"><button class="btn btn-danger w-100 btn-lg" onclick="loadMarketHeat()"><i class="bi bi-fire me-1"></i>生成市場熱度分析</button></div>
              </div>
            </div>
            <div class="row g-4">
              <div class="col-lg-6">
                <div class="card">
                  <div class="card-header bg-danger text-white fw-bold" id="heatBuyTitle"><i class="bi bi-graph-up me-2"></i>跨市場大加總：淨買超前 10 大個股</div>
                  <div class="table-responsive">
                    <table class="table table-hover table-striped align-middle">
                      <thead><tr><th>排名</th><th>股票代號</th><th>股票名稱</th><th class="text-end">跨市場淨加碼(股)</th></tr></thead>
                      <tbody id="heatBuyTableBody"><tr><td colspan="4" class="text-center text-muted py-4">請點擊「生成市場熱度分析」載入數據</td></tr></tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="col-lg-6">
                <div class="card">
                  <div class="card-header bg-success text-white fw-bold" id="heatSellTitle"><i class="bi bi-graph-down me-2"></i>跨市場大加總：淨賣超前 10 大個股</div>
                  <div class="table-responsive">
                    <table class="table table-hover table-striped align-middle">
                      <thead><tr><th>排名</th><th>股票代號</th><th>股票名稱</th><th class="text-end">跨市場淨減持(股)</th></tr></thead>
                      <tbody id="heatSellTableBody"><tr><td colspan="4" class="text-center text-muted py-4">請點擊「生成市場熱度分析」載入數據</td></tr></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="custom-tab-content" id="content-e">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-12">
                  <label class="form-label fw-bold text-secondary mb-2"><i class="bi bi-check2-square me-1"></i>請選擇要比較的 ETF（可多選）</label>
                  <div id="compareEtfCheckboxes" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded" style="max-height: 150px; overflow-y: auto;"></div>
                </div>
                <div class="col-md-3 pt-2"><button class="btn btn-primary w-100 btn-lg" onclick="generateComparison()"><i class="bi bi-layout-three-columns me-1"></i>開始交叉比較</button></div>
              </div>
            </div>
            <div class="card">
              <div class="card-header bg-primary text-white fw-bold" id="compareTitle"><i class="bi bi-layout-three-columns me-2"></i>ETF 持股權重交叉比較矩陣</div>
              <div class="table-responsive">
                <table class="table table-hover table-striped align-middle">
                  <thead><tr id="compareTableHeader"><th>股票代號</th><th>股票名稱</th></tr></thead>
                  <tbody id="compareTableBody"><tr><td colspan="2" class="text-center text-muted py-4">請先勾選上方 ETF 並點擊「開始交叉比較」按鈕</td></tr></tbody>
                </table>
              </div>
            </div>
          </div>

        </div>
      </div>

      <script>
        let globalRawData = __DATA_PLACEHOLDER__;
        let wantgooMarketData = __WANTGOO_PLACEHOLDER__; 
        let twseLiveMarketData = __TWSE_PLACEHOLDER__; 
        let tickerMappingData = __TICKER_PLACEHOLDER__; 
        let etfNameMappingData = __ETF_NAME_PLACEHOLDER__; 
        let activeEtf = "";
        let selectedTargetStocks = []; 

        function switchTab(contentId, tabId) {
            document.querySelectorAll('.custom-tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-tabs .nav-link').forEach(el => el.classList.remove('active'));
            document.getElementById(contentId).classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        document.addEventListener("DOMContentLoaded", function() {
            document.getElementById('loading').style.display = 'none';
            if (!globalRawData || globalRawData.length === 0) {
                document.getElementById('etfButtonList').innerHTML = '<div class="text-center text-danger py-3">後端無有效資料，請檢查 Google 試算表。</div>';
                return;
            }
            initDashboard();
            renderHomePage(); // 執行首頁資料渲染
            
            document.addEventListener('click', function(e) {
                if(!e.target.closest('.position-relative') && !e.target.closest('#stockInput') && !e.target.closest('#matcherInput')) {
                    document.getElementById('stockSuggestions').style.display = 'none';
                    document.getElementById('matcherSuggestions').style.display = 'none';
                }
            });
        });

        function getEtfDisplayLabel(code) {
            let mappedName = etfNameMappingData[code] || "未知名稱";
            return `${code} ${mappedName}`;
        }

        /* 渲染全新首頁表格數據的邏輯 */
        function renderHomePage() {
            let etfSet = new Set();
            globalRawData.forEach(item => { if(item.etf) etfSet.add(item.etf); });
            let etfList = Array.from(etfSet).sort();

            let homeHtml = "";
            etfList.forEach(etf => {
                let mappedName = etfNameMappingData[etf] || "未知名稱";
                
                // 優先從證交所或玩股網 API 中計算現價與漲跌幅
                let price = "-";
                let changePct = "-";
                let colorClass = "text-flat"; // 預設平盤顏色
                
                let twseData = twseLiveMarketData[etf] || null;
                if (twseData) {
                    let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
                    let yesterdayPrice = parseFloat(twseData.y) || 0;
                    if (priceVal > 0) {
                        price = priceVal.toFixed(2);
                        if (yesterdayPrice > 0) {
                            let diff = priceVal - yesterdayPrice;
                            let rawPct = (diff / yesterdayPrice) * 100;
                            changePct = (rawPct > 0 ? "+" : "") + rawPct.toFixed(2) + "%";
                            
                            if (rawPct > 0) colorClass = "text-up";
                            else if (rawPct < 0) colorClass = "text-down";
                        }
                    }
                } else {
                    let liveData = wantgooMarketData[etf] || null;
                    if (liveData && liveData.price !== "-") {
                        price = parseFloat(liveData.price).toFixed(2);
                        if (liveData.change !== "-") {
                            let rawPct = parseFloat(liveData.change);
                            changePct = (rawPct > 0 ? "+" : "") + rawPct.toFixed(2) + "%";
                            
                            if (rawPct > 0) colorClass = "text-up";
                            else if (rawPct < 0) colorClass = "text-down";
                        }
                    }
                }

                homeHtml += `
                  <tr>
                    <td><span class="badge bg-light text-dark border font-monospace px-2 py-1">${etf}</span></td>
                    <td class="fw-bold text-secondary">${mappedName}</td>
                    <td class="font-monospace fw-bold">${price}</td>
                    <td class="font-monospace ${colorClass}">${changePct}</td>
                  </tr>
                `;
            });
            document.getElementById('homeTableBody').innerHTML = homeHtml;
        }

        function initDashboard() {
            let etfSet = new Set();
            globalRawData.forEach(item => { if(item.etf) etfSet.add(item.etf); });
            let etfList = Array.from(etfSet).sort();

            let listHtml = "";
            etfList.forEach(etf => {
                listHtml += `<button class="list-group-item list-group-item-action etf-item-btn" id="btn-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-file-earmark-text me-2"></i>${getEtfDisplayLabel(etf)}</button>`;
            });
            document.getElementById('etfButtonList').innerHTML = listHtml;

            let checkHtml = "";
            etfList.forEach(etf => {
                checkHtml += `
                  <div class="form-check form-check-inline me-3 py-1">
                    <input class="form-check-input etf-compare-cb" type="checkbox" value="${etf}" id="cb-${etf}">
                    <label class="form-check-label fw-bold" for="cb-${etf}">${getEtfDisplayLabel(etf)}</label>
                  </div>`;
            });
            document.getElementById('compareEtfCheckboxes').innerHTML = checkHtml;

            if(etfList.length > 0) {
                selectEtf(etfList[0]);
            }
        }

        function filterEtfList() {
            let q = document.getElementById('etfSearchInput').value.toLowerCase();
            document.querySelectorAll('.etf-item-btn').forEach(btn => {
                let txt = btn.innerText.toLowerCase();
                btn.style.display = txt.includes(q) ? "" : "none";
            });
        }

        function isNormalStock(code, name) {
            let meta = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""];
            let cashEx = [
                "DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨",
                "RDI", "DR_", "RECEIVABLES", "DIVIDENDS", "DISPOSAL", "INVESTMENTS", "權證", "型購", "型售","買權","賣權","TWSE"
            ];
            if (meta.includes(code) || meta.includes(name)) return false;
            
            let upperCode = code.toUpperCase();
            let upperName = name.toUpperCase();
            if (cashEx.some(k => upperCode.includes(k.toUpperCase()) || upperName.includes(k.toUpperCase()))) return false;
            
            return true;
        }

        function searchStockSuggestions(value, boxId, inputId, isMultiple = false) {
            let q = value.trim().toLowerCase();
            let box = document.getElementById(boxId);
            if (!q) { box.style.display = 'none'; return; }

            let matches = [];
            for (let code in tickerMappingData) {
                let name = tickerMappingData[code];
                if (code.toLowerCase().includes(q) || name.toLowerCase().includes(q)) {
                    matches.push({ code: code, name: name });
                }
            }

            if (matches.length === 0) {
                box.innerHTML = '<div class="text-muted p-2 text-center small">無匹配的公司資料</div>';
                box.style.display = 'block';
                return;
            }

            let html = "";
            matches.slice(0, 10).forEach(item => {
                if (isMultiple) {
                    html += `<div class="suggestion-item" onclick="addTargetStockTag('${item.code}', '${item.name}', '${boxId}', '${inputId}')"><b>${item.code}</b> - ${item.name}</div>`;
                } else {
                    html += `<div class="suggestion-item" onclick="selectStockSuggestion('${item.code}', '${item.name}', '${boxId}', '${inputId}')"><b>${item.code}</b> - ${item.name}</div>`;
                }
            });
            box.innerHTML = html;
            box.style.display = 'block';
        }

        function selectStockSuggestion(code, name, boxId, inputId) {
            document.getElementById(inputId).value = code;
            document.getElementById(boxId).style.display = 'none';
        }

        function addTargetStockTag(code, name, boxId, inputId) {
            document.getElementById(inputId).value = "";
            document.getElementById(boxId).style.display = 'none';

            if (selectedTargetStocks.some(x => x.code === code)) return;
            selectedTargetStocks.push({ code: code, name: name });
            renderTargetTags();
            calculateMatchedEtfs();
        }

        function removeTargetStockTag(code) {
            selectedTargetStocks = selectedTargetStocks.filter(x => x.code !== code);
            renderTargetTags();
            calculateMatchedEtfs();
        }

        function renderTargetTags() {
            let container = document.getElementById('selectedTargetContainer');
            if (selectedTargetStocks.length === 0) {
                container.innerHTML = '<span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選</span>';
                return;
            }
            container.innerHTML = selectedTargetStocks.map(x => `
                <span class="selected-stock-tag">
                    <b>${x.code}</b> ${x.name}
                    <i class="bi bi-x-circle-fill" onclick="removeTargetStockTag('${x.code}')"></i>
                </span>
            `).join('');
        }

        function calculateMatchedEtfs() {
            let tbody = document.getElementById('matcherTableBody');
            let badge = document.getElementById('matchedCountBadge');
            
            if (selectedTargetStocks.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">請先在上方新增目標公司，系統將自動進行大數據分析。</td></tr>';
                badge.innerText = "共 0 檔符合";
                return;
            }

            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a, b) => new Date(a) - new Date(b));
            let latestDate = dates[dates.length - 1];

            let etfList = [...new Set(globalRawData.map(d => d.etf))].sort();
            let matchedEtfsResult = [];

            etfList.forEach(eCode => {
                let etfData = globalRawData.filter(d => d.etf === eCode && d.date === latestDate);
                
                let allMatched = true;
                let stockDetailsHtml = '<div class="d-flex flex-column gap-1">';

                for (let target of selectedTargetStocks) {
                    let rowMatch = etfData.find(d => d.stock === target.code);
                    if (!rowMatch || Number(rowMatch.volume) <= 0) {
                        allMatched = false;
                        break;
                    }
                    stockDetailsHtml += `
                        <div class="mb-1">
                            <span class="badge bg-secondary font-monospace me-1">${target.code}</span> 
                            <b class="text-dark me-2">${target.name}</b> ➔ 
                            佔比: <span class="text-danger fw-bold">${Number(rowMatch.weight).toFixed(2)}%</span> , 
                            持有數量: <span class="text-primary font-monospace fw-bold">${Math.round(rowMatch.volume).toLocaleString()}</span> 股
                        </div>`;
                }
                stockDetailsHtml += '</div>';

                if (allMatched) {
                    let etfName = etfNameMappingData[eCode] || "未知名稱";
                    matchedEtfsResult.push({
                        code: eCode,
                        name: etfName,
                        details: stockDetailsHtml
                    });
                }
            });

            badge.innerText = `共 ${matchedEtfsResult.length} 檔符合`;

            if (matchedEtfsResult.length === 0) {
                tbody.innerHTML = '<tr><td colspan="3" class="text-center text-warning py-4 fw-bold"><i class="bi bi-exclamation-triangle me-2"></i>目前全市場沒有任何一檔 ETF 同時包含以上所有選定公司，請減少部分條件。</td></tr>';
                return;
            }

            tbody.innerHTML = matchedEtfsResult.map(x => `
                <tr>
                    <td><span class="badge bg-light text-primary border font-monospace fw-bold" style="font-size:1rem;">${x.code}</span></td>
                    <td class="fw-bold text-secondary" style="font-size:0.95rem;">${x.name}</td>
                    <td>${x.details}</td>
                </tr>
            `).join('');
        }

        function selectEtf(etfName) {
            activeEtf = etfName;
            document.querySelectorAll('.etf-item-btn').forEach(b => b.classList.remove('active'));
            let activeBtn = document.getElementById(`btn-${etfName}`);
            if(activeBtn) activeBtn.classList.add('active');

            let etfData = globalRawData.filter(d => d.etf === etfName);
            let sortedDates = [...new Set(etfData.map(d => d.date))].sort((a, b) => new Date(a) - new Date(b));
            let latestDate = sortedDates[sortedDates.length - 1];
            let latestRows = etfData.filter(d => d.date === latestDate);

            let mappedName = etfNameMappingData[etfName] || "未知名稱";
            document.getElementById('txtEtfCode').innerText = etfName;
            document.getElementById('txtEtfName').innerText = mappedName;
            document.getElementById('etfTitleContainer').style.display = 'block';

            let twseData = twseLiveMarketData[etfName] || null;

            if (twseData) {
                let rawD = twseData.d || "";
                if(rawD.length === 8) {
                    rawD = rawD.substring(0,4) + "-" + rawD.substring(4,6) + "-" + rawD.substring(6,8);
                }
                document.getElementById('txtUpdateDate').innerText = rawD ? `更新日期: ${rawD}` : "";

                let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
                document.getElementById('metaMarketPrice').innerText = priceVal > 0 ? priceVal.toFixed(2) : "-";

                let yesterdayPrice = parseFloat(twseData.y) || 0;
                if(priceVal > 0 && yesterdayPrice > 0) {
                    let changeVal = priceVal - yesterdayPrice;
                    let sign = changeVal > 0 ? "+" : "";
                    let changeColor = changeVal > 0 ? "#dc2626" : (changeVal < 0 ? "#0f766e" : "#1a202c");
                    let metaChangeEl = document.getElementById('metaChange');
                    metaChangeEl.innerText = `${sign}${changeVal.toFixed(2)}`;
                    metaChangeEl.style.color = changeColor;
                } else {
                    document.getElementById('metaChange').innerText = "-";
                }

                let volume張 = parseInt(twseData.v) || 0;
                document.getElementById('metaVolume').innerText = volume張 > 0 ? volume張.toLocaleString() + " 張" : "-";
            } else {
                setMetaFallback();
            }

            let liveData = wantgooMarketData[etfName] || null;
            if (liveData) {
                document.getElementById('metaPremium').innerText = liveData.premium !== null ? liveData.premium + "%" : "-%";
            } else {
                document.getElementById('metaPremium').innerText = (latestRows.find(r => r.stock === "折溢價")?.volume || "-") + "%";
            }

            let sizeVal = latestRows.find(r => r.stock === "規模")?.volume;
            document.getElementById('metaSize').innerText = sizeVal ? (Number(sizeVal)/100000000).toFixed(1) + " 億" : "-";

            document.getElementById('metaContainer').style.display = 'flex';

            let stocks = latestRows.filter(r => isNormalStock(r.stock, r.name)).sort((a,b) => b.weight - a.weight);
            let assets = latestRows.filter(r => !isNormalStock(r.stock, r.name) && !["昨收價","漲跌","市價","規模","折溢價"].includes(r.stock));

            document.getElementById('stockTableBody').innerHTML = stocks.map(r => `
                <tr>
                    <td><span class="badge bg-light text-dark border font-monospace">${r.stock}</span></td>
                    <td class="fw-bold">${r.name}</td>
                    <td class="text-end text-primary fw-bold">${Number(r.weight).toFixed(2)}%</td>
                    <td class="text-end text-secondary font-monospace">${Math.round(r.volume).toLocaleString()} 股</td>
                </tr>
            `).join('');

            document.getElementById('assetTableBody').innerHTML = assets.map(r => `
                <tr>
                    <td><span class="badge bg-light text-muted border font-monospace">${r.stock}</span></td>
                    <td><small class="text-muted">${r.name}</small></td>
                    <td class="text-end">${Number(r.weight) > 0 ? Number(r.weight).toFixed(2)+'%' : '-'}</td>
                    <td class="text-end text-secondary font-monospace">${Number(r.volume) > 0 ? Math.round(r.volume).toLocaleString() : '-'}</td>
                </tr>
            `).join('');

            let rangeType = document.getElementById('rangeType').value;
            if(rangeType !== 'custom') {
                let offset = parseInt(rangeType);
                let idx = Math.max(0, sortedDates.length - 1 - offset);
                document.getElementById('startDate').value = sortedDates[idx];
            }
            document.getElementById('endDate').value = latestDate;

            renderChangeTable(etfData, sortedDates, latestDate);
        }

        function toggleCustomDates() {
            let type = document.getElementById('rangeType').value;
            document.getElementById('customDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function refreshCurrentEtf() { if(activeEtf) selectEtf(activeEtf); }

        function searchStockDistribution() {
            let target = document.getElementById('stockInput').value.trim();
            if(!target) return;

            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            
            let offset = parseInt(document.getElementById('stockRangeType').value);
            let compIdx = Math.max(0, dates.length - 1 - offset);
            let compareDate = dates[compIdx];

            let targetCode = target; let targetName = target;
            if (tickerMappingData[target]) {
                targetName = tickerMappingData[target];
            } else {
                for (let key in tickerMappingData) {
                    if (tickerMappingData[key] === target) { targetCode = key; break; }
                }
            }

            let latestMatches = globalRawData.filter(d => d.date === latestDate && (d.stock === targetCode || d.name === targetName));
            if (latestMatches.length > 0) {
                targetCode = latestMatches[0].stock; targetName = latestMatches[0].name;
            }

            document.getElementById('trendStockHeader').innerText = `【 ${targetCode} 】 ${targetName}`;
            document.getElementById('stockRangeBadge').innerText = `對比區間: ${compareDate} ~ ${latestDate}`;

            let etfList = [...new Set(globalRawData.map(d => d.etf))];
            let changeHtml = ""; let weightHtml = ""; let totalDiff = 0; let involvedEtfCount = 0;

            etfList.forEach(etf => {
                let etfData = globalRawData.filter(d => d.etf === etf);
                let latestRow = etfData.find(d => d.date === latestDate && d.stock === targetCode);
                let latestVol = latestRow ? latestRow.volume : 0;
                let latestWeight = latestRow ? latestRow.weight : 0;
                
                let compareVol = etfData.find(d => d.date === compareDate && d.stock === targetCode)?.volume || 0;
                let diff = latestVol - compareVol;

                if (diff !== 0) {
                    involvedEtfCount++; totalDiff += diff;
                    let colorStyle = diff > 0 ? "color:#dc2626;" : "color:#0f766e;";
                    changeHtml += `<tr><td class="fw-bold text-primary"><i class="bi bi-collection me-2"></i>${getEtfDisplayLabel(etf)}</td><td class="text-end fw-bold font-monospace" style="${colorStyle}">${diff > 0 ? '+' : ''}${Math.round(diff).toLocaleString()} 股</td></tr>`;
                }

                if (latestVol > 0) {
                    weightHtml += `<tr><td class="fw-bold text-dark"><i class="bi bi-pie-chart-fill me-2 text-secondary"></i>${getEtfDisplayLabel(etf)}</td><td class="text-end text-danger fw-bold">${Number(latestWeight).toFixed(2)}%</td><td class="text-end font-monospace text-muted">${Math.round(latestVol).toLocaleString()} 股</td></tr>`;
                }
            });

            document.getElementById('stockTrendCard').style.display = 'block';
            document.getElementById('stockResultCard').style.display = 'block';
            document.getElementById('stockWeightCard').style.display = 'block';
            document.getElementById('trendStockCount').innerText = `${involvedEtfCount} 檔`;
            
            let trendStatusEl = document.getElementById('trendStockStatus');
            trendStatusEl.innerHTML = totalDiff > 0 ? `<span class="badge bg-danger">🔥 淨加碼</span>` : (totalDiff < 0 ? `<span class="badge bg-success">📉 淨減持</span>` : `<span class="badge bg-secondary">持平</span>`);
            
            let totalVolEl = document.getElementById('trendStockTotalVol');
            totalVolVol = `${totalDiff > 0 ? '+' : ''}${Math.round(totalDiff).toLocaleString()} 股`;
            totalVolEl.innerText = totalVolVol;
            totalVolEl.className = `fw-bold font-monospace mb-0 ${totalDiff > 0 ? 'text-danger' : 'text-success'}`;

            document.getElementById('stockDistBody').innerHTML = changeHtml || `<tr><td colspan="2" class="text-center text-muted py-3">無變動</td></tr>`;
            document.getElementById('stockDistBody2').innerHTML = weightHtml || `<tr><td colspan="3" class="text-center text-muted py-3">目前沒有 ETF 持有此股</td></tr>`;
        }

        function toggleGlobalChanges() {
            document.getElementById('globalCustomDateGroup').style.display = (document.getElementById('globalRangeType').value === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            let compDate = (type === 'custom') ? document.getElementById('globalStartDate').value : dates[Math.max(0, dates.length - 1 - parseInt(type))];

            document.getElementById('globalTitle').innerText = `全市場 ETF 成分股異動排行追蹤 [ 區間：${compDate} ➔ ${latestDate} ]`;
            let body = document.getElementById('globalTableBody'); body.innerHTML = "";
            let anyChange = false;

            let etfList = [...new Set(globalRawData.map(d=>d.etf))];
            etfList.forEach(eCode => {
                let etfAll = globalRawData.filter(d => d.etf === eCode);
                let curStocks = etfAll.filter(d => d.date === latestDate && isNormalStock(d.stock, d.name));
                let compRows = etfAll.filter(d => d.date === compDate);

                curStocks.forEach(r => {
                    let oldVol = compRows.find(c => c.stock === r.stock)?.volume || 0;
                    let diff = r.volume - oldVol;
                    if(diff !== 0) {
                        anyChange = true;
                        let bClass = diff > 0 ? "badge-nature-up" : "badge-nature-down";
                        body.innerHTML += `<tr><td><small class="fw-bold">${getEtfDisplayLabel(eCode)}</small></td><td><span class="badge bg-light text-dark font-monospace border me-2">${r.stock}</span><b>${r.name}</b></td><td><span class="${bClass}">${diff > 0 ? '增加' : '減少'}</span></td><td class="text-end fw-bold font-monospace">${Math.round(diff).toLocaleString()}</td><td><small class="text-muted">區間交叉追蹤完成</small></td></tr>`;
                    }
                });
            });
            if(!anyChange) body.innerHTML = '<tr><td colspan="5" class="text-center text-muted py-3">此範圍內全市場未發生增減異動</td></tr>';
        }

        function toggleHeatCustomDates() {
            document.getElementById('heatCustomDateGroup').style.display = (document.getElementById('heatRangeType').value === 'custom') ? 'block' : 'none';
        }

        function loadMarketHeat() {
            let type = document.getElementById('heatRangeType').value;
            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            let compDate = (type === 'custom') ? document.getElementById('heatStartDate').value : dates[Math.max(0, dates.length - 1 - parseInt(type))];

            document.getElementById('heatBuyTitle').innerHTML = `<i class="bi bi-graph-up me-2"></i>跨市場大加總：淨買超前 10 大個股 (${compDate} ~ ${latestDate})`;
            document.getElementById('heatSellTitle').innerHTML = `<i class="bi bi-graph-down me-2"></i>跨市場大加總：淨賣超前 10 大個股 (${compDate} ~ ${latestDate})`;

            let agg = {};
            globalRawData.forEach(r => {
                if(!isNormalStock(r.stock, r.name)) return;
                if(!agg[r.stock]) agg[r.stock] = { code: r.stock, name: r.name, nVol: 0, oVol: 0 };
                if(r.date === latestDate) agg[r.stock].nVol += Number(r.volume);
                if(r.date === compDate) agg[r.stock].oVol += Number(r.volume);
            });

            let list = Object.values(agg).map(x => { x.diff = x.nVol - x.oVol; return x; }).filter(x => x.diff !== 0);
            let topBuy = [...list].sort((a,b)=>b.diff - a.diff).slice(0, 10);
            let topSell = [...list].sort((a,b)=>a.diff - b.diff).slice(0, 10);

            document.getElementById('heatBuyTableBody').innerHTML = topBuy.map((x, i) => `<tr><td><span class="rank-badge bg-danger text-white">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-danger fw-bold font-monospace">+${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('');
            document.getElementById('heatSellTableBody').innerHTML = topSell.map((x, i) => `<tr><td><span class="rank-badge bg-teal text-white" style="background-color:#0f766e;">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-success fw-bold font-monospace">${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('');
        }

        function generateComparison() {
            let checkedCbs = Array.from(document.querySelectorAll('.etf-compare-cb:checked')).map(c => c.value);
            if(checkedCbs.length === 0) { alert("請至少勾選一檔 ETF 進行交叉矩陣比對！"); return; }

            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];

            let header = document.getElementById('compareTableHeader');
            header.innerHTML = `<th>股票代號</th><th>股票名稱</th>` + checkedCbs.map(c => `<th class="text-end" style="min-width:140px;">${getEtfDisplayLabel(c)}<br>權重</th>`).join('');

            let stockMap = {};
            globalRawData.forEach(r => {
                if(r.date === latestDate && checkedCbs.includes(r.etf) && isNormalStock(r.stock, r.name)) { stockMap[r.stock] = r.name; }
            });

            let body = document.getElementById('compareTableBody');
            body.innerHTML = Object.keys(stockMap).map(sCode => {
                let row = `<td><span class="badge bg-light text-dark font-monospace border">${sCode}</span></td><td class="fw-bold">${stockMap[sCode]}</td>`;
                checkedCbs.forEach(eCode => {
                    let match = globalRawData.find(x => x.date === latestDate && x.etf === eCode && x.stock === sCode);
                    let w = match ? Number(match.weight) : 0;
                    row += `<td class="text-end ${w > 0 ? 'text-primary fw-bold' : 'text-muted'}">${w > 0 ? w.toFixed(2)+'%' : '-'}</td>`;
                });
                return `<tr>${row}</tr>`;
            }).join('');
        }
        function setMetaFallback() {
            document.getElementById('metaMarketPrice').innerText = "-";
            document.getElementById('metaChange').innerText = "-";
            document.getElementById('metaVolume').innerText = "-";
            document.getElementById('txtUpdateDate').innerText = "未取得即時盤態";
        }
      </script>
    </body>
    </html>
    """

    final_html = html_template.replace(
        "__DATA_PLACEHOLDER__", json_data
    ).replace(
        "__WANTGOO_PLACEHOLDER__", wantgoo_json
    ).replace(
        "__TWSE_PLACEHOLDER__", twse_json
    ).replace(
        "__TICKER_PLACEHOLDER__", ticker_json
    ).replace(
        "__ETF_NAME_PLACEHOLDER__", etf_name_json
    )
    components.html(final_html, height=1600, scrolling=True)

if __name__ == "__main__":
    main()
