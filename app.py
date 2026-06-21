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
#    讓你的網頁能夠 100% 滿版無縫填滿整個螢幕！
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

def is_global_stock_code(df):
    meta_keywords = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""]
    exclude_keywords = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨"]
    mask_meta = df['stock'].str.upper().isin(meta_keywords) | df['name'].str.upper().isin(meta_keywords)
    mask_exclude = df['stock'].str.upper().str.contains('|'.join(exclude_keywords)) | df['name'].str.upper().str.contains('|'.join(exclude_keywords))
    return ~(mask_meta | mask_exclude)

def calculate_continuous_status(df_target, sorted_dates, key_col='stock'):
    status_dict = {}
    if len(sorted_dates) < 2:
        return {k: "-" for k in df_target[key_col].unique()}
        
    for code, group in df_target.groupby(key_col):
        series = group.groupby('date')['volume'].sum().reindex(sorted_dates, fill_value=0)
        diff_values = series.diff().values[::-1] 
        
        trend_count = 0
        current_trend = ""
        for d_vol in diff_values[:-1]:
            if d_vol > 0:
                if current_trend == "": current_trend = "買"
                if current_trend == "買": trend_count += 1
                else: break
            elif d_vol < 0:
                if current_trend == "": current_trend = "賣"
                if current_trend == "賣": trend_count += 1
                else: break
            else:
                break
        status_dict[code] = f"連{current_trend} {trend_count} 日" if trend_count > 0 else "-"
    return status_dict


# ==============================================================================
# 4. 【後端真實資料打包】(已修正：確保轉出標準不帶引號的 JS Array 結構)
# ==============================================================================
def fetch_backend_data_from_python():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg:
        return None
    df, clean_err = process_and_standardize(raw_data)
    if clean_err or df.empty:
        return None
    
    # 【核心修改點】將 DataFrame 轉成 Python 字典列表，再透過 json.dumps 轉成符合 JS 陣列語法的字串
    records = df.to_dict(orient="records")
    return json.dumps(records, ensure_ascii=False)


# ==============================================================================
# 5. 讀取 index.html 並透過字串替換完成真資料對接渲染
# ==============================================================================
def main():
    html_filename = "index.html"
    
    if os.path.exists(html_filename):
        with open(html_filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # 🎯 關鍵對接點：獲取 Python 處理好的真實數據
        python_data = fetch_backend_data_from_python()
        
        if python_data:
            # 將前端原先死資料/空陣列變數，強制替換成 Python 從 Google Sheets 抓取的最新真資料 JSON 
            html_content = html_content.replace('let globalRawData = [];', f'let globalRawData = {python_data};')
        else:
            st.warning("⚠️ 後端未能成功讀取 Google 試算表資料，目前將顯示 index.html 預設資料。")

        # 使用滿版 iframe 組件進行渲染
        components.html(html_content, height=1600, scrolling=True)
        
    else:
        # 防呆機制
        st.error(f"❌ 找不到 `{html_filename}` 檔案！")
        st.info("請確認你的資料夾結構，`app.py` 與 `index.html` 必須放在同一個目錄階層下喔！")

if __name__ == "__main__":
    main()
