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
    if not creds_json:
        st.error("未設定 GOOGLE_CREDENTIALS 憑證。")
        st.stop()
    
    if isinstance(creds_json, str):
        creds_info = json.loads(creds_json)
    else:
        creds_info = creds_json

    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    gc = gspread.service_account_from_dict(creds_info)
    return gc

@st.cache_data(ttl=300)
def load_data():
    try:
        gc = get_sheets_client()
        sh = gc.open(SHEET_NAME)
        
        w_hist = sh.worksheet(WORKSHEET_HISTORY)
        data_hist = w_hist.get_all_records()
        df = pd.DataFrame(data_hist)
        
        w_tick = sh.worksheet(WORKSHEET_TICKER)
        data_tick = w_tick.get_all_values()
        if len(data_tick) > 1:
            df_ticker = pd.DataFrame(data_tick[1:], columns=data_tick[0])
        else:
            df_ticker = pd.DataFrame(columns=["股票代號", "股票名稱"])
            
        w_etf = sh.worksheet(WORKSHEET_ETF_NAME)
        data_etf = w_etf.get_all_values()
        if len(data_etf) > 1:
            df_etf_name = pd.DataFrame(data_etf[1:], columns=data_etf[0])
        else:
            df_etf_name = pd.DataFrame(columns=["ETF代號", "ETF名稱"])
            
        return df, df_ticker, df_etf_name
    except Exception as e:
        st.error(f"資料載入失敗: {e}")
        st.stop()

# ==========================================
# 3. 資料清洗與標準化邏輯
# ==========================================
def process_and_standardize(df):
    df = df.copy()
    
    rename_dict = {
        "日期": "date",
        "ETF代號": "etf",
        "股票代號": "stock",
        "股票名稱": "name",
        "權重(%)": "weight",
        "持股張數": "volume"
    }
    df = df.rename(columns=rename_dict)
    
    required_cols = ["date", "etf", "stock", "name", "weight", "volume"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = ""
            
    df['date'] = df['date'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    
    return df[required_cols]

# ==========================================
# 4. 主程式與資料準備
# ==========================================
raw_df, df_ticker, df_etf_name = load_data()

if raw_df.empty:
    st.warning("歷史資料庫為空。")
    st.stop()

df = process_and_standardize(raw_df)

# --- 修正處：直接從 Google Sheet 歷史資料中抓取最新與次新的兩個日期 ---
unique_dates = sorted(df['date'].unique(), reverse=True) if not df.empty else []
latest_date = unique_dates[0] if len(unique_dates) > 0 else ""
yesterday = unique_dates[1] if len(unique_dates) > 1 else ""

# 建立昨日權重字典
df_yesterday = df[df['date'] == yesterday] if yesterday else pd.DataFrame()
yesterday_map = {}
for _, r in df_yesterday.iterrows():
    yesterday_map[f"{r['etf']}_{r['stock']}"] = r['weight']

df_latest = df[df['date'] == latest_date].copy() if latest_date else pd.DataFrame()
chg_list = []
for _, r in df_latest.iterrows():
    k = f"{r['etf']}_{r['stock']}"
    chg_list.append(r['weight'] - yesterday_map.get(k, 0.0))
df_latest['chg'] = chg_list

ticker_map = dict(zip(df_ticker['股票代號'].astype(str).str.strip(), df_ticker['股票名稱'].astype(str).str.strip()))
etf_name_map = dict(zip(df_etf_name['ETF代號'].astype(str).str.strip(), df_etf_name['ETF名稱'].astype(str).str.strip()))

json_list = []
for _, r in df_latest.iterrows():
    s_code = r['stock']
    e_code = r['etf']
    json_list.append({
        "date": r['date'],
        "etf": e_code,
        "etf_name": etf_name_map.get(e_code, e_code),
        "stock": s_code,
        "name": ticker_map.get(s_code, r['name']),
        "weight": r['weight'],
        "volume": r['volume'],
        "chg": r['chg']
    })
json_data = json.dumps(json_list, ensure_ascii=False)

# 抓取外部大盤與籌碼數據 (玩股網 & 證交所)
wantgoo_json = "[]"
try:
    res_wg = requests.get("https://api.wantgoo.com/twstock/index/marketinfo", timeout=5)
    if res_wg.status_code == 200:
        wantgoo_json = json.dumps(res_wg.json(), ensure_ascii=False)
except:
    pass

twse_json = "[]"
try:
    res_tw = requests.get("https://openapi.twse.com.tw/v1/rwd/opendata/t187ap14_H", timeout=5)
    if res_tw.status_code == 200:
        twse_json = json.dumps(res_tw.json(), ensure_ascii=False)
except:
    pass

# ==========================================
# 5. 前端 HTML / JS 範本定義
# ==========================================
html_template = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Dashboard</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background-color: #f8f9fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
    .card { border: none; box-shadow: 0 0.125rem 0.25rem rgba(0, 0, 0, 0.075); margin-bottom: 1rem; }
    .table th { background-color: #f1f3f5; font-weight: 600; text-align: center; vertical-align: middle; }
    .table td { vertical-align: middle; }
    .nav-pills .nav-link { border-radius: 0.25rem; font-weight: 500; color: #495057; }
    .nav-pills .nav-link.active { background-color: #0d6efd; color: #fff; }
    .badge-increase { background-color: #dc3545; color: white; }
    .badge-decrease { background-color: #198754; color: white; }
    .text-increase { color: #dc3545; }
    .text-decrease { color: #198754; }
    .sticky-header { position: sticky; top: 0; background: white; z-index: 100; padding: 0.5rem 1rem; border-bottom: 1px solid #dee2e6; }
  </style>
</head>
<body>
  <div class="container-fluid p-3">
    <div class="row g-2 mb-3">
        <div class="col-md-3">
            <div class="card p-2 text-center">
                <div class="text-muted small">加權指數</div>
                <div id="m_price" class="fs-4 fw-bold">-</div>
                <div id="m_chg" class="small">-</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card p-2 text-center">
                <div class="text-muted small">外資買賣超 (億)</div>
                <div id="m_fi" class="fs-4 fw-bold">-</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card p-2 text-center">
                <div class="text-muted small">投信買賣超 (億)</div>
                <div id="m_sitc" class="fs-4 fw-bold">-</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card p-2 text-center">
                <div class="text-muted small">自營商買賣超 (億)</div>
                <div id="m_prop" class="fs-4 fw-bold">-</div>
            </div>
        </div>
    </div>

    <div class="card p-2 mb-2">
        <div class="d-flex flex-wrap align-items-center justify-content-between gap-2">
            <ul class="nav nav-pills" id="mainTabs" role="tablist">
                <li class="nav-item"><button class="nav-link active" id="tab1-btn" data-bs-toggle="pill" data-bs-target="#tab1" type="button">單一 ETF 內容</button></li>
                <li class="nav-item"><button class="nav-link" id="tab2-btn" data-bs-toggle="pill" data-bs-target="#tab2" type="button" onclick="renderCompare()">跨 ETF 交叉交叉比對</button></li>
            </ul>
            <div class="d-flex align-items-center gap-2">
                <span class="text-muted small fw-bold">更新日期: <span id="lblDate">-</span></span>
            </div>
        </div>
    </div>

    <div class="tab-content" id="mainTabsContent">
        <div class="tab-pane fade show active" id="tab1" role="tabpanel">
            <div class="row g-2">
                <div class="col-md-2">
                    <div class="card p-2" style="max-height: 75vh; overflow-y: auto;">
                        <div class="fw-bold mb-2 pb-1 border-bottom text-secondary small">選擇 ETF</div>
                        <div id="etfRadioGroup" class="d-flex flex-column gap-1"></div>
                    </div>
                </div>
                <div class="col-md-10">
                    <div class="card p-2">
                        <div class="d-flex flex-wrap align-items-center justify-content-between mb-2 gap-2">
                            <h5 id="selectedEtfTitle" class="m-0 fw-bold text-primary">請選擇 ETF</h5>
                            <div class="d-flex gap-2">
                                <input type="text" id="searchStock" class="form-control form-control-sm" placeholder="搜尋代號/名稱..." oninput="filterSingleTable()">
                                <select id="sortSelect" class="form-select form-select-sm" style="width:140px;" onchange="renderSingleTable()">
                                    <option value="weightDesc">權重：高到低</option>
                                    <option value="weightAsc">權重：低到高</option>
                                    <option value="chgDesc">日變動：正多到負</option>
                                    <option value="chgAsc">日變動：負多到正</option>
                                </select>
                            </div>
                        </div>
                        <div style="max-height: 68vh; overflow-y: auto;">
                            <table class="table table-sm table-hover table-bordered m-0">
                                <thead class="sticky-top">
                                    <tr>
                                        <th style="width: 15%;">股票代號</th>
                                        <th style="width: 30%;">股票名稱</th>
                                        <th style="width: 18%;">權重</th>
                                        <th style="width: 18%;">日變動</th>
                                        <th style="width: 19%;">張數</th>
                                    </tr>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="tab2" role="tabpanel">
                <div class="row g-2">
                    <div class="col-md-2">
                        <div class="card p-2" style="max-height: 75vh; overflow-y: auto;">
                            <div class="fw-bold mb-2 pb-1 border-bottom text-secondary small">選取比對 ETF (可複選)</div>
                            <div id="etfCheckGroup" class="d-flex flex-column gap-1"></div>
                        </div>
                    </div>
                    <div class="col-md-10">
                        <div class="card p-2">
                            <div style="max-height: 73vh; overflow-y: auto;">
                                <table class="table table-sm table-hover table-bordered m-0">
                                    <thead class="sticky-top" id="compareTableHeader"></thead>
                                    <tbody id="compareTableBody"></tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
      </div>

      <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
      <script>
        const globalRawData = __DATA_PLACEHOLDER__;
        const globalWantGoo = __WANTGOO_PLACEHOLDER__;
        const globalTwse = __TWSE_PLACEHOLDER__;

        function isNormalStock(code, name) {
            let meta = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""];
            let cashEx = [
                "DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨",
                "RDI", "DR_", "RECEIVABLES", "DIVIDENDS", "DISPOSAL", "INVESTMENTS", "權證", "型購", "型售","買權","賣權","TWSE"
            ];
            if (meta.includes(code) || meta.includes(name)) return false;
            let upperCode = code.toUpperCase().trim();
            let upperName = name.toUpperCase().trim();
            if (cashEx.some(k => upperCode.includes(k.toUpperCase()) || upperName.includes(k.toUpperCase()))) return false;
            if (/^[GBAHF][A-Z0-9]{5}$/.test(upperCode)) return false;
            return true;
        }

        function formatNum(v) {
            if(v === undefined || v === null || isNaN(v)) return '0';
            return Number(v).toLocaleString('zh-TW', {maximumFractionDigits:0});
        }

        let latestDate = "";
        let etfList = [];
        let currentEtf = "";

        document.addEventListener("DOMContentLoaded", function() {
            try {
                if(globalWantGoo && globalWantGoo.Price) {
                    document.getElementById('m_price').innerText = formatNum(globalWantGoo.Price);
                    let chg = Number(globalWantGoo.Change || 0);
                    let chgP = Number(globalWantGoo.ChangePercent || 0);
                    let el = document.getElementById('m_chg');
                    if(chg > 0) {
                        el.innerText = `▲ ${chg.toFixed(2)} (+${chgP.toFixed(2)}%)`;
                        el.className = 'small text-increase';
                    } else if(chg < 0) {
                        el.innerText = `▼ ${Math.abs(chg).toFixed(2)} (${chgP.toFixed(2)}%)`;
                        el.className = 'small text-decrease';
                    } else {
                        el.innerText = `0.00 (0.00%)`;
                    }
                }
            } catch(e){}

            try {
                if(globalTwse && globalTwse.length > 0) {
                    let latestTwse = globalTwse[globalTwse.length - 1];
                    let fi = (Number(latestTwse["外資及陸資(不含外資自營商)買賣超金額"]) / 100000000).toFixed(2);
                    let sitc = (Number(latestTwse["投信買賣超金額"]) / 100000000).toFixed(2);
                    let prop = (Number(latestTwse["自營商買賣超金額(自行買賣)"]) + Number(latestTwse["自營商買賣超金額(避險)"])) / 100000000;
                    
                    let fiEl = document.getElementById('m_fi');
                    fiEl.innerText = (fi > 0 ? '+' : '') + fi;
                    fiEl.className = 'fs-4 fw-bold ' + (fi > 0 ? 'text-increase' : (fi < 0 ? 'text-decrease' : ''));

                    let sitcEl = document.getElementById('m_sitc');
                    sitcEl.innerText = (sitc > 0 ? '+' : '') + sitc;
                    sitcEl.className = 'fs-4 fw-bold ' + (sitc > 0 ? 'text-increase' : (sitc < 0 ? 'text-decrease' : ''));

                    let propEl = document.getElementById('m_prop');
                    propEl.innerText = (prop > 0 ? '+' : '') + prop.toFixed(2);
                    propEl.className = 'fs-4 fw-bold ' + (prop > 0 ? 'text-increase' : (prop < 0 ? 'text-decrease' : ''));
                }
            } catch(e){}

            if(globalRawData.length > 0) {
                latestDate = globalRawData[0].date;
                document.getElementById('lblDate').innerText = latestDate;

                let etfMap = {};
                globalRawData.forEach(r => {
                    if(r.date === latestDate) {
                        etfMap[r.etf] = r.etf_name || r.etf;
                    }
                });
                etfList = Object.keys(etfMap).map(k => ({code: k, name: etfMap[k]}));

                let radioHtml = '';
                let checkHtml = '';
                etfList.forEach((e, i) => {
                    radioHtml += `
                        <div class="form-check">
                            <input class="form-check-input" type="radio" name="etfRadio" id="radio_${e.code}" value="${e.code}" ${i===0?'checked':''} onchange="selectEtf('${e.code}')">
                            <label class="form-check-label small fw-bold text-truncate w-100" for="radio_${e.code}" title="${e.code} ${e.name}">
                                ${e.code} <span class="text-muted fw-normal">${e.name}</span>
                            </label>
                        </div>`;
                    checkHtml += `
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="etfCheck" id="check_${e.code}" value="${e.code}" checked onchange="renderCompare()">
                            <label class="form-check-label small fw-bold text-truncate w-100" for="check_${e.code}" title="${e.code} ${e.name}">
                                ${e.code} <span class="text-muted fw-normal">${e.name}</span>
                            </label>
                        </div>`;
                });
                document.getElementById('etfRadioGroup').innerHTML = radioHtml;
                document.getElementById('etfCheckGroup').innerHTML = checkHtml;

                if(etfList.length > 0) {
                    selectEtf(etfList[0].code);
                }
            }
        });

        function selectEtf(code) {
            currentEtf = code;
            let item = etfList.find(x => x.code === code);
            document.getElementById('selectedEtfTitle').innerHTML = `${item.code} <span class="text-muted fs-6 fw-normal">${item.name}</span>`;
            document.getElementById('searchStock').value = '';
            renderSingleTable();
        }

        function getFilteredSingleData() {
            let filtered = globalRawData.filter(r => r.date === latestDate && r.etf === currentEtf && isNormalStock(r.stock, r.name));
            let q = document.getElementById('searchStock').value.trim().toUpperCase();
            if(q) {
                filtered = filtered.filter(r => r.stock.toUpperCase().includes(q) || r.name.toUpperCase().includes(q));
            }
            
            let sortVal = document.getElementById('sortSelect').value;
            if(sortVal === 'weightDesc') filtered.sort((a,b) => b.weight - a.weight);
            else if(sortVal === 'weightAsc') filtered.sort((a,b) => a.weight - b.weight);
            else if(sortVal === 'chgDesc') filtered.sort((a,b) => b.chg - a.chg);
            else if(sortVal === 'chgAsc') filtered.sort((a,b) => a.chg - b.chg);
            return filtered;
        }

        function renderSingleTable() {
            let data = getFilteredSingleData();
            let body = document.getElementById('singleTableBody');
            body.innerHTML = data.map(r => {
                let chgBadge = '-';
                if(r.chg > 0) chgBadge = `<span class="badge badge-increase">+${r.chg.toFixed(2)}%</span>`;
                else if(r.chg < 0) chgBadge = `<span class="badge badge-decrease">${r.chg.toFixed(2)}%</span>`;
                
                return `
                    <tr>
                        <td><span class="badge bg-light text-dark font-monospace border">${r.stock}</span></td>
                        <td class="fw-bold">${r.name}</td>
                        <td class="text-end fw-bold">${r.weight.toFixed(2)}%</td>
                        <td class="text-center">${chgBadge}</td>
                        <td class="text-end text-muted">${formatNum(r.volume)}</td>
                    </tr>`;
            }).join('');
        }

        function filterSingleTable() {
            renderSingleTable();
        }

        function getShortLabel(name) {
            return name.replace("元大台灣卓越50","元大50").replace("元大台灣高股息","元大高股息").replace("國泰台灣ESG永續高股息","國泰永續高股息").replace("復華台灣科技優息","復華科技優息");
        }

        function renderCompare() {
            let cbs = document.getElementsByName('etfCheck');
            let checkedCbs = [];
            cbs.forEach(c => { if(c.checked) checkedCbs.push(c.value); });

            let header = document.getElementById('compareTableHeader');
            if(checkedCbs.length === 0) {
                header.innerHTML = '<tr><th>請至少選取一個 ETF</th></tr>';
                document.getElementById('compareTableBody').innerHTML = '';
                return;
            }

            let headRow = `<tr><th style="width:12%;">代號</th><th style="width:20%;">成分股名稱</th>`;
            checkedCbs.forEach(c => {
                let item = etfList.find(x => x.code === c);
                let shortName = item ? getShortLabel(item.name) : c;
                headRow += `<th class="text-center">${shortName}<br><span class="text-muted font-monospace small" style="font-size:11px;">${c}</span></th>`;
            });
            header.innerHTML = headRow + '</tr>';

            let stockMap = {};
            globalRawData.forEach(r => {
                if(r.date === latestDate && checkedCbs.includes(r.etf) && isNormalStock(r.stock, r.name)) { stockMap[r.stock] = r.name; }
            });

            let body = document.getElementById('compareTableBody');
            body.innerHTML = Object.keys(stockMap).map(sCode => {
                let row = `<td><span class="badge bg-light text-dark font-monospace border">${sCode}</span></td><td class=\"fw-bold\">${stockMap[sCode]}</td>`;
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
)

components.html(final_html, height=900, scrolling=True)
