import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定 (全寬佈局、暗色科技風)
# ==========================================
st.set_page_config(page_title="ETF 籌碼監控儀表板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "etf history"  # 主要歷史紀錄資料表名稱

def get_sheets_client():
    # 優先從 Streamlit Secrets 中讀取憑證
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]

    if creds_json:
        try:
            # 清洗字串，防止前後帶有單雙引號導致 JSON 解析出錯
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
# 2. 高效資料載入與清洗 (Pandas 向量化優化)
# ==========================================
@st.cache_data(ttl=600)  # 快取 10 分鐘，避免頻繁讀取觸發 Google API 上限
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
    """ 模擬原 getRequiredColumns 與容錯搜尋欄位索引 """
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
    
    # 向量化資料清洗
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df['weight'] = df['weight'].astype(str).str.replace('%', '').str.replace(',', '').astype(float)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100  # 統一轉換為百分比格式 (例如 5.23)
        
    df['volume'] = df['volume'].astype(str).str.replace(',', '').astype(float).fillna(0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    return df

def is_global_stock_code(df):
    """ 智慧資產分類：判斷是否為全球市場股票（精確排除現金與應收應付等元數據） """
    meta_keywords = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""]
    exclude_keywords = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨", "遠期"]
    
    mask_code_meta = df['stock'].str.upper().isin(meta_keywords)
    mask_name_meta = df['name'].str.upper().isin(meta_keywords)
    mask_exclude = df['stock'].str.upper().str.contains('|'.join(exclude_keywords)) | \
                   df['name'].str.upper().str.contains('|'.join(exclude_keywords))
                   
    return ~(mask_code_meta | mask_name_meta | mask_exclude)

# ==========================================
# 3. 核心業務邏輯運算面板
# ==========================================

def calculate_continuous_status(df_target, sorted_dates, key_col='stock'):
    """ 逆向追溯日線趨勢：計算連續買賣狀態 """
    status_dict = {}
    if len(sorted_dates) < 2:
        return {k: "-" for k in df_target[key_col].unique()}
        
    for code, group in df_target.groupby(key_col):
        group = group.set_index('date').reindex(sorted_dates, fill_value=0)
        diff_values = group['volume'].diff().values[::-1] # 最新日期往回推
        
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
    """ 功能 A：獲取單檔 ETF 的最新持股明細與動態異動 """
    df_etf = df[df['etf'] == etf_code].copy()
    if df_etf.empty: return None
    
    sorted_dates = sorted(df_etf['date'].unique())
    if range_type == "custom" and start_date and end_date:
        latest_date, compare_date = end_date, start_date
    else:
        latest_date = sorted_dates[-1]
        offset = int(range_type) if range_type.isdigit() else 1
        compare_date = sorted_dates[max(0, len(sorted_dates) - 1 - offset)]
        sorted_dates = sorted_dates[-25:] # 限制 25 天視窗計算連續狀態

    df_latest = df_etf[df_etf['date'] == latest_date]
    
    # 擷取 Meta 元數據
    get_meta = lambda x: df_latest[df_latest['stock'] == x]['volume'].values[0] if x in df_latest['stock'].values else "-"
    meta = {k: get_meta(v) for k, v in {"lastClose": "昨收價", "change": "漲跌", "marketPrice": "市價", "size": "規模", "premium": "折溢價"}.items()}
    
    # 資產分類
    is_stock = is_global_stock_code(df_latest)
    stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()
    assets_df = df_latest[~is_stock].copy()
    
    # 計算異動
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
    """ 功能 B：個股分佈查詢 """
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
    """ 功能 C：全市場成分股異動總覽 """
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
    # 補齊因 outer join 遺失的基礎資訊
    for col in ['etf', 'stock', 'name']:
        df_merged[col] = df_merged[col].replace(0, np.nan).fillna(df_merged['etf_stock'].str.split('_').str[0] if col=='etf' else (df_merged['etf_stock'].str.split('_').str[1] if col=='stock' else ''))

    df_merged['diff'] = df_merged['volume_new'] - df_merged['volume_old']
    df_change = df_merged[df_merged['diff'] != 0].copy()
    
    if df_change.empty: return None
    
    def judge_nature(r):
        if r['volume_old'] == 0 and r['volume_new'] > 0: return "新增", 1
        if r['volume_old'] > 0 and r['diff'] > 0: return "增加", 2
        if r['volume_new'] > 0 and r['diff'] < 0: return "減少", 3
        return "刪除", 4
        
    res = df_change.apply(judge_nature, axis=1)
    df_change['nature'], df_change['natureOrder'] = [r[0] for r in res], [r[1] for r in res]
    
    df_history_filtered = df[df['date'].isin(sorted_dates) & is_global_stock_code(df)].copy()
    df_history_filtered['etf_stock'] = df_history_filtered['etf'] + "_" + df_history_filtered['stock']
    status_map = calculate_continuous_status(df_history_filtered, sorted_dates, 'etf_stock')
    
    df_change['continuousStatus'] = df_change['etf_stock'].map(status_map)
    df_change = df_change.sort_values(by=['natureOrder', 'etf']).drop(columns=['natureOrder', 'etf_stock'])
    
    return {"latestDate": latest_date, "compareDate": compare_date, "changes": df_change}

def get_multi_etf_comparison(df, etf_codes):
    """ 新增功能：多檔 ETF 成分股交叉比對面板 """
    sorted_dates = sorted(df['date'].unique())
    if not sorted_dates or not etf_codes: return None
    latest_date = sorted_dates[-1]
    
    # 篩選最新一天、指定幾檔 ETF 且為全球股票的資料
    df_sub = df[(df['date'] == latest_date) & (df['etf'].isin(etf_codes)) & is_global_stock_code(df)]
    if df_sub.empty: return None
    
    # 利用好用的 Pivot Table 機制，瞬間完成交叉矩陣比對
    pivot_weight = df_sub.pivot(index=['stock', 'name'], columns='etf', values='weight').fillna(0)
    pivot_volume = df_sub.pivot(index=['stock', 'name'], columns='etf', values='volume').fillna(0)
    
    # 改名方便前端識別
    pivot_weight.columns = [f"{c} 權重(%)" for c in pivot_weight.columns]
    pivot_volume.columns = [f"{c} 持股數" for c in pivot_volume.columns]
    
    comparison_df = pd.concat([pivot_weight, pivot_volume], axis=1).reset_index()
    return comparison_df

# ==========================================
# 4. 前端 UI 介面佈局與渲染
# ==========================================
def main():
    st.title("📊 ETF 籌碼大數據監控面板")
    
    if not sh:
        st.warning("請先將試算表權限與 Secrets 設定完成。")
        return
        
    df = load_historical_data()
    if df.empty:
        st.info("暫無有效歷史數據，請確認試算表欄位名稱與內容。")
        return

    etf_list = sorted(df['etf'].dropna().unique().tolist())
    
    # 側邊欄設計
    st.sidebar.header("⚡ 監控核心控制台")
    mode = st.sidebar.radio("切換監控面板", ["單檔 ETF 明細與異動", "個股全市場分佈", "全市場成分股異動總覽", "多檔 ETF 交叉比對"])
    
    range_type = "1"
    start_date, end_date = None, None
    if mode in ["單檔 ETF 明細與異動", "全市場成分股異動總覽"]:
        range_type = st.sidebar.selectbox("對比時間窗口", ["1", "5", "10", "custom"], index=0)
        if range_type == "custom":
            available_dates = sorted(df['date'].unique())
            start_date = st.sidebar.selectbox("起始對比日", available_dates, index=0)
            end_date = st.sidebar.selectbox("結束基準日", available_dates, index=len(available_dates)-1)

    # 執行與渲染對應功能
    if mode == "單檔 ETF 明細與異動":
        selected_etf = st.sidebar.selectbox("選擇監控 ETF", etf_list)
        res = get_etf_detail_data(df, selected_etf, range_type, start_date, end_date)
        if res:
            st.subheader(f"🔍 {selected_etf} 營運資訊 ({res['latestDate']})")
            m = res['meta']
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("市價 / 昨收", f"{m['marketPrice']} / {m['lastClose']}")
            c2.metric("最新漲跌", f"{m['change']}")
            c3.metric("基金規模", f"{m['size']}")
            c4.metric("折溢價比率", f"{m['premium']}")
            
            t1, t2, t3 = st.tabs(["📋 當前成分股明細", "🔄 籌碼異動追蹤", "💰 其他資產/現金項目"])
            with t1:
                st.dataframe(res['stocks'][['stock', 'name', 'weight', 'volume']].rename(columns={'weight':'持股權重(%)', 'volume':'持有股數'}), use_container_width=True, hide_index=True)
            with t2:
                if not res['changes'].empty:
                    st.dataframe(res['changes'].rename(columns={'nature':'異動性質', 'diff':'股數增減', 'continuousStatus':'趨勢狀態'}), use_container_width=True, hide_index=True)
                else:
                    st.info("該時間窗口內，成分股持股數無任何變動。")
            with t3:
                st.dataframe(res['assets'][['stock', 'name', 'weight', 'volume']], use_container_width=True, hide_index=True)

    elif mode == "個股全市場分佈":
        all_stocks = sorted(df[is_global_stock_code(df)]['stock'].unique())
        target_stock = st.sidebar.selectbox("選擇查詢個股", all_stocks)
        dist = get_stock_distribution(df, target_stock)
        if dist:
            st.subheader(f"🎯 個股監測：{dist['stockCode']} - {dist['stockName']}")
            c1, c2 = st.columns(2)
            c1.metric("全市場 ETF 總持有量", f"{int(dist['totalVolume']):,} 股")
            c2.metric("納入此股之 ETF 檔數", f"{dist['totalEtfCount']} 檔")
            st.dataframe(dist['data'].rename(columns={'etf':'持股 ETF', 'weight':'持股比重(%)', 'volume':'持有股數'}), use_container_width=True, hide_index=True)

    elif mode == "全市場成分股異動總覽":
        res = get_all_global_changes(df, range_type, start_date, end_date)
        if res:
            st.subheader(f"🌍 全市場成分股異動快照 ({res['compareDate']} ➔ {res['latestDate']})")
            st.dataframe(res['changes'][['etf', 'stock', 'name', 'nature', 'diff', 'continuousStatus']].rename(columns={'etf':'ETF代號','stock':'股票代號','name':'股票名稱','nature':'異動狀態','diff':'股數變動','continuousStatus':'連續買賣紀錄'}), use_container_width=True, hide_index=True)
        else:
            st.info("全市場在此區間內無任何成分股增減持異動。")

    elif mode == "多檔 ETF 交叉比對":
        selected_etfs = st.sidebar.multiselect("請挑選多檔 ETF 進行交叉權重對比", etf_list, default=etf_list[:2] if len(etf_list) >= 2 else etf_list)
        if selected_etfs:
            comp_df = get_multi_etf_comparison(df, selected_etfs)
            if comp_df is not None:
                st.subheader("⚔️ 多檔 ETF 成分股權重與持股同步交叉矩陣")
                st.dataframe(comp_df, use_container_width=True, hide_index=True)
            else:
                st.warning("所選 ETF 組合查無對應持股明細數據。")
        else:
            st.info("請於左側控制台挑選至少一檔以上的 ETF 進行比對。")

if __name__ == "__main__":
    main()
