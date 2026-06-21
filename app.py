import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定與精美前端樣式注入 (完美對齊隨附截圖)
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"

# 全面注入對齊截圖與網頁規格的極致 CSS 視覺特徵
st.markdown("""
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        /* 全域背景色與字體規範 */
        html, body, [data-testid="stAppViewContainer"] {
            font-family: 'Noto Sans TC', sans-serif !important;
            background-color: #f4f6f9 !important;
            color: #333333;
        }
        
        /* 頂部極簡漸層導覽列 */
        .custom-navbar {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            padding: 10px 20px;
            border-radius: 8px;
            margin-bottom: 1.25rem;
        }
        .custom-navbar-brand {
            color: #ffffff !important;
            font-size: 1.15rem;
            font-weight: 700;
            text-decoration: none;
            display: flex;
            align-items: center;
        }
        
        /* 區塊標題樣式 */
        .section-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: #2d3748;
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
        }
        
        /* 頂部 6 聯排獨立彩色頂邊框 Meta 卡片 */
        .meta-card-grid {
            background: #ffffff;
            padding: 12px;
            border-radius: 6px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.03);
            text-align: center;
            margin-bottom: 1rem;
            min-height: 75px;
        }
        .meta-label {
            font-size: 0.8rem;
            color: #718096;
            margin-bottom: 6px;
            font-weight: 500;
        }
        .meta-value {
            font-size: 1.1rem;
            font-weight: 700;
            color: #1a202c;
        }
        
        /* 底部深色緞帶 Banner 樣式 */
        .dark-banner-header {
            background-color: #2d3748;
            color: #ffffff;
            padding: 10px 15px;
            font-weight: 700;
            font-size: 0.95rem;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        /* 輕量級容器外殼 */
        .custom-container-box {
            background-color: #ffffff;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.02);
            padding: 15px;
            margin-bottom: 1.25rem;
            border: 1px solid #e2e8f0;
        }
        
        /* 頁籤微調使其貼近原版 */
        button[data-baseweb="tab"] {
            font-size: 0.9rem !important;
            padding: 0.5rem 1rem !important;
            border-radius: 6px !important;
        }
        
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# 頂部導覽列
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
# 2. 資料清洗與處理
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
# 3. 核心業務指標運算
# ==========================================
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
    
    is_stock = is_global_stock_code(df_latest)
    stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()
    assets_df = df_latest[~is_stock].copy()
    
    # 建立對齊截圖的 6 大營運數據字典
    meta = {
        "lastClose": get_meta("昨收價"),
        "change": get_meta("漲跌"),
        "marketPrice": get_meta("市價"),
        "volume": get_meta("股數") if "股數" in df_latest['stock'].values else f"{int(stocks_df['volume'].sum()):,}" if not stocks_df.empty else "-",
        "size": get_meta("規模"),
        "premium": get_meta("折溢價")
    }
    
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
    
    for col in ['etf', 'stock', 'name']:
        if col == 'etf': fill_val = df_merged['etf_stock'].str.split('_').str[0]
        elif col == 'stock': fill_val = df_merged['etf_stock'].str.split('_').str[1]
        else: fill_val = ''
        df_merged[col] = df_merged[col].replace(0, np.nan).fillna(fill_val)

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
    
    # 🎯 修正拼寫，確保交叉透視功能完美無瑕
    pivot_weight = df_sub.pivot_table(index=['stock', 'name'], columns='etf', values='weight', aggfunc='sum').fillna(0)
    pivot_weight.columns = [f"{c} 權重(%)" for c in pivot_weight.columns]
    return pivot_weight.reset_index()

# ==========================================
# 4. 介面多層級版型渲染 (Tab A 完美重構)
# ==========================================
def main():
    df = load_historical_data()
    if df.empty:
        st.info("💡 試算表連線中或無有效數據，請確認 Google Secrets 與試算表名稱欄位。")
        return

    etf_list = sorted(df['etf'].dropna().unique().tolist())
    
    # 全域側邊時間窗
    st.sidebar.header("⚙️ 全域對比視窗")
    range_type = st.sidebar.selectbox("歷史對比時間窗口", ["1", "5", "10", "custom"], index=0)
    
    start_date, end_date = None, None
    if range_type == "custom":
        available_dates = sorted(df['date'].unique())
        start_date = st.sidebar.selectbox("起始對比日", available_dates, index=0)
        end_date = st.sidebar.selectbox("結束基準日", available_dates, index=len(available_dates)-1)

    tabs = st.tabs([
        "📋 單檔 ETF 籌碼與持股", 
        "🔗 個股籌碼分佈", 
        "🌍 全市場異動總覽", 
        "🔥 市場熱度排行", 
        "⚔️ ETF 交叉比較"
    ])
    
    # ------------------------------------------
    # Tab A: 單檔 ETF 籌碼與持股 (對齊附圖核心改造)
    # ------------------------------------------
    with tabs[0]:
        # 建立左右不對稱核心骨架 (左側選擇器：寬度 1；右側主視窗：寬度 3.2)
        col_left, col_right = st.columns([1, 3.2])
        
        with col_left:
            st.markdown('<div class="section-title"><i class="bi bi-list-ul me-2"></i>請選擇 ETF 代號</div>', unsafe_allow_html=True)
            search_query = st.text_input("篩選", placeholder="輸入關鍵字篩選...", label_visibility="collapsed", key="search_left")
            
            # 過濾清單項目
            filtered_etfs = [e for e in etf_list if search_query.lower() in e.lower()] if search_query else etf_list
            selected_etf = st.selectbox("ETF清單", filtered_etfs, label_visibility="collapsed", key="etf_select_box")
            
        with col_right:
            res = get_etf_detail_data(df, selected_etf, range_type, start_date, end_date)
            if res:
                m = res['meta']
                # 頂部 6 聯排獨立彩色邊框 Meta 卡片
                mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
                mc1.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #cbd5e1;"><div class="meta-label">昨收價</div><div class="meta-value">{m["lastClose"]}</div></div>', unsafe_allow_html=True)
                mc2.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #ef4444;"><div class="meta-label">漲跌</div><div class="meta-value">{m["change"]}</div></div>', unsafe_allow_html=True)
                mc3.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #3b82f6;"><div class="meta-label">市價</div><div class="meta-value">{m["marketPrice"]}</div></div>', unsafe_allow_html=True)
                mc4.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #f97316;"><div class="meta-label">股數</div><div class="meta-value">{m["volume"]}</div></div>', unsafe_allow_html=True)
                mc5.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #a855f7;"><div class="meta-label">規模</div><div class="meta-value">{m["size"]}</div></div>', unsafe_allow_html=True)
                mc6.markdown(f'<div class="meta-card-grid" style="border-top: 3px solid #14b8a6;"><div class="meta-label">折溢價</div><div class="meta-value">{m["premium"]}</div></div>', unsafe_allow_html=True)
                
                # 中層雙表格左右並排佈局
                sub_col1, sub_col2 = st.columns([2.2, 1.1])
                with sub_col1:
                    st.markdown('<div class="section-title"><i class="bi bi-grid-3x3-gap-fill me-2"></i>最新成分股持股明細</div>', unsafe_allow_html=True)
                    st.dataframe(res['stocks'][['stock', 'name', 'weight', 'volume']].rename(columns={'stock':'股票代號','name':'股票名稱','weight':'持股權重','volume':'最新持股(股)'}), use_container_width=True, hide_index=True, height=350)
                with sub_col2:
                    st.markdown('<div class="section-title"><i class="bi bi-shield-lock-fill me-2"></i>非股票資產項目</div>', unsafe_allow_html=True)
                    st.dataframe(res['assets'][['stock', 'name', 'weight', 'volume']].rename(columns={'stock':'資產代號','name':'資產項目','weight':'權重','volume':'資產價值(股)'}), use_container_width=True, hide_index=True, height=350)
                
                # 籌碼控制範圍區塊
                st.markdown('<div class="custom-container-box">', unsafe_allow_html=True)
                st.markdown('<h6><i class="bi bi-sliders me-2"></i>籌碼比較天數 / 範圍</h6>', unsafe_allow_html=True)
                ctrl_c1, ctrl_c2 = st.columns([3, 1])
                with ctrl_c1:
                    range_display = st.selectbox("比較日選擇", [f"與前 {range_type} 筆紀錄比較 (日變動)"], label_visibility="collapsed", disabled=True)
                with ctrl_c2:
                    st.button("📋 重新計算籌碼", use_container_width=True, key="recalc_btn")
                st.markdown(f'<p style="font-size:0.85rem; color:#4a5568; margin-top:5px; margin-bottom:0;">🗓️ <b>籌碼分析區間：</b> 比較日 <span class="badge bg-light text-dark">[{res["compareDate"]}]</span> ➔ 基準日 <span class="badge bg-light text-dark">[{res["latestDate"]}]</span></p>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
                
                # 底部深色緞帶動態分析區塊
                st.markdown(f"""
                    <div class="dark-banner-header">
                        <span><i class="bi bi-lightning-charge-fill text-warning me-2"></i>動態籌碼異動計算與連續狀態追蹤</span>
                        <span style="font-size: 0.8rem; opacity: 0.9;">基準最新日: {res['latestDate']}</span>
                    </div>
                """, unsafe_allow_html=True)
                
                if not res['changes'].empty:
                    st.dataframe(res['changes'][['stock', 'name', 'nature', 'diff', 'continuousStatus']].rename(columns={'stock':'成分股','name':'股票名稱','nature':'異動性質','diff':'區間增減股數','continuousStatus':'核心歷史連續買賣狀態'}), use_container_width=True, hide_index=True)
                else:
                    st.info("該時間區間內此 ETF 成分股數量無發生增減持異動。")

    # ------------------------------------------
    # Tab B: 個股籌碼分佈
    # ------------------------------------------
    with tabs[1]:
        st.markdown('<div class="custom-container-box"><h5><i class="bi bi-share-fill me-2"></i>核心個股穿透分析</h5>', unsafe_allow_html=True)
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
        st.markdown('<div class="custom-container-box"><h5><i class="bi bi-globe me-2"></i>全市場 ETF 成分股異動快照大數據</h5>', unsafe_allow_html=True)
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
        st.markdown('<div class="custom-container-box"><h5><i class="bi bi-fire me-2 text-danger"></i>全市場熱度追蹤排行</h5>', unsafe_allow_html=True)
        heat = get_market_heat_ranking(df)
        if heat:
            st.caption(f"最新計算基準日：{heat['date']}")
            hc1, hc2 = st.columns(2)
            
            with hc1:
                st.markdown('<div style="border-top: 4px solid #ef4444; padding-top:10px;"><h5>🔺 <span class="badge bg-danger">Top 10</span> 全市場投信法人加碼熱度榜</h5></div>', unsafe_allow_html=True)
                st.dataframe(heat['bought'].rename(columns={'stock':'股票代號','name':'股票名稱','net_change':'全市場淨加碼股數','volume_new':'當前總持股數'}).drop(columns=['volume_old']), use_container_width=True, hide_index=True)
                
            with hc2:
                st.markdown('<div style="border-top: 4px solid #10b981; padding-top:10px;"><h5>🔻 <span class="badge bg-success">Top 10</span> 全市場投信法人減碼熱度榜</h5></div>', unsafe_allow_html=True)
                st.dataframe(heat['sold'].rename(columns={'stock':'股票代號','name':'股票名稱','net_change':'全市場淨減碼股數','volume_new':'當前總持股數'}).drop(columns=['volume_old']), use_container_width=True, hide_index=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ------------------------------------------
    # Tab E: ETF 交叉比較
    # ------------------------------------------
    with tabs[4]:
        st.markdown('<div class="custom-container-box"><h5><i class="bi bi-arrow-left-right me-2"></i>多檔 ETF 成分股持股權重同步交叉矩陣</h5>', unsafe_allow_html=True)
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
