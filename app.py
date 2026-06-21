import streamlit as st
import streamlit.components.v1 as components
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
#    讓網頁能夠 100% 滿版無縫填滿整個螢幕，完全交由 index.html 渲染
# ==============================================================================
st.markdown("""
    <style>
    /* 隱藏頂部主選單與裝飾條 */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    
    /* 隱藏底部浮水印 footer */
    footer {visibility: hidden;}
    
    /* 將 Streamlit 預設的主容器邊距（Padding）歸零 */
    .block-container {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        padding-left: 0rem !important;
        padding-right: 0rem !important;
    }
    
    /* 確保內嵌的 iframe 底部沒有奇異的空白間距 */
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

# ==============================================================================
# 4. 【純後端資料清洗與打包】
#    移除原本在 Python 裡的 UI 邏輯，純粹將 Google Sheets 資料轉為前端所需的 JS Array 物件
# ==============================================================================
def fetch_backend_data_from_python():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg:
        return None
    df, clean_err = process_and_standardize(raw_data)
    if clean_err or df.empty:
        return None
    
    # 將清洗後的完整歷史資料轉成 dict 清單，再序列化為符合 JavaScript 陣列語法的標準 JSON 數據
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False)

# ==============================================================================
# 5. 主程式入口：讀取 index.html 並將真資料動態注入渲染
# ==============================================================================
def main():
    html_filename = "index.html"
    
    if os.path.exists(html_filename):
        with open(html_filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # 從 Google 試算表獲取最新清洗完畢的數據
        python_data = fetch_backend_data_from_python()
        
        if python_data:
            # 🎯 修正點：加上反單引號 ` 或單引號 '，將其宣告為字串，讓前端的 JSON.parse() 能正確解析
            html_content = html_content.replace('let globalRawData = [];', f'let globalRawData = `{python_data}`;')
        else:
            st.warning("⚠️ 後端未能成功讀取 Google 試算表資料，目前將顯示 index.html 預設資料。")

        # 使用滿版無邊框 iframe 組件將 index.html 渲染出來（高度可依前端頁面長度自行調整）
        components.html(html_content, height=1600, scrolling=True)
        
    else:
        # 防呆與目錄層級檢查提示
        st.error(f"❌ 找不到 `{html_filename}` 檔案！")
        st.info("請確認您的專案資料夾結構，`app.py` 與 `index.html` 必須放置在同一個目錄階層下喔！")

if __name__ == "__main__":
    main()
