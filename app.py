import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定 (全寬佈局、注入前端優雅白/藍視覺樣式)
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"  # 已精確修正為指定名稱

# 注入自訂 CSS，完美還原你提供的前端 HTML/Bootstrap 視覺特徵
st.markdown("""
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        /* 全域字體與背景色調整 */
        html, body, [data-testid="stAppViewContainer"] {
            font-family: 'Noto Sans TC', sans-serif !important;
            background-color: #f4f6f9 !important;
            color: #333333;
        }
        
        /* 頂部導覽列風格模擬 */
        .custom-navbar {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            padding: 15px 25px;
            color: white;
            font-size: 1.3rem;
            font-weight: 700;
            border-radius: 8px;
            margin-bottom: 25px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            display: flex;
            align-items: center;
        }
        
        /* 自訂卡片樣式 */
        .custom-card {
            background-color: #ffffff;
            border-radius: 12px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            padding: 20px;
            margin-bottom: 25px;
            border: none;
        }
        
        /* 營運指標卡片 (Meta Card) */
        .custom-meta-card {
            background: #ffffff;
            border-left: 4px solid #2a5298;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.04);
            text-align: center;
            margin-bottom: 15px;
        }
        .meta-label {
            font-size: 0.85rem;
            color: #718096;
            margin-bottom: 4px;
            font-weight: 500;
        }
        .meta-value {
            font-size: 1.25rem;
            font-weight: 700;
            color: #1a202c;
        }
        
        /* 隱藏 Streamlit 原生內建的不必要元件以維持乾淨 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# 顯示自訂頂欄
st.markdown('<div class="custom-navbar">💻 ETF 籌碼大數據監控面板</div>', unsafe_allow_html=True)

def get_sheets_client():
    # 優先從 Streamlit Secrets 中讀取憑證
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]

    if creds_json:
        try:
            clean_json = creds_json.strip().strip("'").strip('"')
            creds_data = json.loads(clean_json)
            return gspread.service_account_from_dict(creds_data)
        except Exception as e:
            st.error(f"❌ Secrets 中的 GOOGLE_CREDENTIALS JSON 解析失敗: {e}")

    # 本地開發備用
    json_path = os.path.join(os.getcwd(), 'credentials.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return gspread.service_account_from_dict(json.load(f))
    return None

# 初始化 Google Sheets
@st.cache_resource
def init_gspread():
    try:
        gc = get_sheets_client()
        if gc:
            return gc.open(SHEET_NAME)
        return None
    except Exception as e:
        st.error(f"❌ 雲端試算表連線失敗: {e}")
        return None

sh = init_gspread()

# ==========================================
# 2. 高效資料載入與清洗 (Pandas 運算優化)
# ==========================================
@st.cache_data(ttl=600)
def load_historical_data():
    if not sh:
        return pd.DataFrame()
    try:
        ws = sh.worksheet(WORKSHEET_HISTORY)
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        return standardize_df(df)
    except Exception as e:
        st.error(f"❌ 讀取工作表「{WORKSHEET_HISTORY}」失敗: {e}")
        return pd.DataFrame()

def standardize_df(df):
    if df.empty:
        return df
        
    alias_map = {
        "etf": ["ETF代號", "ETF", "ETF碼"],
        "date": ["日期", "時間", "Date"],
        "stock": ["股票代號", "成分股代號", "代號", "商品代號", "Stock Code"],
        "name": ["股票名稱", "成分股名稱", "名稱", "商品名稱", "Stock Name"],
        "weight": ["權重", "權重(%)", "持股比例", "持股權重", "Weight"],
        "volume": ["持有數", "張數", "持有張數", "股數", "持有股數", "持有數量", "Volume"]
    }
    
    df.columns = [str(c).strip() for c in df.columns]
    rename_dict = {}
    
    for standard_name, aliases in alias_map.items():
        found = False
        for alias in aliases:
            if alias in df.columns:
                rename_dict[alias] = standard_name
                found = True
                break
        if not found and standard_name in ["etf", "date", "stock", "weight"]:
            st.error(f"工作表欄位名稱不符，找不到以下對應欄位：{aliases}")
            return pd.DataFrame()
            
    df = df.rename(columns=rename_dict)
    
    # 向量化清洗
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df['weight'] = df['weight'].astype(str).str.replace('%', '').str.replace(',', '').astype(float)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = df['volume'].astype(str).str.replace(',', '').astype(float).fillna(0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    return df

def is_global_stock_code(df):
    meta_keywords = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""]
    exclude_keywords = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨", "遠期"]
    
    mask_code_meta = df['stock'].str.upper().isin(meta_keywords)
    mask_name_meta = df['name'].str.upper().isin(meta_keywords)
    mask_exclude = df['stock'].str.upper().str.contains('|'.join(exclude_keywords)) | \
                   df['name'].str.upper().str.contains('|'.join(exclude_keywords))
                   
    return ~(mask_code_meta | mask_name_meta | mask_exclude)

# ==========================================
# 3. 核心業務邏輯與熱度計算
# ==========================================
def calculate_continuous_status(df_target, sorted_dates, key_col='stock'):
    status_dict = {}
    if len(sorted_dates) < 2:
        return {k: "-" for k in df_target[key_col].unique()}
        
    for code, group in df_target.groupby(key_col):
        group = group.set_index('date').reindex(sorted_dates, fill_value=0)
        diff_values = group['volume'].diff().values[::-1]
        
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

def get_etf_detail_data(df, etf_code, range_type, start_date=None, end_date=None):
    df_etf = df[df['etf'] == etf_code].copy()
    if df_etf.empty: return None
    
    sorted_dates = sorted(df_etf['date'].unique())
    if range_type == "custom" and start_date and end_date:
        latest_date, compare_date = end_date, start_date
    else:
        latest_date = sorted_dates[-1]
        offset = int(range_type) if range_type.isdigit() else 1
        compare_date = sorted_dates[max(0, len(sorted_dates) - 1 - offset)]
        sorted_dates = sorted_dates[-25:]

    df_latest = df_etf[df_etf['date'] == latest_date]
    
    get_meta = lambda x: df_latest[df_latest['stock'] == x]['volume'].values[0] if x in df_latest['stock'].values else "-"
    meta = {k: get_meta(v) for k, v in {"lastClose": "昨收價", "change": "漲跌", "marketPrice": "市價", "size": "規模", "premium": "折溢價"}.items()}
    
    is_stock = is_global_stock_code(df_latest)
    stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()
    assets_df = df_latest[~is_stock].copy()
    
    df_comp = df_etf[df_etf['date'] == compare_date]
    df_merged = pd.merge(stocks_df[['stock', 'name', 'volume']], df_comp[['stock', 'volume']], on='stock', how='outer', suffixes=('_new', '_old')).fillna(0)
    df_merged['diff'] = df_merged['volume_new'] - df_merged['volume_old']
    df_change = df_merged[df_merged['diff'] != 0].copy()
    
    if not df_change.empty:
        def judge_nature(r):
            if r['volume_old'] == 0 and r['volume_new'] > 0: return "新增", 1
            if r['volume_old'] > 0 and r['diff'] > 0: return "增加", 2
            if r['volume_new'] > 0 and r['diff'] < 0: return "減少", 3
            return "刪除", 4
            
        res = df_change.apply(judge_nature, axis=1)
        df_change['nature'], df_change['natureOrder'] = [r[0] for r in res], [r[1] for r in res]
        
        status_map = calculate_continuous_status(df_etf[is_global_stock_code(df_etf)], sorted_dates, 'stock')
        df_change['continuousStatus'] = df_change['stock'].map(status_map)
        df_change = df_change.sort_values(by=['natureOrder', 'stock']).drop(columns=['natureOrder'])
        
    return {"latestDate": latest_date, "compareDate": compare_date, "meta": meta, "stocks": stocks_df, "assets": assets_df, "changes": df_change}

def get_stock_distribution(df, stock_code):
    sorted_dates = sorted(df['date'].unique())
    if not sorted_dates: return None
    df_latest = df[df['date'] == sorted_dates[-1]]
    df_target = df_latest[df_latest['stock'] == stock_code].copy()
    
    if df_target.empty: return None
    return {
        "stockCode": stock_code, "stockName": df_target['name'].iloc[0],
        "totalVolume": df_target['volume'].sum(), "totalEtfCount": len(df_target),
        "data": df_target.sort_values(by='weight', ascending=False)[['etf', 'weight', 'volume']]
    }

def get_all_global_changes(df, range_type, start_date=None, end_date=None):
    sorted_dates = sorted(df['date'].unique())
    if range_type == "custom" and start_date and end_date:
        latest_date, compare_date = end_date, start_date
    else:
        latest_date = sorted_dates[-1]
        compare_date = sorted_dates[max(0, len(sorted_dates) - 1 - (int(range_type) if range_type.isdigit() else 1))]
        sorted_dates = sorted_dates[-25:]

    df_filtered = df[df['date'].isin([latest_date, compare_date]) & is_global_stock_code(df)].copy()
    df_filtered['etf_stock'] = df_filtered['etf'] + "_" + df_filtered['stock']
    
    df_lat = df_filtered[df_filtered['date'] == latest_date]
    df_comp = df_filtered[df_filtered['date'] == compare_date]
    
    df_merged = pd.merge(df_lat[['etf_stock', 'etf', 'stock', 'name', 'volume']], df_comp[['etf_stock', 'volume']], on='etf_stock', how='outer', suffixes=('_new', '_old')).fillna(0)
    for col in
