import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==============================================================================
# 1. 初始化 Streamlit 頁面設定（開啟寬螢幕模式、預設收起側邊欄）
# ==============================================================================
st.set_page_config(
    page_title="ETF 籌碼大數據監控面板",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==============================================================================
# 2. 強大隱藏術：注入 CSS 拔除 Streamlit 所有原生邊框與元件
# ==============================================================================
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

# ==============================================================================
# 3. 獨立安全的連線與資料載入核心
# ==============================================================================
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
        return None, "無法連線至 Google 試算表，請檢查憑證與網路設定。"
    try:
        ws = sh.worksheet(WORKSHEET_HISTORY)
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return None, f"工作表「{WORKSHEET_HISTORY}」內無有效資料數據。"
        return raw_data, None
    except Exception as e:
        return None, f"讀取工作表「{WORKSHEET_HISTORY}」失敗: {str(e)}"

def process_and_standardize(raw_data):
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
        return pd.DataFrame(), f"主要欄位對照失敗，缺少必要屬性: {missing}"

    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.dropna(subset=['date'])
    
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','', regex=False).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',','', regex=False).str.strip(), errors='coerce').fillna(0.0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    return df, None

def fetch_backend_data_from_python():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg:
        return None
    df, clean_err = process_and_standardize(raw_data)
    if clean_err or df.empty:
        return None
    
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False)

# ==============================================================================
# 5. 主程式入口：使用 st.html 替代 components.html
# ==============================================================================
def main():
    html_filename = "index.html"
    
    if os.path.exists(html_filename):
        with open(html_filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        python_data = fetch_backend_data_from_python()
        
        if python_data:
            html_content = html_content.replace('let globalRawData = [];', f'let globalRawData = {python_data};')
        else:
            st.warning("⚠️ 後端未能成功讀取 Google 試算表資料，目前將顯示 index.html 預設資料。")

        # 🎯 改用 st.html 進行滿版渲染，它不依賴額外的 components 模組
        st.html(html_content)
        
    else:
        st.error(f"❌ 找不到 `{html_filename}` 檔案！")

if __name__ == "__main__":
    main()
