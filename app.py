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
WORKSHEET_TICKER = "代號"      # 個股代號對照工作表
WORKSHEET_ETF_NAME = "名稱"    # ETF名稱對照工作表

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

# 👍 修正優化：動態偵測表頭欄位位置，徹底排除 KeyError
@st.cache_data(ttl=300)
def fetch_ticker_mapping():
    if not sh: return {}, "無法連線至 Google 試算表"
    try:
        ws = sh.worksheet(WORKSHEET_TICKER)
        raw_ticker = ws.get_all_values()
        if not raw_ticker or len(raw_ticker) < 1: return {}, None
        
        # 清除表頭前後空白
        headers = [str(h).strip() for h in raw_ticker[0]]
        code_idx = None
        name_idx = None
        
        # 自動搜尋欄位索引
        for idx, h in enumerate(headers):
            if h in ["股票代號", "代號", "成分股代號", "商品代號"]:
                code_idx = idx
            if h in ["公司名稱", "股票名稱", "名稱", "成分股名稱", "商品名稱"]:
                name_idx = idx
                
        # 安全退路機制：若找不到對應表頭，預設第 0 欄為代號，第 1 欄為名稱
        if code_idx is None: code_idx = 0
        if name_idx is None: name_idx = 1 if len(headers) > 1 else 0
        
        ticker_map = {}
        for row in raw_ticker[1:]:
            if len(row) > max(code_idx, name_idx):
                code = str(row[code_idx]).strip()
                name = str(row[name_idx]).strip()
                if code: 
                    ticker_map[code] = name
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

# 👍 修正優化：alias_map 補上「公司名稱」對應，並引入「安全初始化機制」防堵所有 KeyError
def process_and_standardize(raw_data, ticker_map=None):
    df = pd.DataFrame(raw_data[1:], columns=raw_data[0])
    df.columns = [str(c).strip() for c in df.columns]
    
    alias_map = {
        "etf": ["ETF代號", "ETF", "ETF碼"],
        "date": ["日期", "時間", "Date"],
        "stock": ["成分股代號", "股票代號", "代號", "商品代號"],
        "name": ["成分股名稱", "股票名稱", "公司名稱", "名稱", "商品名稱"], # 👈 已成功加入「公司名稱」
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
    
    # 🔥 核心修正點：如果歷史表格中原先就沒有任何名稱欄位，先建立空的 'name'，避免後續 fillna 拋出 KeyError
    if 'name' not in df.columns:
        df['name'] = ""
    
    if ticker_map:
        mapped_series = df['stock'].map(ticker_map)
        df['name'] = mapped_series.fillna(df['name']).astype(str).str.strip()
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

    # 📌 完整保留您原本極其龐大的 HTML / Bootstrap 面板與全套前端 JavaScript 互動邏輯
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
          background: rgba(255,255,255,0.7);
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
                  <tbody id="homeTableBody"></tbody>
                </table>
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
                        <option value="1">昨日比較 (1日變動)</option>
                        <option value="5">週變動比較 (5日變動)</option>
                        <option value="10">月變動比較 (10日變動)</option>
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
                  <div class="card-header text-dark bg-white"><i class="bi bi-arrow-left-right me-2 text-primary"></i>成分股經理人籌碼異動異動明細</div>
                  <div class="table-responsive">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr>
                          <th>股票標的</th>
                          <th>異動屬性</th>
                          <th class="text-end">張數 / 股數增減變動</th>
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
                  <label class="form-label fw-bold text-dark fs-5"><i class="bi bi-search me-1 text-primary"></i>搜尋單一台灣上市櫃股票</label>
                  <input type="text" id="stockSearchInput" class="form-control form-control-lg" placeholder="請輸入股票名稱或代號 (如: 2330 或 台積電)" onkeyup="searchStockSuggestions(this.value, 'searchSuggestions', 'stockSearchInput', false)">
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
                  <div class="card p-4 text-center">
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
              
              <div class="card mt-4">
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
                  <div id="selectedTargetContainer" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded min-height" style="min-height: 58px;">
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
          
          <div class="custom-tab-content" id="content-c">
            <div class="card p-3 mb-4 bg-light border">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>全市場異動比較天數 / 範圍</label>
                  <select id="globalRangeType" class="form-select" onchange="toggleGlobalChanges()">
                    <option value="1">昨日比較 (1日變動)</option>
                    <option value="5">週變動比較 (5日變動)</option>
                    <option value="10">月變動比較 (10日變動)</option>
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
                <div class="col-md-3 pt-2">
                  <button class="btn btn-danger w-100 btn-lg" onclick="loadMarketHeat()"><i class="bi bi-fire me-1"></i>生成市場熱度分析</button>
                </div>
              </div>
            </div>
            
            <div class="row g-4">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header text-danger"><i class="bi bi-graph-up-arrow me-2"></i>全市場經理人淨加碼前 10 大股票</div>
                  <div class="table-responsive">
                    <table class="table align-middle">
                      <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">區間總加碼股數</th></tr></thead>
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
                      <thead><tr><th>排行</th><th>股票標的</th><th class="text-end">區間總減持股數</th></tr></thead>
                      <tbody id="heatSellBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
          
          <div class="custom-tab-content" id="content-e">
            <div class="card p-3 mb-4 bg-light">
              <div class="fw-bold text-dark mb-2"><i class="bi bi-check2-square me-1"></i>勾選欲交叉比較的 ETF 基金清單（支援複選多檔進行橫向權重對照）</div>
              <div class="d-flex flex-wrap gap-3 p-3 bg-white border rounded" id="compareCheckboxContainer"></div>
            </div>
            
            <div class="card">
              <div class="card-header text-primary"><i class="bi bi-table me-2"></i>橫向交叉對比多方持股權重對照矩陣</div>
              <div class="table-responsive">
                <table class="table table-bordered table-hover align-middle">
                  <thead><tr id="compareTableHeader"><th>股票代號</th><th>股票名稱</th></tr></thead>
                  <tbody id="compareTableBody"><tr><td colspan="2" class="text-center py-4 text-muted">請在上方至少勾選兩檔以上的 ETF 進行多方橫向對照。</td></tr></tbody>
                </table>
              </div>
            </div>
          </div>

        </div>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
      <script>
        const globalRawData = __DATA_PLACEHOLDER__;
        const wantgooMarketData = __WANTGOO_PLACEHOLDER__;
        const twseLiveMarketData = __TWSE_PLACEHOLDER__;
        const tickerMappingData = __TICKER_PLACEHOLDER__;
        const etfNameMappingData = __ETF_NAME_PLACEHOLDER__;

        let selectedEtf = null;
        let selectedTargetStocks = [];

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
    
    // --- 新增：過濾台灣普通公司債 (G, B, A, H, F 開頭 + 5碼英數字，總長度固定為 6) ---
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
            
            let listHtml = "";
            let compareHtml = "";
            let homeHtml = "";

            sortedEtfs.forEach(etf => {
                let mappedName = etfNameMappingData[etf] || "未知名稱";
                listHtml += `<button class="list-group-item list-group-item-action etf-item-btn font-monospace" id="btn-etf-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-box-se me-2 text-primary"></i><b>${etf}</b> <span class="text-muted small ms-1">${mappedName}</span></button>`;
                compareHtml += `<div class="form-check form-check-inline"><input class="form-check-input" type="checkbox" value="${etf}" id="chk-${etf}" onchange="renderCompareMatrix()"><label class="form-check-label font-monospace" for="chk-${etf}"><b>${etf}</b> <span class="text-muted small">${mappedName}</span></label></div>`;
                
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

                homeHtml += `<tr><td class="font-monospace fw-bold">${etf}</td><td class="fw-bold text-secondary">${mappedName}</td><td class="font-monospace fw-bold">${price}</td><td class="font-monospace ${styleColor}">${displayChange}</td></tr>`;
            });

            listGroup.innerHTML = listHtml;
            compareContainer.innerHTML = compareHtml;
            document.getElementById('homeTableBody').innerHTML = homeHtml;

            if(sortedEtfs.length > 0) {
                selectEtf(sortedEtfs[0]);
            }
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

            stocks.forEach(r => {
                sHtml += `<tr><td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td><td class="fw-bold">${r.name}</td><td class="text-end font-monospace text-primary fw-bold">${Number(r.weight).toFixed(2)}%</td><td class="text-end font-monospace text-secondary">${Math.round(r.volume).toLocaleString()}</td></tr>`;
            });
            assets.forEach(r => {
                aHtml += `<tr><td><span class="badge bg-light text-muted font-monospace border">${r.stock || '-'}</span></td><td class="text-muted">${r.name}</td><td class="text-end font-monospace">${Number(r.weight).toFixed(2)}%</td><td class="text-end font-monospace">${Math.round(r.volume).toLocaleString()}</td></tr>`;
            });

            sBody.innerHTML = sHtml || '<tr><td colspan="4" class="text-center text-muted">無成分股資料</td></tr>';
            aBody.innerHTML = aHtml || '<tr><td colspan="4" class="text-center text-muted">無非股票資產</td></tr>';

            if (sortedDates.length >= 2) {
                document.getElementById('startDateInput').value = sortedDates[sortedDates.length - 2];
                document.getElementById('endDateInput').value = sortedDates[sortedDates.length - 1];
            }
            calculateChipsDelta(etfName, sortedDates);
        }

        function calculateChipsDelta(etfName, sortedDates) {
            let type = document.getElementById('rangeType').value;
            let dOld = null, dNew = null;
            let etfData = globalRawData.filter(d => d.etf === etfName);

            if (type === 'custom') {
                dOld = document.getElementById('startDateInput').value;
                dNew = document.getElementById('endDateInput').value;
            } else {
                let offset = parseInt(type);
                if (sortedDates.length > offset) {
                    dOld = sortedDates[sortedDates.length - 1 - offset];
                    dNew = sortedDates[sortedDates.length - 1];
                } else if (sortedDates.length >= 2) {
                    dOld = sortedDates[0];
                    dNew = sortedDates[sortedDates.length - 1];
                }
            }

            if(!dOld || !dNew) return;

            let rowsOld = etfData.filter(d => d.date === dOld);
            let rowsNew = etfData.filter(d => d.date === dNew);

            let trendMap = {};
            if (sortedDates.length >= 2) {
                let uniqStocks = [...new Set(etfData.filter(d => isNormalStock(d.stock, d.name)).map(d => d.stock))];
                uniqStocks.forEach(sCode => {
                    let streakCount = 0;
                    let currentTrend = null;
                    for (let i = sortedDates.length - 1; i > 0; i--) {
                        let dateN = sortedDates[i];
                        let dateO = sortedDates[i - 1];
                        let vNew = etfData.find(d => d.date === dateN && d.stock === sCode)?.volume || 0;
                        let vOld = etfData.find(d => d.date === dateO && d.stock === sCode)?.volume || 0;
                        let diff = vNew - vOld;
                        if (diff === 0) break;
                        let dayTrend = diff > 0 ? "買" : "賣";
                        if (currentTrend === null) { currentTrend = dayTrend; streakCount = 1; }
                        else if (dayTrend === currentTrend) { streakCount++; }
                        else { break; }
                    }
                    if (streakCount >= 2 && currentTrend) { trendMap[sCode] = '連' + currentTrend + streakCount + '日'; }
                });
            }

            let allStocks = [...new Set([...rowsOld.map(r=>r.stock), ...rowsNew.map(r=>r.stock)])].filter(s => {
                let match = rowsNew.find(x=>x.stock===s) || rowsOld.find(x=>x.stock===s);
                return match ? isNormalStock(match.stock, match.name) : false;
            });

            let changes = allStocks.map(sCode => {
                let oRow = rowsOld.find(x => x.stock === sCode);
                let nRow = rowsNew.find(x => x.stock === sCode);
                let oVol = oRow ? Number(oRow.volume) : 0;
                let nVol = nRow ? Number(nRow.volume) : 0;
                let diff = nVol - oVol;
                let name = nRow ? nRow.name : (oRow ? oRow.name : "");
                let nature = "";
                if (oVol === 0 && nVol > 0) nature = "NEW";
                else if (oVol > 0 && nVol === 0) nature = "DELETE";
                else if (diff > 0) nature = "UP";
                else if (diff < 0) nature = "DOWN";
                else nature = "NONE";
                return { stock: sCode, name: name, diff: diff, nature: nature };
            }).filter(x => x.nature !== "NONE");

            let htmlNew = "", htmlAdd = "", htmlSub = "", htmlDel = "";
            changes.sort((a,b) => b.diff - a.diff).forEach(r => {
                let badge = "";
                let dStyle = "";
                if(r.nature === "NEW") { badge = `<span class="badge-nature-new">🆕 新增納入</span>`; dStyle = "color:#ea580c;"; }
                else if(r.nature === "UP") { badge = `<span class="badge-nature-up">🔺 經理人加碼</span>`; dStyle = "color:#dc2626;"; }
                else if(r.nature === "DOWN") { badge = `<span class="badge-nature-down">🔻 經理人減持</span>`; dStyle = "color:#0f766e;"; }
                else if(r.nature === "DELETE") { badge = `<span class="badge-nature-delete">❌ 完整剔除</span>`; dStyle = "color:#4b5563;"; }

                let trendStr = trendMap[r.stock] || "無變動";
                let trendHtml = `<span class="text-muted">無變動</span>`;
                if(trendStr.includes("買")) trendHtml = `<span class="badge-trend-buy">📈 ${trendStr}</span>`;
                if(trendStr.includes("賣")) trendHtml = `<span class="badge-trend-sell">📉 ${trendStr}</span>`;

                let rowHtml = `<tr><td class="fw-bold">${r.stock} <span class="text-muted small fw-normal ms-2">${r.name}</span></td><td>${badge}</td><td class="text-end fw-bold font-monospace" style="${dStyle}">${Math.round(r.diff).toLocaleString()} 股</td><td class="px-4">${trendHtml}</td></tr>`;
                
                if(r.nature === "NEW") htmlNew += rowHtml;
                else if(r.nature === "UP") htmlAdd += rowHtml;
                else if(r.nature === "DOWN") htmlSub += rowHtml;
                else if(r.nature === "DELETE") htmlDel += rowHtml;
            });

            document.getElementById('changeTableBody').innerHTML = (htmlNew + htmlAdd + htmlSub + htmlDel) || '<tr><td colspan="4" class="text-center text-muted py-3">此區間成分股數量與持有股數未發生任何變動</td></tr>';
        }

        function toggleCustomDates() {
            let type = document.getElementById('rangeType').value;
            document.getElementById('customDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function refreshCurrentEtf() {
            if(!selectedEtf) return;
            let etfData = globalRawData.filter(d => d.etf === selectedEtf);
            let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            calculateChipsDelta(selectedEtf, dates);
        }

        function searchStockSuggestions(keyword, boxId, inputId, isMultiple) {
            let box = document.getElementById(boxId);
            if (!keyword || keyword.trim() === "") { box.style.display = 'none'; return; }
            let k = keyword.trim().toLowerCase();
            
            let stockMap = {};
            globalRawData.forEach(r => {
                if (r.stock && isNormalStock(r.stock, r.name)) { stockMap[r.stock] = r.name; }
            });
            
            let matches = [];
            Object.keys(stockMap).forEach(code => {
                let name = stockMap[code];
                if (code.includes(k) || name.toLowerCase().includes(k)) { matches.push({ code: code, name: name }); }
            });
            
            if (matches.length === 0) { box.style.display = 'none'; return; }
            
            let html = "";
            matches.slice(0, 8).forEach(item => {
                if (isMultiple) { html += `<div class="suggestion-item" onclick="addTargetStockTag('${item.code}', '${item.name}', '${boxId}', '${inputId}')"><b>${item.code}</b> - ${item.name}</div>`; }
                else { html += `<div class="suggestion-item" onclick="selectStockSuggestion('${item.code}', '${item.name}', '${boxId}', appraisal_done='${inputId}')"><b>${item.code}</b> - ${item.name}</div>`; }
            });
            box.innerHTML = html;
            box.style.display = 'block';
        }

        function selectStockSuggestion(code, name, boxId, inputId) {
            document.getElementById('stockSearchInput').value = code;
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
            if (selectedTargetStocks.length === 0) { container.innerHTML = '<span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選組合</span>'; return; }
            container.innerHTML = selectedTargetStocks.map(x => `<span class="selected-stock-tag"><b>${x.code}</b> ${x.name} <i class="bi bi-x-circle-fill" onclick="removeTargetStockTag('${x.code}')"></i></span>`).join('');
        }

        function calculateMatchedEtfs() {
            let body = document.getElementById('matchResultBody');
            if(selectedTargetStocks.length === 0) { body.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>'; return; }

            let etfSet = new Set();
            globalRawData.forEach(r => { if(r.etf) etfSet.add(r.etf); });
            
            let res = [];
            etfSet.forEach(eCode => {
                let etfData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                let latestDate = dates[dates.length - 1];
                let latestRows = etfData.filter(d => d.date === latestDate);

                let matchCount = 0;
                let totalWeight = 0;
                let details = [];

                selectedTargetStocks.forEach(t => {
                    let match = latestRows.find(x => x.stock === t.code);
                    if (match) {
                        matchCount++;
                        let w = Number(match.weight);
                        totalWeight += w;
                        details.push(`<span class="badge bg-light text-primary border me-1">${t.name}: ${w.toFixed(2)}%</span>`);
                    } else {
                        details.push(`<span class="badge bg-light text-muted border me-1">${t.name}: ❌ 未持有</span>`);
                    }
                });

                if (matchCount === selectedTargetStocks.length) {
                    res.push({ etf: eCode, name: etfNameMappingData[eCode] || "未知名稱", totalWeight: totalWeight, details: details.join('') });
                }
            });

            res.sort((a,b) => b.totalWeight - a.totalWeight);
            body.innerHTML = res.map(x => `<tr><td class="font-monospace fw-bold">${x.etf}</td><td class="fw-bold text-secondary">${x.name}</td><td class="text-end font-monospace text-success fw-bold fs-5">${x.totalWeight.toFixed(2)}%</td><td class="px-4">${x.details}</td></tr>`).join('') || '<tr><td colspan="4" class="text-center text-muted py-4"><i class="bi bi-exclamation-triangle me-1"></i>全市場查無同時重疊包含這些目標公司的 ETF。請精簡您的目標清單再試一次。</td></tr>';
        }

        function searchStockDistribution() {
            let code = document.getElementById('stockSearchInput').value.trim();
            if(!code) { alert("請輸入個股代號或名稱"); return; }
            
            let matchRow = globalRawData.find(x => x.stock === code || x.name === code);
            if (!matchRow) { alert("查無此股票資料，請輸入完整正確代號"); return; }
            let sCode = matchRow.stock;
            let sName = matchRow.name;

            document.getElementById('resStockTitle').innerText = `${sCode} - ${sName}`;
            document.getElementById('stockResultContainer').style.display = 'block';

            let etfSet = new Set(globalRawData.map(d => d.etf));
            let distRows = [];
            let weightRows = [];

            let totalDiff = 0;

            etfSet.forEach(eCode => {
                let etfData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                if(dates.length >= 2) {
                    let dOld = dates[dates.length - 2];
                    let dNew = dates[dates.length - 1];
                    let oVol = etfData.find(d => d.date === dOld && d.stock === sCode)?.volume || 0;
                    let nVol = etfData.find(d => d.date === dNew && d.stock === sCode)?.volume || 0;
                    let diff = nVol - oVol;
                    if(diff !== 0) {
                        totalDiff += diff;
                        distRows.push({ etf: eCode, name: etfNameMappingData[eCode] || "未知名稱", diff: diff });
                    }
                }
                let latestDate = dates[dates.length - 1];
                let lRow = etfData.find(d => d.date === latestDate && d.stock === sCode);
                if (lRow) { weightRows.push({ etf: eCode, name: etfNameMappingData[eCode] || "未知名稱", weight: Number(lRow.weight) }); }
            });

            let trendStatusEl = document.getElementById('trendStockStatus');
            trendStatusEl.innerHTML = totalDiff > 0 ? `<span class="badge bg-danger">🔥 淨加碼</span>` : (totalDiff < 0 ? `<span class="badge bg-success">📉 淨減持</span>` : `<span class="badge bg-secondary">持平</span>`);
            let totalVolEl = document.getElementById('trendStockTotalVol');
            totalVolEl.innerText = `${totalDiff > 0 ? '+' : ''}${Math.round(totalDiff).toLocaleString()} 股`;
            totalVolEl.className = `fw-bold font-monospace mb-0 ${totalDiff > 0 ? 'text-danger' : 'text-success'}`;

            let changeHtml = distRows.sort((a,b)=>b.diff - a.diff).map(x => `<tr><td><b>${x.etf}</b> <span class="text-muted small">${x.name}</span></td><td class="text-end font-monospace fw-bold ${x.diff > 0 ? 'text-danger' : 'text-success'}">${x.diff > 0 ? '+' : ''}${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('');
            let weightHtml = weightRows.sort((a,b)=>b.weight - a.weight).map(x => `<tr><td class="font-monospace fw-bold">${x.etf}</td><td class="fw-bold text-secondary">${x.name}</td><td class="text-end font-monospace text-primary fw-bold">${x.weight.toFixed(2)}%</td></tr>`).join('');

            document.getElementById('stockDistBody').innerHTML = changeHtml || `<tr><td colspan="2" class="text-center text-muted py-3">近一日無經理人在此標的進行調整變動</td></tr>`;
            document.getElementById('stockDistBody2').innerHTML = weightHtml || `<tr><td colspan="3" class="text-center text-muted py-3">全市場無 ETF 持有此股票標的</td></tr>`;
        }

        function toggleGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            document.getElementById('globalCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let etfSet = new Set(globalRawData.map(d => d.etf));
            
            let addedMap = {}, deletedMap = {};

            etfSet.forEach(eCode => {
                let etfData = globalRawData.filter(d => d.etf === eCode);
                let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
                let dOld = null, dNew = null;
                
                if (type === 'custom') {
                    dOld = document.getElementById('globalStartDate').value;
                    dNew = document.getElementById('globalEndDate').value;
                } else {
                    let offset = parseInt(type);
                    if(dates.length > offset) { dOld = dates[dates.length - 1 - offset]; dNew = dates[dates.length - 1]; }
                }

                if(dOld && dNew) {
                    let oldStocks = etfData.filter(d => d.date === dOld && isNormalStock(d.stock, d.name)).map(d => d.stock);
                    let newStocks = etfData.filter(d => d.date === dNew && isNormalStock(d.stock, d.name)).map(d => d.stock);
                    
                    newStocks.forEach(s => {
                        if (!oldStocks.includes(s)) {
                            let r = etfData.find(d => d.date === dNew && d.stock === s);
                            let sName = r ? r.name : "";
                            let k = s + "||" + sName;
                            if(!addedMap[k]) addedMap[k] = [];
                            addedMap[k].push(eCode);
                        }
                    });

                    oldStocks.forEach(s => {
                        if (!newStocks.includes(s)) {
                            let r = etfData.find(d => d.date === dOld && d.stock === s);
                            let sName = r ? r.name : "";
                            let k = s + "||" + sName;
                            if(!deletedMap[k]) deletedMap[k] = [];
                            deletedMap[k].push(eCode);
                        }
                    });
                }
            });

            let nHtml = Object.keys(addedMap).map(k => {
                let [s, name] = k.split("||");
                let list = addedMap[k].map(e => `<span class="badge bg-light text-danger border me-1"><b>${e}</b></span>`).join('');
                return `<tr><td class="fw-bold">${s} <span class="text-muted small fw-normal ms-1">${name}</span></td><td>${list}</td></tr>`;
            }).join('');

            let dHtml = Object.keys(deletedMap).map(k => {
                let [s, name] = k.split("||");
                let list = deletedMap[k].map(e => `<span class="badge bg-light text-secondary border me-1"><b>${e}</b></span>`).join('');
                return `<tr><td class="fw-bold">${s} <span class="text-muted small fw-normal ms-1">${name}</span></td><td>${list}</td></tr>`;
            }).join('');

            document.getElementById('globalNewBody').innerHTML = nHtml || '<tr><td colspan="2" class="text-center text-muted py-3">無新增成分股項目</td></tr>';
            document.getElementById('globalDelBody').innerHTML = dHtml || '<tr><td colspan="2" class="text-center text-muted py-3">無剔除成分股項目</td></tr>';
        }

        function toggleHeatCustomDates() {
            let type = document.getElementById('heatRangeType').value;
            document.getElementById('heatCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
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
        function renderCompareMatrix() {
            let checkedCbs = Array.from(document.querySelectorAll('#compareCheckboxContainer input:checked')).map(cb => cb.value);
            let header = document.getElementById('compareTableHeader');
            if(checkedCbs.length === 0) {
                header.innerHTML = '<th>股票代號</th><th>股票名稱</th>';
                document.getElementById('compareTableBody').innerHTML = '<tr><td colspan="2" class="text-center py-4 text-muted">請在上方至少勾選兩檔以上的 ETF 進行多方橫向對照。</td></tr>';
                return;
            }

            header.innerHTML = '<th>股票代號</th><th>股票名稱</th>' + checkedCbs.map(c => `<th class="text-end font-monospace">${c}<br>權重</th>`).join('');

            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            let latestDate = dates[dates.length - 1];

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
                    row += `<td class="text-end font-monospace ${w > 0 ? 'text-primary fw-bold' : 'text-muted'}\">${w > 0 ? w.toFixed(2)+'%' : '-'}</td>`;
                });
                return `<tr>${row}</tr>`;
            }).join('') || '<tr><td colspan="2" class="text-center text-muted py-4">所勾選的 ETF 組合中目前無共同持股標的</td></tr>';
        }
      </script>
    </body>
    </html>
    """

    # 將後端清洗過後的 JSON 資料動態注入到前端 JavaScript 中
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

    # 符合 Streamlit 2026 最新規範，將已被棄用的 st.components.v1.html 升級替換為新版專用的 st.iframe 
    components.html(final_html, height=1200, scrolling=True)
if __name__ == "__main__":
    main()
