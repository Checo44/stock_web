import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os
import requests
import re

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
        
        for idx, h in enumerate(headers):
            if h in ["股票代號", "代號", "成分股代號", "商品代號"]:
                code_idx = idx
            if h in ["公司名稱", "股票名稱", "名稱", "成分股名稱", "商品名稱"]:
                name_idx = idx
                
        if code_idx is None: code_idx = 0
        if name_idx is None: name_idx = 1 if len(headers) > 1 else 0
        
        ticker_map = {}
        for row in raw_ticker[1:]:
            if len(row) > max(code_idx, name_idx):
                code = str(row[code_idx]).strip()
                name = str(row[name_idx]).strip()
                if code: 
                    if code.isalpha():
                        code = f"{code} US"
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
      <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
      
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
            <button class="nav-link" id="tab-g" onclick="switchTab('content-g', 'tab-g')"><i class="bi bi-bounding-box-circles text-info me-2"></i>力導向網絡拓撲星系圖</button>
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

          <div class="custom-tab-content" id="content-g">
            <div class="card p-3 mb-4 bg-light border">
              <div class="fw-bold text-dark mb-2"><i class="bi bi-check2-square me-1"></i>勾選欲加入金融星系的 ETF 基金清單（支援複選多檔進行交叉拓撲宇宙撞擊分析）</div>
              <div class="d-flex flex-wrap gap-3 p-3 bg-white border rounded" id="galaxyCheckboxContainer"></div>
              
              <div class="row align-items-center g-3 mt-2">
                <div class="col-md-5">
                  <label class="form-label fw-bold text-secondary"><i class="bi bi-funnel-fill me-1"></i>星系觀測過濾模式</label>
                  <select id="galaxyFilterMode" class="form-select form-select-lg fw-bold text-primary" onchange="renderGalaxyChart()">
                    <option value="top20">過濾模式一：僅觀測各 ETF 「持股前 20 大」成分股</option>
                    <option value="weight5">過濾模式二：僅觀測各 ETF 「持股權重高於 5%」之絕對核心圈</option>
                  </select>
                </div>
                <div class="col-md-7 text-md-end pt-md-4 text-muted small">
                  <div><i class="bi bi-info-circle me-1"></i> <b>星系指南：</b>核心大節點為 ETF，外圍小節點為成分股。連線粗細代表持股權重。</div>
                  <div>點擊 <b>ETF 核心</b>可全面高亮聚焦該星系；點擊 <b>外圍個股</b>可即時彈窗透視全市場交叉持股比例！</div>
                </div>
              </div>
            </div>
            
            <div class="card p-0 position-relative border-0 shadow-sm rounded-4" style="background: #fafafa; overflow: hidden;">
              <div id="galaxyChart" style="width: 100%; height: 780px;"></div>
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
                            <tr><th>股票代號</th><th>股票名稱</th><th class="text-end">持股權重</th><th>最新持股(股)</th></tr>
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
                            <tr><th>資產代號</th><th>資產項目</th><th class="text-end">權重</th><th>資產價值(股)</th></tr>
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
                  <div class="card-header text-dark bg-white"><i class="bi bi-arrow-left-right me-2 text-primary"></i>成分股經理人籌碼異動明細</div>
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
                  <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>全市場比較天數 / 範圍</label>
                  <select id="globalRangeType" class="form-select" onchange="toggleGlobalCustomDates()">
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
                      <input type="text" id="globalStartDateInput" class="form-control" placeholder="如: 2024-01-02">
                    </div>
                    <div class="col-6">
                      <label class="form-label small text-muted">比較新日期 (YYYY-MM-DD)</label>
                      <input type="text" id="globalEndDateInput" class="form-control" placeholder="如: 2024-01-09">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-md-4">
                  <button class="btn btn-primary w-100" onclick="loadGlobalDelta()"><i class="bi bi-search me-1"></i>執行全市場掃描</button>
                </div>
              </div>
            </div>
            
            <div class="row g-4">
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header text-danger"><i class="bi bi-plus-circle me-2"></i>全市場新納入成分股之 ETF 聯動追蹤</div>
                  <div class="table-responsive" style="max-height: 550px;">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr><th>新增個股標的</th><th>新納入此個股之 ETF 基金清單</th></tr>
                      </thead>
                      <tbody id="globalNewBody"></tbody>
                    </table>
                  </div>
                </div>
              </div>
              <div class="col-md-6">
                <div class="card">
                  <div class="card-header text-secondary"><i class="bi bi-dash-circle me-2"></i>全市場經理人完整剔除成分股之 ETF 聯動追蹤</div>
                  <div class="table-responsive" style="max-height: 550px;">
                    <table class="table table-hover align-middle">
                      <thead>
                        <tr><th>剔除個股標的</th><th>將此股完整剔除之 ETF 基金清單</th></tr>
                      </thead>
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
                  <label class="form-label fw-bold text-dark"><i class="bi bi-calendar-range me-1"></i>熱度分析天數 / 範圍</label>
                  <select id="heatRangeType" class="form-select" onchange="toggleHeatCustomDates()">
                    <option value="1">昨日比較 (1日變動)</option>
                    <option value="5">週變動比較 (5日變動)</option>
                    <option value="10">月變動比較 (10日變動)</option>
                    <option value="custom">自訂指定雙日期區間</option>
                  </select>
                </div>
                <div class="col-md-5" id="heatCustomDateGroup" style="display: none;">
                  <div class="row">
                    <div class="col-6">
                      <label class="form-label small text-muted">基準舊日期 (YYYY-MM-DD)</label>
                      <input type="text" id="heatStartDateInput" class="form-control" placeholder="如: 2024-01-02">
                    </div>
                    <div class="col-6">
                      <label class="form-label small text-muted">比較新日期 (YYYY-MM-DD)</label>
                      <input type="text" id="heatEndDateInput" class="form-control" placeholder="如: 2024-01-09">
                    </div>
                  </div>
                </div>
                <div class="col-md-3 pt-md-4">
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
                <i class="bi bi-shield-heart-fill me-2 text-danger"></i>【英雄所見略同】共同核心持股矩陣（所選 ETF 共同完全重疊持股）
              </div>
              <div class="table-responsive">
                <table class="table table-bordered align-middle text-center">
                  <thead id="compareCoreTableHeader"></thead>
                  <tbody id="compareCoreTableBody"></tbody>
                </table>
              </div>
            </div>
            
            <div class="card" id="uniqueHoldingsCard" style="display: none;">
              <div class="card-header bg-white text-secondary fw-bold d-flex align-items-center">
                <i class="bi bi-diamond-half me-2 text-info"></i>【各自美麗特色】成分股差異化矩陣（個別 ETF 特色獨立持股）
              </div>
              <div class="table-responsive">
                <table class="table table-bordered align-middle text-center">
                  <thead id="compareUniqueTableHeader"></thead>
                  <tbody id="compareUniqueTableBody"></tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>

      <script>
        const globalRawData = __DATA_PLACEHOLDER__;
        const twseLiveMarketData = __TWSE_PLACEHOLDER__;
        const tickerMappingData = __TICKER_PLACEHOLDER__;
        const etfNameMappingData = __ETF_PLACEHOLDER__;

        let selectedEtf = null;
        let selectedTargetStocks = []; 
        let galaxyChartInstance = null;

        function switchTab(contentId, tabId) {
          document.querySelectorAll('.custom-tab-content').forEach(el => el.classList.remove('active'));
          document.querySelectorAll('.nav-tabs .nav-link').forEach(el => el.classList.remove('active'));
          
          document.getElementById(contentId).classList.add('active');
          document.getElementById(tabId).classList.add('active');

          if(contentId === 'content-g') {
            setTimeout(() => { renderGalaxyChart(); }, 50);
          }
        }

        function isNormalStock(code, name) {
          let meta = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL"];
          if (!code || code.trim() === "") return false;
          let cleanCode = code.trim();
          let cleanName = name ? name.trim() : "";
          if (meta.includes(cleanCode) || (cleanName && meta.includes(cleanName))) return false;
          let cashEx = [
            "DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨", "RDI", "DR_", "RECEIVABLES", "DIVIDENDS", "DISPOSAL", "INVESTMENTS", "權證", "型購", "型售","買權","賣權","TWSE"
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
          globalRawData.forEach(r => {
            if(r.etf) etfSet.add(r.etf);
          });
          let sortedEtfs = Array.from(etfSet).sort();
          let listGroup = document.getElementById('etfListGroup');
          let compareContainer = document.getElementById('compareCheckboxContainer');
          let galaxyContainer = document.getElementById('galaxyCheckboxContainer');
          let listHtml = "";
          let compareHtml = "";
          let galaxyHtml = "";
          let homeHtml = "";

          sortedEtfs.forEach((etf, index) => {
            let mappedName = etfNameMappingData[etf] || "未知名稱";
            listHtml += `<button class="list-group-item list-group-item-action etf-item-btn font-monospace" id="btn-etf-${etf}" onclick="selectEtf('${etf}')"><i class="bi bi-wallet2 me-2"></i><b>${etf}</b> <span class="text-secondary small">${mappedName}</span></button>`;
            
            compareHtml += `
              <div class="form-check form-check-inline m-2">
                <input class="form-check-input compare-cb" type="checkbox" value="${etf}" id="cb-compare-${etf}" onchange="runEtfComparison()">
                <label class="form-check-label fw-bold font-monospace text-dark" for="cb-compare-${etf}">${etf} <span class="text-muted fw-normal small">${mappedName}</span></label>
              </div>`;

            galaxyHtml += `
              <div class="form-check form-check-inline m-2">
                <input class="form-check-input galaxy-cb" type="checkbox" value="${etf}" id="cb-galaxy-${etf}" onchange="renderGalaxyChart()" ${index < 3 ? 'checked' : ''}>
                <label class="form-check-label fw-bold font-monospace text-dark" for="cb-galaxy-${etf}">${etf} <span class="text-muted fw-normal small">${mappedName}</span></label>
              </div>`;

            let twseData = twseLiveMarketData[etf] || null;
            let displayPrice = "-";
            let displayPct = "-";
            let pctClass = "";
            if (twseData) {
              let priceVal = parseFloat(twseData.z) || parseFloat(twseData.p) || 0;
              displayPrice = priceVal > 0 ? priceVal.toFixed(2) : "-";
              let yPrice = parseFloat(twseData.y) || 0;
              if (priceVal > 0 && yPrice > 0) {
                let diff = priceVal - yPrice;
                let pct = ((diff / yPrice) * 100).toFixed(2);
                displayPct = diff > 0 ? `+${pct}%` : `${pct}%`;
                pctClass = diff > 0 ? "text-danger fw-bold" : "text-success fw-bold";
              }
            }
            homeHtml += `<tr><td class="font-monospace fw-bold"><a href="#" onclick="switchTab('content-a', 'tab-a'); selectEtf('${etf}'); return false;"><i class="bi bi-arrow-up-right-square me-1"></i>${etf}</a></td><td class="fw-bold">${mappedName}</td><td class="font-monospace">${displayPrice}</td><td class="${pctClass} font-monospace">${displayPct}</td></tr>`;
          });

          listGroup.innerHTML = listHtml;
          compareContainer.innerHTML = compareHtml;
          galaxyContainer.innerHTML = galaxyHtml;
          document.getElementById('homeTableBody').innerHTML = homeHtml;

          if (sortedEtfs.length > 0) {
            selectEtf(sortedEtfs[0]);
          }
          document.getElementById('loading').style.display = 'none';
        }

        function selectEtf(etfName) {
          selectedEtf = etfName;
          document.querySelectorAll('.etf-item-btn').forEach(btn => btn.classList.remove('active'));
          let activeBtn = document.getElementById(`btn-etf-${etfName}`);
          if (activeBtn) activeBtn.classList.add('active');

          let etfData = globalRawData.filter(d => d.etf === etfName);
          let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
          let latestDate = dates[dates.length - 1];

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
          sBody.innerHTML = "";
          aBody.innerHTML = "";

          let latestRows = etfData.filter(d => d.date === latestDate);
          latestRows.sort((a,b) => Number(b.weight) - Number(a.weight));

          latestRows.forEach(r => {
            let sName = r.name || tickerMappingData[r.stock] || "未知名稱";
            let rowHtml = `<tr><td class="font-monospace">${r.stock}</td><td class="fw-bold">${sName}</td><td class="text-end fw-bold text-primary font-monospace">${Number(r.weight).toFixed(2)}%</td><td class="font-monospace text-muted text-end">${Math.round(r.volume).toLocaleString()}</td></tr>`;
            if (isNormalStock(r.stock, r.name)) {
              sBody.innerHTML += rowHtml;
            } else {
              aBody.innerHTML += rowHtml;
            }
          });

          if (!sBody.innerHTML) sBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">無股票資產</td></tr>';
          if (!aBody.innerHTML) aBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">無其他非股票資產</td></tr>';

          document.getElementById('rangeType').value = "1";
          toggleCustomDates();
          calculateChipsDelta(etfData, dates, dates.length - 1, dates.length - 2);
        }

        function calculateChipsDelta(etfData, sortedDates, idxNew, idxOld) {
          if (idxNew < 0 || idxOld < 0 || idxNew >= sortedDates.length || idxOld >= sortedDates.length) {
            document.getElementById('changeTableBody').innerHTML = '<tr><td colspan="4" class="text-center text-muted py-3">選定日期數據範圍不足，無法計算籌碼變動</td></tr>';
            return;
          }
          let dateNew = sortedDates[idxNew];
          let dateOld = sortedDates[idxOld];

          let rowsOld = etfData.filter(d => d.date === dateOld);
          let rowsNew = etfData.filter(d => d.date === dateNew);

          let trendMap = {};
          if (idxNew >= 2) {
            let uniqStocks = [...new Set(etfData.map(d => d.stock))].filter(sCode => {
              let match = etfData.find(d => d.stock === sCode);
              return match ? isNormalStock(match.stock, match.name) : false;
            });
            uniqStocks.forEach(sCode => {
              let streakCount = 0;
              let currentTrend = null;
              for (let i = idxNew; i > 0; i--) {
                let dateN = sortedDates[i];
                let dateO = sortedDates[i - 1];
                let vNew = etfData.find(d => d.date === dateN && d.stock === sCode)?.volume || 0;
                let vOld = etfData.find(d => d.date === dateO && d.stock === sCode)?.volume || 0;
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
              if (streakCount >= 1 && currentTrend) {
                trendMap[sCode] = '連' + currentTrend + streakCount + '日';
              }
            });
          }

          let allStocks = [...new Set([...rowsOld.map(r=>r.stock), ...rowsNew.map(r=>r.stock)])].filter(s => {
            let match = rowsNew.find(x=>x.stock===s) || rowsOld.find(x=>x.stock===s);
            return match ? isNormalStock(match.stock, match.name) : false;
          });

          let changes = allStocks.map(sCode => {
            let oRow = rowsOld.find(x => x.stock === sCode);
            let nRow = rowsNew.find(x => x.stock === sCode);
            let vOld = oRow ? Number(oRow.volume) : 0;
            let vNew = nRow ? Number(nRow.volume) : 0;
            let diff = vNew - vOld;
            let sName = (nRow ? nRow.name : (oRow ? oRow.name : "")) || tickerMappingData[sCode] || "未知名稱";
            
            let nature = "KEEP";
            if (vOld === 0 && vNew > 0) nature = "NEW";
            else if (vOld > 0 && vNew === 0) nature = "DELETE";
            else if (diff > 0) nature = "UP";
            else if (diff < 0) nature = "DOWN";

            return { stock: sCode, name: sName, diff: diff, nature: nature };
          });

          let htmlNew = "";
          let htmlAdd = "";
          let htmlSub = "";
          let htmlDel = "";

          changes.forEach(r => {
            if(r.nature === "KEEP" || r.diff === 0) return;
            let badge = "";
            let dStyle = "";
            if(r.nature === "NEW") {
              badge = `<span class="badge-nature-new">🆕 新建持股</span>`;
              dStyle = "color:#ea580c;";
            } else if(r.nature === "UP") {
              badge = `<span class="badge-nature-up">🔺 經理人加碼</span>`;
              dStyle = "color:#dc2626;";
            } else if(r.nature === "DOWN") {
              badge = `<span class="badge-nature-down">🔻 經理人減碼</span>`;
              dStyle = "color:#0f766e;";
            } else if(r.nature === "DELETE") {
              badge = `<span class="badge-nature-delete">❌ 完整剔除</span>`;
              dStyle = "color:#4b5563;";
            }

            let trendStr = trendMap[r.stock] || "區間無連續動向";
            let trendHtml = `<span class="text-muted">首日首筆變動</span>`;
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
          
          let type = document.getElementById('rangeType').value;
          if (type === 'custom') {
            let sd = document.getElementById('startDateInput').value.trim();
            let ed = document.getElementById('endDateInput').value.trim();
            if(!sd || !ed) {
              alert("請完整輸入自訂的雙基準日期範圍(YYYY-MM-DD)！");
              return;
            }
            let idxO = dates.indexOf(sd);
            let idxN = dates.indexOf(ed);
            if(idxO < 0 || idxN < 0) {
              alert("您輸入的自訂日期在資料庫中找不到，請確認試算表中有此段日期記錄！");
              return;
            }
            calculateChipsDelta(etfData, dates, idxN, idxO);
          } else {
            let offset = parseInt(type);
            let idxN = dates.length - 1;
            let idxO = dates.length - 1 - offset;
            calculateChipsDelta(etfData, dates, idxN, idxO);
          }
        }

        function searchStockSuggestions(val, suggestId, inputId, isTaiwanOnly) {
          let sDiv = document.getElementById(suggestId);
          if(!val || val.trim() === "") {
            sDiv.style.display = 'none';
            return;
          }
          let query = val.trim().toLowerCase();
          let uniqueStocks = {};

          globalRawData.forEach(r => {
            if (r.stock && isTaiwanOnly && r.stock.includes(" US")) return;
            if (r.stock) {
              let name = r.name || tickerMappingData[r.stock] || "未知";
              if (isTaiwanOnly && !isNormalStock(r.stock, r.name)) return;
              if (r.stock.toLowerCase().includes(query) || name.toLowerCase().includes(query)) {
                uniqueStocks[r.stock] = name;
              }
            }
          });

          let keys = Object.keys(uniqueStocks);
          if (keys.length === 0) {
            sDiv.style.display = 'none';
            return;
          }

          sDiv.innerHTML = keys.slice(0, 10).map(k => {
            return `<div class="suggestion-item" onclick="selectStockSuggestion('${k}', '${uniqueStocks[k]}', '${suggestId}', '${inputId}', ${isTaiwanOnly})"><b>${k}</b> - ${uniqueStocks[k]}</div>`;
          }).join('');
          sDiv.style.display = 'block';
        }

        function selectStockSuggestion(code, name, suggestId, inputId, isTaiwanOnly) {
          document.getElementById(inputId).value = `${code} ${name}`;
          document.getElementById(suggestId).style.display = 'none';

          if (isTaiwanOnly) {
            let exists = selectedTargetStocks.some(x => x.code === code);
            if (!exists) {
              selectedTargetStocks.push({ code: code, name: name });
              renderSelectedTargets();
              runAiMatcherAnalysis();
            }
            document.getElementById(inputId).value = "";
          }
        }

        function renderSelectedTargets() {
          let container = document.getElementById('selectedTargetContainer');
          if (selectedTargetStocks.length === 0) {
            container.innerHTML = '<span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選組合</span>';
            return;
          }
          container.innerHTML = selectedTargetStocks.map((t, idx) => `
            <span class="selected-stock-tag font-monospace fw-bold">
              ${t.code} ${t.name}
              <i class="bi bi-x-circle-fill" onclick="removeTargetStock(${idx})"></i>
            </span>
          `).join('');
        }

        function removeTargetStock(index) {
          selectedTargetStocks.splice(index, 1);
          renderSelectedTargets();
          runAiMatcherAnalysis();
        }

        function runAiMatcherAnalysis() {
          let body = document.getElementById('matchResultBody');
          if(selectedTargetStocks.length === 0) {
            body.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-muted">請先在上方搜尋並點選加入欲觀測的個股目標組合。</td></tr>';
            return;
          }
          let etfSet = new Set();
          globalRawData.forEach(r => {
            if(r.etf) etfSet.add(r.etf);
          });

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
              res.push({
                etf: eCode,
                name: etfNameMappingData[eCode] || "未知名稱",
                totalWeight: totalWeight,
                details: details.join('')
              });
            }
          });

          res.sort((a,b) => b.totalWeight - a.totalWeight);
          body.innerHTML = res.map(x => `
            <tr>
              <td class="font-monospace fw-bold">${x.etf}</td>
              <td class="fw-bold text-secondary">${x.name}</td>
              <td class="text-end font-monospace text-primary fw-bold fs-5">${x.totalWeight.toFixed(2)}%</td>
              <td class="px-4">${x.details}</td>
            </tr>
          `).join('') || '<tr><td colspan="4" class="text-center py-4 text-warning fw-bold"><i class="bi bi-exclamation-triangle me-1"></i> 哎呀！全市場大數據回溯中，目前沒有任何一款 ETF 能同時完全重疊包辦這群目標公司所有組合。</td></tr>';
        }

        function searchStockDistribution() {
          let inputVal = document.getElementById('stockSearchInput').value.trim();
          if(!inputVal) {
            alert("請先輸入股票代號或名稱！");
            return;
          }
          let code = inputVal.split(' ')[0].trim();
          let sRow = globalRawData.find(r => r.stock === code);
          let sName = (sRow ? sRow.name : "") || tickerMappingData[code] || "未知股票";

          document.getElementById('resStockTitle').innerText = `${code} ${sName}`;
          document.getElementById('stockResultContainer').style.display = 'block';

          let etfSet = new Set();
          globalRawData.forEach(r => { if(r.etf) etfSet.add(r.etf); });

          let distRows = [];
          let weightRows = [];
          let totalDiff = 0;

          etfSet.forEach(eCode => {
            let etfData = globalRawData.filter(d => d.etf === eCode);
            let dates = [...new Set(etfData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            if(dates.length >= 2) {
              let latestDate = dates[dates.length - 1];
              let prevDate = dates[dates.length - 2];
              let rowL = etfData.find(d => d.date === latestDate && d.stock === code);
              let rowP = etfData.find(d => d.date === prevDate && d.stock === code);
              let vL = rowL ? Number(rowL.volume) : 0;
              let vP = rowP ? Number(rowP.volume) : 0;
              let diff = vL - vP;
              if (diff !== 0) {
                totalDiff += diff;
                distRows.push({ etf: eCode, name: etfNameMappingData[eCode] || "未知名稱", diff: diff });
              }
            }
            let latestDate = dates[dates.length - 1];
            let lRow = etfData.find(d => d.date === latestDate && d.stock === code);
            if (lRow) {
              weightRows.push({ eCode: eCode, name: etfNameMappingData[eCode] || "未知名稱", weight: Number(lRow.weight) });
            }
          });

          let trendStatusEl = document.getElementById('trendStockStatus');
          trendStatusEl.innerHTML = totalDiff > 0 ? `<span class="badge bg-danger">🔥 淨加碼</span>` : (totalDiff < 0 ? `<span class="badge bg-success">📉 淨減持</span>` : `<span class="badge bg-secondary">持平</span>`);
          
          let totalVolEl = document.getElementById('trendStockTotalVol');
          totalVolEl.innerText = `${totalDiff > 0 ? '+' : ''}${Math.round(totalDiff).toLocaleString()} 股`;
          totalVolEl.className = `fw-bold font-monospace mb-0 ${totalDiff > 0 ? 'text-danger' : 'text-success'}`;

          let changeHtml = distRows.sort((a,b)=>b.diff - a.diff).map(x => `
            <tr>
              <td><b>${x.etf}</b> <span class="text-muted small">${x.name}</span></td>
              <td class="text-end font-monospace fw-bold ${x.diff > 0 ? 'text-danger' : 'text-success'}">
                ${x.diff > 0 ? '+' : ''}${Math.round(x.diff).toLocaleString()} 股
              </td>
            </tr>`).join('');
          document.getElementById('stockDistBody').innerHTML = changeHtml || '<tr><td colspan="2" class="text-center text-muted">本區間各大基金經理人未對其進行任何增減部位異動</td></tr>';

          let weightHtml = weightRows.sort((a,b)=>b.weight - a.weight).map(x => `
            <tr>
              <td class="font-monospace fw-bold">${x.eCode}</td>
              <td class="fw-bold text-secondary">${x.name}</td>
              <td class="text-end font-monospace text-primary fw-bold">${x.weight.toFixed(2)}%</td>
            </tr>`).join('');
          document.getElementById('stockDistBody2').innerHTML = weightHtml || '<tr><td colspan="3" class="text-center text-muted">目前尚未有任何 ETF 包含此成分股</td></tr>';
        }

        function toggleGlobalCustomDates() {
          let type = document.getElementById('globalRangeType').value;
          document.getElementById('globalCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadGlobalDelta() {
          document.getElementById('loading').style.display = 'flex';
          setTimeout(() => {
            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            let type = document.getElementById('globalRangeType').value;
            let idxN = dates.length - 1;
            let idxO = dates.length - 2;

            if (type === 'custom') {
              let sd = document.getElementById('globalStartDateInput').value.trim();
              let ed = document.getElementById('globalEndDateInput').value.trim();
              idxO = dates.indexOf(sd);
              idxN = dates.indexOf(ed);
              if (idxO < 0 || idxN < 0) {
                alert("找不到指定的基準日期！");
                document.getElementById('loading').style.display = 'none';
                return;
              }
            } else {
              idxO = dates.length - 1 - parseInt(type);
            }

            let dateNew = dates[idxN];
            let dateOld = dates[idxO];

            let etfSet = new Set();
            globalRawData.forEach(r => { if(r.etf) etfSet.add(r.etf); });

            let addedMap = {};
            let deletedMap = {};

            etfSet.forEach(eCode => {
              let etfData = globalRawData.filter(d => d.etf === eCode);
              let rowsO = etfData.filter(d => d.date === dateOld);
              let rowsN = etfData.filter(d => d.date === dateNew);

              rowsN.forEach(n => {
                if(!isNormalStock(n.stock, n.name)) return;
                let oMatch = rowsO.find(o => o.stock === n.stock);
                if(!oMatch || Number(oMatch.volume) === 0) {
                  let sName = n.name || tickerMappingData[n.stock] || "未知名稱";
                  let k = n.stock + "||" + sName;
                  if(!addedMap[k]) addedMap[k] = [];
                  addedMap[k].push(eCode);
                }
              });

              rowsO.forEach(o => {
                if(!isNormalStock(o.stock, o.name)) return;
                let nMatch = rowsN.find(n => n.stock === o.stock);
                if(!nMatch || Number(nMatch.volume) === 0) {
                  let sName = o.name || tickerMappingData[o.stock] || "未知名稱";
                  let k = o.stock + "||" + sName;
                  if(!deletedMap[k]) deletedMap[k] = [];
                  deletedMap[k].push(eCode);
                }
              });
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
            document.getElementById('loading').style.display = 'none';
          }, 50);
        }

        function toggleHeatCustomDates() {
          let type = document.getElementById('heatRangeType').value;
          document.getElementById('heatCustomDateGroup').style.display = (type === 'custom') ? 'block' : 'none';
        }

        function loadMarketHeat() {
          document.getElementById('loading').style.display = 'flex';
          setTimeout(() => {
            let type = document.getElementById('heatRangeType').value;
            let stockMap = {};

            globalRawData.forEach(r => {
              if (r.stock && isNormalStock(r.stock, r.name)) {
                stockMap[r.stock] = r.name || tickerMappingData[r.stock] || "未知名稱";
              }
            });

            let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
            let idxN = dates.length - 1;
            let idxO = dates.length - 2;

            if (type === 'custom') {
              let sd = document.getElementById('heatStartDateInput').value.trim();
              let ed = document.getElementById('heatEndDateInput').value.trim();
              idxO = dates.indexOf(sd);
              idxN = dates.indexOf(ed);
              if (idxO < 0 || idxN < 0) {
                alert("找不到指定日期！");
                document.getElementById('loading').style.display = 'none';
                return;
              }
            } else {
              idxO = dates.length - 1 - parseInt(type);
            }

            let dateNew = dates[idxN];
            let dateOld = dates[idxO];

            let rawNew = globalRawData.filter(d => d.date === dateNew);
            let rawOld = globalRawData.filter(d => d.date === dateOld);

            let heatList = [];
            Object.keys(stockMap).forEach(sCode => {
              let sumNew = 0;
              let sumOld = 0;
              rawNew.forEach(r => { if(r.stock === sCode) sumNew += Number(r.volume); });
              rawOld.forEach(r => { if(r.stock === sCode) sumOld += Number(r.volume); });
              let diff = sumNew - sumOld;
              if (diff !== 0) {
                heatList.push({ code: sCode, name: stockMap[sCode], diff: diff });
              }
            });

            let topBuy = heatList.filter(x => x.diff > 0).sort((a,b)=>b.diff - a.diff).slice(0, 10);
            let topSell = heatList.filter(x => x.diff < 0).sort((a,b)=>a.diff - b.diff).slice(0, 10);

            let maxBuy = topBuy.length > 0 ? topBuy[0].diff : 1;
            let maxSell = topSell.length > 0 ? Math.abs(topSell[0].diff) : 1;

            document.getElementById('heatBuyBody').innerHTML = topBuy.map((x, i) => {
              let medalClass = i === 0 ? 'medal-1' : (i === 1 ? 'medal-2' : (i === 2 ? 'medal-3' : 'medal-other'));
              let medalContent = i < 3 ? `<i class="bi bi-trophy-fill"></i>` : (i + 1);
              let barWidth = maxBuy > 0 ? (Math.abs(x.diff) / maxBuy * 100) : 0;
              return `<tr>
                <td><span class="rank-medal ${medalClass}">${medalContent}</span></td>
                <td class="fw-bold">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                <td>
                  <div class="heat-progress-container">
                    <span class="font-monospace text-danger fw-bold">+${Math.round(x.diff).toLocaleString()} 股</span>
                    <div class="progress heat-bar-wrapper d-none d-md-flex" style="height: 6px;">
                      <div class="progress-bar bg-danger" role="progressbar" style="width: ${barWidth}%"></div>
                    </div>
                  </div>
                </td>
              </tr>`;
            }).join('') || '<tr><td colspan="3" class="text-center text-muted">無加碼數據</td></tr>';

            document.getElementById('heatSellBody').innerHTML = topSell.map((x, i) => {
              let medalClass = i === 0 ? 'medal-1' : (i === 1 ? 'medal-2' : (i === 2 ? 'medal-3' : 'medal-other'));
              let medalContent = i < 3 ? `<i class="bi bi-trophy-fill"></i>` : (i + 1);
              let barWidth = maxSell > 0 ? (Math.abs(x.diff) / maxSell * 100) : 0;
              return `<tr>
                <td><span class="rank-medal ${medalClass}">${medalContent}</span></td>
                <td class="fw-bold">${x.code} <span class="text-muted small fw-normal ms-1">${x.name}</span></td>
                <td>
                  <div class="heat-progress-container">
                    <span class="font-monospace text-success fw-bold">${Math.round(x.diff).toLocaleString()} 股</span>
                    <div class="progress heat-bar-wrapper d-none d-md-flex" style="height: 6px;">
                      <div class="progress-bar bg-success" role="progressbar" style="width: ${barWidth}%"></div>
                    </div>
                  </div>
                </td>
              </tr>`;
            }).join('') || '<tr><td colspan="3" class="text-center text-muted">無減持數據</td></tr>';

            document.getElementById('loading').style.display = 'none';
          }, 50);
        }

        function runEtfComparison() {
          let checkedCbs = Array.from(document.querySelectorAll('.compare-cb:checked')).map(cb => cb.value);
          let summarySection = document.getElementById('compareSummarySection');
          let summaryCards = document.getElementById('compareSummaryCards');
          let coreCard = document.getElementById('coreHoldingsCard');
          let uniqueCard = document.getElementById('uniqueHoldingsCard');

          if (checkedCbs.length === 0) {
            summarySection.style.display = 'none';
            coreCard.style.display = 'none';
            uniqueCard.style.display = 'none';
            return;
          }

          let allCheckedStockSet = new Set();
          let stockAnalysis = [];

          let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
          let latestDate = dates[dates.length - 1];

          checkedCbs.forEach(eCode => {
            let etfData = globalRawData.filter(d => d.etf === eCode && d.date === latestDate);
            etfData.forEach(r => {
              if (isNormalStock(r.stock, r.name)) {
                allCheckedStockSet.add(r.stock);
              }
            });
          });

          allCheckedStockSet.forEach(sCode => {
            let matchCount = 0;
            let totalWeightAcross = 0;
            let weightsByEtf = {};
            let sName = "";

            checkedCbs.forEach(eCode => {
              let match = globalRawData.find(d => d.etf === eCode && d.date === latestDate && d.stock === sCode);
              if (match) {
                matchCount++;
                totalWeightAcross += Number(match.weight);
                weightsByEtf[eCode] = Number(match.weight);
                if (!sName) sName = match.name || tickerMappingData[sCode];
              } else {
                weightsByEtf[eCode] = 0;
              }
            });

            stockAnalysis.push({
              code: sCode,
              name: sName || "未知名稱",
              heldByCount: matchCount,
              totalWeightAcross: totalWeightAcross,
              weights: weightsByEtf
            });
          });

          stockAnalysis.sort((a,b) => {
            if(b.heldByCount !== a.heldByCount) return b.heldByCount - a.heldByCount;
            return b.totalWeightAcross - a.totalWeightAcross;
          });

          let overlapStocks = stockAnalysis.filter(x => x.heldByCount > (checkedCbs.length > 1 ? 1 : 0));
          let top3 = overlapStocks.slice(0, 3);
          if(top3.length === 0 && stockAnalysis.length > 0) {
            top3 = stockAnalysis.slice(0, 3);
          }

          if(top3.length > 0) {
            summarySection.style.display = 'block';
            summaryCards.innerHTML = top3.map((x, idx) => {
              return `<div class="col-md-4">
                <div class="summary-card">
                  <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="badge bg-primary rounded-pill font-monospace">TOP ${idx + 1} 重疊焦點</span>
                    <span class="badge bg-light text-primary border">${x.heldByCount} 檔共同持有</span>
                  </div>
                  <h4 class="fw-bold mb-1">${x.code}</h4>
                  <div class="text-secondary fw-bold mb-3">${x.name}</div>
                  <div class="small text-muted">各基金持有佔比總加: <b class="text-dark">${x.totalWeightAcross.toFixed(2)}%</b></div>
                </div>
              </div>`;
            }).join('');
          } else {
            summarySection.style.display = 'none';
          }

          let thHtml = `<tr><th rowspan="2" class="align-middle">成分股票代碼</th><th rowspan="2" class="align-middle">股票名稱</th>`;
          checkedCbs.forEach(eCode => {
            thHtml += `<th class="font-monospace">${eCode}</th>`;
          });
          thHtml += `</tr><tr>`;
          checkedCbs.forEach(eCode => {
            let mappedName = etfNameMappingData[eCode] || "未知";
            thHtml += `<th class="text-secondary small fw-normal py-1">${mappedName}</th>`;
          });
          thHtml += `</tr>`;

          document.getElementById('compareCoreTableHeader').innerHTML = thHtml;
          document.getElementById('compareUniqueTableHeader').innerHTML = thHtml;

          let coreRowsHtml = "";
          let uniqueRowsHtml = "";

          stockAnalysis.forEach(x => {
            let isFullCore = (x.heldByCount === checkedCbs.length);
            let rowHtml = `<td class="font-monospace fw-bold">${x.code}</td><td class="fw-bold text-secondary">${x.name}</td>`;
            
            checkedCbs.forEach(eCode => {
              let w = x.weights[eCode] || 0;
              let displayVal = w > 0 ? `${w.toFixed(2)}%` : "—";
              let cellClass = "weight-none";
              if (w >= 5) {
                cellClass = "weight-high";
              } else if (w >= 1.5) {
                cellClass = "weight-med";
              } else if (w > 0) {
                cellClass = "weight-low";
              }
              rowHtml += `<td class="text-end font-monospace ${cellClass}">${displayVal}</td>`;
            });
            rowHtml = `<tr>${rowHtml}</tr>`;
            
            if(isFullCore) { coreRowsHtml += rowHtml; } 
            else { uniqueRowsHtml += rowHtml; }
          });

          coreCard.style.display = 'block';
          document.getElementById('compareCoreTableBody').innerHTML = coreRowsHtml || `<tr><td colspan="${2 + checkedCbs.length}" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 所選定的 ETF 組合之間目前無任何完全重疊的核心持股</td></tr>`;
          
          uniqueCard.style.display = 'block';
          document.getElementById('compareUniqueTableBody').innerHTML = uniqueRowsHtml || `<tr><td colspan="${2 + checkedCbs.length}" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 無個別差異特色持股</td></tr>`;
        }

        // ==========================================
        // 🔮 力導向星系圖 ECharts 圖表生成邏輯 (熱高亮、彈窗、動態交互)
        // ==========================================
        function renderGalaxyChart() {
          let chartDom = document.getElementById('galaxyChart');
          if (!chartDom) return;
          if (!galaxyChartInstance) {
            galaxyChartInstance = echarts.init(chartDom);
          }

          let checkedEtfs = Array.from(document.querySelectorAll('.galaxy-cb:checked')).map(cb => cb.value);
          if (checkedEtfs.length === 0) {
            galaxyChartInstance.clear();
            galaxyChartInstance.setOption({
              title: {
                text: "請勾選上方至少一檔 ETF 開始觀測金融拓撲星系",
                left: "center",
                top: "center",
                textStyle: { color: "#999", fontSize: 16 }
              }
            });
            return;
          }

          let dates = [...new Set(globalRawData.map(d => d.date))].sort((a,b) => new Date(a) - new Date(b));
          let latestDate = dates[dates.length - 1];

          let nodesMap = {};
          let links = [];
          let filterMode = document.getElementById('galaxyFilterMode').value;

          checkedEtfs.forEach(eCode => {
            let mappedName = etfNameMappingData[eCode] || "未知基金";
            nodesMap[eCode] = {
              id: eCode,
              name: eCode,
              value: mappedName,
              symbolSize: 45,
              isEtf: true,
              itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [
                  { offset: 0, color: '#1e3c72' },
                  { offset: 1, color: '#2a5298' }
                ]),
                borderColor: '#fff',
                borderWidth: 2,
                shadowBlur: 12,
                shadowColor: 'rgba(30, 60, 114, 0.6)'
              },
              label: {
                show: true,
                position: 'inside',
                color: '#ffffff',
                fontWeight: 'bold',
                fontSize: 12,
                formatter: '{b}'
              }
            };
          });

          checkedEtfs.forEach(eCode => {
            let etfData = globalRawData.filter(d => d.etf === eCode && d.date === latestDate);
            let normalStocks = etfData.filter(r => isNormalStock(r.stock, r.name));

            normalStocks.sort((a, b) => Number(b.weight) - Number(a.weight));

            let targetStocks = [];
            if (filterMode === 'top20') {
              targetStocks = normalStocks.slice(0, 20);
            } else if (filterMode === 'weight5') {
              targetStocks = normalStocks.filter(r => Number(r.weight) >= 5);
            }

            targetStocks.forEach(r => {
              let sCode = r.stock;
              let sName = r.name || tickerMappingData[sCode] || "未知股票";
              let weightVal = Number(r.weight);

              if (!nodesMap[sCode]) {
                nodesMap[sCode] = {
                  id: sCode,
                  name: sCode,
                  value: sName,
                  symbolSize: 18,
                  isEtf: false,
                  itemStyle: {
                    color: '#e2e8f0',
                    borderColor: '#94a3b8',
                    borderWidth: 1.5
                  },
                  label: {
                    show: false
                  }
                };
              }

              links.push({
                source: eCode,
                target: sCode,
                weightValue: weightVal,
                lineStyle: {
                  width: Math.min(Math.max(weightVal * 0.8, 1), 12),
                  color: 'rgba(148, 163, 184, 0.45)',
                  curveness: 0.1
                }
              });
            });
          });

          let nodesArray = Object.values(nodesMap);

          let option = {
            tooltip: {
              trigger: 'item',
              backgroundColor: 'rgba(255, 255, 255, 0.95)',
              borderColor: '#e2e8f0',
              borderWidth: 1,
              textStyle: { color: '#333' },
              formatter: function (params) {
                if (params.dataType === 'node') {
                  if (params.data.isEtf) {
                    return `<div class="p-1"><b>基金核心：${params.data.name}</b><br><span class="text-muted small">${params.data.value}</span></div>`;
                  } else {
                    return `<div class="p-1"><b>成分股：${params.data.name}</b><br><span class="text-muted small">${params.data.value}</span></div>`;
                  }
                } else if (params.dataType === 'edge') {
                  return `<div class="p-1">拓撲連線關係：<br><b>${params.data.source}</b> ➔ <b>${params.data.target}</b><br>持股權重：<span class="text-primary fw-bold">${params.data.weightValue.toFixed(2)}%</span></div>`;
                }
              }
            },
            series: [{
              type: 'graph',
              layout: 'force',
              data: nodesArray,
              links: links,
              roam: true,
              draggable: true,
              emphasis: {
                focus: 'adjacency',
                label: {
                  show: true,
                  position: 'right',
                  formatter: function(p) {
                    return p.data.isEtf ? p.data.name : `${p.data.name} ${p.data.value}`;
                  },
                  color: '#1a202c',
                  backgroundColor: '#ffffff',
                  padding: [4, 8],
                  borderRadius: 4,
                  shadowBlur: 4,
                  shadowColor: 'rgba(0,0,0,0.15)'
                },
                lineStyle: {
                  width: 6,
                  opacity: 1
                }
              },
              force: {
                repulsion: 220,
                edgeLength: [60, 150],
                gravity: 0.12
              },
              lineStyle: {
                opacity: 0.6,
                curveness: 0.1
              }
            }]
          };

          galaxyChartInstance.setOption(option);

          // 👑 加入點擊交互事件
          galaxyChartInstance.off('click');
          galaxyChartInstance.on('click', function(params) {
            if (params.dataType === 'node') {
              if (params.data.isEtf) {
                // 如果點擊核心 ETF 節點，直接切換到該 ETF 的籌碼頁面並載入
                switchTab('content-a', 'tab-a');
                selectEtf(params.data.name);
              } else {
                // 如果點擊外圍成分股，切換到個股分佈頁並直接分析該股
                switchTab('content-b', 'tab-b');
                document.getElementById('stockSearchInput').value = `${params.data.name} ${params.data.value}`;
                searchStockDistribution();
              }
            }
          });
        }

        window.onload = function() {
          initDashboard();
          loadGlobalDelta();
          loadMarketHeat();
        };

        window.onresize = function() {
          if (galaxyChartInstance) {
            galaxyChartInstance.resize();
          }
        };
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
        "__ETF_PLACEHOLDER__", etf_name_json
    )

    components.html(final_html, height=1350, scrolling=True)

if __name__ == "__main__":
    main()
