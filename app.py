import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定與極致白底美化 CSS 注入
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"

# 注入完美對齊原圖的白底、輕量邊框、彩色頂條 CSS
st.markdown("""
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        /* 全域白底樣式與字體規範 */
        html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
            font-family: 'Noto Sans TC', sans-serif !important;
            background-color: #f8f9fa !important;
            color: #333333 !important;
        }
        
        /* 左側與右側區塊的外殼白底樣式 */
        .white-panel-card {
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 16px;
            margin-bottom: 1rem;
        }
        
        /* 區塊小標題 */
        .panel-title {
            font-size: 0.9rem;
            font-weight: 700;
            color: #2d3748;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
        }
        
        /* 頂部 6 聯排獨立彩色頂邊框 Meta 卡片 */
        .meta-box {
            background: #ffffff;
            border: 1px solid #e2e8f0;
            border-radius: 4px;
            padding: 10px;
            text-align: center;
            margin-bottom: 1rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.02);
        }
        .meta-title {
            font-size: 0.8rem;
            color: #718096;
            margin-bottom: 4px;
            font-weight: 500;
        }
        .meta-num {
            font-size: 1.15rem;
            font-weight: 700;
            color: #1a202c;
            min-height: 27px;
        }
        
        /* 底部深色緞帶 Banner */
        .dark-ribbon-header {
            background-color: #1a202c;
            color: #ffffff;
            padding: 10px 16px;
            font-weight: 700;
            font-size: 0.9rem;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        /* 移除隱藏元件 */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 雲端試算表資料串接與清洗
# ==========================================
def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json and "GOOGLE_CREDENTIALS" in st.secrets:
        creds_json = st.secrets["GOOGLE_CREDENTIALS"]

    if creds_json:
        try:
            clean_json = creds_json.strip().strip("'").strip('"')
            return gspread.service_account_from_dict(json.loads(clean_json))
        except Exception as e:
            st.error(f"❌ Google Credentials 解析失敗: {e}")

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
        return None
    except Exception as e:
        st.error(f"❌ 雲端試算表連線失敗: {e}")
        return None

sh = init_gspread()

@st.cache_data(ttl=600)
def load_historical_data():
    if not sh: return pd.DataFrame()
    try:
        ws = sh.worksheet(WORKSHEET_HISTORY)
        raw_data = ws.get_all_values()
        if not raw_data or len(raw_data) < 2: return pd.DataFrame()
        return standardize_df(pd.DataFrame(raw_data[1:], columns=raw_data[0]))
    except Exception as e:
        st.error(f"❌ 讀取工作表「{WORKSHEET_HISTORY}」失敗: {e}")
        return pd.DataFrame()

def standardize_df(df):
    if df.empty: return df
    alias_map = {
        "etf": ["ETF代號", "ETF", "ETF碼"],
        "date": ["日期", "時間", "Date"],
        "stock": ["股票代號", "成分股代號", "代號", "商品代號"],
        "name": ["股票名稱", "成分股名稱", "名稱", "商品名稱"],
        "weight": ["權重", "權重(%)", "持股比例"],
        "volume": ["持有數", "張數", "持有張數", "股數", "持有股數"]
    }
    df.columns = [str(c).strip() for c in df.columns]
    rename_dict = {}
    for standard, aliases in alias_map.items():
        for alias in aliases:
            if alias in df.columns:
                rename_dict[alias] = standard
                break
    df = df.rename(columns=rename_dict)
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d')
    df['weight'] = pd.to_numeric(df['weight'].astype(str).str.replace('%','').str.replace(',',''), errors='coerce').fillna(0.0)
    if df['weight'].max() <= 1.0: df['weight'] = df['weight'] * 100
    df['volume'] = pd.to_numeric(df['volume'].astype(str).str.replace(',',''), errors='coerce').fillna(0.0)
    df['stock'] = df['stock'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['etf'] = df['etf'].astype(str).str.strip()
    return df.dropna(subset=['date'])

def is_global_stock_code(df):
    meta_keywords = ["昨收價", "漲跌", "市價", "張數", "股數", "規模", "折溢價", "昨收", "UNDEFINED", "NULL", ""]
    exclude_keywords = ["DA_", "CASH", "C_", "PFUR_", "USD", "TWD", "NTD", "現金", "應付", "應收", "保證金", "期貨"]
    mask_meta = df['stock'].str.upper().isin(meta_keywords) | df['name'].str.upper().isin(meta_keywords)
    mask_exclude = df['stock'].str.upper().str.contains('|'.join(exclude_keywords)) | df['name'].str.upper().str.contains('|'.join(exclude_keywords))
    return ~(mask_meta | mask_exclude)

# ==========================================
# 3. 籌碼動態核心邏輯運算
# ==========================================
def calculate_continuous_status(df_target, sorted_dates, key_col='stock'):
    status_dict = {}
    if len(sorted_dates) < 2:
        return {k: "-" for k in df_target[key_col].unique()}
        
    for code, group in df_target.groupby(key_col):
        series = group.groupby('date')['volume'].sum().reindex(sorted_dates, fill_value=0)
        diff_values = series.diff().values[::-1] # 從最新日期倒回去算
        
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

# ==========================================
# 4. 主渲染畫面 (完美對應白底與控制邏輯)
# ==========================================
def main():
    df = load_historical_data()
    if df.empty:
        st.info("💡 試算表載入中，或未發現有效歷史欄位數據。")
        return

    etf_list = sorted(df['etf'].dropna().unique().tolist())

    # 建立左右不對稱主結構 (左側選擇面板 1.1 : 右側主視覺監控 3.5)
    main_left, main_right = st.columns([1.1, 3.5])

    # ------------------------------------------
    # 左側面板：請選擇 ETF 代號 (精準還原截圖樣式)
    # ------------------------------------------
    with main_left:
        st.markdown('<div class="panel-title"><b>::: 請選擇 ETF 代號</b></div>', unsafe_allow_html=True)
        search_query = st.text_input("輸入關鍵字篩選...", placeholder="輸入關鍵字篩選...", label_visibility="collapsed", key="left_filter")
        
        filtered_etfs = [e for e in etf_list if search_query.lower() in e.lower()] if search_query else etf_list
        
        # 模擬左側縱向點選清單 (使用單選鈕美化或選單)
        selected_etf = st.radio("ETF清單列表", filtered_etfs, label_visibility="collapsed", key="left_etf_radio")

    # ------------------------------------------
    # 右側面板：核心大數據監控看板
    # ------------------------------------------
    with main_right:
        df_etf = df[df['etf'] == selected_etf].copy()
        if df_etf.empty:
            st.warning("該 ETF 查無關聯歷史明細。")
            return
            
        sorted_dates = sorted(df_etf['date'].unique())
        latest_date = sorted_dates[-1]

        # 籌碼比較天數/範圍控制盒 (在此處接接收使用者的設定值)
        st.markdown('<div class="white-panel-card">', unsafe_allow_html=True)
        st.markdown('<div class="panel-title"><b>🗃️ 籌碼比較天數 / 範圍</b></div>', unsafe_allow_html=True)
        
        ctrl_c1, ctrl_c2 = st.columns([3, 1])
        with ctrl_c1:
            # 建立真正能選擇並動態轉換邏輯的下拉選單
            comp_option = st.selectbox(
                "比較日選擇",
                ["與前 1 筆紀錄比較 (日變動)", "與前 5 筆紀錄比較", "與前 10 筆紀錄比較"],
                label_visibility="collapsed"
            )
            # 根據下拉選單解析 offset 天數
            if "1" in comp_option: offset = 1
            elif "5" in comp_option: offset = 5
            else: offset = 10
            
            compare_index = max(0, len(sorted_dates) - 1 - offset)
            compare_date = sorted_dates[compare_index]
            
        with ctrl_c2:
            recalc_triggered = st.button("🧮 重新計算籌碼", use_container_width=True)
            
        st.markdown(f'<p style="font-size:0.85rem; color:#4a5568; margin: 4px 0 0 0;">📊 <b>籌碼分析區間：</b> 比較日 <span class="badge bg-light text-dark" style="border:1px solid #cbd5e1;">{compare_date}</span> ➔ 基準日 <span class="badge bg-light text-dark" style="border:1px solid #cbd5e1;">{latest_date}</span></p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # 提取最新一日的對應資料
        df_latest = df_etf[df_etf['date'] == latest_date]
        
        def fetch_meta_val(key_name):
            val_set = df_latest[df_latest['stock'] == key_name]['volume'].values
            return str(val_set[0]) if len(val_set) > 0 and str(val_set[0]).strip() != "" else "-"

        is_stock = is_global_stock_code(df_latest)
        stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()
        assets_df = df_latest[~is_stock].copy()

        # 頂部 6 聯排獨立彩色頂邊框 Meta 卡片
        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        
        # 昨收價 (灰色)
        mc1.markdown(f'<div class="meta-box" style="border-top: 3px solid #718096;"><div class="meta-title">昨收價</div><div class="meta-num">{fetch_meta_val("昨收價")}</div></div>', unsafe_allow_html=True)
        # 漲跌 (紅色)
        mc2.markdown(f'<div class="meta-box" style="border-top: 3px solid #e53e3e;"><div class="meta-title">漲跌</div><div class="meta-num">{fetch_meta_val("漲跌")}</div></div>', unsafe_allow_html=True)
        # 市價 (藍色)
        mc3.markdown(f'<div class="meta-box" style="border-top: 3px solid #3182ce;"><div class="meta-title">市價</div><div class="meta-num">{fetch_meta_val("市價")}</div></div>', unsafe_allow_html=True)
        # 股數 (橘色)
        stock_vol_str = fetch_meta_val("股數") if "股數" in df_latest['stock'].values else f"{int(stocks_df['volume'].sum()):,}" if not stocks_df.empty else "-"
        mc4.markdown(f'<div class="meta-box" style="border-top: 3px solid #dd6b20;"><div class="meta-title">股數</div><div class="meta-num">{stock_vol_str}</div></div>', unsafe_allow_html=True)
        # 規模 (紫色)
        mc5.markdown(f'<div class="meta-box" style="border-top: 3px solid #805ad5;"><div class="meta-title">規模</div><div class="meta-num">{fetch_meta_val("規模")}</div></div>', unsafe_allow_html=True)
        # 折溢價 (青綠色)
        mc6.markdown(f'<div class="meta-box" style="border-top: 3px solid #319795;"><div class="meta-title">折溢價</div><div class="meta-num">{fetch_meta_val("折溢價")}</div></div>', unsafe_allow_html=True)

        # 中層雙表格：最新成分股持股明細 vs 非股票資產項目
        sub_col1, sub_col2 = st.columns([2.1, 1.1])
        
        with sub_col1:
            st.markdown('<div class="panel-title"><b>📋 最新成分股持股明細</b></div>', unsafe_allow_html=True)
            st.dataframe(
                stocks_df[['stock', 'name', 'weight', 'volume']],
                use_container_width=True,
                hide_index=True,
                height=320,
                column_config={
                    "stock": st.column_config.TextColumn("股票代號", help="成分股代號"),
                    "name": "股票名稱",
                    "weight": st.column_config.NumberColumn("持股權重", format="%.2f%%"),
                    "volume": st.column_config.NumberColumn("最新持股(股)", format="%d 股")
                }
            )
            
        with sub_col2:
            st.markdown('<div class="panel-title"><b>🔒 非股票資產項目</b></div>', unsafe_allow_html=True)
            st.dataframe(
                assets_df[['stock', 'name', 'weight', 'volume']],
                use_container_width=True,
                hide_index=True,
                height=320,
                column_config={
                    "stock": "資產代號",
                    "name": "資產項目",
                    "weight": st.column_config.TextColumn("權重"),
                    "volume": st.column_config.NumberColumn("資產價值(股)", format="%d")
                }
            )

        # 底部深色高質感動態分析看板
        st.markdown(f"""
            <div class="dark-ribbon-header">
                <span>⚡ 動態籌碼異動計算與連續狀態追蹤</span>
                <span style="font-size: 0.8rem; font-weight: 400; opacity: 0.85;">基準最新日: {latest_date}</span>
            </div>
        """, unsafe_allow_html=True)

        # 計算對比日之間的籌碼異動量
        df_comp = df_etf[df_etf['date'] == compare_date]
        df_merged = pd.merge(
            stocks_df[['stock', 'name', 'volume']], 
            df_comp[['stock', 'volume']], 
            on='stock', 
            how='outer', 
            suffixes=('_new', '_old')
        ).fillna(0)
        
        df_merged['diff'] = df_merged['volume_new'] - df_merged['volume_old']
        df_change = df_merged[df_merged['diff'] != 0].copy()

        if not df_change.empty:
            def judge_nature(r):
                if r['volume_old'] == 0 and r['volume_new'] > 0: return "新增"
                if r['volume_old'] > 0 and r['diff'] > 0: return "增加"
                if r['volume_new'] > 0 and r['diff'] < 0: return "減少"
                return "刪除"
            df_change['nature'] = df_change.apply(judge_nature, axis=1)
            
            # 計算歷史連續狀態
            status_map = calculate_continuous_status(df_etf[is_global_stock_code(df_etf)], sorted_dates, 'stock')
            df_change['continuousStatus'] = df_change['stock'].map(status_map)
            
            # 美化展示變動數據表格
            st.dataframe(
                df_change[['stock', 'name', 'nature', 'diff', 'continuousStatus']],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "stock": "成分股",
                    "name": "股票名稱",
                    "nature": "異動性質",
                    "diff": st.column_config.NumberColumn("區間增減股數", format="%d 股"),
                    "continuousStatus": "核心歷史連續買賣狀態"
                }
            )
        else:
            st.info("💡 該對比區間內，此 ETF 成分股持倉數量未發生任何增減變動。")

if __name__ == "__main__":
    main()
