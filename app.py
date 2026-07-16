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
    if not creds_json:
        return None
    try:
        info = json.loads(creds_json)
        gc = gspread.service_account_from_dict(info)
        return gc
    except Exception as e:
        st.error(f"憑證解析失敗: {str(e)}")
        return None

# 初始化試算表連線
gc = get_sheets_client()
sh = None
if gc:
    try:
        sh = gc.open(SHEET_NAME)
    except Exception as e:
        st.error(f"無法開啟試算表「{SHEET_NAME}」: {str(e)}")

@st.cache_data(ttl=300)
def fetch_raw_sheet_data():
    if not sh: 
        return None, "無法連線至 Google 試算表，請檢查憑證設定。"
    try:
        ws = sh.worksheet(WORKSHEET_HISTORY)
        # 原始安全方法：讀取所有儲存格二維矩陣，規避標題列重複造成 gspread 崩潰的問題
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return None, f"工作表「{WORKSHEET_HISTORY}」內沒有足夠的數據列。"
        return raw_data, None
    except Exception as e:
        return None, f"讀取工作表「{WORKSHEET_HISTORY}」失敗: {str(e)}"

@st.cache_data(ttl=3600)
def fetch_ticker_map():
    if not sh:
        return {}
    try:
        ws = sh.worksheet(WORKSHEET_TICKER)
        # 安全讀取：改用 get_all_values() 並在記憶體中解析對照表，防止重複/空白標題阻擋
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return {}
            
        # 清洗與過濾空白欄位
        raw_headers = [str(h).strip() for h in raw_data[0]]
        valid_indices = [i for i, h in enumerate(raw_headers) if h != ""]
        
        col_ticker_idx = -1
        col_name_idx = -1
        for idx in valid_indices:
            h = raw_headers[idx]
            if "代號" in h or "Ticker" in h or "Symbol" in h:
                col_ticker_idx = idx
            elif "名稱" in h or "Name" in h:
                col_name_idx = idx
        
        # 若找不到明確標題，預設前兩欄
        if col_ticker_idx == -1 or col_name_idx == -1:
            col_ticker_idx = 0 if len(raw_headers) > 0 else -1
            col_name_idx = 1 if len(raw_headers) > 1 else -1
            
        if col_ticker_idx == -1 or col_name_idx == -1:
            return {}
            
        ticker_map = {}
        for row in raw_data[1:]:
            padded_row = row + [""] * (max(col_ticker_idx, col_name_idx) + 1 - len(row))
            ticker = str(padded_row[col_ticker_idx]).strip()
            name = str(padded_row[col_name_idx]).strip()
            if ticker:
                ticker_map[ticker] = name
        return ticker_map
    except Exception as e:
        st.warning(f"載入個股代號對照表失敗: {str(e)}")
        return {}

@st.cache_data(ttl=3600)
def fetch_etf_name_map():
    if not sh:
        return {}
    try:
        ws = sh.worksheet(WORKSHEET_ETF_NAME)
        # 安全讀取：改用 get_all_values() 並在記憶體中解析對照表，防止重複/空白標題阻擋
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return {}
            
        # 清洗與過濾空白欄位
        raw_headers = [str(h).strip() for h in raw_data[0]]
        valid_indices = [i for i, h in enumerate(raw_headers) if h != ""]
        
        col_etf_idx = -1
        col_name_idx = -1
        for idx in valid_indices:
            h = raw_headers[idx]
            if "代號" in h or "ETF" in h:
                col_etf_idx = idx
            elif "名稱" in h or "Name" in h:
                col_name_idx = idx
                
        # 若找不到明確標題，預設前兩欄
        if col_etf_idx == -1 or col_name_idx == -1:
            col_etf_idx = 0 if len(raw_headers) > 0 else -1
            col_name_idx = 1 if len(raw_headers) > 1 else -1
            
        if col_etf_idx == -1 or col_name_idx == -1:
            return {}
            
        etf_name_map = {}
        for row in raw_data[1:]:
            padded_row = row + [""] * (max(col_etf_idx, col_name_idx) + 1 - len(row))
            etf = str(padded_row[col_etf_idx]).strip()
            name = str(padded_row[col_name_idx]).strip()
            if etf:
                etf_name_map[etf] = name
        return etf_name_map
    except Exception as e:
        st.warning(f"載入 ETF 名稱對照表失敗: {str(e)}")
        return {}

def process_and_standardize(raw_data, ticker_map=None):
    # ---------------- 🛡️ 安全過濾重複空白標題防禦機制 ----------------
    # 1. 取得原始第一列標題，並進行去首尾空白處理
    raw_headers = [str(h).strip() for h in raw_data[0]]
    
    # 2. 僅篩選出「非空字串」的欄位索引。這能完美過濾右側所有的無效空白欄位！
    valid_indices = [i for i, h in enumerate(raw_headers) if h != ""]
    
    # 3. 取得過濾後的乾淨標題
    clean_headers = [raw_headers[i] for i in valid_indices]
    
    # 4. 僅抽取對應有效欄位索引的資料（若某列長度不足，補齊空字串防止 Index 溢出）
    clean_rows = []
    for row in raw_data[1:]:
        padded_row = row + [""] * (len(raw_headers) - len(row))
        clean_row = [padded_row[i] for i in valid_indices]
        clean_rows.append(clean_row)
    
    # 5. 安全轉換為 DataFrame，欄位名稱現在絕對是乾淨且不重複的
    df = pd.DataFrame(clean_rows, columns=clean_headers)
    # ---------------------------------------------------------------
    
    # 標準欄位名稱對照表 (Alias)
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
    
    # 驗證必要欄位是否齊全
    missing = [k for k in ["etf", "date", "stock", "weight", "volume"] if k not in df.columns]
    if missing:
        return pd.DataFrame(), f"主要欄位對照失敗。缺少對應: {missing}"

    # 清洗與格式轉換
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.dropna(subset=['date'])
    
    # 清理 weight 並處理百分比符號
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','', regex=False).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    # 判定美股或台股（美股若純英文自動加上 ' US'）
    is_pure_english = df['stock'].str.match(r'^[A-Za-z]+$')
    df.loc[is_pure_english, 'stock'] = df.loc[is_pure_english, 'stock'] + ' US'
    
    if 'name' not in df.columns:
        df['name'] = ""
    
    # 帶入代號名稱對照表
    if ticker_map:
        mapped_series = df['stock'].map(ticker_map)
        df['name'] = mapped_series.fillna(df['name']).astype(str).str.strip()
    else:
        df['name'] = df['name'].astype(str).str.strip()
        
    return df, None

# ==========================================
# 3. 玩股網與三大法人籌碼爬蟲
# ==========================================
@st.cache_data(ttl=1800)
def fetch_wantgoo_chips():
    try:
        url = "https://www.wantgoo.com/investor/institutional-investors/net-buy-sell-shares"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.wantgoo.com/"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return []
        
        # 爬取網頁內的 script 變數
        html = res.text
        pattern = r"var\s+data\s*=\s*(\[.*?\]);"
        match = re.search(pattern, html, re.DOTALL)
        if match:
            json_str = match.group(1)
            # 清理可能存在的 JS 注解或非標準語法
            json_str = re.sub(r'//.*?\n', '', json_str)
            data = json.loads(json_str)
            return data
    except Exception as e:
        pass
    return []

@st.cache_data(ttl=1800)
def fetch_twse_chips():
    try:
        # 串接證交所三大法人買賣超排行 API
        url = "https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date=&selectType=ALL"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if "data" in data:
                return data["data"]
    except Exception as e:
        pass
    return []

# ==========================================
# 4. 主程式流程與 HTML/JS 面板繪製
# ==========================================
raw_data, err = fetch_raw_sheet_data()

if err:
    st.error(err)
    st.info("💡 解決方案：請確認試算表名稱是否為 'ETF daily'，且工作表名稱是否為 'ETF History'，並確認您設定的憑證金鑰擁有其檢視與編輯權限。")
else:
    ticker_map = fetch_ticker_map()
    etf_name_map = fetch_etf_name_map()
    df, process_err = process_and_standardize(raw_data, ticker_map)
    
    if process_err:
        st.error(process_err)
    else:
        # 將轉換好的數據以及額外的爬蟲數據傳遞給前端 HTML
        json_data = df.to_json(orient="records", force_ascii=False)
        wantgoo_data = fetch_wantgoo_chips()
        twse_data = fetch_twse_chips()
        
        wantgoo_json = json.dumps(wantgoo_data, ensure_ascii=False)
        twse_json = json.dumps(twse_data, ensure_ascii=False)
        ticker_json = json.dumps(ticker_map, ensure_ascii=False)
        etf_name_json = json.dumps(etf_name_map, ensure_ascii=False)

        # ---------------- HTML Dashboard 模板 ----------------
        html_template = """<!DOCTYPE html>
        <html lang="zh-TW">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>ETF 籌碼監控面版</title>
          <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
          <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
          <script src="https://cdn.tailwindcss.com"></script>
          <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
          <style>
            body { background-color: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
            .card { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
            .table { color: #f8fafc !important; }
            .table th { background-color: #334155 !important; color: #94a3b8 !important; border-bottom: 2px solid #475569 !important; }
            .table td { border-bottom: 1px solid #334155 !important; background-color: transparent !important; color: #f8fafc !important; }
            .table-striped tbody tr:nth-of-type(odd) td { background-color: #1b2537 !important; }
            .nav-tabs .nav-link { color: #94a3b8; border: none; font-weight: 500; }
            .nav-tabs .nav-link.active { color: #38bdf8; background-color: #1e293b; border-bottom: 3px solid #38bdf8; }
            .weight-high { color: #ef4444 !important; font-weight: bold; }
            .weight-mid { color: #f97316 !important; }
            .weight-low { color: #3b82f6 !important; }
            .text-green { color: #22c55e !important; }
            .text-red { color: #ef4444 !important; }
          </style>
        </head>
        <body class="p-4">
          <div class="container-fluid">
            <div class="d-flex justify-content-between align-items-center mb-4 pb-3 border-bottom border-slate-700">
              <div>
                <h1 class="text-3xl font-extrabold text-sky-400 d-flex align-items-center">
                  <i class="bi bi-cpu-fill me-2"></i> ETF 籌碼大數據監控面板
                </h1>
                <p class="text-slate-400 text-sm mt-1">智慧解析 Google 試算表底層大數據，動態分析成分股持倉重疊、權重位移與極端籌碼變化</p>
              </div>
              <div class="text-end">
                <span id="dataTimeSpan" class="badge bg-slate-800 text-slate-300 border border-slate-700 p-2">
                  <i class="bi bi-clock-history me-1"></i> 資料加載中...
                </span>
              </div>
            </div>

            <div class="card p-3 mb-4">
              <div class="row g-3 align-items-center">
                <div class="col-md-3">
                  <label class="form-label text-slate-400 text-xs fw-bold"><i class="bi bi-funnel"></i> 選擇基準 ETF (主成分)</label>
                  <select id="mainEtfSelect" class="form-select bg-slate-800 border-slate-700 text-white" onchange="onFilterChange()"></select>
                </div>
                <div class="col-md-3">
                  <label class="form-label text-slate-400 text-xs fw-bold"><i class="bi bi-calendar3"></i> 選擇比較日期</label>
                  <select id="dateSelect" class="form-select bg-slate-800 border-slate-700 text-white" onchange="onFilterChange()"></select>
                </div>
                <div class="col-md-6 d-flex align-items-end justify-content-end">
                  <div class="btn-group gap-2">
                    <button class="btn btn-outline-sky border-sky-500 text-sky-400 hover:bg-sky-500 hover:text-white transition" onclick="resetFilters()">
                      <i class="bi bi-arrow-counterclockwise me-1"></i> 重設篩選
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <ul class="nav nav-tabs mb-4" id="dashboardTabs" role="tablist">
              <li class="nav-item">
                <button class="nav-link active" id="overview-tab" data-bs-toggle="tab" data-bs-target="#overview" type="button"><i class="bi bi-grid-1x2-fill me-1"></i> 持股權重分析</button>
              </li>
              <li class="nav-item">
                <button class="nav-link" id="compare-tab" data-bs-toggle="tab" data-bs-target="#compare" type="button"><i class="bi bi-columns-gap me-1"></i> 跨 ETF 重疊分析</button>
              </li>
              <li class="nav-item">
                <button class="nav-link" id="market-tab" data-bs-toggle="tab" data-bs-target="#market" type="button"><i class="bi bi-activity me-1"></i> 外圍大盤及法人指標</button>
              </li>
            </ul>

            <div class="tab-content" id="dashboardTabsContent">
              <div class="tab-pane fade show active" id="overview" role="tabpanel">
                <div class="row g-4">
                  <div class="col-lg-7">
                    <div class="card p-4 h-100">
                      <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="text-lg font-bold text-slate-200 d-flex align-items-center">
                          <i class="bi bi-list-ol me-2 text-sky-500"></i> <span id="tableTitle">ETF 成分持股名單</span>
                        </h5>
                        <div class="text-sm text-slate-400">顯示前 25 大持股</div>
                      </div>
                      <div class="table-responsive">
                        <table class="table table-striped table-hover mb-0 align-middle">
                          <thead>
                            <tr>
                              <th>排行</th>
                              <th>成分股代號</th>
                              <th>成分股名稱</th>
                              <th class="text-end">權重 (%)</th>
                              <th class="text-end">持有股數</th>
                            </tr>
                          </thead>
                          <tbody id="etfTableBody">
                            <tr><td colspan="5" class="text-center py-5 text-slate-400">請選取 ETF 與日期載入資料...</td></tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>

                  <div class="col-lg-5">
                    <div class="card p-4 h-100">
                      <h5 class="text-lg font-bold text-slate-200 mb-3 d-flex align-items-center">
                        <i class="bi bi-pie-chart-fill me-2 text-sky-500"></i> 前十大持股佔比圖
                      </h5>
                      <div class="flex justify-center items-center h-80">
                        <canvas id="weightPieChart"></canvas>
                      </div>
                      <div class="mt-4 border-t border-slate-700 pt-3">
                        <div class="d-flex justify-content-between mb-2">
                          <span class="text-slate-400">成分股加總總數:</span>
                          <span id="totalStocksSpan" class="fw-bold text-white">-</span>
                        </div>
                        <div class="d-flex justify-content-between">
                          <span class="text-slate-400">前 10 大權重合計:</span>
                          <span id="top10WeightSpan" class="fw-bold text-sky-400">-</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="tab-pane fade" id="compare" role="tabpanel">
                <div class="card p-4">
                  <h5 class="text-lg font-bold text-slate-200 mb-3"><i class="bi bi-search me-1"></i> 多重 ETF 成分股交叉比對系統</h5>
                  <p class="text-slate-400 text-sm mb-4">勾選以下複數個 ETF，系統將比對出在當前基準日「同時持股」的重複核心成分股與獨佔特色股。</p>
                  
                  <div class="mb-4">
                    <label class="form-label text-slate-400 text-xs fw-bold">1. 請選擇要共同交叉比對的 ETF 列表</label>
                    <div id="compareEtfCheckboxList" class="flex flex-wrap gap-3 mt-2"></div>
                  </div>

                  <div class="row g-4 mt-2">
                    <div id="compareCoreCard" class="col-md-6" style="display:none;">
                      <div class="card p-3 border-danger">
                        <h6 class="text-base font-bold text-rose-400 mb-3 d-flex align-items-center">
                          <i class="bi bi-bookmark-star-fill me-1"></i> 100% 共同重疊成分股
                        </h6>
                        <div class="table-responsive">
                          <table class="table align-middle">
                            <thead>
                              <tr id="compareCoreHeader"></tr>
                            </thead>
                            <tbody id="compareCoreTableBody"></tbody>
                          </table>
                        </div>
                      </div>
                    </div>

                    <div id="compareUniqueCard" class="col-md-6" style="display:none;">
                      <div class="card p-3 border-sky-900">
                        <h6 class="text-base font-bold text-sky-400 mb-3 d-flex align-items-center">
                          <i class="bi bi-bullseye me-1"></i> 各別差異特色股
                        </h6>
                        <div class="table-responsive">
                          <table class="table align-middle">
                            <thead>
                              <tr id="compareUniqueHeader"></tr>
                            </thead>
                            <tbody id="compareUniqueTableBody"></tbody>
                          </table>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div class="tab-pane fade" id="market" role="tabpanel">
                <div class="row g-4">
                  <div class="col-md-6">
                    <div class="card p-4">
                      <h5 class="text-lg font-bold text-slate-200 mb-3"><i class="bi bi-lightning-fill text-warning"></i> 玩股網籌碼主力雷達</h5>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead>
                            <tr>
                              <th>名稱</th>
                              <th class="text-end">累計買賣</th>
                              <th class="text-end">增減比例</th>
                            </tr>
                          </thead>
                          <tbody id="wantgooTableBody">
                            <tr><td colspan="3" class="text-center text-slate-400 py-4">無近期主力數據或正在加載中...</td></tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                  
                  <div class="col-md-6">
                    <div class="card p-4">
                      <h5 class="text-lg font-bold text-slate-200 mb-3"><i class="bi bi-bank2 text-sky-400"></i> 三大法人排行異動 (TWSE)</h5>
                      <div class="table-responsive">
                        <table class="table align-middle">
                          <thead>
                            <tr>
                              <th>代號</th>
                              <th>名稱</th>
                              <th class="text-end">外資買賣超</th>
                              <th class="text-end">投信買賣超</th>
                            </tr>
                          </thead>
                          <tbody id="twseTableBody">
                            <tr><td colspan="4" class="text-center text-slate-400 py-4">證交所法人統計目前未開市或尚未加載...</td></tr>
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <script>
            // Streamlit 傳遞進來的 JSON 資料
            const rawData = __DATA_PLACEHOLDER__;
            const wantgooRaw = __WANTGOO_PLACEHOLDER__;
            const twseRaw = __TWSE_PLACEHOLDER__;
            const tickerMap = __TICKER_PLACEHOLDER__;
            const etfNameMap = __ETF_NAME_PLACEHOLDER__;

            let myChart = null;

            window.onload = function() {
                initSelectors();
                renderWantgoo();
                renderTwse();
                onFilterChange();
            };

            function initSelectors() {
                // 1. 取得不重複的 ETF 與 日期
                const etfs = [...new Set(rawData.map(d => d.etf))].sort();
                const dates = [...new Set(rawData.map(d => d.date))].sort().reverse();

                // 2. 渲染下拉選單
                const mainSelect = document.getElementById('mainEtfSelect');
                mainSelect.innerHTML = etfs.map(e => `<option value="${e}">${e} ${etfNameMap[e] || ''}</option>`).join('');

                const dateSelect = document.getElementById('dateSelect');
                dateSelect.innerHTML = dates.map(d => `<option value="${d}">${d}</option>`).join('');
                
                if(dates.length > 0) {
                    document.getElementById('dataTimeSpan').innerHTML = `<i class="bi bi-clock-history me-1"></i> 最新數據庫基準日: ${dates[0]}`;
                }

                // 3. 跨 ETF 比對複選清單
                const checkList = document.getElementById('compareEtfCheckboxList');
                checkList.innerHTML = etfs.map(e => `
                    <div class="form-check form-check-inline bg-slate-800 border border-slate-700 px-3 py-2 rounded">
                      <input class="form-check-input" type="checkbox" id="cb_${e}" value="${e}" onchange="onCompareChange()">
                      <label class="form-check-label text-slate-200 cursor-pointer" for="cb_${e}">${e} <small class="text-slate-400">${etfNameMap[e] || ''}</small></label>
                    </div>
                `).join('');
            }

            function onFilterChange() {
                const selectedEtf = document.getElementById('mainEtfSelect').value;
                const selectedDate = document.getElementById('dateSelect').value;
                
                // 過濾出指定 ETF 與日期的資料
                let filtered = rawData.filter(d => d.etf === selectedEtf && d.date === selectedDate);
                // 排序 (由大到小)
                filtered.sort((a, b) => b.weight - a.weight);

                // 更新頁面
                document.getElementById('tableTitle').innerText = `${selectedEtf} 在 ${selectedDate} 的最新成分權重`;
                renderTable(filtered);
                renderChart(filtered);
                
                // 計算總持股與前10持股
                document.getElementById('totalStocksSpan').innerText = filtered.length + " 檔標的";
                const top10Sum = filtered.slice(0, 10).reduce((sum, d) => sum + d.weight, 0);
                document.getElementById('top10WeightSpan').innerText = top10Sum.toFixed(2) + "%";
            }

            function renderTable(data) {
                const tbody = document.getElementById('etfTableBody');
                if(data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="text-center py-5 text-slate-400">目前所選的篩選區間尚無數據資料</td></tr>';
                    return;
                }

                // 顯示前 25 大持股
                const showData = data.slice(0, 25);
                tbody.innerHTML = showData.map((d, index) => {
                    let weightClass = "weight-low";
                    if(d.weight >= 5.0) { weightClass = "weight-high"; }
                    else if(d.weight >= 2.0) { weightClass = "weight-mid"; }

                    // 本地格式化
                    const formattedVolume = Number(d.volume).toLocaleString(undefined, {maximumFractionDigits: 0});
                    
                    return `
                        <tr>
                          <td><span class="badge ${index < 3 ? 'bg-sky-950 text-sky-400 border border-sky-800' : 'bg-slate-800 text-slate-400'}">${index + 1}</span></td>
                          <td class="font-monospace">${d.stock}</td>
                          <td>${d.name || tickerMap[d.stock] || '<span class="text-slate-500">未指定個股</span>'}</td>
                          <td class="text-end font-monospace ${weightClass}">${d.weight.toFixed(2)}%</td>
                          <td class="text-end font-monospace text-slate-300">${formattedVolume}</td>
                        </tr>
                    `;
                }).join('');
            }

            function renderChart(data) {
                const ctx = document.getElementById('weightPieChart').getContext('2d');
                const top10 = data.slice(0, 10);
                const othersSum = data.slice(10).reduce((sum, d) => sum + d.weight, 0);

                const labels = top10.map(d => d.name || d.stock);
                const weights = top10.map(d => d.weight);
                
                if(othersSum > 0) {
                    labels.push("其他");
                    weights.push(othersSum);
                }

                if(myChart) {
                    myChart.destroy();
                }

                myChart = new Chart(ctx, {
                    type: 'doughnut',
                    data: {
                        labels: labels,
                        datasets: [{
                            data: weights,
                            backgroundColor: [
                                '#38bdf8', '#0ea5e9', '#0284c7', '#0369a1', '#075985',
                                '#0f172a', '#1e293b', '#334155', '#475569', '#64748b', '#94a3b8'
                            ],
                            borderWidth: 1,
                            borderColor: '#1e293b'
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                position: 'right',
                                labels: { color: '#f8fafc', boxWidth: 12 }
                            }
                        }
                    }
                });
            }

            function renderWantgoo() {
                const tbody = document.getElementById('wantgooTableBody');
                if(!wantgooRaw || wantgooRaw.length === 0) return;
                
                tbody.innerHTML = wantgooRaw.slice(0, 15).map(item => {
                    const value = item.NetVolume || item.netBuySell || 0;
                    const valClass = value >= 0 ? "text-green" : "text-red";
                    const formatted = (value).toLocaleString();
                    return `
                        <tr>
                          <td>${item.Name || item.stockName || '-'}</td>
                          <td class="text-end font-monospace ${valClass}">${formatted}</td>
                          <td class="text-end font-monospace">${item.Ratio || item.ratio || '-'}%</td>
                        </tr>
                    `;
                }).join('');
            }

            function renderTwse() {
                const tbody = document.getElementById('twseTableBody');
                if(!twseRaw || twseRaw.length === 0) return;

                tbody.innerHTML = twseRaw.slice(0, 15).map(item => {
                    // item 格式通常為 array [排名, 證券代號, 證券名稱, 買進股數, 賣出股數, 買賣超股數, 投信...]
                    const code = item[1] || '-';
                    const name = item[2] || '-';
                    const foreign超 = Number(item[5]) || 0;
                    const local超 = Number(item[11]) || 0;
                    
                    const fClass = foreign超 >= 0 ? "text-green" : "text-red";
                    const lClass = local超 >= 0 ? "text-green" : "text-red";
                    
                    return `
                        <tr>
                          <td class="font-monospace">${code}</td>
                          <td>${name}</td>
                          <td class="text-end font-monospace ${fClass}">${(foreign超/1000).toFixed(0)}K</td>
                          <td class="text-end font-monospace ${lClass}">${(local超/1000).toFixed(0)}K</td>
                        </tr>
                    `;
                }).join('');
            }

            function resetFilters() {
                document.getElementById('mainEtfSelect').selectedIndex = 0;
                document.getElementById('dateSelect').selectedIndex = 0;
                onFilterChange();
            }

            function onCompareChange() {
                const checkedCbs = Array.from(document.querySelectorAll('#compareEtfCheckboxList input:checked')).map(cb => cb.value);
                const selectedDate = document.getElementById('dateSelect').value;
                
                const coreCard = document.getElementById('compareCoreCard');
                const uniqueCard = document.getElementById('compareUniqueCard');

                if(checkedCbs.length < 2) {
                    coreCard.style.display = 'none';
                    uniqueCard.style.display = 'none';
                    return;
                }

                // 比對開始：取得所有被勾選 ETF 的當日成分持股
                let compareData = rawData.filter(d => checkedCbs.includes(d.etf) && d.date === selectedDate);
                
                // 整理各成分股在不同 ETF 的持倉
                let stockMap = {}; // stock_code => { name, etf1: weight, etf2: weight... }
                compareData.forEach(item => {
                    if(!stockMap[item.stock]) {
                        stockMap[item.stock] = { name: item.name || tickerMap[item.stock] || '', weights: {} };
                    }
                    stockMap[item.stock].weights[item.etf] = item.weight;
                });

                // 動態組裝 Table Headers
                let coreHeaderHtml = `<th>成分股</th><th>名稱</th>`;
                let uniqueHeaderHtml = `<th>成分股</th><th>名稱</th>`;
                checkedCbs.forEach(etf => {
                    coreHeaderHtml += `<th class="text-end">${etf} (%)</th>`;
                    uniqueHeaderHtml += `<th class="text-end">${etf} (%)</th>`;
                });
                document.getElementById('compareCoreHeader').innerHTML = coreHeaderHtml;
                document.getElementById('compareUniqueHeader').innerHTML = uniqueHeaderHtml;

                // 分析重疊和獨特性
                let coreRowsHtml = "";
                let uniqueRowsHtml = "";

                Object.keys(stockMap).forEach(stockCode => {
                    const info = stockMap[stockCode];
                    const holdEtfs = Object.keys(info.weights);
                    
                    const isFullCore = (holdEtfs.length === checkedCbs.length);
                    const isUnique = (holdEtfs.length === 1);

                    if(!isFullCore && !isUnique) return; // 略過中間重疊的（非 100% 重疊或獨特性）

                    let rowHtml = `<td class="font-monospace">${stockCode}</td><td>${info.name}</td>`;
                    checkedCbs.forEach(etf => {
                        const val = info.weights[etf];
                        const displayVal = val ? val.toFixed(2) + '%' : '-';
                        let cellClass = "text-muted";
                        if(val) {
                            cellClass = val >= 5.0 ? "weight-high" : val >= 2.0 ? "weight-mid" : "weight-low";
                        }
                        rowHtml += `<td class="text-end font-monospace ${cellClass}">${displayVal}</td>`;
                    });
                    
                    rowHtml = `<tr>${rowHtml}</tr>`;
                    
                    if(isFullCore) { coreRowsHtml += rowHtml; } 
                    else { uniqueRowsHtml += rowHtml; }
                });
                
                coreCard.style.display = 'block';
                document.getElementById('compareCoreTableBody').innerHTML = coreRowsHtml || `<tr><td colspan="${2 + checkedCbs.length}" class="text-center py-4 text-slate-400"><i class="bi bi-info-circle me-1"></i> 所選定的 ETF 組合之間目前無任何 100% 交叉重疊的共同持股</td></tr>`;
                
                uniqueCard.style.display = 'block';
                document.getElementById('compareUniqueTableBody').innerHTML = uniqueRowsHtml || `<tr><td colspan="${2 + checkedCbs.length}" class="text-center py-4 text-slate-400"><i class="bi bi-info-circle me-1"></i> 無個別差異特色持股</td></tr>`;
            }
          </script>
        </body>
        </html>
        """

        # ---------------- 參數替換前端注入 ----------------
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

        # ---------------- Streamlit 渲染 ----------------
        components.html(final_html, height=1000, scrolling=True)
