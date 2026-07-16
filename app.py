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
        from google.oauth2.service_account import Credentials
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        return gspread.authorize(creds)
    else:
        return gspread.service_account()

@st.cache_data(ttl=600)
def load_gsheet_data():
    client = get_sheets_client()
    sheet = client.open(SHEET_NAME)
    
    # 讀取歷史明細
    w_hist = sheet.worksheet(WORKSHEET_HISTORY)
    records_hist = w_hist.get_all_records()
    df_hist = pd.DataFrame(records_hist)
    
    # 讀取股號對照表
    w_tick = sheet.worksheet(WORKSHEET_TICKER)
    records_tick = w_tick.get_all_records()
    df_tick = pd.DataFrame(records_tick)
    
    # 讀取 ETF 名稱對照表
    w_name = sheet.worksheet(WORKSHEET_ETF_NAME)
    records_name = w_name.get_all_records()
    df_name = pd.DataFrame(records_name)
    
    return df_hist, df_tick, df_name

# ==========================================
# 3. 爬蟲核心 (爬取即時預估淨值與市價)
# ==========================================
def get_wantgoo_data(df_name):
    """
    從玩股網獲取即時與歷史折溢價、淨值資料
    """
    results = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    etf_list = df_name["ETF代號"].astype(str).tolist()
    
    for etf in etf_list:
        clean_etf = etf.strip()
        url = f"https://www.wantgoo.com/invest/etf/discount?symbol={clean_etf}"
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                html = res.text
                
                # 簡單正則解析即時數據
                price_match = re.search(r'id="currentPrice"[^>]*>([\d\.]+)', html)
                nav_match = re.search(r'id="estimatedNav"[^>]*>([\d\.]+)', html)
                discount_match = re.search(r'id="discountRate"[^>]*>([\-\d\.]+)', html)
                
                price = float(price_match.group(1)) if price_match else None
                nav = float(nav_match.group(1)) if nav_match else None
                discount = float(discount_match.group(1)) if discount_match else None
                
                results[clean_etf] = {
                    "price": price,
                    "nav": nav,
                    "discount": discount,
                    "status": "success"
                }
            else:
                results[clean_etf] = {"status": "failed", "error": f"HTTP {res.status_code}"}
        except Exception as e:
            results[clean_etf] = {"status": "failed", "error": str(e)}
            
    return results

def get_twse_data():
    """
    從證交所獲取全市場大盤即時資訊
    """
    url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if "msgArray" in data and len(data["msgArray"]) > 0:
                info = data["msgArray"][0]
                return {
                    "status": "success",
                    "index": info.get("z", info.get("y", "N/A")),       # 指數
                    "change": info.get("diff", "N/A"),                  # 漲跌
                    "percent": info.get("pe", "N/A"),                   # 漲跌幅
                    "volume": info.get("v", "N/A")                      # 總成交量
                }
    except Exception as e:
        return {"status": "failed", "error": str(e)}
    return {"status": "failed", "error": "No data available"}

# ==========================================
# 4. 主控板邏輯與 JSON 封裝
# ==========================================
try:
    df_hist, df_tick, df_name = load_gsheet_data()
    
    # 格式清理
    df_hist['日期'] = pd.to_datetime(df_hist['日期']).dt.strftime('%Y-%m-%d')
    df_hist['持股股數'] = pd.to_numeric(df_hist['持股股數'], errors='coerce').fillna(0).astype(int)
    df_hist['持股權重'] = pd.to_numeric(df_hist['持股權重'], errors='coerce').fillna(0).astype(float)
    
    # 合併代號與名稱對照
    df_merged = df_hist.merge(df_tick, on='股票代號', how='left')
    df_merged['股票名稱'] = df_merged['股票名稱'].fillna(df_merged['股票代號'])
    
    df_merged = df_merged.merge(df_name, on='ETF代號', how='left')
    df_merged['ETF名稱'] = df_merged['ETF名稱'].fillna(df_merged['ETF代號'])
    
    # 轉為前端所需的 JSON
    json_data = df_merged.to_json(orient='records', force_ascii=False)
    wantgoo_json = json.dumps(get_wantgoo_data(df_name), ensure_ascii=False)
    twse_json = json.dumps(get_twse_data(), ensure_ascii=False)
    ticker_json = df_tick.set_index('股票代號')['股票名稱'].to_json(force_ascii=False)
    
except Exception as e:
    st.error(f"⚠️ 資料庫讀取失敗，請確認 Google 試算表設定與憑證。錯誤訊息: {str(e)}")
    st.stop()

# ==========================================
# 5. 大型前端 HTML/CSS/JS 面板模板
# ==========================================
html_template = """
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>主動式 ETF 籌碼大數據監控面板</title>
    <link href="https://fastly.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fastly.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
    <script src="https://fastly.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
    <script src="https://fastly.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    
    <style>
        :root {
            --dark-bg: #0d0e12;
            --card-bg: #161823;
            --border-color: #242736;
            --accent-primary: #0d6efd;
            --text-muted: #8c90a6;
        }
        
        body {
            background-color: var(--dark-bg);
            color: #f1f2f6;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            overflow-x: hidden;
            padding-bottom: 50px;
        }
        
        .navbar-custom {
            background-color: var(--card-bg);
            border-bottom: 1px solid var(--border-color);
            padding: 10px 20px;
        }
        
        .card-custom {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
            margin-bottom: 20px;
        }
        
        .card-header-custom {
            font-size: 1.1rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            color: #ffffff;
        }
        
        .nav-tabs {
            border-bottom: 2px solid var(--border-color);
        }
        
        .nav-link {
            color: var(--text-muted);
            border: none !important;
            padding: 12px 20px;
            font-weight: 500;
            transition: all 0.2s ease;
        }
        
        .nav-link:hover {
            color: #ffffff;
            background: rgba(255,255,255,0.05);
        }
        
        .nav-link.active {
            color: #ffffff !important;
            background-color: transparent !important;
            border-bottom: 3px solid var(--accent-primary) !important;
        }
        
        .metric-value {
            font-size: 1.8rem;
            font-weight: 700;
            font-family: 'SF Mono', Consolas, monospace;
        }
        
        .table-custom {
            color: #e1e3e8;
        }
        
        .table-custom th {
            color: var(--text-muted);
            font-weight: 500;
            border-bottom: 2px solid var(--border-color);
        }
        
        .table-custom td {
            border-bottom: 1px solid var(--border-color);
            padding: 12px 8px;
        }
        
        /* 滾動條美化 */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: var(--dark-bg);
        }
        ::-webkit-scrollbar-thumb {
            background: var(--border-color);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: #4b4f69;
        }
        
        .weight-high { color: #2ed573; font-weight: bold; }
        .weight-mid { color: #ffa502; }
        .weight-low { color: #ff4757; }
        
        .text-up { color: #ff4757; }
        .text-down { color: #2ed573; }
        
        .cursor-pointer { cursor: pointer; }
    </style>
</head>
<body>

    <div class="navbar-custom d-flex justify-content-between align-items-center">
        <div class="d-flex align-items-center">
            <span class="h5 mb-0 fw-bold text-primary"><i class="bi bi-activity me-2"></i>主動型 ETF 大數據籌碼監控中心</span>
            <span class="badge bg-secondary ms-3 small">Beta 1.5</span>
        </div>
        <div class="d-flex align-items-center gap-4 text-end" id="marketBar">
            <span class="small text-muted">加權指數: <strong class="text-white" id="twseIndex">載入中...</strong></span>
            <span class="small text-muted">大盤漲跌: <strong id="twseChange">--</strong></span>
            <span class="small text-muted">大盤量能: <strong class="text-white" id="twseVolume">--</strong></span>
        </div>
    </div>

    <div class="container-fluid mt-3">
        <ul class="nav nav-tabs border-0" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="tab-a" data-bs-toggle="tab" data-bs-target="#content-a" type="button" role="tab" aria-controls="content-a" aria-selected="true">
                    <i class="bi bi-pie-chart me-1"></i>單檔 ETF 籌碼與持股
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="tab-b" data-bs-toggle="tab" data-bs-target="#content-b" type="button" role="tab" aria-controls="content-b" aria-selected="false">
                    <i class="bi bi-search me-1"></i>個股籌碼分佈查詢
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="tab-c" data-bs-toggle="tab" data-bs-target="#content-c" type="button" role="tab" aria-controls="content-c" aria-selected="false">
                    <i class="bi bi-cpu me-1"></i>AI 投資組合回溯目標搜尋器
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="tab-d" data-bs-toggle="tab" data-bs-target="#content-d" type="button" role="tab" aria-controls="content-d" aria-selected="false">
                    <i class="bi bi-fire me-1"></i>全市場經理人加減碼熱度分析
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="tab-e" data-bs-toggle="tab" data-bs-target="#content-e" type="button" role="tab" aria-controls="content-e" aria-selected="false">
                    <i class="bi bi-grid-3x3-gap me-1"></i>核心與特色持股交叉對比
                </button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="tab-g" data-bs-toggle="tab" data-bs-target="#content-g" type="button" role="tab" aria-controls="content-g" aria-selected="false">
                    <i class="bi bi-radar me-1"></i>共識雷達與反向指標
                </button>
            </li>
        </ul>

        <div class="tab-content mt-3" id="myTabContent">
            
            <div class="tab-pane fade show active" id="content-a" role="tabpanel" aria-labelledby="tab-a">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 mb-3">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-gear-fill text-primary me-2"></i>監控設定
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">選擇主動型 ETF</label>
                                <select class="form-select" id="etfSelect" onchange="updateSingleETF()"></select>
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">比較區間</label>
                                <select class="form-select" id="timeframeSelect" onchange="toggleCustomDates(); updateSingleETF();">
                                    <option value="1">1 日 (前一日比對)</option>
                                    <option value="5">5 日 (週變動比對)</option>
                                    <option value="20" selected>20 日 (月線變動比對)</option>
                                    <option value="custom">自訂區間比對</option>
                                </select>
                            </div>
                            <div id="customDateGroup" class="d-none mb-3">
                                <div class="row g-2">
                                    <div class="col-6">
                                        <label class="form-label text-muted small">起始日期</label>
                                        <select class="form-select" id="dateStartSelect" onchange="updateSingleETF();"></select>
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label text-muted small">結束日期</label>
                                        <select class="form-select" id="dateEndSelect" onchange="updateSingleETF();"></select>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="card card-custom p-3 mb-3" id="etfSummaryCard" style="display: none;"></div>

                        <div class="card card-custom p-3 mb-3" id="etfStyleCard" style="display: none;">
                            <div class="card-header-custom mb-2 border-bottom border-secondary pb-2 d-flex justify-content-between align-items-center">
                                <span><i class="bi bi-person-badge text-primary me-2"></i>經理人操盤診斷</span>
                                <span class="badge bg-secondary" id="etfStyleLabel">分析中</span>
                            </div>
                            <div class="d-flex align-items-center justify-content-between mb-3 bg-dark p-2 rounded">
                                <span class="small text-muted">估計換股率 (Turnover)</span>
                                <span class="h5 mb-0 font-monospace text-warning fw-bold" id="etfTurnoverRate">0.00%</span>
                            </div>
                            <div class="mb-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <span class="small text-success fw-bold"><i class="bi bi-shield-shaded me-1"></i>核心持股 (Core)</span>
                                    <span class="badge bg-success small" id="etfCoreCount">0 檔</span>
                                </div>
                                <div id="etfCorePills" class="d-flex flex-wrap gap-1 overflow-auto" style="max-height: 80px;"></div>
                                <div class="text-muted" style="font-size: 0.7rem; margin-top: 2px;">*定義: 權重 &ge; 5% 且歷史持有天數占比 &ge; 80%</div>
                            </div>
                            <div>
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <span class="small text-info fw-bold"><i class="bi bi-rocket-takeoff me-1"></i>衛星持股 (Satellite)</span>
                                    <span class="badge bg-info text-dark small" id="etfSatelliteCount">0 檔</span>
                                </div>
                                <div id="etfSatellitePills" class="d-flex flex-wrap gap-1 overflow-auto" style="max-height: 80px;"></div>
                                <div class="text-muted" style="font-size: 0.7rem; margin-top: 2px;">*定義: 權重 &lt; 2% 且歷史持有天數占比 &lt; 30%</div>
                            </div>
                        </div>
                    </div>

                    <div class="col-12 col-xl-9">
                        <div class="card card-custom p-3 h-100">
                            <div class="d-flex justify-content-between align-items-center mb-3">
                                <div class="card-header-custom">
                                    <i class="bi bi-list-stars text-primary me-2"></i>最新持股變動與經理人操作明細
                                </div>
                                <span class="text-muted small">點擊欄位標頭可進行動態排序</span>
                            </div>
                            <div class="table-responsive">
                                <table class="table table-custom table-dark table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th onclick="sortSingleTable(0)" class="cursor-pointer">股票代號 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(1)" class="cursor-pointer">股票名稱 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(2)" class="text-end cursor-pointer">起始持股股數 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(3)" class="text-end cursor-pointer">最新持股股數 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(4)" class="text-end cursor-pointer">股數增減 (量變動) <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(5)" class="text-end cursor-pointer">起始持股權重 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(6)" class="text-end cursor-pointer">最新持股權重 <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                            <th onclick="sortSingleTable(7)" class="text-end cursor-pointer">權重增減 (%) <i class="bi bi-arrow-down-up ms-1 small"></i></th>
                                        </tr>
                                    </thead>
                                    <tbody id="singleTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="content-b" role="tabpanel" aria-labelledby="tab-b">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-search text-primary me-2"></i>個股篩選
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">選擇或輸入股票</label>
                                <select class="form-select" id="stockSelect" onchange="updateStockDistribution()"></select>
                            </div>
                            <div class="alert alert-info py-2 px-3 small border-0 mb-0" style="background: rgba(13,110,253,0.1); color: #9ec5fe;">
                                <i class="bi bi-info-circle me-1"></i> 您可在此查詢特定成分股被哪些主動式 ETF 持有，以及經理人們最新的權重分配與持股變化。
                            </div>
                        </div>
                    </div>
                    <div class="col-12 col-xl-9">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-share text-primary me-2"></i>該股在各主動式 ETF 之佔比與持股明細
                            </div>
                            <div class="table-responsive">
                                <table class="table table-custom table-dark table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th>主動式 ETF 代號</th>
                                            <th>主動式 ETF 名稱</th>
                                            <th class="text-end">最新持股股數</th>
                                            <th class="text-end">最新持股權重</th>
                                            <th class="text-end">歷史平均權重</th>
                                        </tr>
                                    </thead>
                                    <tbody id="stockTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="content-c" role="tabpanel" aria-labelledby="tab-c">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-cpu text-primary me-2"></i>目標持股多重設定
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">選擇看好的個股組合 (多選)</label>
                                <div id="aiStockChecklist" class="overflow-auto border border-secondary rounded p-2" style="max-height: 300px; background: rgba(0,0,0,0.2);"></div>
                            </div>
                            <button class="btn btn-primary w-100" onclick="updateAISearch()">
                                <i class="bi bi-search me-1"></i> 搜尋最匹配的主動式 ETF
                            </button>
                        </div>
                    </div>
                    <div class="col-12 col-xl-9">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-trophy text-primary me-2"></i>大數據推薦主動式 ETF 排名
                            </div>
                            <div class="table-responsive">
                                <table class="table table-custom table-dark table-hover align-middle mb-0">
                                    <thead>
                                        <tr>
                                            <th class="text-center">推薦指數排名</th>
                                            <th>主動型 ETF 名稱 / 代號</th>
                                            <th class="text-center">命中個股數量</th>
                                            <th class="text-end">累計看好股權重 (%)</th>
                                            <th>所含看好個股明細 (含權重)</th>
                                        </tr>
                                    </thead>
                                    <tbody id="aiTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="content-d" role="tabpanel" aria-labelledby="tab-d">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-funnel-fill text-primary me-2"></i>篩選與分析設定
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">分析區間</label>
                                <select class="form-select" id="managerTimeframeSelect" onchange="toggleManagerCustomDates(); updateManagerHotness();">
                                    <option value="1">1 日 (前一日變動)</option>
                                    <option value="5">5 日 (週變動)</option>
                                    <option value="20" selected>20 日 (月線變動)</option>
                                    <option value="custom">自訂區間</option>
                                </select>
                            </div>
                            <div id="managerCustomDateGroup" class="d-none mb-3">
                                <div class="row g-2">
                                    <div class="col-6">
                                        <label class="form-label text-muted small">起始日期</label>
                                        <select class="form-select" id="managerDateStartSelect" onchange="updateManagerHotness();"></select>
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label text-muted small">結束日期</label>
                                        <select class="form-select" id="managerDateEndSelect" onchange="updateManagerHotness();"></select>
                                    </div>
                                </div>
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">最小權重變動門檻 (%)</label>
                                <input type="number" class="form-control" id="managerThreshold" value="0.2" step="0.1" min="0" onchange="updateManagerHotness()">
                            </div>
                        </div>
                    </div>
                    <div class="col-12 col-xl-9">
                        <div class="card card-custom p-3 h-100" style="min-height: 500px;">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-bar-chart-line text-primary me-2"></i>全市場主動型經理人「加減碼」個股排行
                            </div>
                            <div id="managerHotnessChart" style="width: 100%; height: 400px;"></div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="content-e" role="tabpanel" aria-labelledby="tab-e">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-ui-checks text-primary me-2"></i>選取比對 ETF (至少選 2 個)
                            </div>
                            <div id="compareEtfChecklist" class="overflow-auto border border-secondary rounded p-2 mb-3" style="max-height: 350px; background: rgba(0,0,0,0.2);"></div>
                            <button class="btn btn-primary w-100" onclick="updateCompare()">
                                <i class="bi bi-arrow-left-right me-1"></i> 開始交叉對比
                            </button>
                        </div>
                    </div>
                    <div class="col-12 col-xl-9">
                        <div class="card card-custom p-3 mb-3" id="compareCoreCard" style="display: none;">
                            <div class="card-header-custom text-success mb-3">
                                <i class="bi bi-shield-check text-success me-2"></i>法人英雄所見略同：100% 完全重疊核心持股 (共識核心)
                            </div>
                            <div class="table-responsive">
                                <table class="table table-custom table-dark table-hover mb-0">
                                    <thead id="compareCoreHead"></thead>
                                    <tbody id="compareCoreTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                        
                        <div class="card card-custom p-3" id="compareUniqueCard" style="display: none;">
                            <div class="card-header-custom text-info mb-3">
                                <i class="bi bi-lightning-fill text-warning me-2"></i>個別特色持股分析 (差異化持股)
                            </div>
                            <div class="table-responsive">
                                <table class="table table-custom table-dark table-hover mb-0">
                                    <thead id="compareUniqueHead"></thead>
                                    <tbody id="compareUniqueTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="content-g" role="tabpanel" aria-labelledby="tab-g">
                <div class="row g-3">
                    <div class="col-12 col-xl-3">
                        <div class="card card-custom p-3 h-100">
                            <div class="card-header-custom mb-3">
                                <i class="bi bi-funnel text-primary me-2"></i>篩選控制面板
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small">比對區間</label>
                                <select class="form-select" id="radarTimeframeSelect" onchange="toggleRadarCustomDates(); updateRadar();">
                                    <option value="1">1 日 (前一日比對)</option>
                                    <option value="5">5 日 (週變動比對)</option>
                                    <option value="20" selected>20 日 (月線變動比對)</option>
                                    <option value="custom">自訂區間比對</option>
                                </select>
                            </div>
                            <div id="radarCustomDateGroup" class="d-none mb-3">
                                <div class="row g-2">
                                    <div class="col-6">
                                        <label class="form-label text-muted small">起始日期</label>
                                        <select class="form-select" id="radarDateStartSelect" onchange="updateRadar();"></select>
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label text-muted small">結束日期</label>
                                        <select class="form-select" id="radarDateEndSelect" onchange="updateRadar();"></select>
                                    </div>
                                </div>
                            </div>
                            <div class="mb-3">
                                <label class="form-label text-muted small d-flex justify-content-between align-items-center">
                                    <span>納入計算的 ETF</span>
                                    <span class="text-primary cursor-pointer small" onclick="selectAllRadarEtfs(true)">全選</span>
                                </label>
                                <div id="radarEtfList" class="overflow-auto border border-secondary rounded p-2" style="max-height: 250px; background: rgba(0,0,0,0.2);"></div>
                            </div>
                            <div class="alert alert-info py-2 px-3 small border-0 mb-0" style="background: rgba(13, 110, 253, 0.1); color: #9ec5fe;">
                                <i class="bi bi-info-circle me-1"></i> <strong>經理人共識：</strong>系統透過比對各主動式 ETF 在分析期間內的「持股股數變動」，精準提煉出被最多檔 ETF 同時買進加碼（黃金共識）或同時賣出減碼（避險警示）的指標股。
                            </div>
                        </div>
                    </div>

                    <div class="col-12 col-xl-9">
                        <div class="row g-3 h-100">
                            <div class="col-12 col-md-6">
                                <div class="card card-custom p-3 h-100" style="min-height: 600px;">
                                    <div class="d-flex justify-content-between align-items-center mb-3 border-bottom border-secondary pb-2">
                                        <div class="card-header-custom text-success">
                                            <i class="bi bi-trophy-fill text-warning me-2"></i>黃金共識股 (最多 ETF 同時加碼)
                                        </div>
                                        <span class="badge bg-success" id="consensusAddCount">0 檔</span>
                                    </div>
                                    <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                                        <table class="table table-dark table-hover align-middle mb-0" style="font-size: 0.9rem;">
                                            <thead>
                                                <tr>
                                                    <th class="text-center" style="width: 12%;">排名</th>
                                                    <th style="width: 38%;">成分股</th>
                                                    <th class="text-center" style="width: 20%;">加碼 ETF 數</th>
                                                    <th style="width: 30%;">加碼 ETF 明細</th>
                                                </tr>
                                            </thead>
                                            <tbody id="radarAddTableBody"></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>

                            <div class="col-12 col-md-6">
                                <div class="card card-custom p-3 h-100" style="min-height: 600px;">
                                    <div class="d-flex justify-content-between align-items-center mb-3 border-bottom border-secondary pb-2">
                                        <div class="card-header-custom text-danger">
                                            <i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>避險警示股 (最多 ETF 同時減碼)
                                        </div>
                                        <span class="badge bg-danger" id="consensusSubCount">0 檔</span>
                                    </div>
                                    <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                                        <table class="table table-dark table-hover align-middle mb-0" style="font-size: 0.9rem;">
                                            <thead>
                                                <tr>
                                                    <th class="text-center" style="width: 12%;">排名</th>
                                                    <th style="width: 38%;">成分股</th>
                                                    <th class="text-center" style="width: 20%;">減碼 ETF 數</th>
                                                    <th style="width: 30%;">減碼 ETF 明細</th>
                                                </tr>
                                            </thead>
                                            <tbody id="radarSubTableBody"></tbody>
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

    <script>
        // 資料載入核心
        const rawData = __DATA_PLACEHOLDER__;
        const wantgooData = __WANTGOO_PLACEHOLDER__;
        const twseData = __TWSE_PLACEHOLDER__;
        const tickerMapping = __TICKER_PLACEHOLDER__;
        
        // 取得歷史日期清單
        const dates = [...new Set(rawData.map(item => item.日期))].sort().reverse();
        const etfs = [...new Set(rawData.map(item => item.ETF代號))].sort();
        const stocks = [...new Set(rawData.map(item => item.股票代號))].sort();
        
        let singleSortCol = 6; // 預設依最新持股權重降序排序
        let singleSortAsc = false;
        let managerChart = null;
        let radarInitialized = false;

        // 初始化
        window.addEventListener('DOMContentLoaded', () => {
            initMarketBar();
            initSingleSelects();
            initStockSelect();
            initAIChecklist();
            initCompareChecklist();
            initManagerSelects();
            
            toggleCustomDates();
            updateSingleETF();
            
            // Tab 切換監聽器
            const triggerTabList = [].slice.call(document.querySelectorAll('button[data-bs-toggle="tab"]'))
            triggerTabList.forEach(function (triggerEl) {
                triggerEl.addEventListener('shown.bs.tab', function (event) {
                    const tabId = event.target.id;
                    if (tabId === 'tab-d') {
                        if (managerChart) {
                            managerChart.resize();
                        } else {
                            updateManagerHotness();
                        }
                    } else if (tabId === 'tab-g') {
                        if (!radarInitialized) {
                            initRadar();
                            radarInitialized = true;
                        } else {
                            updateRadar();
                        }
                    }
                });
            });
        });

        // 頂部行情條渲染
        function initMarketBar() {
            if (twseData.status === 'success') {
                document.getElementById('twseIndex').textContent = parseFloat(twseData.index).toFixed(2);
                const changeEl = document.getElementById('twseChange');
                const changeVal = parseFloat(twseData.change);
                const percentVal = parseFloat(twseData.percent);
                const symbol = changeVal > 0 ? '▲' : (changeVal < 0 ? '▼' : '');
                
                changeEl.textContent = `${symbol} ${Math.abs(changeVal).toFixed(2)} (${percentVal.toFixed(2)}%)`;
                if (changeVal > 0) changeEl.className = 'text-up fw-bold';
                else if (changeVal < 0) changeEl.className = 'text-down fw-bold';
                
                document.getElementById('twseVolume').textContent = (parseFloat(twseData.volume)/100000000).toFixed(1) + ' 億';
            }
        }

        // ------------------------------------------
        // 分頁 A：單檔分析邏輯
        // ------------------------------------------
        function initSingleSelects() {
            const etfSelect = document.getElementById('etfSelect');
            etfSelect.innerHTML = '';
            etfs.forEach(etf => {
                const name = rawData.find(item => item.ETF代號 === etf).ETF名稱 || etf;
                etfSelect.appendChild(new Option(`${etf} ${name}`, etf));
            });
            
            const startSelect = document.getElementById('dateStartSelect');
            const endSelect = document.getElementById('dateEndSelect');
            dates.forEach((d, idx) => {
                startSelect.appendChild(new Option(d, d));
                const opt = new Option(d, d);
                if (idx === Math.min(20, dates.length - 1)) opt.selected = true; // 預設結束為 20 日前
                endSelect.appendChild(opt);
            });
        }

        function toggleCustomDates() {
            const tf = document.getElementById('timeframeSelect').value;
            const group = document.getElementById('customDateGroup');
            if (tf === 'custom') group.classList.remove('d-none');
            else group.classList.add('d-none');
        }

        function getCompareDates() {
            const tf = document.getElementById('timeframeSelect').value;
            let dateStart, dateEnd;
            if (tf === 'custom') {
                dateStart = document.getElementById('dateStartSelect').value;
                dateEnd = document.getElementById('dateEndSelect').value;
                
                // 確保 dateStart (最新) 比 dateEnd (舊) 還新
                const idxStart = dates.indexOf(dateStart);
                const idxEnd = dates.indexOf(dateEnd);
                if (idxStart > idxEnd) {
                    // 互換
                    const tmp = dateStart;
                    dateStart = dateEnd;
                    dateEnd = tmp;
                }
            } else {
                dateStart = dates[0];
                const offset = parseInt(tf);
                const targetIdx = Math.min(offset, dates.length - 1);
                dateEnd = dates[targetIdx];
            }
            return { dateStart, dateEnd };
        }

        let currentSingleTableData = [];

        function updateSingleETF() {
            const selectedEtf = document.getElementById('etfSelect').value;
            if (!selectedEtf) return;
            
            const { dateStart, dateEnd } = getCompareDates();
            
            // 篩選與比對數據
            const dataNew = rawData.filter(item => item.ETF代號 === selectedEtf && item.日期 === dateStart);
            const dataOld = rawData.filter(item => item.ETF代號 === selectedEtf && item.日期 === dateEnd);
            
            const oldMap = {};
            dataOld.forEach(item => { oldMap[item.股票代號] = item; });
            
            const merged = [];
            dataNew.forEach(item => {
                const oldItem = oldMap[item.股票代號];
                const oldShares = oldItem ? oldItem.持股股數 : 0;
                const oldWeight = oldItem ? oldItem.持股權重 : 0;
                
                merged.push({
                    id: item.股票代號,
                    name: item.股票名稱,
                    startShares: oldShares,
                    endShares: item.持股股數,
                    sharesDiff: item.持股股數 - oldShares,
                    startWeight: oldWeight,
                    endWeight: item.持股權重,
                    weightDiff: item.持股權重 - oldWeight
                });
                delete oldMap[item.股票代號];
            });
            
            // 補齊被完全剔除的持股
            for (const key in oldMap) {
                const item = oldMap[key];
                merged.push({
                    id: item.股票代號,
                    name: item.股票名稱,
                    startShares: item.持股股數,
                    endShares: 0,
                    sharesDiff: -item.持股股數,
                    startWeight: item.持股權重,
                    endWeight: 0,
                    weightDiff: -item.持股權重
                });
            }
            
            currentSingleTableData = merged;
            renderSingleTable();
            renderSingleSummary(selectedEtf, dateStart, dateEnd, dataNew.length);
            analyzeETFStyle(selectedEtf, dateStart, dateEnd);
        }

        function renderSingleTable() {
            // 排序
            currentSingleTableData.sort((a, b) => {
                let valA, valB;
                switch (singleSortCol) {
                    case 0: valA = a.id; valB = b.id; break;
                    case 1: valA = a.name; valB = b.name; break;
                    case 2: valA = a.startShares; valB = b.startShares; break;
                    case 3: valA = a.endShares; valB = b.endShares; break;
                    case 4: valA = a.sharesDiff; valB = b.sharesDiff; break;
                    case 5: valA = a.startWeight; valB = b.startWeight; break;
                    case 6: valA = a.endWeight; valB = b.endWeight; break;
                    case 7: valA = a.weightDiff; valB = b.weightDiff; break;
                }
                
                if (typeof valA === 'string') {
                    return singleSortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
                } else {
                    return singleSortAsc ? valA - valB : valB - valA;
                }
            });
            
            const tbody = document.getElementById('singleTableBody');
            tbody.innerHTML = '';
            
            currentSingleTableData.forEach(row => {
                const sharesDiffClass = row.sharesDiff > 0 ? 'text-up font-monospace' : (row.sharesDiff < 0 ? 'text-down font-monospace' : 'text-muted font-monospace');
                const weightDiffClass = row.weightDiff > 0 ? 'text-up font-monospace' : (row.weightDiff < 0 ? 'text-down font-monospace' : 'text-muted font-monospace');
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="font-monospace">${row.id}</td>
                    <td class="fw-bold">${row.name}</td>
                    <td class="text-end font-monospace">${row.startShares.toLocaleString()}</td>
                    <td class="text-end font-monospace">${row.endShares.toLocaleString()}</td>
                    <td class="text-end ${sharesDiffClass}">${row.sharesDiff > 0 ? '+' : ''}${row.sharesDiff.toLocaleString()}</td>
                    <td class="text-end font-monospace">${row.startWeight.toFixed(2)}%</td>
                    <td class="text-end font-monospace">${row.endWeight.toFixed(2)}%</td>
                    <td class="text-end ${weightDiffClass}">${row.weightDiff > 0 ? '+' : ''}${row.weightDiff.toFixed(2)}%</td>
                `;
                tbody.appendChild(tr);
            });
        }

        function sortSingleTable(colIdx) {
            if (singleSortCol === colIdx) {
                singleSortAsc = !singleSortAsc;
            } else {
                singleSortCol = colIdx;
                singleSortAsc = true;
            }
            renderSingleTable();
        }

        function renderSingleSummary(etfId, dateStart, dateEnd, count) {
            const sumCard = document.getElementById('etfSummaryCard');
            sumCard.style.display = 'block';
            
            // 找尋 WantGoo 數據
            const wg = wantgooData[etfId] || { price: 'N/A', nav: 'N/A', discount: 'N/A' };
            const priceStr = typeof wg.price === 'number' ? `${wg.price.toFixed(2)}` : 'N/A';
            const navStr = typeof wg.nav === 'number' ? `${wg.nav.toFixed(2)}` : 'N/A';
            const discountStr = typeof wg.discount === 'number' ? `${wg.discount.toFixed(2)}%` : 'N/A';
            const discountClass = wg.discount > 0 ? 'text-up font-monospace' : (wg.discount < 0 ? 'text-down font-monospace' : 'text-muted font-monospace');
            
            sumCard.innerHTML = `
                <div class="card-header-custom mb-3 border-bottom border-secondary pb-2">
                    <i class="bi bi-info-circle text-primary me-2"></i>即時行情與規格
                </div>
                <div class="row g-2 mb-2">
                    <div class="col-6">
                        <div class="text-muted small">最新即時市價</div>
                        <div class="metric-value text-white">${priceStr}</div>
                    </div>
                    <div class="col-6">
                        <div class="text-muted small">即時預估淨值</div>
                        <div class="metric-value text-warning">${navStr}</div>
                    </div>
                </div>
                <div class="row g-2 mb-2">
                    <div class="col-6">
                        <div class="text-muted small">即時折溢價幅度</div>
                        <div class="metric-value ${discountClass}">${discountStr}</div>
                    </div>
                    <div class="col-6">
                        <div class="text-muted small">當前持股檔數</div>
                        <div class="metric-value text-info font-monospace">${count} <span style="font-size:1rem;">檔</span></div>
                    </div>
                </div>
                <div class="text-muted mt-2" style="font-size:0.75rem;">
                    <div>*基準數據日期：${dateStart}</div>
                    <div>*折溢價與淨值由玩股網、證交所即時提供</div>
                </div>
            `;
        }

        // 經理人投資風格與持股診斷計算
        function analyzeETFStyle(selectedEtf, dateStart, dateEnd) {
            const styleCard = document.getElementById('etfStyleCard');
            styleCard.style.display = 'block';
            
            const etfHist = rawData.filter(item => item.ETF代號 === selectedEtf);
            const etfDates = [...new Set(etfHist.map(item => item.日期))].sort().reverse();
            const totalDates = etfDates.length || 1;

            const startRecords = etfHist.filter(r => r.日期 === dateStart);
            const endRecords = etfHist.filter(r => r.日期 === dateEnd);

            if (startRecords.length === 0 || endRecords.length === 0) {
                document.getElementById('etfTurnoverRate').textContent = 'N/A';
                document.getElementById('etfStyleLabel').textContent = '無足夠資料';
                document.getElementById('etfStyleLabel').className = 'badge bg-secondary';
                return;
            }

            const weightsStart = {};
            const weightsEnd = {};

            startRecords.forEach(r => { weightsStart[r.股票代號] = r.持股權重; });
            endRecords.forEach(r => { weightsEnd[r.股票代號] = r.持股權重; });

            // 計算期間換股率
            const unionStocks = new Set([...Object.keys(weightsStart), ...Object.keys(weightsEnd)]);
            let sumDiff = 0;
            unionStocks.forEach(stockId => {
                const wStart = weightsStart[stockId] || 0;
                const wEnd = weightsEnd[stockId] || 0;
                sumDiff += Math.abs(wStart - wEnd);
            });

            const turnoverRate = 0.5 * sumDiff;
            
            let styleLabel = "評估中";
            let badgeClass = "bg-secondary";
            if (turnoverRate < 10) {
                styleLabel = "長期價值投資";
                badgeClass = "bg-success";
            } else if (turnoverRate <= 30) {
                styleLabel = "穩健動態調整";
                badgeClass = "bg-warning text-dark";
            } else {
                styleLabel = "高頻波段交易";
                badgeClass = "bg-danger";
            }

            document.getElementById('etfTurnoverRate').textContent = turnoverRate.toFixed(2) + '%';
            const labelEl = document.getElementById('etfStyleLabel');
            labelEl.textContent = styleLabel;
            labelEl.className = `badge ${badgeClass}`;

            // 核心與衛星持股診斷
            const occurrenceCounts = {};
            etfHist.forEach(r => {
                occurrenceCounts[r.股票代號] = (occurrenceCounts[r.股票代號] || 0) + 1;
            });

            const coreList = [];
            const satelliteList = [];

            startRecords.forEach(r => {
                const stockId = r.股票代號;
                const stockName = r.股票名稱 || r.股票代號;
                const occurrences = occurrenceCounts[stockId] || 0;
                const occurrenceRate = occurrences / totalDates;
                const weight = r.持股權重;

                if (weight >= 5.0 && occurrenceRate >= 0.8) {
                    coreList.push({ name: stockName, id: stockId, weight, rate: occurrenceRate });
                } else if (weight < 2.0 && occurrenceRate < 0.3) {
                    satelliteList.push({ name: stockName, id: stockId, weight, rate: occurrenceRate });
                }
            });

            // 核心持股渲染
            const corePillsEl = document.getElementById('etfCorePills');
            document.getElementById('etfCoreCount').textContent = coreList.length + ' 檔';
            if (coreList.length > 0) {
                corePillsEl.innerHTML = coreList.map(item => 
                    `<span class="badge bg-success me-1 mb-1" style="font-size:0.75rem;" title="最新權重: ${item.weight.toFixed(2)}%, 歷史出現率: ${(item.rate*100).toFixed(0)}%">
                        ${item.name} (${item.id})
                    </span>`
                ).join('');
            } else {
                corePillsEl.innerHTML = '<span class="text-muted small">無符合核心持股</span>';
            }

            // 衛星持股渲染
            const satPillsEl = document.getElementById('etfSatellitePills');
            document.getElementById('etfSatelliteCount').textContent = satelliteList.length + ' 檔';
            if (satelliteList.length > 0) {
                satPillsEl.innerHTML = satelliteList.map(item => 
                    `<span class="badge bg-info text-dark me-1 mb-1" style="font-size:0.75rem;" title="最新權重: ${item.weight.toFixed(2)}%, 歷史出現率: ${(item.rate*100).toFixed(0)}%">
                        ${item.name} (${item.id})
                    </span>`
                ).join('');
            } else {
                satPillsEl.innerHTML = '<span class="text-muted small">無符合衛星持股</span>';
            }
        }

        // ------------------------------------------
        // 分頁 B：個股籌碼分佈邏輯
        // ------------------------------------------
        function initStockSelect() {
            const stockSelect = document.getElementById('stockSelect');
            stockSelect.innerHTML = '';
            
            // 計算個股在最新日期的曝光度
            const latestDate = dates[0];
            const activeStocks = [...new Set(rawData.filter(item => item.日期 === latestDate).map(item => item.股票代號))];
            
            activeStocks.forEach(stk => {
                const name = tickerMapping[stk] || stk;
                stockSelect.appendChild(new Option(`${stk} ${name}`, stk));
            });
        }

        function updateStockDistribution() {
            const stockVal = document.getElementById('stockSelect').value;
            if (!stockVal) return;
            
            const latestDate = dates[0];
            const records = rawData.filter(item => item.股票代號 === stockVal && item.日期 === latestDate);
            
            const tbody = document.getElementById('stockTableBody');
            tbody.innerHTML = '';
            
            records.forEach(r => {
                // 計算歷史平均持股權重
                const hist = rawData.filter(item => item.股票代號 === stockVal && item.ETF代號 === r.ETF代號);
                const avgWeight = hist.reduce((sum, x) => sum + x.持股權重, 0) / hist.length;
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="fw-bold font-monospace text-primary">${r.ETF代號}</td>
                    <td>${r.ETF名稱}</td>
                    <td class="text-end font-monospace">${r.持股股數.toLocaleString()}</td>
                    <td class="text-end font-monospace text-warning fw-bold">${r.持股權重.toFixed(2)}%</td>
                    <td class="text-end font-monospace text-muted">${avgWeight.toFixed(2)}%</td>
                `;
                tbody.appendChild(tr);
            });
            
            if (records.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 目前沒有任何主動式 ETF 持有此個股</td></tr>`;
            }
        }

        // ------------------------------------------
        // 分頁 C：AI 投資組合搜尋邏輯
        // ------------------------------------------
        function initAIChecklist() {
            const listDiv = document.getElementById('aiStockChecklist');
            listDiv.innerHTML = '';
            
            const latestDate = dates[0];
            const activeStocks = [...new Set(rawData.filter(item => item.日期 === latestDate).map(item => item.股票代號))].sort();
            
            activeStocks.forEach(stk => {
                const name = tickerMapping[stk] || stk;
                const div = document.createElement('div');
                div.className = 'form-check py-1';
                div.innerHTML = `
                    <input class="form-check-input" type="checkbox" value="${stk}" id="ai_cb_${stk}">
                    <label class="form-check-label text-light small cursor-pointer" for="ai_cb_${stk}">
                        <strong>${stk}</strong> <span class="text-muted" style="font-size:0.75rem;">${name}</span>
                    </label>
                `;
                listDiv.appendChild(div);
            });
        }

        function updateAISearch() {
            const checkedBoxes = document.querySelectorAll('#aiStockChecklist input[type="checkbox"]:checked');
            const targetStocks = Array.from(checkedBoxes).map(cb => cb.value);
            
            if (targetStocks.length === 0) {
                alert("請至少選擇 1 檔心儀的成分股");
                return;
            }
            
            const latestDate = dates[0];
            const results = [];
            
            etfs.forEach(etf => {
                const etfRecords = rawData.filter(r => r.ETF代號 === etf && r.日期 === latestDate);
                const matched = etfRecords.filter(r => targetStocks.includes(r.股票代號));
                
                if (matched.length > 0) {
                    const totalWeight = matched.reduce((sum, x) => sum + x.持股權重, 0);
                    const name = rawData.find(item => item.ETF代號 === etf).ETF名稱 || etf;
                    results.push({
                        id: etf,
                        name: name,
                        matchCount: matched.length,
                        totalWeight: totalWeight,
                        details: matched.map(m => `${m.股票名稱} (${m.持股權重.toFixed(1)}%)`).join(', ')
                    });
                }
            });
            
            // 排序：命中數量降序、累計權重降序
            results.sort((a, b) => b.matchCount !== a.matchCount ? b.matchCount - a.matchCount : b.totalWeight - a.totalWeight);
            
            const tbody = document.getElementById('aiTableBody');
            tbody.innerHTML = '';
            
            results.forEach((r, idx) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="text-center font-monospace"><span class="badge bg-secondary rounded-circle">${idx + 1}</span></td>
                    <td class="fw-bold">${r.name} <span class="text-muted font-monospace small">(${r.id})</span></td>
                    <td class="text-center font-monospace fw-bold text-success" style="font-size: 1.1rem;">${r.matchCount} / ${targetStocks.length}</td>
                    <td class="text-end font-monospace text-warning fw-bold">${r.totalWeight.toFixed(2)}%</td>
                    <td class="text-muted" style="font-size: 0.85rem;">${r.details}</td>
                `;
                tbody.appendChild(tr);
            });
            
            if (results.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 未找到任何持有此投資組合的主動型 ETF</td></tr>`;
            }
        }

        // ------------------------------------------
        // 分頁 D：經理人加減碼熱度分析
        // ------------------------------------------
        function initManagerSelects() {
            const startSelect = document.getElementById('managerDateStartSelect');
            const endSelect = document.getElementById('managerDateEndSelect');
            
            startSelect.innerHTML = '';
            endSelect.innerHTML = '';
            
            dates.forEach((d, idx) => {
                startSelect.appendChild(new Option(d, d));
                const opt = new Option(d, d);
                if (idx === Math.min(20, dates.length - 1)) opt.selected = true;
                endSelect.appendChild(opt);
            });
        }

        function toggleManagerCustomDates() {
            const tf = document.getElementById('managerTimeframeSelect').value;
            const group = document.getElementById('managerCustomDateGroup');
            if (tf === 'custom') group.classList.remove('d-none');
            else group.classList.add('d-none');
        }

        function getManagerCompareDates() {
            const tf = document.getElementById('managerTimeframeSelect').value;
            let dateStart, dateEnd;
            if (tf === 'custom') {
                dateStart = document.getElementById('managerDateStartSelect').value;
                dateEnd = document.getElementById('managerDateEndSelect').value;
                const idxStart = dates.indexOf(dateStart);
                const idxEnd = dates.indexOf(dateEnd);
                if (idxStart > idxEnd) {
                    const tmp = dateStart;
                    dateStart = dateEnd;
                    dateEnd = tmp;
                }
            } else {
                dateStart = dates[0];
                const offset = parseInt(tf);
                const targetIdx = Math.min(offset, dates.length - 1);
                dateEnd = dates[targetIdx];
            }
            return { dateStart, dateEnd };
        }

        function updateManagerHotness() {
            const { dateStart, dateEnd } = getManagerCompareDates();
            const threshold = parseFloat(document.getElementById('managerThreshold').value) || 0;
            
            const dataNew = rawData.filter(r => r.日期 === dateStart);
            const dataOld = rawData.filter(r => r.日期 === dateEnd);
            
            // 計算各股的累計權重變動
            const changeMap = {};
            
            // 初始化
            rawData.forEach(r => {
                if (!changeMap[r.股票代號]) {
                    changeMap[r.股票代號] = { name: r.股票名稱, diffSum: 0 };
                }
            });
            
            // 新權重
            dataNew.forEach(r => {
                if (changeMap[r.股票代號]) changeMap[r.股票代號].diffSum += r.持股權重;
            });
            
            // 扣除舊權重
            dataOld.forEach(r => {
                if (changeMap[r.股票代號]) changeMap[r.股票代號].diffSum -= r.持股權重;
            });
            
            // 轉為數組並過濾符合門檻的
            const list = [];
            for (const key in changeMap) {
                const item = changeMap[key];
                if (Math.abs(item.diffSum) >= threshold) {
                    list.push({ id: key, name: item.name, diff: item.diffSum });
                }
            }
            
            // 排序前 10 大加碼與前 10 大減碼
            list.sort((a, b) => b.diff - a.diff);
            
            const topAdd = list.slice(0, 10).reverse();
            const topSub = list.slice(-10);
            
            const finalData = [...topSub, ...topAdd];
            
            // 繪製 ECharts
            const chartDom = document.getElementById('managerHotnessChart');
            if (!managerChart) {
                managerChart = echarts.init(chartDom, 'dark');
            }
            
            const option = {
                backgroundColor: 'transparent',
                tooltip: {
                    trigger: 'axis',
                    axisPointer: { type: 'shadow' },
                    formatter: function(params) {
                        const p = params[0];
                        const color = p.value >= 0 ? '#ff4757' : '#2ed573';
                        return `<strong>${p.name}</strong><br/>全市場淨變動: <span style="color:${color}; font-weight:bold;">${p.value.toFixed(2)}%</span>`;
                    }
                },
                grid: { left: '3%', right: '4%', bottom: '3%', top: '5%', containLabel: true },
                xAxis: {
                    type: 'value',
                    splitLine: { lineStyle: { color: '#242736' } }
                },
                yAxis: {
                    type: 'category',
                    data: finalData.map(item => `${item.name} (${item.id})`),
                    axisLabel: { color: '#e1e3e8' }
                },
                series: [
                    {
                        name: '權重變動',
                        type: 'bar',
                        data: finalData.map(item => item.diff),
                        itemStyle: {
                            color: function(params) {
                                return params.value >= 0 ? '#ff4757' : '#2ed573';
                            }
                        }
                    }
                ]
            };
            
            managerChart.setOption(option);
        }

        // ------------------------------------------
        // 分頁 E：核心與特色持股對比邏輯
        // ------------------------------------------
        function initCompareChecklist() {
            const listDiv = document.getElementById('compareEtfChecklist');
            listDiv.innerHTML = '';
            
            etfs.forEach(etf => {
                const name = rawData.find(item => item.ETF代號 === etf).ETF名稱 || etf;
                const div = document.createElement('div');
                div.className = 'form-check py-1';
                div.innerHTML = `
                    <input class="form-check-input" type="checkbox" value="${etf}" id="comp_cb_${etf}" checked>
                    <label class="form-check-label text-light small cursor-pointer" for="comp_cb_${etf}">
                        <strong>${etf}</strong> <span class="text-muted" style="font-size:0.75rem;">${name}</span>
                    </label>
                `;
                listDiv.appendChild(div);
            });
        }

        function updateCompare() {
            const checkedBoxes = document.querySelectorAll('#compareEtfChecklist input[type="checkbox"]:checked');
            const checkedCbs = Array.from(checkedBoxes).map(cb => cb.value);
            
            if (checkedCbs.length < 2) {
                alert("請至少選擇 2 檔 ETF 進行交叉對比");
                return;
            }
            
            const latestDate = dates[0];
            const coreCard = document.getElementById('compareCoreCard');
            const uniqueCard = document.getElementById('compareUniqueCard');
            
            // 收集所選 ETF 之成分股權重對照
            const weightMap = {}; // stockId -> { name, etfs: { etfId: weight } }
            
            checkedCbs.forEach(etf => {
                const records = rawData.filter(r => r.ETF代號 === etf && r.日期 === latestDate);
                records.forEach(r => {
                    if (!weightMap[r.股票代號]) {
                        weightMap[r.股票代號] = { name: r.股票名稱, etfs: {} };
                    }
                    weightMap[r.股票代號].etfs[etf] = r.持股權重;
                });
            });
            
            // 構建表頭
            let headHtml = `<tr><th>股票名稱/代號</th>`;
            checkedCbs.forEach(etf => {
                headHtml += `<th class="text-end">${etf} 權重</th>`;
            });
            headHtml += `</tr>`;
            document.getElementById('compareCoreHead').innerHTML = headHtml;
            document.getElementById('compareUniqueHead').innerHTML = headHtml;
            
            let coreRowsHtml = '';
            let uniqueRowsHtml = '';
            
            Object.keys(weightMap).sort().forEach(stockId => {
                const item = weightMap[stockId];
                let hits = 0;
                let rowHtml = `<td><strong>${item.name}</strong> <span class="text-muted small font-monospace">(${stockId})</span></td>`;
                let isFullCore = true;
                
                checkedCbs.forEach(etf => {
                    const weight = item.etfs[etf] || 0;
                    if (weight > 0) hits++;
                    if (weight < 5.0) isFullCore = false; // 非皆為核心 (大於 5%)
                    
                    const displayVal = weight > 0 ? `${weight.toFixed(2)}%` : '--';
                    let cellClass = 'text-muted';
                    if (weight >= 5.0) cellClass = 'weight-high';
                    else if (weight > 0) cellClass = 'weight-low';
                    
                    rowHtml += `<td class="text-end font-monospace ${cellClass}">${displayVal}</td>`;
                });
                
                rowHtml = `<tr>${rowHtml}</tr>`;
                
                if (hits === checkedCbs.length && isFullCore) {
                    coreRowsHtml += rowHtml;
                } else if (hits === 1) { // 僅被其中一檔 ETF 持有的特色持股
                    uniqueRowsHtml += rowHtml;
                }
            });
            
            coreCard.style.display = 'block';
            document.getElementById('compareCoreTableBody').innerHTML = coreRowsHtml || `<tr><td colspan="${1 + checkedCbs.length}" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 所選定的 ETF 組合之間目前無任何完全重疊的核心持股(&ge;5%)</td></tr>`;
            
            uniqueCard.style.display = 'block';
            document.getElementById('compareUniqueTableBody').innerHTML = uniqueRowsHtml || `<tr><td colspan="${1 + checkedCbs.length}" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 無個別差異特色持股</td></tr>`;
        }

        // ------------------------------------------
        // 分頁 G：共識雷達與反向指標邏輯
        // ------------------------------------------
        function toggleRadarCustomDates() {
            const tf = document.getElementById('radarTimeframeSelect').value;
            const group = document.getElementById('radarCustomDateGroup');
            if (tf === 'custom') group.classList.remove('d-none');
            else group.classList.add('d-none');
        }

        function selectAllRadarEtfs(checked) {
            const checkboxes = document.querySelectorAll('#radarEtfList input[type="checkbox"]');
            checkboxes.forEach(cb => { cb.checked = checked; });
            updateRadar();
        }

        function initRadar() {
            const startSelect = document.getElementById('radarDateStartSelect');
            const endSelect = document.getElementById('radarDateEndSelect');
            
            startSelect.innerHTML = '';
            endSelect.innerHTML = '';
            
            dates.forEach((d, idx) => {
                startSelect.appendChild(new Option(d, d));
                const opt = new Option(d, d);
                if (idx === Math.min(20, dates.length - 1)) opt.selected = true;
                endSelect.appendChild(opt);
            });
            
            // 建立主動式 ETF 名稱對照與清單
            const etfMap = {};
            rawData.forEach(item => {
                if (!etfMap[item.ETF代號]) {
                    etfMap[item.ETF代號] = item.ETF名稱 || item.ETF代號;
                }
            });
            
            const listEl = document.getElementById('radarEtfList');
            listEl.innerHTML = '';
            
            Object.keys(etfMap).sort().forEach(etfId => {
                const div = document.createElement('div');
                div.className = 'form-check py-1';
                div.innerHTML = `
                    <input class="form-check-input" type="checkbox" value="${etfId}" id="radar_cb_${etfId}" checked onchange="updateRadar()">
                    <label class="form-check-label text-light small cursor-pointer" for="radar_cb_${etfId}">
                        <strong>${etfId}</strong> <span class="text-muted" style="font-size:0.75rem;">${etfMap[etfId]}</span>
                    </label>
                `;
                listEl.appendChild(div);
            });
            
            updateRadar();
        }

        function updateRadar() {
            const tf = document.getElementById('radarTimeframeSelect').value;
            let dateNewer, dateOlder;
            
            if (tf === 'custom') {
                dateNewer = document.getElementById('radarDateStartSelect').value;
                dateOlder = document.getElementById('radarDateEndSelect').value;
                const idxNewer = dates.indexOf(dateNewer);
                const idxOlder = dates.indexOf(dateOlder);
                if (idxNewer > idxOlder) {
                    const tmp = dateNewer;
                    dateNewer = dateOlder;
                    dateOlder = tmp;
                }
            } else {
                dateNewer = dates[0];
                const offset = parseInt(tf);
                const targetIdx = Math.min(offset, dates.length - 1);
                dateOlder = dates[targetIdx];
            }
            
            const checkedCbs = document.querySelectorAll('#radarEtfList input[type="checkbox"]:checked');
            const selectedEtfs = Array.from(checkedCbs).map(cb => cb.value);
            
            if (selectedEtfs.length === 0) {
                document.getElementById('radarAddTableBody').innerHTML = `<tr><td colspan="4" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 請勾選至少一檔主動式 ETF 進行分析</td></tr>`;
                document.getElementById('radarSubTableBody').innerHTML = `<tr><td colspan="4" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 請勾選至少一檔主動式 ETF 進行分析</td></tr>`;
                document.getElementById('consensusAddCount').textContent = '0 檔';
                document.getElementById('consensusSubCount').textContent = '0 檔';
                return;
            }
            
            const recordsNewer = rawData.filter(r => r.日期 === dateNewer && selectedEtfs.includes(r.ETF代號));
            const recordsOlder = rawData.filter(r => r.日期 === dateOlder && selectedEtfs.includes(r.ETF代號));
            
            const newerMap = {};
            const olderMap = {};
            
            recordsNewer.forEach(r => {
                if (!newerMap[r.ETF代號]) newerMap[r.ETF代號] = {};
                newerMap[r.ETF代號][r.股票代號] = { shares: r.持股股數, weight: r.持股權重, name: r.股票名稱 };
            });
            
            recordsOlder.forEach(r => {
                if (!olderMap[r.ETF代號]) olderMap[r.ETF代號] = {};
                olderMap[r.ETF代號][r.股票代號] = { shares: r.持股股數, weight: r.持股權重, name: r.股票名稱 };
            });
            
            const allStockIds = new Set();
            recordsNewer.forEach(r => allStockIds.add(r.股票代號));
            recordsOlder.forEach(r => allStockIds.add(r.股票代號));
            
            const stockNames = {};
            rawData.forEach(r => {
                if (!stockNames[r.股票代號]) stockNames[r.股票代號] = r.股票名稱 || r.股票代號;
            });
            
            const stockConsensus = {};
            
            allStockIds.forEach(stockId => {
                stockConsensus[stockId] = {
                    stockName: stockNames[stockId] || stockId,
                    addedBy: [],
                    reducedBy: []
                };
                
                selectedEtfs.forEach(etfId => {
                    const itemNew = (newerMap[etfId] || {})[stockId];
                    const itemOld = (olderMap[etfId] || {})[stockId];
                    
                    const sNew = itemNew ? itemNew.shares : 0;
                    const sOld = itemOld ? itemOld.shares : 0;
                    
                    const wNew = itemNew ? itemNew.weight : 0;
                    const wOld = itemOld ? itemOld.weight : 0;
                    
                    const sDiff = sNew - sOld;
                    const wDiff = wNew - wOld;
                    
                    if (sDiff > 0) {
                        stockConsensus[stockId].addedBy.push({ etfId, sDiff, wDiff });
                    } else if (sDiff < 0) {
                        stockConsensus[stockId].reducedBy.push({ etfId, sDiff, wDiff });
                    }
                });
            });
            
            // 過濾並排序 黃金共識股
            const addList = Object.keys(stockConsensus)
                .map(id => ({ stockId: id, ...stockConsensus[id] }))
                .filter(item => item.addedBy.length > 0)
                .sort((a, b) => {
                    if (b.addedBy.length !== a.addedBy.length) {
                        return b.addedBy.length - a.addedBy.length;
                    }
                    const aW = a.addedBy.reduce((sum, x) => sum + x.wDiff, 0);
                    const bW = b.addedBy.reduce((sum, x) => sum + x.wDiff, 0);
                    return bW - aW;
                });
            
            // 過濾並排序 避險警示股
            const subList = Object.keys(stockConsensus)
                .map(id => ({ stockId: id, ...stockConsensus[id] }))
                .filter(item => item.reducedBy.length > 0)
                .sort((a, b) => {
                    if (b.reducedBy.length !== a.reducedBy.length) {
                        return b.reducedBy.length - a.reducedBy.length;
                    }
                    const aW = a.reducedBy.reduce((sum, x) => sum + x.wDiff, 0);
                    const bW = b.reducedBy.reduce((sum, x) => sum + x.wDiff, 0);
                    return aW - bW;
                });
            
            // 渲染黃金共識
            const addTable = document.getElementById('radarAddTableBody');
            document.getElementById('consensusAddCount').textContent = addList.length + ' 檔';
            if (addList.length === 0) {
                addTable.innerHTML = `<tr><td colspan="4" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 期間內無任何主動式 ETF 加碼之股票</td></tr>`;
            } else {
                addTable.innerHTML = addList.map((item, idx) => {
                    const badges = item.addedBy.map(x => 
                        `<span class="badge bg-success-subtle text-success border border-success me-1 mb-1" style="font-size:0.75rem;" title="股數增加: +${x.sDiff.toLocaleString()} / 權重增加: +${x.wDiff.toFixed(2)}%">
                            ${x.etfId}
                        </span>`
                    ).join('');
                    return `
                        <tr>
                            <td class="text-center font-monospace"><span class="badge bg-secondary rounded-circle">${idx + 1}</span></td>
                            <td>
                                <div class="fw-bold text-white">${item.stockName}</div>
                                <div class="text-muted small font-monospace">${item.stockId}</div>
                            </td>
                            <td class="text-center">
                                <span class="h6 mb-0 text-success fw-bold font-monospace">${item.addedBy.length}</span> <span class="text-muted small">檔 ETF</span>
                            </td>
                            <td><div class="d-flex flex-wrap">${badges}</div></td>
                        </tr>
                    `;
                }).join('');
            }
            
            // 渲染避險警示
            const subTable = document.getElementById('radarSubTableBody');
            document.getElementById('consensusSubCount').textContent = subList.length + ' 檔';
            if (subList.length === 0) {
                subTable.innerHTML = `<tr><td colspan="4" class="text-center py-4 text-muted"><i class="bi bi-info-circle me-1"></i> 期間內無任何主動式 ETF 減碼之股票</td></tr>`;
            } else {
                subTable.innerHTML = subList.map((item, idx) => {
                    const badges = item.reducedBy.map(x => 
                        `<span class="badge bg-danger-subtle text-danger border border-danger me-1 mb-1" style="font-size:0.75rem;" title="股數減少: -${Math.abs(x.sDiff).toLocaleString()} / 權重減少: ${x.wDiff.toFixed(2)}%">
                            ${x.etfId}
                        </span>`
                    ).join('');
                    return `
                        <tr>
                            <td class="text-center font-monospace"><span class="badge bg-secondary rounded-circle">${idx + 1}</span></td>
                            <td>
                                <div class="fw-bold text-white">${item.stockName}</div>
                                <div class="text-muted small font-monospace">${item.stockId}</div>
                            </td>
                            <td class="text-center">
                                <span class="h6 mb-0 text-danger fw-bold font-monospace">${item.reducedBy.length}</span> <span class="text-muted small">檔 ETF</span>
                            </td>
                            <td><div class="d-flex flex-wrap">${badges}</div></td>
                        </tr>
                    `;
                }).join('');
            }
        }

        // 監聽螢幕 RWD 縮放
        window.addEventListener('resize', function() {
            if (managerChart) managerChart.resize();
        });
    </script>
</body>
</html>
"""

# 將整合後的 JSON 載入 HTML 中渲染
final_html = html_template.replace(
    "__DATA_PLACEHOLDER__", json_data
).replace(
    "__WANTGOO_PLACEHOLDER__", wantgoo_json
).replace(
    "__TWSE_PLACEHOLDER__", twse_json
).replace(
    "__TICKER_PLACEHOLDER__", ticker_json
)

# 渲染完整前端頁面
components.html(final_html, height=1050, scrolling=True)
