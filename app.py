import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定 (全寬佈局、注入 Bootstrap 5 完美版型樣式)
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"

# 全面注入你提供的精美 CSS 樣式，完美還原前端視覺
st.markdown("""
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        /* 全域樣式覆蓋 */
        html, body, [data-testid="stAppViewContainer"] {
            font-family: 'Noto Sans TC', sans-serif !important;
            background-color: #f4f6f9 !important;
            color: #333333;
        }
        
        /* 頂部導覽列風格完美還原 */
        .custom-navbar {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            padding: 12px 25px;
            border-radius: 10px;
            margin-bottom: 1.5rem;
        }
        .custom-navbar-brand {
            color: #ffffff !important;
            font-size: 1.25rem;
            font-weight: 700;
            text-decoration: none;
            display: flex;
            align-items: center;
        }
        
        /* 自訂 Card 區塊樣式 */
        .custom-card {
            border: none !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 6px rgba(0,0,0,0.05) !important;
            margin-bottom: 1.5rem !important;
            background-color: #ffffff !important;
            padding: 1.25rem;
        }
        .custom-card-header {
            background-color: #ffffff;
            border-bottom: 1px solid #edf2f9;
            font-weight: 700;
            font-size: 1.1rem;
            padding-bottom: 0.75rem;
            margin-bottom: 1rem;
            color: #1e3c72;
        }
        
        /* 營運指標快照卡片 (Meta Card) */
        .custom-meta-card {
            background: #ffffff;
            border-left: 4px solid #2a5298;
            padding: 12px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.04);
            text-align: center;
            margin-bottom: 1rem;
        }
        .meta-label {
            font-size: 0.85rem;
            color: #718096;
            margin-bottom: 4px;
            font-weight: 500;
        }
        .meta-value {
            font-size: 1.15rem;
            font-weight: 700;
            color: #1a202c;
        }
        
        /* 模擬 Nav Tabs 的選取狀態樣式 */
        button[data-baseweb="tab"] {
            border: none !important;
            color: #4a5568 !important;
            font-weight: 500 !important;
            padding: 0.5rem 1rem !important;
            border-radius: 8px !important;
            background-color: transparent !important;
            transition: all 0.2s;
        }
        button[data-baseweb="tab"]:hover {
            background-color: #f1f5f9 !important;
        }
        button[data-baseweb="tab"][aria-selected="true"] {
            background-color: #e2e8f0 !important;
            color: #1e3c72 !important;
            font-weight: 700 !important;
        }
        
        /* 排行榜數字圓圈 */
        .rank-badge {
            width: 24px;
            height: 24px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 50%;
            font-weight: bold;
            font-size: 0.85rem;
            color: white;
        }
        
        /* 隱藏不必要的 Streamlit 預設腳標 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# 渲染精美頂部導覽列
st.markdown("""
    <nav class="custom-navbar">
        <a class="custom-navbar-brand" href="#">
            <i class="bi bi-cpu-fill me-2"></i> ETF 籌碼大數據監控面板
        </a>
    </nav>
""", unsafe_allow_html=True)

def get_sheets_client():
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

    json_path = os.path.join(os.getcwd(), 'credentials.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return gspread.service_account_from_dict(json.load(f))
    return None

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
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2:
            return pd.DataFrame()
            
        df = pd.DataFrame(raw_data[1:], columns=raw_data[0])
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
    
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    
    df['weight'] = df['weight'].astype(str).str.replace('%', '', regex=False).str.replace(',', '', regex=False).str.strip()
    df['weight'] = pd.to_numeric(df['weight'], errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: 
        df['weight'] = df['weight'] * 100
        
    df['volume'] = df['volume'].astype(str).str.replace(',', '', regex=False).str.strip()
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0.0)
    
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    
    df = df.dropna(subset=['date'])
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
# 3. 核心業務邏輯與熱度計算 (🎯 已修正 Duplicate Labels Bug)
# ==========================================
def calculate_continuous_status(df_target, sorted_dates, key_col='stock'):
    status_dict = {}
    if len(sorted_dates) < 2:
        return {k: "-" for k in df_target[key_col].unique()}
        
    for code, group in df_target.groupby(key_col):
        # 💡 核心 Bug 修復點：先利用 groupby('date') 進行體積加總，徹底杜絕重複標籤引起的 reindex 崩潰
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
    
    df_merged = pd.merge(
        df_lat[['etf_stock', 'etf', 'stock', 'name', 'volume']], 
        df_comp[['etf_stock', 'volume']], 
        on='etf_stock', 
        how='outer', 
        suffixes=('_new', '_old')
    ).fillna(0)
    
    for col in ['etf', 'stock', 'name']:
        if col == 'etf':
            fill_val = df_merged['etf_stock'].str.split('_').str[0]
        elif col == 'stock':
            fill_val = df_merged['etf_stock'].str.split('_').str[1]
        else:
            fill_val = ''
        df_merged[col] = df_merged[col].replace(0, np.nan).fillna(fill_val)

    df_merged['diff'] = df_merged['volume_new'] - df_merged['volume_old']
    df_change = df_merged[df_merged['diff'] != 0].copy()
    
    if df_change.empty: 
        return None
    
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

def get_market_heat_ranking(df):
    sorted_dates = sorted(df['date'].unique())
    if len(sorted_dates) < 2: return None
    
    latest_date = sorted_dates[-1]
    compare_date = sorted_dates[-2]
    
    df_filtered = df[df['date'].isin([latest_date, compare_date]) & is_global_stock_code(df)].copy()
    
    df_lat = df_filtered[df_filtered['date'] == latest_date]
    df_comp = df_filtered[df_filtered['date'] == compare_date]
    
    sum_lat = df_lat.groupby(['stock', 'name'])['volume'].sum().reset_index()
    sum_comp = df_comp.groupby(['stock', 'name'])['volume'].sum().reset_index()
    
    merged = pd.merge(sum_lat, sum_comp, on=['stock', 'name'], how='outer', suffixes=('_new', '_old')).fillna(0)
    merged['net_change'] = merged['volume_new'] - merged['volume_old']
    
    top_bought = merged[merged['net_change'] > 0].sort_values(by='net_change', ascending=False).head(10)
    top_sold = merged[merged['net_change'] < 0].sort_values(by='net_change', ascending=True).head(10)
    
    return {"date": latest_date, "bought": top_bought, "sold": top_sold}

def get_multi_etf_comparison(df, etf_codes):
    sorted_dates = sorted(df['date'].unique())
    if not sorted_dates or not etf_codes: return None
    latest_date = sorted_dates[-1]
    
    df_sub = df[(df['date'] == latest_date) & (df['etf'].isin(etf_codes)) & is_global_stock_code(df)]
    if df_sub.empty: return None
    
    pivot_weight = df_sub.pivot_index = df_sub.pivot_table(index=['stock', 'name'], columns='etf', values='weight', aggfunc='sum').fillna(0)
    pivot_weight.columns = [f"{c} 權重(%)" for c in pivot_weight.columns]
    
    return pivot_weight.reset_index()

# ==========================================
# 4. 介面佈局與標籤頁渲染
# ==========================================
def main():
    df = load_historical_data()
    if df.empty:
        st.info("💡 試算表連線中或無有效數據，請確認 Google Secrets 與試算表名稱欄位。")
        return

    etf_list = sorted(df['etf'].dropna().unique().tolist())
    
    st.sidebar.header("⚙️ 條件篩選控制台")
    range_type = st.sidebar.selectbox("歷史對比時間窗口", ["1", "5", "10", "custom"], index=0)
    
    start_date, end_date = None, None
    if range_type == "custom":
        available_dates = sorted(df['date'].unique())
        start_date = st.sidebar.selectbox("起始對比日", available_dates, index=0)
        end_date = st.sidebar.selectbox("結束基準日", available_dates, index=len(available_dates)-1)

    # 綁定 Bootstrap 5 頁籤名稱與圖標特徵
    tabs = st.tabs([
        "📊 單檔 ETF 籌碼與持股", 
        "🔗 個股籌碼分佈", 
        "🌍 全市場異動總覽", 
        "🔥 市場熱度排行", 
        "⚔️ ETF 交叉比較"
    ])
    
    # ------------------------------------------
    # Tab A: 單檔 ETF 籌碼與持股
    # ------------------------------------------
    with tabs[0]:
        st.markdown('<div class="custom-card"><div class="custom-card-header"><i class="bi bi-pie-chart-fill me-2"></i>單檔 ETF 籌碼與持股監測</div>', unsafe_allow_html=True)
        selected_etf = st.selectbox("請選擇監控的 ETF 代號", etf_list, key="tab_a_etf")
        res = get_etf_detail_data(df, selected_etf, range_type, start_date, end_date)
        
        if res:
            st.markdown(f"##### 📋 {selected_etf} 營運快照指標 ({res['latestDate']})")
            
            m = res['meta']
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.markdown(f'<div class="custom-meta-card"><div class="meta-label">市價</div><div class="meta-value">{m["marketPrice"]}</div></div>', unsafe_allow_html=True)
            c2.markdown(f'<div class="custom-meta-card"><div class="meta-label">昨收價</div><div class="meta-value">{m["lastClose"]}</div></div>', unsafe_allow_html=True)
            c3.markdown(f'<div class="custom-meta-card"><div class="meta-label">漲跌</div><div class="meta-value">{m["change"]}</div></div>', unsafe_allow_html=True)
            c4.markdown(f'<div class="custom-meta-card"><div class="meta-label">基金規模</div><div class="meta-value">{m["size"]}</div></div>', unsafe_allow_html=True)
            c5.markdown(f'<div class="custom-meta-card"><div class="meta-label">折溢價比</div><div class="meta-value">{m["premium"]}</div></div>', unsafe_allow_html=True)
            
            sub_t1, sub_t2, sub_t3 = st.tabs(["📋 當前股票持股明細", "🔄 期間籌碼異動追蹤", "💰 現金與其他資產項目"])
            with sub_t1:
                st.dataframe(res['stocks'][['stock', 'name', 'weight', 'volume']].rename(columns={'stock':'股票代號','name':'股票名稱','weight':'持股權重(%)','volume':'持有股數'}), use_container_width=True, hide_index=True)
            with sub_t2:
                if not res['changes'].empty:
                    st.dataframe(res['changes'].rename(columns={'stock':'股票代號','name':'股票名稱','nature':'異動性質','diff':'股數增減','continuousStatus':'連續買賣趨勢'}), use_container_width=True, hide_index=True)
                else:
                    st.info("該時間區間內持股數量無異動。")
            with sub_t3:
                st.dataframe(res['assets'][['stock', 'name', 'weight', 'volume']].rename(columns={'stock':'資產代碼','name':'項目名稱','weight':'權重(%)','volume':'金額/數量'}), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # Tab B: 個股籌碼分佈
    # ------------------------------------------
    with tabs[1]:
        st.markdown('<div class="custom-card"><div class="custom-card-header"><i class="bi bi-share-fill me-2"></i>核心個股穿透分析</div>', unsafe_allow_html=True)
        all_stocks = sorted(df[is_global_stock_code(df)]['stock'].unique())
        target_stock = st.selectbox("請輸入或選擇標的個股代號", all_stocks, key="tab_b_stock")
        dist = get_stock_distribution(df, target_stock)
        
        if dist:
            st.markdown(f"<h4>🎯 {dist['stockCode']} - {dist['stockName']}</h4>", unsafe_allow_html=True)
            cc1, cc2 = st.columns(2)
            cc1.metric("全市場 ETF 總持股量", f"{int(dist['totalVolume']):,} 股")
            cc2.metric("納入此標的之 ETF 總檔數", f"{dist['totalEtfCount']} 檔")
            
            st.markdown("##### 📊 各大 ETF 持股佔比明細")
            st.dataframe(dist['data'].rename(columns={'etf':'持有此股之 ETF','weight':'持股權重(%)','volume':'持有股數'}), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # Tab C: 全市場異動總覽
    # ------------------------------------------
    with tabs[2]:
        st.markdown('<div class="custom-card"><div class="custom-card-header"><i class="bi bi-globe me-2"></i>全市場 ETF 成分股異動快照大數據</div>', unsafe_allow_html=True)
        res_c = get_all_global_changes(df, range_type, start_date, end_date)
        if res_c:
            st.caption(f"數據對比區間：{res_c['compareDate']} ➔ {res_c['latestDate']}")
            st.dataframe(res_c['changes'][['etf', 'stock', 'name', 'nature', 'diff', 'continuousStatus']].rename(columns={'etf':'ETF代號','stock':'股票代號','name':'股票名稱','nature':'異動狀態','diff':'股數變動','continuousStatus':'連續買賣紀錄'}), use_container_width=True, hide_index=True)
        else:
            st.info("全市場在此時間視窗內無任何成分股增減持異動。")
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # Tab D: 市場熱度排行
    # ------------------------------------------
    with tabs[3]:
        st.markdown('<div class="custom-card"><div class="custom-card-header"><i class="bi bi-fire me-2 text-danger"></i>全市場熱度追蹤排行</div>', unsafe_allow_html=True)
        heat = get_market_heat_ranking(df)
        if heat:
            st.caption(f"最新計算基準日：{heat['date']}")
            hc1, hc2 = st.columns(2)
            
            with hc1:
                st.markdown('<div style="border-top: 4px solid #de2a2a; padding-top:10px;"><h5>🔺 <span class="badge bg-danger">Top 10</span> 全市場投信法人加碼熱度榜</h5></div>', unsafe_allow_html=True)
                st.dataframe(heat['bought'].rename(columns={'stock':'股票代號','name':'股票名稱','net_change':'全市場淨加碼股數','volume_new':'當前總持股數'}).drop(columns=['volume_old']), use_container_width=True, hide_index=True)
                
            with hc2:
                st.markdown('<div style="border-top: 4px solid #2ade34; padding-top:10px;"><h5>🔻 <span class="badge bg-success">Top 10</span> 全市場投信法人減碼熱度榜</h5></div>', unsafe_allow_html=True)
                st.dataframe(heat['sold'].rename(columns={'stock':'股票代號','name':'股票名稱','net_change':'全市場淨減碼股數','volume_new':'當前總持股數'}).drop(columns=['volume_old']), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # Tab E: ETF 交叉比較
    # ------------------------------------------
    with tabs[4]:
        st.markdown('<div class="custom-card"><div class="custom-card-header"><i class="bi bi-arrow-left-right me-2"></i>多檔 ETF 成分股持股權重同步交叉矩陣</div>', unsafe_allow_html=True)
        selected_etfs = st.multiselect("請挑選多檔欲進行權重對比的 ETF", etf_list, default=etf_list[:2] if len(etf_list) >= 2 else etf_list)
        
        if selected_etfs:
            comp_df = get_multi_etf_comparison(df, selected_etfs)
            if comp_df is not None:
                st.dataframe(comp_df, use_container_width=True, hide_index=True)
            else:
                st.warning("選擇的 ETF 組合查無對應交叉持股數據。")
        else:
            st.info("請先挑選至少一檔以上的 ETF 進行矩陣比對。")
        st.markdown('</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
