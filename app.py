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
            import json
            creds_dict = json.loads(creds_json)
            return gspread.service_account_from_dict(creds_dict)
        except Exception as e:
            st.error(f"憑證解析失敗: {e}")
    
    # 嘗試預設路徑
    possible_paths = [
        "account.json",
        "../account.json",
        "config/account.json"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return gspread.service_account(filename=path)
            
    st.error("找不到任何合法的 Google 憑證 (環境變數、Secrets 或本地 json)。")
    st.stop()

@st.cache_data(ttl=600)  # 快取 10 分鐘防止過度讀取
def load_all_sheets_data():
    try:
        client = get_sheets_client()
        sh = client.open(SHEET_NAME)
        
        # 1. 讀取歷史成分股資料
        w_history = sh.worksheet(WORKSHEET_HISTORY)
        raw_history = w_history.get_all_records()
        df_hist = pd.DataFrame(raw_history)
        
        # 2. 讀取代號對照表
        try:
            w_ticker = sh.worksheet(WORKSHEET_TICKER)
            df_tick = pd.DataFrame(w_ticker.get_all_records())
            ticker_map = dict(zip(df_tick.iloc[:, 0].astype(str), df_tick.iloc[:, 1].astype(str)))
        except:
            ticker_map = {}
            
        # 3. 讀取 ETF 名稱對照表
        try:
            w_etf_name = sh.worksheet(WORKSHEET_ETF_NAME)
            df_etf_name = pd.DataFrame(w_etf_name.get_all_records())
            etf_name_map = dict(zip(df_etf_name.iloc[:, 0].astype(str), df_etf_name.iloc[:, 1].astype(str)))
        except:
            etf_name_map = {}
            
        return df_hist, ticker_map, etf_name_map
    except Exception as e:
        st.error(f"Google Sheets 載入發生異常錯誤: {e}")
        st.stop()

# ==========================================
# 3. 證交所與玩股網即時盤態 APIs
# ==========================================
@st.cache_data(ttl=60)  # 即時價格快取 1 分鐘
def fetch_twse_live_market(etf_codes):
    """ 從證交所 Mis 系統批量撈取最新市價與張數變動 """
    if not etf_codes:
        return {}
    results = {}
    try:
        # 構造證交所與櫃買中心查詢參數
        param_list = []
        for code in etf_codes:
            param_list.append(f"tse_{code}.tw")
            param_list.append(f"otc_{code}.tw")
            
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={'|'.join(param_list)}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if "msgArray" in data:
                for row in data["msgArray"]:
                    c = row.get("c") # 代號
                    if c in etf_codes:
                        results[c] = row
    except Exception as e:
        pass
    return results

@st.cache_data(ttl=60)
def fetch_wantgoo_etf_live():
    """ 撈取玩股網全市場折溢價即時大數據 """
    url = "https://www.wantgoo.com/api/etf/all"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.wantgoo.com/"
    }
    results = {}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("data", [])
            for item in items:
                id_code = item.get("id") or item.get("code")
                if id_code:
                    results[str(id_code).strip()] = {
                        "premium": item.get("discountPremiumRate") or item.get("premiumRate") or 0.0,
                        "nav": item.get("nav") or 0.0,
                        "price": item.get("price") or 0.0
                    }
    except:
        pass
    return results

# ==========================================
# 4. 主程式控制器與資料處理
# ==========================================
df_hist, ticker_map, etf_name_map = load_all_sheets_data()

# 清除所有欄位名稱的隱形空白
df_hist.columns = df_hist.columns.astype(str).str.strip()

# 進行實際中文欄位對照，並建立英文別名供前端 HTML5 大數據面板使用
if 'ETF代號' in df_hist.columns:
    df_hist['etf'] = df_hist['ETF代號'].astype(str).str.strip()
elif 'etf代號' in df_hist.columns:
    df_hist['etf'] = df_hist['etf代號'].astype(str).str.strip()
else:
    st.error("在『ETF History』工作表中找不到『ETF代號』欄位，請確認試算表第一行標頭是否完全符合。")
    st.stop()

df_hist['stock']  = df_hist['成分股代號'].astype(str).str.strip() if '成分股代號' in df_hist.columns else ""
df_hist['name']   = df_hist['成分股名稱'].astype(str).str.strip() if '成分股名稱' in df_hist.columns else ""
df_hist['date']   = df_hist['日期'].astype(str).str.strip() if '日期' in df_hist.columns else ""
df_hist['weight'] = pd.to_numeric(df_hist['持股權重'], errors='coerce').fillna(0.0) if '持股權重' in df_hist.columns else 0.0
df_hist['volume'] = pd.to_numeric(df_hist['持有數量'], errors='coerce').fillna(0.0) if '持有數量' in df_hist.columns else 0.0

# 獲取全市場 ETF 清單
all_etf_codes = sorted(df_hist['etf'].unique().tolist())

# 併入即時第三方大數據
twse_live_data = fetch_twse_live_market(all_etf_codes)
wantgoo_live_data = fetch_wantgoo_etf_live()

# 轉為前端 JSON 格式
json_data = df_hist.to_json(orient="records", force_ascii=False)
wantgoo_json = json.dumps(wantgoo_live_data, ensure_ascii=False)
twse_json = json.dumps(twse_live_data, ensure_ascii=False)
ticker_json = json.dumps(ticker_map, ensure_ascii=False)
etf_name_json = json.dumps(etf_name_map, ensure_ascii=False)

# ==========================================
# 5. 極致前端多功能儀表面板 (HTML5 / Bootstrap 5 / Bi Icons)
# ==========================================
html_template = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF 籌碼監控核心面板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css" rel="stylesheet">
    <style>
        :root {
            --primary-color: #2563eb;
            --secondary-color: #475569;
            --bg-light: #f8fafc;
            --card-border: #e2e8f0;
        }
        body {
            background-color: #f1f5f9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            color: #1e293b;
            padding: 12px;
        }
        .main-card {
            background: #ffffff;
            border-radius: 12px;
            box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05), 0 2px 4px -2px rgb(0 0 0 / 0.05);
            border: 1px solid var(--card-border);
            margin-bottom: 16px;
        }
        .nav-tabs {
            border-bottom: 2px solid #e2e8f0;
            background: #ffffff;
            padding: 8px 12px 0 12px;
            border-radius: 12px 12px 0 0;
        }
        .nav-tabs .nav-link {
            border: none;
            color: var(--secondary-color);
            font-weight: 600;
            padding: 10px 16px;
            border-radius: 6px 6px 0 0;
            margin-right: 4px;
            transition: all 0.2s ease;
        }
        .nav-tabs .nav-link:hover {
            background-color: #f1f5f9;
            color: var(--primary-color);
        }
        .nav-tabs .nav-link.active {
            color: var(--primary-color);
            background-color: #ffffff;
            border-bottom: 3px solid var(--primary-color);
        }
        .custom-tab-content {
            display: none;
            padding: 16px;
            background: #ffffff;
            border-radius: 0 0 12px 12px;
            border: 1px solid var(--card-border);
            border-top: none;
        }
        .custom-tab-content.active {
            display: block;
        }
        .etf-sidebar {
            max-height: 720px;
            overflow-y: auto;
            border: 1px solid var(--card-border);
            border-radius: 8px;
        }
        .etf-item-btn {
            border: none;
            border-bottom: 1px solid #f1f5f9;
            padding: 11px 14px;
            text-align: left;
            font-weight: 500;
            font-size: 0.95rem;
            transition: all 0.15s;
        }
        .etf-item-btn.active {
            background-color: #eff6ff !important;
            color: var(--primary-color) !important;
            font-weight: 700;
            border-left: 4px solid var(--primary-color);
        }
        .table-container {
            max-height: 520px;
            overflow-y: auto;
            border: 1px solid var(--card-border);
            border-radius: 8px;
        }
        .table thead th {
            position: sticky;
            top: 0;
            background-color: #f8fafc;
            z-index: 10;
            border-bottom: 2px solid #e2e8f0;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .meta-box {
            background: #f8fafc;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 12px 16px;
            text-align: center;
        }
        .meta-box h6 {
            margin-bottom: 4px;
            color: #64748b;
            font-size: 0.8rem;
            font-weight: 700;
        }
        .meta-box p {
            margin: 0;
            font-size: 1.35rem;
            font-weight: 800;
        }
        .suggestion-box {
            position: absolute;
            background: white;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            width: 100%;
            max-height: 250px;
            overflow-y: auto;
            z-index: 999;
            box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1);
            display: none;
        }
        .suggestion-item {
            padding: 8px 12px;
            cursor: pointer;
            border-bottom: 1px solid #f1f5f9;
            font-size: 0.9rem;
        }
        .suggestion-item:hover {
            background-color: #f1f5f9;
            color: var(--primary-color);
        }
        .badge-nature-new { background-color: #ffedd5; color: #ea580c; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; }
        .badge-nature-up { background-color: #fee2e2; color: #dc2626; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; }
        .badge-nature-down { background-color: #ccfbf1; color: #0f766e; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; }
        .badge-nature-delete { background-color: #f3f4f6; color: #4b5563; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; }
        .badge-trend-buy { background-color: #fef2f2; color: #b91c1c; padding: 2px 6px; border-radius: 4px; border: 1px solid #fca5a5; font-size: 0.78rem; font-weight: 600;}
        .badge-trend-sell { background-color: #f0fdfa; color: #0d9488; padding: 2px 6px; border-radius: 4px; border: 1px solid #99f6e4; font-size: 0.78rem; font-weight: 600;}
        
        .selected-stock-tag {
            display: inline-flex;
            align-items: center;
            background-color: #e0f2fe;
            color: #0369a1;
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.88rem;
            font-weight: 600;
            margin: 4px;
            border: 1px solid #bae6fd;
        }
        .selected-stock-tag i {
            margin-left: 6px;
            cursor: pointer;
            color: #0284c7;
        }
        .selected-stock-tag i:hover {
            color: #b91c1c;
        }
        #loading {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(255,255,255,0.85);
            z-index: 9999;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
        }
    </style>
</head>
<body>

    <div id="loading">
        <div class="spinner-border text-primary mb-2" role="status"></div>
        <div class="fw-bold text-secondary">晶片核心大數據計算中，請稍候...</div>
    </div>

    <ul class="nav nav-tabs" id="mainTabs" role="tablist">
        <li class="nav-item">
            <button class="nav-link active" id="tab1" onclick="switchTab('content1', 'tab1')"><i class="bi bi-pie-chart me-2"></i>單檔 ETF 籌碼分析</button>
        </li>
        <li class="nav-item">
            <button class="nav-link" id="tab2" onclick="switchTab('content2', 'tab2')"><i class="bi bi-search me-2"></i>個股籌碼分佈追蹤</button>
        </li>
        <li class="nav-item">
            <button class="nav-link" id="tab3" onclick="switchTab('content3', 'tab3')"><i class="bi bi-shuffle me-2"></i>多檔 ETF 交叉權重矩陣</button>
        </li>
        <li class="nav-item">
            <button class="nav-link" id="tab4" onclick="switchTab('content4', 'tab4')"><i class="bi bi-activity me-2"></i>全市場成分股異動排行</button>
        </li>
        <li class="nav-item">
            <button class="nav-link" id="tab5" onclick="switchTab('content5', 'tab5')"><i class="bi bi-cpu-fill me-2 text-danger"></i>ETF 智能組合篩選</button>
        </li>
    </ul>

    <div id="content1" class="custom-tab-content active">
        <div class="row g-3">
            <div class="col-md-3">
                <div class="card p-2 shadow-sm bg-light mb-2">
                    <div class="input-group input-group-sm">
                        <span class="input-group-text bg-white border-end-0"><i class="bi bi-filter"></i></span>
                        <input type="text" id="etfSearchInput" class="form-control border-start-0" placeholder="快速過濾 ETF 代號/名稱..." onkeyup="filterEtfList()">
                    </div>
                </div>
                <div class="list-group etf-sidebar shadow-sm" id="etfButtonList"></div>
            </div>
            
            <div class="col-md-9">
                <div id="etfTitleContainer" class="mb-3" style="display:none;">
                    <div class="d-flex align-items-center gap-2">
                        <h2 class="mb-0 text-primary fw-800" id="txtEtfCode"></h2>
                        <h3 class="mb-0 text-secondary fw-600" id="txtEtfName"></h3>
                        <span class="badge bg-dark ms-auto" id="txtUpdateDate"></span>
                    </div>
                </div>

                <div class="row g-2 mb-3 shadow-sm" id="metaContainer" style="display:none;">
                    <div class="col-6 col-md-3">
                        <div class="meta-box">
                            <h6>即時市價</h6>
                            <p class="text-primary font-monospace" id="metaMarketPrice">-</p>
                        </div>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="meta-box">
                            <h6>即時漲跌</h6>
                            <p class="font-monospace" id="metaChange">-</p>
                        </div>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="meta-box">
                            <h6>即時折溢價</h6>
                            <p class="text-warning font-monospace" id="metaPremium">-</p>
                        </div>
                    </div>
                    <div class="col-6 col-md-3">
                        <div class="meta-box">
                            <h6>當日前十大成交量</h6>
                            <p class="text-success font-monospace" id="metaVolume">-</p>
                        </div>
                    </div>
                    <div class="col-12 mt-2">
                        <div class="p-2 bg-light rounded text-muted small d-flex justify-content-between">
                            <span>最新公告資產規模：<b class="text-dark font-monospace" id="metaSize">-</b></span>
                            <span id="compareDateBadge" class="badge bg-secondary"></span>
                        </div>
                    </div>
                </div>

                <div class="card p-3 mb-3 bg-light border">
                    <div class="row g-2 align-items-center">
                        <div class="col-md-4">
                            <label class="form-label small fw-bold text-secondary mb-1">對比區間選擇</label>
                            <select id="rangeType" class="form-select form-select-sm" onchange="toggleCustomDates(); refreshCurrentEtf();">
                                <option value="1">與前一日比較 (日增減)</option>
                                <option value="2">與前二日比較</option>
                                <option value="4">與前一週比較 (週變動)</option>
                                <option value="20">與前一月比較 (月變動)</option>
                                <option value="custom">自訂特定比對日期</option>
                            </select>
                        </div>
                        <div class="col-md-8" id="customDateGroup" style="display:none;">
                            <div class="row g-2">
                                <div class="col-6">
                                    <label class="form-label small fw-bold text-secondary mb-1">比較基準日 (舊)</label>
                                    <input type="date" id="startDate" class="form-control form-control-sm" onchange="refreshCurrentEtf()">
                                </div>
                                <div class="col-6">
                                    <label class="form-label small fw-bold text-secondary mb-1">目標截止日 (新)</label>
                                    <input type="date" id="endDate" class="form-control form-control-sm" readonly>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="row g-3">
                    <div class="col-lg-6">
                        <div class="main-card p-3">
                            <h5 class="fw-bold mb-2 text-danger"><i class="bi bi-lightning-charge me-2"></i>區間成分股增減股數變動排行</h5>
                            <div class="p-2 mb-2 rounded bg-light text-muted small" id="dateDisplayInfo"></div>
                            <div class="table-container" style="max-height:450px;">
                                <table class="table table-sm table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th>成分股</th>
                                            <th>性質</th>
                                            <th class="text-end">增減股數變動</th>
                                            <th class="px-4">連續波段趨勢</th>
                                        </tr>
                                    </thead>
                                    <tbody id="changeTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                    
                    <div class="col-lg-6">
                        <div class="main-card p-3">
                            <h5 class="fw-bold mb-3 text-dark"><i class="bi bi-list-check me-2"></i>最新完整成分股明細 (含非股票資產)</h5>
                            
                            <ul class="nav nav-pills mb-2" id="pills-tab" role="tablist">
                              <li class="nav-item" role="presentation">
                                <button class="nav-link active py-1 px-3 small" id="pills-stocks-tab" data-bs-toggle="pill" data-bs-target="#pills-stocks" type="button" role="tab">上市櫃股票個股</button>
                              </li>
                              <li class="nav-item" role="presentation">
                                <button class="nav-link py-1 px-3 small" id="pills-assets-tab" data-bs-toggle="pill" data-bs-target="#pills-assets" type="button" role="tab">現金、指標與其餘資產</button>
                              </li>
                            </ul>
                            
                            <div class="tab-content" id="pills-tabContent">
                              <div class="tab-pane fade show active" id="pills-stocks" role="tabpanel">
                                <div class="table-container" style="max-height:380px;">
                                    <table class="table table-sm table-hover align-middle mb-0">
                                        <thead>
                                            <tr>
                                                <th>代號</th>
                                                <th>名稱</th>
                                                <th class="text-end">最新權重</th>
                                                <th class="text-end">最新持有股數</th>
                                            </tr>
                                        </thead>
                                        <tbody id="stockTableBody"></tbody>
                                    </table>
                                </div>
                              </div>
                              <div class="tab-pane fade" id="pills-assets" role="tabpanel">
                                <div class="table-container" style="max-height:380px;">
                                    <table class="table table-sm table-hover align-middle mb-0">
                                        <thead>
                                            <tr>
                                                <th>資產項目</th>
                                                <th>描述</th>
                                                <th class="text-end">權重</th>
                                                <th class="text-end">帳面數量/金額</th>
                                            </tr>
                                        </thead>
                                        <tbody id="assetTableBody"></tbody>
                                    </table>
                                </div>
                              </div>
                            </div>
                        </div>
                    </div>
                </div>

            </div>
        </div>
    </div>

    <div id="content2" class="custom-tab-content">
        <div class="main-card p-4">
            <h4 class="fw-bold text-dark mb-3"><i class="bi bi-building me-2 text-primary"></i>全市場單一個股之 ETF 籌碼滲透率反查</h4>
            <div class="row g-3 align-items-end mb-4 bg-light p-3 rounded border">
                <div class="col-md-5 position-relative">
                    <label class="form-label small fw-bold">請輸入臺灣上市櫃股票代號或名稱</label>
                    <input type="text" id="stockInput" class="form-control" placeholder="例如: 2330 或 台積電" onkeyup="searchStockSuggestions(this.value, 'stockSuggestions', 'stockInput', false)">
                    <div id="stockSuggestions" class="suggestion-box"></div>
                </div>
                <div class="col-md-4">
                    <label class="form-label small fw-bold">統計時間跨度</label>
                    <select id="stockRangeType" class="form-select">
                        <option value="1">對比前一日 (日變動)</option>
                        <option value="4">對比一週前 (週變動)</option>
                        <option value="20">對比一月前 (月變動)</option>
                    </select>
                </div>
                <div class="col-md-3">
                    <button class="btn btn-primary w-100 fw-bold" onclick="searchStockDistribution()"><i class="bi bi-search me-2"></i>啟動大數據反查分析</button>
                </div>
            </div>

            <div id="stockTrendCard" class="card p-3 mb-3 bg-gradient text-dark border-0 shadow-sm" style="display:none; background: linear-gradient(135deg, #e0e7ff 0%, #f1f5f9 100%);">
                <div class="row text-center align-items-center">
                    <div class="col-md-4 border-end">
                        <small class="text-secondary fw-bold">當前分析個股目標</small>
                        <h3 class="fw-800 text-primary mb-0 mt-1" id="trendStockHeader">-</h3>
                    </div>
                    <div class="col-md-3 border-end">
                        <small class="text-secondary fw-bold">鎖定佈局之 ETF 總數</small>
                        <h4 class="fw-bold text-dark mb-0 mt-1" id="trendStockCount">0 檔</h4>
                    </div>
                    <div class="col-md-2 border-end">
                        <small class="text-secondary fw-bold">區間籌碼增減狀態</small>
                        <div class="mt-1" id="trendStockStatus">-</div>
                    </div>
                    <div class="col-md-3">
                        <small class="text-secondary fw-bold">全體 ETF 總持股股數淨增減</small>
                        <h4 class="mb-0 mt-1" id="trendStockTotalVol">0 股</h4>
                    </div>
                </div>
                <div class="text-end mt-2"><span class="badge bg-secondary text-white" id="stockRangeBadge"></span></div>
            </div>

            <div class="row g-3">
                <div class="col-md-6" id="stockResultCard" style="display:none;">
                    <div class="card p-3 shadow-sm border">
                        <h6 class="fw-bold text-danger mb-3"><i class="bi bi-graph-up-arrow me-2"></i>1. 期間各 ETF 增減該股股數明細排行</h6>
                        <div class="table-container">
                            <table class="table table-hover table-sm align-middle mb-0">
                                <thead>
                                    <tr><th>ETF 機構代碼名稱</th><th class="text-end">經理人買賣超增減股數</th></tr>
                                </thead>
                                <tbody id="stockDistBody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
                <div class="col-md-6" id="stockWeightCard" style="display:none;">
                    <div class="card p-3 shadow-sm border">
                        <h6 class="fw-bold text-dark mb-3"><i class="bi bi-pie-chart-fill me-2 text-secondary"></i>2. 最新各 ETF 對該股之權重佔比與庫存明細</h6>
                        <div class="table-container">
                            <table class="table table-hover table-sm align-middle mb-0">
                                <thead>
                                    <tr><th>ETF 機構代碼名稱</th><th class="text-end">成分股佔比權重</th><th class="text-end">目前庫存總股數</th></tr>
                                </thead>
                                <tbody id="stockDistBody2"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="content3" class="custom-tab-content">
        <div class="main-card p-4">
            <h4 class="fw-bold text-dark mb-3"><i class="bi bi-grid-3x3-gap me-2 text-primary"></i>全市場 ETF 成分股成分交叉持股權重矩陣</h4>
            <p class="text-muted small">勾選您想要交叉對比的多檔 ETF 系統將自動交叉比對最新成分股，抓出共同重疊權重核心：</p>
            
            <div class="card p-3 bg-light mb-3 border">
                <div id="compareEtfCheckboxes" class="mb-3"></div>
                <button class="btn btn-success fw-bold px-4" onclick="generateComparison()"><i class="bi bi-lightning me-2"></i>一鍵產生交叉分析矩陣</button>
            </div>

            <div class="table-responsive shadow-sm border rounded" style="max-height: 600px;">
                <table class="table table-sm table-bordered table-hover align-middle mb-0 bg-white" style="font-size:0.9rem;">
                    <thead id="compareTableHeader"></thead>
                    <tbody id="compareTableBody"><tr><td class="text-center text-muted py-4">請先勾選上方 ETF 並點選產生分析矩陣。</td></tr></tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="content4" class="custom-tab-content">
        <div class="main-card p-4">
            <h4 class="fw-bold text-dark mb-3"><i class="bi bi-globe me-2 text-primary"></i>跨市場大數據：全市場 ETF 成分股籌碼調倉排行榜</h4>
            
            <div class="card p-3 bg-light mb-4 border">
                <div class="row g-3 align-items-end">
                    <div class="col-md-4">
                        <label class="form-label small fw-bold">調倉計算時間跨度</label>
                        <select id="globalRangeType" class="form-select" onchange="toggleGlobalChanges()">
                            <option value="1">對比前一日 (一日調倉追蹤)</option>
                            <option value="4">對比一週前 (一週調倉追蹤)</option>
                            <option value="20">對比一月前 (一月長線調倉)</option>
                            <option value="custom">自訂比對區間</option>
                        </select>
                    </div>
                    <div class="col-md-5" id="globalCustomDateGroup" style="display:none;">
                        <label class="form-label small fw-bold">請選擇自訂比較基準日</label>
                        <input type="date" id="globalStartDate" class="form-control">
                    </div>
                    <div class="col-md-3">
                        <button class="btn btn-primary w-100 fw-bold" onclick="loadGlobalChanges(); loadMarketHeat();"><i class="bi bi-cpu me-2"></i>執行跨市場交叉排行計算</button>
                    </div>
                </div>
            </div>

            <h5 class="fw-bold text-primary mb-3 text-center" id="globalTitle"></h5>

            <div class="row g-3 mb-4">
                <div class="col-md-6">
                    <div class="card p-3 border shadow-sm" style="border-top:4px solid #dc2626 !important;">
                        <h6 class="fw-bold text-danger mb-3" id="heatBuyTitle"><i class="bi bi-graph-up me-2"></i>跨市場大加總：淨買超前 10 大個股</h6>
                        <table class="table table-sm table-hover mb-0 align-middle">
                            <thead><tr><th>名次</th><th>代號</th><th>個股名稱</th><th class="text-end">全體 ETF 淨加碼股數</th></tr></thead>
                            <tbody id="heatBuyTableBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="col-md-6">
                    <div class="card p-3 border shadow-sm" style="border-top:4px solid #0f766e !important;">
                        <h6 class="fw-bold text-teal mb-3" id="heatSellTitle" style="color:#0f766e;"><i class="bi bi-graph-down me-2"></i>跨市場大加總：淨賣超前 10 大個股</h6>
                        <table class="table table-sm table-hover mb-0 align-middle">
                            <thead><tr><th>名次</th><th>代號</th><th>個股名稱</th><th class="text-end">全體 ETF 淨減持股數</th></tr></thead>
                            <tbody id="heatSellTableBody"></tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="card p-3 border">
                <h5 class="fw-bold text-dark mb-2"><i class="bi bi-database-fill-check me-2 text-secondary"></i>全市場所有調倉軌跡明細流水賬</h5>
                <div class="table-responsive" style="max-height: 450px;">
                    <table class="table table-sm table-striped table-hover align-middle mb-0">
                        <thead>
                            <tr>
                                <th>ETF 機構</th>
                                <th>成分標的</th>
                                <th>異動性質</th>
                                <th class="text-end">異動股數 (股)</th>
                                <th>資料庫檢索狀態</th>
                            </tr>
                        </thead>
                        <tbody id="globalTableBody"><tr><td colspan="5" class="text-center text-muted py-3">請點選上方按鈕執行大數據運算。</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div id="content5" class="custom-tab-content">
        <div class="main-card p-4">
            <h4 class="fw-bold text-dark mb-1"><i class="bi bi-cpu-fill text-danger me-2"></i>成分股反查組合智能篩選器</h4>
            <p class="text-muted small">輸入並加入您想指定的「多個成分股」，系統將即時比對大數據，篩選出「同時包含這些所有股票項目」的強大 ETF 投資清單。</p>
            
            <div class="card p-3 bg-light mb-4 border">
                <div class="row g-2 align-items-end">
                    <div class="col-md-9 position-relative">
                        <label class="form-label small fw-bold text-primary"><i class="bi bi-plus-circle me-1"></i>搜尋並加入您要求的目標成分股公司（可連續加入多檔）</label>
                        <input type="text" id="matcherInput" class="form-control" placeholder="輸入股票代號或名稱，例如: 2330 或 聯發科" onkeyup="searchStockSuggestions(this.value, 'matcherSuggestions', 'matcherInput', true)">
                        <div id="matcherSuggestions" class="suggestion-box"></div>
                    </div>
                    <div class="col-md-3">
                        <button class="btn btn-outline-danger w-100 fw-bold" onclick="selectedTargetStocks=[]; renderTargetTags(); calculateMatchedEtfs();"><i class="bi bi-trash3 me-2"></i>清空目前全部條件</button>
                    </div>
                </div>
                
                <div class="mt-3 p-3 bg-white rounded border">
                    <div class="small fw-bold text-secondary mb-2">當前選定的目標公司條件組合群：</div>
                    <div id="selectedTargetContainer" class="d-flex flex-wrap align-items-center">
                        <span class="text-muted small py-1" id="noTargetText">尚未選取任何公司，請從上方搜尋框輸入並挑選</span>
                    </div>
                </div>
            </div>

            <div class="card p-3 shadow-sm border">
                <div class="d-flex justify-content-between align-items-center mb-3 border-bottom pb-2">
                    <h5 class="fw-bold text-dark mb-0"><i class="bi bi-trophy-fill text-warning me-2"></i>符合條件之 ETF 篩選搜尋分析結果</h5>
                    <span class="badge bg-primary px-3 py-2" id="matchedCountBadge" style="font-size:0.9rem;">共 0 檔符合</span>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light">
                            <tr>
                                <th style="width: 15%;">ETF 代號</th>
                                <th style="width: 25%;">ETF 完整名稱</th>
                                <th style="width: 60%;">目標成分股在該 ETF 內之持股佔比與權重明細</th>
                            </tr>
                        </thead>
                        <tbody id="matcherTableBody">
                            <tr>
                                <td colspan="3" class="text-center text-muted py-4">請先在上方新增目標公司，系統將自動進行大數據分析。</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        let globalRawData = __DATA_PLACEHOLDER__;
        let wantgooMarketData = __WANTGOO_PLACEHOLDER__; 
        let twseLiveMarketData = __TWSE_PLACEHOLDER__; 
        let tickerMappingData = __TICKER_PLACEHOLDER__; 
        let etfNameMappingData = __ETF_NAME_PLACEHOLDER__; 
        let activeEtf = "";
        let selectedTargetStocks = []; 

        let historyStockMapping = {};

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
            
            initHistoryStockMapping();
            initDashboard();
            
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
                    <input class="form-check-input etf-compare-cb" type="checkbox" value="${etf}" id="cb-${etf}" checked>
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
            let cashEx = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨", "RDI", "權證", "DR"];
            
            let upperCode = String(code).toUpperCase().trim();
            let upperName = String(name).toUpperCase().trim();

            if (meta.includes(upperCode) || meta.includes(upperName)) return false;
            if (cashEx.some(k => upperCode.includes(k) || upperName.includes(k))) return false;
            return true;
        }

        function initHistoryStockMapping() {
            historyStockMapping = {};
            globalRawData.forEach(item => {
                let code = String(item.stock).trim();
                let name = String(item.name).trim();
                if (code && isNormalStock(code, name)) {
                    historyStockMapping[code] = name;
                }
            });
        }

        function searchStockSuggestions(value, boxId, inputId, isMultiple = false) {
            let q = value.trim().toLowerCase();
            let box = document.getElementById(boxId);
            if (!q) { box.style.display = 'none'; return; }

            let matches = [];
            for (let code in historyStockMapping) {
                let name = historyStockMapping[code];
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

        function setMetaFallback() {
            document.getElementById('metaMarketPrice').innerText = "-";
            document.getElementById('metaChange').innerText = "-";
            document.getElementById('metaVolume').innerText = "-";
            document.getElementById('txtUpdateDate').innerText = "未取得即時盤態";
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

                        if (diff === 0) break;
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

            let htmlNew = ""; let htmlAdd = ""; let htmlSub = ""; let htmlDel = "";

            currentStocks.forEach(r => {
                let oldVol = compRows.find(c => c.stock === r.stock)?.volume || 0;
                let diff = r.volume - oldVol;

                if (diff !== 0) {
                    let nature = oldVol === 0 ? "新增" : (diff > 0 ? "增加" : "減少");
                    let badge = ""; let dStyle = "";
                    if (nature === "新增") {
                        badge = `<span class="badge-nature-new">${nature}</span>`; dStyle = "color:#ea580c;";
                    } else if (nature === "增加") {
                        badge = `<span class="badge-nature-up">${nature}</span>`; dStyle = "color:#dc2626;";
                    } else {
                        badge = `<span class="badge-nature-down">${nature}</span>`; dStyle = "color:#0f766e;";
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
                        let dStyle = "color:#4b5563;"; let diff = -r.volume;
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

            document.getElementById('changeTableBody').innerHTML = (htmlNew + htmlAdd + htmlSub + htmlDel) || '<tr><td colspan="4" class="text-center text-muted py-3">此區間成分股數量未發生增減變動</td></tr>';
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

        function loadMarketHeat() {
            let type = document.getElementById('globalRangeType').value;
            let dates = [...new Set(globalRawData.map(d=>d.date))].sort((a,b)=>new Date(a)-new Date(b));
            let latestDate = dates[dates.length - 1];
            let compDate = (type === 'custom') ? document.getElementById('globalStartDate').value : dates[Math.max(0, dates.length - 1 - parseInt(type))];

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

            document.getElementById('heatBuyTableBody').innerHTML = topBuy.map((x, i) => `<tr><td><span class="rank-badge bg-danger text-white" style="padding: 2px 6px; border-radius: 4px;">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-danger fw-bold font-monospace">+${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('');
            document.getElementById('heatSellTableBody').innerHTML = topSell.map((x, i) => `<tr><td><span class="rank-badge bg-teal text-white" style="background-color:#0f766e; padding: 2px 6px; border-radius: 4px;">${i+1}</span></td><td>${x.code}</td><td class="fw-bold">${x.name}</td><td class="text-end text-success fw-bold font-monospace">${Math.round(x.diff).toLocaleString()} 股</td></tr>`).join('');
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

components.html(final_html, height=1400, scrolling=True)
