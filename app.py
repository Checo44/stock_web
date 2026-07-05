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
    
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False), {}, twse_live_market, ticker_map, etf_name_map

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

        /* 仿照圖片樣式的首頁表格自訂樣式 */
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
                    </tr>
                  </thead>
                  <tbody id="homeTableBody">
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
                  <input type="text" id="stockInput" class="form-control form-control-lg" placeholder="例如: 聯 或 2330" onkeyup="searchStockSuggestions(this.value, 'stockSuggestions', 'stock[...] 
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
              <p class="text-muted small">請輸入您想要投資的核心公司（可連續新增多檔台灣股票代號與名稱），系統將即時為您篩選出「同時具備」這些公[...] 
              
              <div class="row align-items-center g-3" style="position: relative;">
                <div class="col-md-5" style="position: relative;">
                  <label class="form-label fw-bold text-secondary">請輸入台股個股名稱或代號（支援模糊搜尋）</label>
                  <input type="text" id="matcherInput" class="form-control" placeholder="僅限台股標的" onkeyup="searchStockSuggestions(this.value, 'matcherSuggestions', 'matcherInput', true[...] 
                  <div id="matcherSuggestions" class="suggestion-box" style="display: none;"></div>
                </div>
                <div class="col-12 mt-3">
                  <div class="fw-bold text-secondary mb-2">目前已選取的台灣投資目標公司：</div>
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
                <div class="col-md-3 pt-2"><button class="btn btn-primary w-100 btn-lg" onclick="generateComparison()"><i class="bi bi-layout-three-columns me-1"></i>開始交叉比較</button></[...]
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

        // 黑名單：僅針對「市場熱度排行」頁面 (loadMarketHeat) 不顯示
        const HEAT_BLACKLIST_CODES = ["B718AJ"]; // 可以新增更多明確的代號
        const HEAT_BLACKLIST_KEYWORDS = ["P13中油1A", "P13"]; // 名稱關鍵字過濾

        function isHeatBlacklisted(code, name) {
            if (!code && !name) return false;
            const c = (code || "").toUpperCase().trim();
            const n = (name || "").toUpperCase();
            // 直接比對代號清單
            if (HEAT_BLACKLIST_CODES.some(x => x.toUpperCase() === c)) return true;
            // 名稱關鍵字
            if (HEAT_BLACKLIST_KEYWORDS.some(k => k.toUpperCase() && n.includes(k.toUpperCase()))) return true;
            // 常見 pattern：BxxxAJ
            if (/^B\d+AJ$/i.test(c)) return true;
            return false;
        }

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
                } else {
                    let liveData = wantgooMarketData[etf] || null;
                    if (liveData && liveData.price !== "-") {
                        price = parseFloat(liveData.price).toFixed(2);
                        changePct = liveData.change !== "-" ? parseFloat(liveData.change).toFixed(2) : "-";
                    }
                }

                homeHtml += `
                  <tr>
                    <td>${etf}</td>
                    <td>${mappedName}</td>
                    <td>${price}</td>
                    <td>${changePct}</td>
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
                listHtml += `<button class="list-group-item list-group-item-action etf-item-btn" id="btn-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-file-earmark-text me-2"></i>${getEtf[...]
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
                let stockDetailsHtml = '<div class="d-flex flex-column gap-1'...