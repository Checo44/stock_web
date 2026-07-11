import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os
import requests
from google.oauth2.service_account import Credentials

# ==========================================
# 1. 網頁基本設定與首頁滿版樣式 (源自 app (1).py)
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide", initial_sidebar_state="collapsed")

# 隱藏 Streamlit 原生元件，確保極簡科技感外觀
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
WORKSHEET_ETF_NAME = "名稱"    

# ==========================================
# 2. 安全資料載入核心
# ==========================================
@st.cache_resource(ttl=3600)
def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]
    
    if not creds_json:
        st.error("未找到 GOOGLE_CREDENTIALS 憑證，請檢查環境變數設定。")
        st.stop()
        
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)

@st.cache_data(ttl=600)
def load_all_data():
    try:
        gc = get_sheets_client()
        sh = gc.open(SHEET_NAME)
        
        # 讀取主要歷史資料
        ws_hist = sh.worksheet(WORKSHEET_HISTORY)
        df_hist = pd.DataFrame(ws_hist.get_all_records())
        
        # 讀取代號與名稱對照表
        try:
            df_ticker = pd.DataFrame(sh.worksheet(WORKSHEET_TICKER).get_all_records())
        except:
            df_ticker = pd.DataFrame(columns=['stock', 'name'])
            
        try:
            df_etf_name = pd.DataFrame(sh.worksheet(WORKSHEET_ETF_NAME).get_all_records())
        except:
            df_etf_name = pd.DataFrame(columns=['etf', 'etf_name'])
            
        return df_hist, df_ticker, df_etf_name
    except Exception as e:
        st.error(f"資料讀取失敗: {str(e)}")
        st.stop()

# 載入後端資料
df_hist, df_ticker, df_etf_name = load_all_data()

# 轉換為前端 JavaScript 易讀取的 JSON 字串
json_data = df_hist.to_json(orient="records", force_ascii=False)
ticker_json = df_ticker.to_json(orient="records", force_ascii=False)
etf_name_json = df_etf_name.to_json(orient="records", force_ascii=False)

# 模擬外部市場熱度資料來源（可串接實際 API）
wantgoo_json = json.dumps([]) 
twse_json = json.dumps([])

# ==========================================
# 3. 前端 HTML / CSS / JS 模板整合
# ==========================================
html_template = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF 籌碼大數據監控面板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+TC:wght@300;400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-main: #f8f9fa;
            --card-bg: #ffffff;
            --text-dark: #212529;
            --primary-color: #0d6efd;
        }
        body {
            font-family: 'Inter', 'Noto Sans TC', sans-serif;
            background-color: var(--bg-main);
            color: var(--text-dark);
            font-size: 0.925rem;
        }
        .navbar-brand-custom {
            font-weight: 700;
            letter-spacing: -0.5px;
            color: #111;
        }
        .nav-tabs .nav-link {
            border: none;
            color: #6c757d;
            font-weight: 500;
            padding: 1rem 1.25rem;
            border-bottom: 2px solid transparent;
        }
        .nav-tabs .nav-link.active {
            color: var(--primary-color);
            border-bottom: 2px solid var(--primary-color);
            background: none;
        }
        .card {
            border: 1px solid rgba(0,0,0,.08);
            border-radius: 12px;
            box-shadow: 0 2px 4px rgba(0,0,0,.02);
            background-color: var(--card-bg);
        }
        /* 市場熱度排行專用樣式 (源自 app.py 排版) */
        .heat-rank-card {
            background: linear-gradient(145deg, #ffffff, #f1f3f5);
            border-left: 4px solid #fd7e14;
        }
        .rank-number {
            font-size: 1.5rem;
            font-weight: 700;
            color: #fd7e14;
        }
        .font-monospace {
            font-family: 'SFMono-Regular', Menlo, Monaco, Consolas, monospace !important;
        }
    </style>
</head>
<body>

    <nav class="navbar navbar-expand-lg navbar-light bg-white border-bottom sticky-top py-2">
        <div class="container-fluid px-4">
            <span class="navbar-brand navbar-brand-custom flex-grow-1">📊 ETF 籌碼大數據監控面板</span>
            <ul class="nav nav-tabs border-0" id="panelTabs" role="tablist">
                <li class="nav-item">
                    <button class="nav-link active" id="single-tab" data-bs-toggle="tab" data-bs-target="#single-etf" type="button" role="tab">單檔 ETF 籌碼與持股</button>
                </li>
                <li class="nav-item">
                    <button class="nav-link" id="heat-tab" data-bs-toggle="tab" data-bs-target="#heat-rank" type="button" role="tab">市場熱度排行</button>
                </li>
            </ul>
        </div>
    </nav>

    <div class="container-fluid p-4">
        <div class="tab-content" id="panelTabContent">
            
            <div class="tab-pane fade show active" id="single-etf" role="tabpanel">
                <div class="row g-4">
                    <div class="col-md-3">
                        <div class="card p-3 mb-3">
                            <label class="form-label fw-bold text-secondary mb-2">選擇監控 ETF</label>
                            <select class="form-select shadow-sm" id="etfSelector" onchange="renderSingleEtf()"></select>
                        </div>
                        <div id="etfSummaryCard"></div>
                    </div>
                    <div class="col-md-9">
                        <div class="card p-4">
                            <h5 class="fw-bold mb-3">成分股持股權重明細</h5>
                            <div class="table-responsive">
                                <table class="table table-hover align-middle">
                                    <thead class="table-light">
                                        <tr>
                                            <th>股票代號</th>
                                            <th>股票名稱</th>
                                            <th class="text-end">目前持股權重</th>
                                        </tr>
                                    </thead>
                                    <tbody id="compareTableBody">
                                        </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="heat-rank" role="tabpanel">
                <div class="row row-cols-1 row-cols-md-3 g-4" id="heatRankContainer">
                    </div>
            </div>

        </div>
    </div>

    <script>
        const globalRawData = __DATA_PLACEHOLDER__;
        const globalTickerMap = __TICKER_PLACEHOLDER__;
        const globalEtfNameMap = __ETF_NAME_PLACEHOLDER__;
        
        // ==========================================
        // 4. 資產篩選邏輯核心 (完整沿用 app.py 邏輯)
        // ==========================================
        function isNormalStock(code, name) {
            if (!code) return false;
            code = code.toString().trim();
            name = name ? name.toString().trim() : '';
            
            // 規則 1: 普通股代號通常為 4 碼純數字
            if (code.length !== 4 || isNaN(code)) return false;
            
            // 規則 2: 排除含有特定字眼的非普通股資產項目 (如債券、期貨、反向基金等)
            if (name.includes("債") || name.includes("美債") || name.includes("正2") || name.includes("反1") || name.includes("期")) {
                return false;
            }
            return true;
        }

        // 初始化處理
        let allDates = [...new Set(globalRawData.map(x => x.date))].sort().reverse();
        let latestDate = allDates[0];

        window.onload = function() {
            initSelectors();
            renderSingleEtf();
            renderHeatRank();
        };

        function initSelectors() {
            let etfCodes = [...new Set(globalRawData.map(x => x.etf))];
            let selector = document.getElementById('etfSelector');
            selector.innerHTML = etfCodes.map(code => {
                let match = globalEtfNameMap.find(e => e.etf === code);
                let name = match ? match.etf_name : '';
                return `<option value="${code}">${code} ${name}</option>`;
            }).join('');
        }

        // 渲染單檔 ETF 籌碼面與持股明細 (採用 app (1).py 的 Badge 與表格外觀)
        function renderSingleEtf() {
            let selectedEtf = document.getElementById('etfSelector').value;
            let body = document.getElementById('compareTableBody');
            
            // 應用 app.py 的資產篩選過濾邏輯
            let etfData = globalRawData.filter(r => r.date === latestDate && r.etf === selectedEtf && isNormalStock(r.stock, r.name));
            
            // 若經篩選後無對應普通股，顯示 app (1).py 標準的無標的提示
            if (etfData.length === 0) {
                body.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-4">所勾選的 ETF 組合中目前無共同持股標的或無普通股資產</td></tr>';
                return;
            }

            // 排序權證由大到小
            etfData.sort((a, b) => Number(b.weight) - Number(a.weight));

            body.innerHTML = etfData.map(r => {
                let w = Number(r.weight);
                return `
                    <tr>
                        <td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td>
                        <td class="fw-bold">${r.name}</td>
                        <td class="text-end font-monospace text-primary fw-bold">${w.toFixed(2)}%</td>
                    </tr>
                `;
            }).join('');
        }

        // 渲染市場熱度排行 (採用 app.py 的專屬多欄位卡片與排行指標排版)
        function renderHeatRank() {
            let container = document.getElementById('heatRankContainer');
            
            // 計算熱門資產標的 (基於最新日期且符合 isNormalStock 條件的出現頻率與累計權重)
            let stockCounter = {};
            globalRawData.filter(r => r.date === latestDate && isNormalStock(r.stock, r.name)).forEach(r => {
                if(!stockCounter[r.stock]) {
                    stockCounter[r.stock] = { name: r.name, count: 0, totalWeight: 0 };
                }
                stockCounter[r.stock].count += 1;
                stockCounter[r.stock].totalWeight += Number(r.weight);
            });

            let sortedRank = Object.keys(stockCounter).map(code => ({
                code: code,
                ...stockCounter[code]
            })).sort((a, b) => b.totalWeight - a.totalWeight).slice(0, 9); // 取前 9 名

            if(sortedRank.length === 0) {
                container.innerHTML = '<div class="col-12 text-center text-muted py-4">暫無熱度排行統計資料</div>';
                return;
            }

            container.innerHTML = sortedRank.map((item, index) => `
                <div class="col">
                    <div class="card p-3 heat-rank-card h-100 d-flex align-items-center justify-content-between flex-row">
                        <div>
                            <div class="text-muted small fw-bold">RANK</div>
                            <div class="rank-number">0${index + 1}</div>
                        </div>
                        <div class="text-end">
                            <span class="badge bg-dark font-monospace mb-1">${item.code}</span>
                            <div class="fw-bold text-truncate" style="max-width: 150px;">${item.name}</div>
                            <div class="small text-secondary mt-1">
                                被引核心：<span class="text-dark fw-bold">${item.count}</span> 檔 ETF<br>
                                權重加總：<span class="text-primary fw-bold">${item.totalWeight.toFixed(2)}%</span>
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        }
    </script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

# 將清洗替換的 JSON 資料注入前端
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

# ==========================================
# 5. 輸出滿版網頁組件
# ==========================================
components.html(final_html, height=920, scrolling=True)
