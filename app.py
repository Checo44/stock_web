import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os

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
WORKSHEET_TICKER = "代號"

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
    if not sh:
        return {}, "無法連線至 Google 試算表"
    try:
        ws = sh.worksheet(WORKSHEET_TICKER)
        raw_ticker = ws.get_all_values()
        if not raw_ticker or len(raw_ticker) < 2:
            return {}, None
        
        headers = [str(h).strip() for h in raw_ticker[0]]
        
        code_idx, name_idx = -1, -1
        for i, h in enumerate(headers):
            if h in ["股票代號", "代號", "Stock Code", "Code"]:
                code_idx = i
            if h in ["公司名稱", "股票名稱", "名稱", "Name", "Company Name"]:
                name_idx = i
                
        if code_idx == -1: code_idx = 0
        if name_idx == -1: name_idx = 1
        
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
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    if ticker_map:
        df['name'] = df['stock'].map(ticker_map).fillna(df['name'])
    
    return df, None

# ==========================================
# 3. 主核心資料庫結構轉換與打包
# ==========================================
def fetch_backend_data_to_json():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg:
        return "[]"
        
    ticker_map, _ = fetch_ticker_mapping()
    
    df, clean_err = process_and_standardize(raw_data, ticker_map=ticker_map)
    if clean_err or df.empty:
        return "[]"
    
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False)

# ==========================================
# 4. 主渲染邏輯
# ==========================================
def main():
    json_data = fetch_backend_data_to_json()

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
        }
        .nav-tabs .nav-link.active {
          background-color: #e2e8f0;
          color: #1e3c72;
          font-weight: 700;
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
        
        <ul class="nav nav-tabs mb-4" id="mainTabs" role="tablist">
          <li class="nav-item">
            <button class="nav-link active" id="tab-a" data-bs-toggle="tab" data-bs-target="#content-a" type="button"><i class="bi bi-pie-chart-fill me-2"></i>單檔 ETF 籌碼與持股</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-b" data-bs-toggle="tab" data-bs-target="#content-b" type="button"><i class="bi bi-share-fill me-2"></i>個股籌碼分佈</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-c" data-bs-toggle="tab" data-bs-target="#content-c" type="button"><i class="bi bi-globe me-2"></i>全市場異動總覽</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-d" data-bs-toggle="tab" data-bs-target="#content-d" type="button"><i class="bi bi-fire me-2 text-danger"></i>市場熱度排行</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" id="tab-e" data-bs-toggle="tab" data-bs-target="#content-e" type="button"><i class="bi bi-arrow-left-right me-2"></i>ETF 交叉比較</button>
          </li>
        </ul>

        <div class="tab-content" id="tabsContent">
          
          <div class="tab-pane fade show active" id="content-a" role="tabpanel">
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
                
                <div id="metaContainer" class="row g-3 mb-4" style="display: none;">
                  <div class="col-6 col-md-3">
                    <div class="meta-card" style="border-left-color: #3182ce;">
                      <div class="meta-label">市價</div>
                      <div class="meta-value" id="metaMarketPrice">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md-3">
                    <div class="meta-card" style="border-left-color: #e53e3e;">
                      <div class="meta-label">漲跌</div>
                      <div class="meta-value" id="metaChange">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md-3">
                    <div class="meta-card" style="border-left-color: #319795;">
                      <div class="meta-label">折溢價</div>
                      <div class="meta-value" id="metaPremium">-</div>
                    </div>
                  </div>
                  <div class="col-6 col-md-3">
                    <div class="meta-card" style="border-left-color: #805ad5;">
                      <div class="meta-label">規模</div>
                      <div class="meta-value" id="metaSize">-</div>
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

          <div class="tab-pane fade" id="content-b" role="tabpanel">
            <div class="card p-3">
              <div class="row align-items-center g-3">
                <div class="col-md-6">
                  <label class="form-label fw-bold text-secondary">請輸入個股代號 (如: 2330)</label>
                  <input type="text" id="stockInput" class="form-control form-control-lg" placeholder="例如: 2330">
                </div>
                <div class="col-md-3 pt-4">
                  <button class="btn btn-success btn-lg w-100" onclick="searchStockDistribution()"><i class="bi bi-search me-1"></i>查詢分佈</button>
                </div>
              </div>
            </div>
            <div id="stockResultCard" class="card" style="display: none;">
              <div class="card-header bg-success text-white fw-bold" id="stockResultTitle"></div>
              <div class="table-responsive">
                <table class="table table-hover align-middle">
                  <thead><tr><th>持有該股之 ETF 代號</th><th class="text-end">持股權重占比</th><th class="text-end">持有股數</th></tr></thead>
                  <tbody id="stockDistBody"></tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="tab-pane fade" id="content-c" role="tabpanel">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-md-4">
                  <label class="form-label fw-bold text-secondary">全市場異動比較範圍</label>
                  <select id="globalRangeType" class="form-select" onchange="toggleGlobalCustomDates()">
                    <option value="1">日變動</option>
                    <option value="5">週變動</option>
                    <option value="10">月變動 (10筆)</option>
                    <option value="custom">自訂區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="globalCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6">
                      <input type="text" id="globalStartDate" class="form-control" placeholder="舊日期 YYYY-MM-DD">
                    </div>
                    <div class="col-6">
                      <input type="text" id="globalEndDate" class="form-control" placeholder="新日期 YYYY-MM-DD">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-2">
                  <button class="btn btn-dark w-100 btn-lg" onclick="loadGlobalChanges()"><i class="bi bi-globe2 me-1"></i>生成異動總覽</button>
                </div>
              </div>
            </div>
            <div class="card">
              <div class="card-header bg-danger text-white fw-bold" id="globalTitle">全市場 ETF 成分股異動排行追蹤</div>
              <div class="table-responsive">
                <table class="table table-hover table-striped align-middle">
                  <thead>
                    <tr><th>ETF代號</th><th>成分股</th><th>異動性質</th><th class="text-end">增減股數</th><th>連續買賣狀態</th></tr>
                  </thead>
                  <tbody id="globalTableBody"></tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="tab-pane fade" id="content-d" role="tabpanel">
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
                    <div class="col-6">
                      <input type="text" id="heatStartDate" class="form-control" placeholder="舊日期 YYYY-MM-DD">
                    </div>
                    <div class="col-6">
                      <input type="text" id="heatEndDate" class="form-control" placeholder="新日期 YYYY-MM-DD">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-2">
                  <button class="btn btn-danger w-100 btn-lg" onclick="loadMarketHeat()"><i class="bi bi-fire me-1"></i>生成市場熱度分析</button>
                </div>
              </div>
            </div>

            <div class="row g-4">
              <div class="col-lg-6">
                <div class="card">
                  <div class="card-header bg-danger text-white fw-bold" id="heatBuyTitle"><i class="bi bi-graph-up me-2"></i>跨市場大加總：淨買超前 10 大個股</div>
                  <div class="table-responsive">
                    <table class="table table-hover table-striped align-middle">
                      <thead>
                        <tr><th>排名</th><th>股票代號</th><th>股票名稱</th><th class="text-end">跨市場淨加碼(股)</th></tr>
                      </thead>
                      <tbody id="heatBuyTableBody">
                        <tr><td colspan="4" class="text-center text-muted py-4">請點擊「生成市場熱度分析」載入數據</td></tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div class="col-lg-6">
                <div class="card">
                  <div class="card-header bg-success text-white fw-bold" id="heatSellTitle"><i class="bi bi-graph-down me-2"></i>跨市場大加總：淨賣超前 10 大個股</div>
                  <div class="table-responsive">
                    <table class="table table-hover table-striped align-middle">
                      <thead>
                        <tr><th>排名</th><th>股票代號</th><th>股票名稱</th><th class="text-end">跨市場淨減持(股)</th></tr>
                      </thead>
                      <tbody id="heatSellTableBody">
                        <tr><td colspan="4" class="text-center text-muted py-4">請點擊「生成市場熱度分析」載入數據</td></tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="tab-pane fade" id="content-e" role="tabpanel">
            <div class="card p-3 mb-4 bg-light">
              <div class="row align-items-center g-3">
                <div class="col-12">
                  <label class="form-label fw-bold text-secondary mb-2"><i class="bi bi-check2-square me-1"></i>請選擇要比較的 ETF 代號（可多選）</label>
                  <div id="compareEtfCheckboxes" class="d-flex flex-wrap gap-2 p-3 bg-white border rounded" style="max-height: 150px; overflow-y: auto;"></div>
                </div>
                <div class="col-md-3 pt-2">
                  <button class="btn btn-primary w-100 btn-lg" onclick="generateComparison()"><i class="bi bi-layout-three-columns me-1"></i>開始交叉比較</button>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-header bg-primary text-white fw-bold" id="compareTitle"><i class="bi bi-layout-three-columns me-2"></i>ETF 持股權重交叉比較矩陣</div>
              <div class="table-responsive">
                <table class="table table-hover table-striped align-middle">
                  <thead>
                    <tr id="compareTableHeader">
                      <th>股票代號</th>
                      <th>股票名稱</th>
                    </tr>
                  </thead>
                  <tbody id="compareTableBody">
                    <tr><td colspan="2" class="text-center text-muted py-4">請先勾選上方 ETF 並點擊「開始交叉比較」鈕</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

        </div>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bundle.min.js"></script>

      <script>
        let globalRawData = __DATA_PLACEHOLDER__;
        let activeEtf = "";

        document.addEventListener("DOMContentLoaded", function() {
            document.getElementById('loading').style.display = 'none';
            if (!globalRawData || globalRawData.length === 0) {
                document.getElementById('etfButtonList').innerHTML = '<div class="text-center text-danger py-3">後端無有效資料，請檢查 Google 試算表。</div>';
                return;
            }
            initDashboard();
        });

        function initDashboard() {
            let etfSet = new Set();
            globalRawData.forEach(item => { if(item.etf) etfSet.add(item.etf); });
            let etfList = Array.from(etfSet).sort();

            let listHtml = "";
            etfList.forEach(etf => {
                listHtml += `<button class="list-group-item list-group-item-action etf-item-btn" id="btn-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-file-earmark-text me-2"></i>${etf}</button>`;
            });
            document.getElementById('etfButtonList').innerHTML = listHtml;

            let checkHtml = "";
            etfList.forEach(etf => {
                checkHtml += `
                  <div class="form-check form-check-inline me-3 py-1">
                    <input class="form-check-input etf-compare-cb" type="checkbox" value="${etf}" id="cb-${etf}" checked>
                    <label class="form-check-label fw-bold" for="cb-${etf}">${etf}</label>
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
            let cashEx = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨"];
            if (meta.includes(code) || meta.includes(name)) return false;
            if (cashEx.some(k => code.toUpperCase().includes(k) || name.toUpperCase().includes(k))) return false;
            return true;
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

            let getMeta = (key) => {
                let found = latestRows.find(r => r.stock === key);
                if(!found) return "-";
                return typeof found.volume === 'number' ? found.volume.toLocaleString() : found.volume;
            };

            document.getElementById('metaContainer').style.display = 'flex';
            document.getElementById('metaChange').innerText = getMeta("漲跌");
            document.getElementById('metaMarketPrice').innerText = getMeta("市價");
            document.getElementById('metaPremium').innerText = getMeta("折溢價") + "%";
            
            let sizeVal = latestRows.find(r => r.stock === "規模")?.volume;
            document.getElementById('metaSize').innerText = sizeVal ? (Number(sizeVal)/100000000).toFixed(1) + " 億" : "-";

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

        function renderChangeTable(etfData, sortedDates, latestDate) {
            let compareDate = document.getElementById('startDate').value;
            document.getElementById('dateDisplayInfo').innerHTML = `📊 <b>籌碼區間：</b> 比較日 <span class="badge bg-light text-dark border">${compareDate}</span> ➔ 基準日 <span class="badge bg-light text-dark border">${latestDate}</span>`;
            document.getElementById('compareDateBadge').innerText = `對比區間: ${compareDate} ~ ${latestDate}`;

            let currentStocks = etfData.filter(d => d.date === latestDate && isNormalStock(d.stock, d.name));
            let compRows = etfData.filter(d => d.date === compareDate);

            let trendMap = {};
            if (sortedDates.length >= 2) {
                let uniqStocks = [...new Set(etfData.filter(d => isNormalStock(d.stock, d.name)).map(d => d.stock))];
                uniqStocks.forEach(sCode => {
                    let streakCount = 0;
                    let currentTrend = null;

                    for (let i = sortedDates.length - 1; i > 0; i--) {
                        let dNew = sortedDates[i];
                        let dOld = sortedDates[i - 1];

                        let vNew = etfData.find(d => d.date === dNew && d.stock === sCode)?.volume || 0;
                        let vOld = etfData.find(d => d.date === dOld && d.stock === sCode)?.volume || 0;
                        let diff = vNew - vOld;

                        if (diff === 0) {
                            break;
                        }

                        let dayTrend = diff > 0 ? "買" : "賣";

                        if (currentTrend === null) {
                            currentTrend = dayTrend;
                            streakCount = 1;
                        } else if (dayTrend === currentTrend) {
                            streakCount++;
                        } else {
                            break;
                        }
                    }

                    if (streakCount > 0 && currentTrend !== null) {
                        trendMap[sCode] = `連${currentTrend} ${streakCount} 日`;
                    } else {
                        trendMap[sCode] = "無變動";
                    }
                });
            }

            let htmlNew = "";
            let htmlAdd = "";
            let htmlSub = "";
            let htmlDel = "";

            currentStocks.forEach(r => {
                let oldVol = compRows.find(c => c.stock === r.stock)?.volume || 0;
                let diff = r.volume - oldVol;

                if (diff !== 0) {
                    let nature = oldVol === 0 ? "新增" : (diff > 0 ? "增加" : "減少");
                    
                    let badge = "";
                    let dStyle = "";
                    if (nature === "新增") {
                        badge = `<span class="badge-nature-new">${nature}</span>`;
                        dStyle = "color:#ea580c;";
                    } else if (nature === "增加") {
                        badge = `<span class="badge-nature-up">${nature}</span>`;
                        dStyle = "color:#dc2626;";
                    } else {
                        badge = `<span class="badge-nature-down">${nature}</span>`;
                        dStyle = "color:#0f766e;";
                    }
                    
                    let trendStr = trendMap[r.stock] || "無變動";
                    let trendHtml = `<span class="text-muted">無變動</span>`;
                    if(trendStr.includes("買")) trendHtml = `<span class="badge-trend-buy">📈 ${trendStr}</span>`;
                    if(trendStr.includes("賣")) trendHtml = `<span class="badge-trend-sell">📉 ${trendStr}</span>`;

                    let rowHtml = `
                        <tr>
                            <td class="fw-bold">${r.stock} <span class="text-muted small fw-normal ms-2">${r.name}</span></td>
                            <td>${badge}</td>
                            <td class="text-end fw-bold font-monospace" style="${dStyle}">${diff > 0 ? '+' : ''}${Math.round(diff).toLocaleString()} 股</td>
                            <td class="px-4">${trendHtml}</td>
                        </tr>`;

                    if (nature === "新增") htmlNew += rowHtml;
                    else if (nature === "增加") htmlAdd += rowHtml;
                    else if (nature === "減少") htmlSub += rowHtml;
                }
            });

            compRows.forEach(r => {
                if (isNormalStock(r.stock, r.name)) {
                    let isStillExist = currentStocks.some(c => c.stock === r.stock);
                    if (!isStillExist && r.volume > 0) {
                        let badge = `<span class="badge-nature-delete">刪除</span>`;
                        let dStyle = "color:#4b5563;";
                        let diff = -r.volume;
                        
                        let trendStr = trendMap[r.stock] || "無變動";
                        let trendHtml = `<span class="text-muted">無變動</span>`;
                        if(trendStr.includes("買")) trendHtml = `<span class="badge-trend-buy">📈 ${trendStr}</span>`;
                        if(trendStr.includes("賣")) trendHtml = `<span class="badge-trend-sell">📉 ${trendStr}</span>`;
                        
                        htmlDel += `
                            <tr>
                                <td class="fw-bold">${r.stock} <span class="text-muted small fw-normal ms-2">${r.name}</span></td>
                                <td>${badge}</td>
                                <td class="text-end fw-bold font-monospace" style="${dStyle}">${Math.round(diff).toLocaleString()} 股</td>
                                <td class="px-4">${trendHtml}</td>
                            </tr>`;
                    }
                }
            });

            let changeHtml = htmlNew + htmlAdd + htmlSub + htmlDel;
            document.getElementById('changeTableBody').innerHTML = changeHtml || '<tr><td colspan="4" class="text-center text-muted py-3">此區間成分股數量未發生增減變動</td></tr>';
        }

        function toggleCustomDates() {
            let type = document.getElementById('rangeType').value;
            document.getElementById('customDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function refreshCurrentEtf() {
            if(activeEtf) selectEtf(activeEtf);
        }

        function searchStockDistribution() {
            let target = document.getElementById('stockInput').value.trim();
            if(!target) return;

            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            let matches = globalRawData.filter(d => d.date === latestDate && d.stock === target);

            let rCard = document.getElementById('stockResultCard');
            let body = document.getElementById('stockDistBody');
            rCard.style.display = 'block';
            document.getElementById('stockResultTitle').innerText = `🔍 個股 [${target}] 於全市場 ETF 最新持股分佈明細 (${latestDate})`;

            if(matches.length === 0) {
                body.innerHTML = `<tr><td colspan="3" class="text-center text-muted py-3">全市場目前無 any ETF 持有此資產。</td></tr>`;
                return;
            }

            body.innerHTML = matches.sort((a,b)=>b.volume - a.volume).map(r => `
                <tr>
                    <td class="fw-bold text-primary"><i class="bi bi-collection me-2"></i>${r.etf}</td>
                    <td class="text-end fw-bold text-danger">${Number(r.weight).toFixed(2)}%</td>
                    <td class="text-end text-secondary font-monospace">${Math.round(r.volume).toLocaleString()} 股</td>
                </tr>
            `).join('');
        }

        function toggleGlobalCustomDates() {
            document.getElementById('globalCustomDateGroup').style.display = (document.getElementById('globalRangeType').value === 'custom') ? 'block' : 'none';
        }

        function loadGlobalChanges() {
            let type = document.getElementById('globalRangeType').value;
            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            let compDate = "";

            if(type === 'custom') {
                compDate = document.getElementById('globalStartDate').value;
            } else {
                compDate = dates[Math.max(0, dates.length - 1 - parseInt(type))];
            }

            document.getElementById('globalTitle').innerText = `全市場 ETF 成分股異動排行追蹤 [ 區間：${compDate} ➔ ${latestDate} ]`;
            
            let body = document.getElementById('globalTableBody');
            body.innerHTML = "";
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
                        body.innerHTML += `
                          <tr>
                            <td><small class="fw-bold">${eCode}</small></td>
                            <td><span class="badge bg-light text-dark font-monospace border me-2">${r.stock}</span><b>${r.name}</b></td>
                            <td><span class="${bClass}">${diff > 0 ? '增加' : '減少'}</span></td>
                            <td class="text-end fw-bold font-monospace">${Math.round(diff).toLocaleString()}</td>
                            <td><small class="text-muted">區間交叉追蹤完成</small></td>
                          </tr>`;
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

            document.getElementById('heatBuyTableBody').innerHTML = topBuy.map((x, i) => `<tr><td><span class="rank-badge bg-danger text-white">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-danger fw-bold font-monospace">+${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('') || '<tr><td colspan="4" class="text-center text-muted">無變動</td></tr>';
            document.getElementById('heatSellTableBody').innerHTML = topSell.map((x, i) => `<tr><td><span class="rank-badge bg-teal text-white" style="background-color:#0f766e;">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-success fw-bold font-monospace">${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('') || '<tr><td colspan="4" class="text-center text-muted">無變動</td></tr>';
        }

        function generateComparison() {
            let checkedCbs = Array.from(document.querySelectorAll('.etf-compare-cb:checked')).map(c => c.value);
            if(checkedCbs.length === 0) { alert("請至少勾選一檔 ETF 進行交叉矩陣比對！"); return; }

            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];

            header = document.getElementById('compareTableHeader');
            header.innerHTML = `<th>股票代號</th><th>股票名稱</th>` + checkedCbs.map(c => `<th class="text-end" style="min-width:120px;">${c} 權重</th>`).join('');

            let stockMap = {};
            globalRawData.forEach(r => {
                if(r.date === latestDate && checkedCbs.includes(r.etf) && isNormalStock(r.stock, r.name)) {
                    stockMap[r.stock] = r.name;
                }
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
      </script>
    </body>
    </html>
    """

    final_html = html_template.replace("__DATA_PLACEHOLDER__", json_data)
    components.html(final_html, height=1600, scrolling=True)

if __name__ == "__main__":
    main()
