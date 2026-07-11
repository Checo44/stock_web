import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import gspread
import json
import os
import requests

# ==========================================
# 1. 網頁基本設定與隱藏 Streamlit 原生外框 (採用 app (1).py 全螢幕樣式)
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
# 2. 獨立安全的連線與資料載入核心 (採用 app (1).py)
# ==========================================
def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]
    
    if creds_json:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(creds_json)
        return gspread.service_account_from_dict(creds_dict, scopes=scopes)
    else:
        st.error("未找到 GOOGLE_CREDENTIALS 環境變數或 Secrets 設定。")
        st.stop()

@st.cache_data(ttl=300)
def load_all_data():
    try:
        gc = get_sheets_client()
        sh = gc.open(SHEET_NAME)
        
        # 讀取歷史資料
        ws_hist = sh.worksheet(WORKSHEET_HISTORY)
        df_hist = pd.DataFrame(ws_hist.get_all_records())
        
        # 讀取代號對照
        ws_tick = sh.worksheet(WORKSHEET_TICKER)
        df_tick = pd.DataFrame(ws_tick.get_all_records())
        
        # 讀取名稱對照
        ws_name = sh.worksheet(WORKSHEET_ETF_NAME)
        df_name = pd.DataFrame(ws_name.get_all_records())
        
        return df_hist, df_tick, df_name
    except Exception as e:
        st.error(f"從 Google Sheets 讀取資料失敗: {e}")
        st.stop()

# 載入後端資料
df_hist, df_tick, df_name = load_all_data()

# 將 DataFrames 轉為 JSON 供前端 JavaScript 運用
json_data = df_hist.to_json(orient="records", force_ascii=False)
ticker_json = df_tick.to_json(orient="records", force_ascii=False)
etf_name_json = df_name.to_json(orient="records", force_ascii=False)

# 模擬或對接外部市場資料 placeholder (可根據需求擴充)
wantgoo_json = json.dumps([])
twse_json = json.dumps([])

# ==========================================
# 3. 前端大數據監控面板大熔爐 (HTML + CSS + JS)
# ==========================================
# 融合了 app (1).py 的首頁排版風格與 app.py 的資產篩選邏輯、市場熱度排行
html_template = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ETF 籌碼大數據監控面板</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&family=Roboto+Mono:wght@400;500&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <style>
        /* 採用 app (1).py 的優雅深色/白底與首頁精緻樣式 */
        body {
            font-family: 'Noto Sans TC', sans-serif;
            background-color: #f4f7f6;
            color: #333;
        }
        .navbar-custom {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            padding: 1rem 2rem;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .card-custom {
            border: none;
            border-radius: 12px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.05);
            background: white;
            margin-bottom: 1.5rem;
            transition: transform 0.2s;
        }
        .card-custom:hover {
            transform: translateY(-2px);
        }
        .nav-tabs-custom .nav-link {
            border: none;
            color: #666;
            font-weight: 500;
            padding: 1rem 1.5rem;
            border-bottom: 3px solid transparent;
        }
        .nav-tabs-custom .nav-link.active {
            color: #1e3c72;
            border-bottom: 3px solid #1e3c72;
            background-color: transparent;
        }
        .badge-etf {
            background-color: #eef2f7;
            color: #1e3c72;
            font-weight: 600;
            border: 1px solid #d0daf0;
        }
        .table-custom th {
            background-color: #f8f9fa;
            color: #495057;
            font-weight: 600;
        }
        /* 修正字型對齊 */
        .font-monospace {
            font-family: 'Roboto Mono', monospace !important;
        }
    </style>
</head>
<body>

    <div class="navbar-custom d-flex justify-content-between align-items-center">
        <div class="d-flex align-items-center">
            <i class="fa-solid fa-chart-line fa-2x me-3"></i>
            <div>
                <h4 class="mb-0 fw-bold">ETF 籌碼大數據監控面板</h4>
                <small class="opacity-75">即時追蹤與交互比對系統</small>
            </div>
        </div>
        <div class="text-end">
            <span class="badge bg-white text-dark p-2" id="latestDateBadge">最新資料觀測日: --</span>
        </div>
    </div>

    <div class="container-fluid py-4 px-4">
        <ul class="nav nav-tabs nav-tabs-custom mb-4" id="mainTabs" role="tablist">
            <li class="nav-item">
                <button class="nav-link active" id="home-tab" data-bs-toggle="tab" data-bs-target="#homeContent" type="button"><i class="fa-solid fa-house me-2"></i>首頁總覽</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="single-tab" data-bs-toggle="tab" data-bs-target="#singleContent" type="button"><i class="fa-solid fa-pie-chart me-2"></i>單檔 ETF 籌碼與持股</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="heat-tab" data-bs-toggle="tab" data-bs-target="#heatContent" type="button"><i class="fa-solid fa-fire me-2"></i>市場熱度排行</button>
            </li>
        </ul>

        <div class="tab-content">
            <div class="tab-pane fade show active" id="homeContent" role="tabpanel">
                <div class="row">
                    <div class="col-md-3">
                        <div class="card card-custom p-3">
                            <h5 class="fw-bold mb-3 text-secondary"><i class="fa-solid fa-filter me-2"></i>選擇比對標的</h5>
                            <div id="etfCheckboxGroup" class="d-flex flex-column gap-2" style="max-height: 400px; overflow-y: auto;">
                                </div>
                        </div>
                    </div>
                    <div class="col-md-9">
                        <div class="card card-custom p-4">
                            <h5 class="fw-bold mb-3 text-primary"><i class="fa-solid fa-layer-group me-2"></i>交集核心持股交叉比對</h5>
                            <div class="table-responsive">
                                <table class="table table-striped table-hover table-custom align-middle">
                                    <thead>
                                        <tr id="compareTableHeader">
                                            <th>股票代號</th>
                                            <th>股票名稱</th>
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

            <div class="tab-pane fade" id="singleContent" role="tabpanel">
                <div class="card card-custom p-4">
                    <div class="row align-items-center mb-4">
                        <div class="col-md-4">
                            <label for="singleEtfSelect" class="form-label fw-bold text-secondary"><i class="fa-solid fa-magnifying-glass me-2"></i>選擇觀測單檔 ETF</label>
                            <select class="form-select form-select-lg" id="singleEtfSelect">
                                </select>
                        </div>
                        <div class="col-md-8 d-flex gap-3 justify-content-end align-items-center mt-3 mt-md-0" id="etfMetaBlock">
                            </div>
                    </div>
                    
                    <div class="row">
                        <div class="col-md-12">
                            <h5 class="fw-bold mb-3 text-dark"><i class="fa-solid fa-list-ol me-2"></i>完整持股明細與權重配置</h5>
                            <div class="table-responsive">
                                <table class="table table-bordered table-hover align-middle">
                                    <thead class="table-light">
                                        <tr>
                                            <th>排序</th>
                                            <th>股票代號</th>
                                            <th>股票名稱</th>
                                            <th>持股權重 (%)</th>
                                            <th>增減狀況</th>
                                        </tr>
                                    </thead>
                                    <tbody id="singleEtfTableBody">
                                        </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="heatContent" role="tabpanel">
                <div class="card card-custom p-4">
                    <div class="d-flex justify-content-between align-items-center mb-4">
                        <h5 class="fw-bold mb-0 text-danger"><i class="fa-solid fa-fire-flame-curved me-2"></i>全市場 ETF 資金熱度綜合排行</h5>
                        <span class="text-muted small">依全市場 ETF 持股總權重及覆蓋率綜合排序</span>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-hover table-custom align-middle" id="heatRankTable">
                            <thead>
                                <tr>
                                    <th scope="col" class="text-center" style="width: 80px;">排名</th>
                                    <th scope="col">股票代號</th>
                                    <th scope="col">股票名稱</th>
                                    <th scope="col" class="text-end">被持有 ETF 檔數</th>
                                    <th scope="col" class="text-end">加總權重得分</th>
                                    <th scope="col" class="text-center">市場監控狀態</th>
                                </tr>
                            </thead>
                            <tbody id="heatRankTableBody">
                                </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // 注入後端資料來源
        const globalRawData = __DATA_PLACEHOLDER__;
        const tickerMapData = __TICKER_PLACEHOLDER__;
        const etfNameMapData = __ETF_NAME_PLACEHOLDER__;

        // ==========================================
        // 核心邏輯 A: 引入 app.py 的資產篩選邏輯
        // ==========================================
        function isNormalStock(stockCode, stockName) {
            if (!stockCode) return false;
            let codeStr = String(stockCode).trim();
            let nameStr = String(stockName || '').trim();
            
            // 排除現金、放款、期貨等非正常個股資產項目
            if (codeStr === '世紀鋼' || nameStr.includes('現金') || nameStr.includes('USD') || nameStr.includes('TWD') || nameStr.includes('剩餘款')) {
                return false;
            }
            if (nameStr.includes('期貨') || nameStr.includes('保證金') || nameStr.includes('正2') || nameStr.includes('反1')) {
                return false;
            }
            // 台灣標準個股通常為4位數字或含認購權證代碼，過濾太長或包含特殊外幣代號
            if (codeStr.length > 6) return false;
            
            return true;
        }

        // 取得最新日期
        const allDates = [...new Set(globalRawData.map(x => x.date))].sort().reverse();
        const latestDate = allDates[0] || '';
        document.getElementById('latestDateBadge').innerText = `最新資料觀測日: ${latestDate}`;

        // 取得不重複的 ETF 清單
        const uniqueEtfs = [...new Set(globalRawData.filter(x => x.date === latestDate).map(x => x.etf))];

        // 初始化：渲染首頁 ETF 核取方塊
        const checkboxGroup = document.getElementById('etfCheckboxGroup');
        uniqueEtfs.forEach((etfCode, index) => {
            let etfObj = etfNameMapData.find(x => String(x.code) === String(etfCode));
            let etfName = etfObj ? etfObj.name : '未知型ETF';
            
            let div = document.createElement('div');
            div.className = 'form-check';
            div.innerHTML = `
                <input class="form-check-input etf-cb" type="checkbox" value="${etfCode}" id="cb_${etfCode}" ${index < 3 ? 'checked' : ''}>
                <label class="form-check-label text-truncate" for="cb_${etfCode}" style="max-width: 220px;">
                    <span class="badge badge-etf me-1">${etfCode}</span>${etfName}
                </label>
            `;
            checkboxGroup.appendChild(div);
        });

        // 初始化：渲染單檔 ETF 選項 (採用 app (1).py 邏輯)
        const singleSelect = document.getElementById('singleEtfSelect');
        uniqueEtfs.forEach(etfCode => {
            let etfObj = etfNameMapData.find(x => String(x.code) === String(etfCode));
            let etfName = etfObj ? etfObj.name : '';
            let opt = document.createElement('option');
            opt.value = etfCode;
            opt.innerText = `${etfCode} - ${etfName}`;
            singleSelect.appendChild(opt);
        });

        // 綁定事件
        document.querySelectorAll('.etf-cb').forEach(cb => cb.addEventListener('change', renderCompareTable));
        singleSelect.addEventListener('change', renderSingleEtf);

        // 執行首次渲染
        renderCompareTable();
        renderSingleEtf();
        renderHeatRanking();

        // ==========================================
        // 渲染首頁交叉比對表 (使用 app (1).py 的表格與提示文字樣式)
        // ==========================================
        function getEtfLabel(code) {
            let found = etfNameMapData.find(x => String(x.code) === String(code));
            return found ? found.name.substring(0,4) : code;
        }

        function renderCompareTable() {
            let checkedCbs = Array.from(document.querySelectorAll('.etf-cb:checked')).map(cb => cb.value);
            let header = document.getElementById('compareTableHeader');
            
            // 重置表頭
            header.innerHTML = '<th>股票代號</th><th>股票名稱</th>' + checkedCbs.map(c => `<th class="text-end">${getEtfLabel(c)}<br><span class="text-muted font-monospace" style="font-size:11px;">${c}</span></th>`).join('');

            let stockMap = {};
            globalRawData.forEach(r => {
                // 導入 app.py 的資產篩選過濾邏輯：isNormalStock
                if (r.date === latestDate && checkedCbs.includes(r.etf) && isNormalStock(r.stock, r.name)) {
                    stockMap[r.stock] = r.name;
                }
            });

            let body = document.getElementById('compareTableBody');
            body.innerHTML = Object.keys(stockMap).map(sCode => {
                let row = `<td><span class="badge bg-light text-dark font-monospace border">${sCode}</span></td><td class="fw-bold">${stockMap[sCode]}</td>`;
                checkedCbs.forEach(eCode => {
                    let match = globalRawData.find(x => x.date === latestDate && x.etf === eCode && x.stock === sCode);
                    let w = match ? Number(match.weight) : 0;
                    row += `<td class="text-end font-monospace ${w > 0 ? 'text-primary fw-bold' : 'text-muted'}">${w > 0 ? w.toFixed(2)+'%' : '-'}</td>`;
                });
                return `<tr>${row}</tr>`;
            }).join('') || '<tr><td colspan="' + (2 + checkedCbs.length) + '" class="text-center text-muted py-4">所勾選的 ETF 組合中目前無共同持股標的</td></tr>';
        }

        // ==========================================
        // 渲染單檔 ETF 明細 (採用 app (1).py 的完整功能與樣式)
        // ==========================================
        function renderSingleEtf() {
            let selectedEtf = singleSelect.value;
            if (!selectedEtf) return;

            // 算歷史前一期以計算增減 (模擬或尋找次新日期)
            let prevDate = allDates[1] || '';

            let currentHoldings = globalRawData.filter(x => x.date === latestDate && x.etf === selectedEtf);
            // 單檔展示亦套用資產過濾，使呈現資訊乾淨
            currentHoldings = currentHoldings.filter(x => isNormalStock(x.stock, x.name));
            currentHoldings.sort((a,b) => Number(b.weight) - Number(a.weight));

            // 更新 Meta 資訊欄位 (app (1).py 風格)
            let etfMetaBlock = document.getElementById('etfMetaBlock');
            let totalWeightCombined = currentHoldings.reduce((acc, x) => acc + Number(x.weight), 0);
            etfMetaBlock.innerHTML = `
                <div class="p-2 border rounded bg-light text-center">
                    <small class="text-muted d-block">觀測成分股計</small>
                    <span class="fw-bold text-dark h5">${currentHoldings.length} 檔</span>
                </div>
                <div class="p-2 border rounded bg-light text-center">
                    <small class="text-muted d-block">涵蓋精選權重總計</small>
                    <span class="fw-bold text-primary h5">${totalWeightCombined.toFixed(2)}%</span>
                </div>
            `;

            let tbody = document.getElementById('singleEtfTableBody');
            tbody.innerHTML = currentHoldings.map((h, index) => {
                // 計算增減變化
                let prevMatch = globalRawData.find(x => x.date === prevDate && x.etf === selectedEtf && x.stock === h.stock);
                let diffStr = '-';
                if (prevMatch) {
                    let diff = Number(h.weight) - Number(prevMatch.weight);
                    if (diff > 0.01) diffStr = `<span class="text-danger fw-bold"><i class="fa-solid fa-arrow-up me-1"></i>+${diff.toFixed(2)}%</span>`;
                    else if (diff < -0.01) diffStr = `<span class="text-success fw-bold"><i class="fa-solid fa-arrow-down me-1"></i>${diff.toFixed(2)}%</span>`;
                } else {
                    diffStr = '<span class="text-main bg-light px-1 rounded text-primary small">新進榜</span>';
                }

                return `
                    <tr>
                        <td class="text-center font-monospace">${index + 1}</td>
                        <td><span class="badge bg-light text-dark font-monospace border">${h.stock}</span></td>
                        <td class="fw-bold">${h.name}</td>
                        <td class="text-end font-monospace text-primary fw-bold">${Number(h.weight).toFixed(2)}%</td>
                        <td class="text-center">${diffStr}</td>
                    </tr>
                `;
            }).join('');
        }

        // ==========================================
        // 渲染市場熱度排行 (採用 app.py 的特有核心排版)
        // ==========================================
        function renderHeatRanking() {
            let heatSummary = {};
            
            // 遍歷所有最新資料並利用篩選邏輯精煉
            globalRawData.forEach(r => {
                if (r.date === latestDate && isNormalStock(r.stock, r.name)) {
                    if (!heatSummary[r.stock]) {
                        heatSummary[r.stock] = { name: r.name, count: 0, totalWeight: 0 };
                    }
                    heatSummary[r.stock].count += 1;
                    heatSummary[r.stock].totalWeight += Number(r.weight);
                }
            });

            // 轉成陣列並進行熱度排序
            let sortedHeat = Object.keys(heatSummary).map(code => {
                return {
                    code: code,
                    name: heatSummary[code].name,
                    count: heatSummary[code].count,
                    totalWeight: heatSummary[code].totalWeight
                };
            }).sort((a, b) => b.totalWeight - a.totalWeight);

            let tbody = document.getElementById('heatRankTableBody');
            tbody.innerHTML = sortedHeat.slice(0, 30).map((item, index) => {
                // 依權重給予監控燈號與狀態標籤 (承襲 app.py 的視覺排版特徵)
                let statusBadge = '<span class="badge bg-secondary">常態持有</span>';
                if (index < 5) statusBadge = '<span class="badge bg-danger animate-pulse"><i class="fa-solid fa-fire me-1"></i>極高熱度核心</span>';
                else if (index < 12) statusBadge = '<span class="badge bg-warning text-dark"><i class="fa-solid fa-bolt me-1"></i>高度關注標的</span>';

                return `
                    <tr>
                        <td class="text-center fw-bold font-monospace">${index + 1}</td>
                        <td><span class="badge bg-dark text-white font-monospace">${item.code}</span></td>
                        <td class="fw-bold">${item.name}</td>
                        <td class="text-end font-monospace fw-bold text-secondary">${item.count} 檔 ETF</td>
                        <td class="text-end font-monospace text-danger fw-bold">${item.totalWeight.toFixed(2)}%</td>
                        <td class="text-center">${statusBadge}</td>
                    </tr>
                `;
            }).join('');
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

# ==========================================
# 4. 輸出渲染結果
# ==========================================
components.html(final_html, height=950, scrolling=True)
