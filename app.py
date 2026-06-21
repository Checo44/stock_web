import streamlit as st

import pandas as pd

import numpy as np

import gspread

import json

import os



# ==========================================

# 1. 網頁基本設定與全安全客製化 CSS 注入

# ==========================================

st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")



SHEET_NAME = "ETF daily"

WORKSHEET_HISTORY = "ETF History"



# 使用純內建 CSS 機制，移除外部 Link 避免干擾 React 前端

st.markdown("""

    <style>

        /* 全域清爽白底與字體規範 */

        html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {

            font-family: 'Helvetica Neue', Arial, 'Noto Sans TC', sans-serif !important;

            background-color: #f8f9fa !important;

            color: #333333 !important;

        }

        

        /* 區塊白底高質感卡片 */

        .white-panel-card {

            background-color: #ffffff;

            border: 1px solid #e2e8f0;

            border-radius: 6px;

            padding: 16px;

            margin-bottom: 1rem;

            box-shadow: 0 1px 3px rgba(0,0,0,0.02);

        }

        

        /* 區塊小標題 */

        .panel-title {

            font-size: 0.95rem;

            font-weight: 700;

            color: #2d3748;

            margin-bottom: 12px;

            display: flex;

            align-items: center;

        }

        

        /* 頂部 6 聯排獨立彩色頂邊框卡片 */

        .meta-box {

            background: #ffffff;

            border: 1px solid #e2e8f0;

            border-radius: 4px;

            padding: 12px 10px;

            text-align: center;

            margin-bottom: 1rem;

            box-shadow: 0 1px 2px rgba(0,0,0,0.01);

        }

        .meta-title {

            font-size: 0.8rem;

            color: #718096;

            margin-bottom: 4px;

            font-weight: 500;

        }

        .meta-num {

            font-size: 1.2rem;

            font-weight: 700;

            color: #1a202c;

            min-height: 28px;

        }

        

        /* 底部深色緞帶明細表頭 */

        .dark-ribbon-header {

            background-color: #1a202c;

            color: #ffffff;

            padding: 12px 16px;

            font-weight: 700;

            font-size: 0.9rem;

            border-top-left-radius: 6px;

            border-top-right-radius: 6px;

            display: flex;

            justify-content: space-between;

            align-items: center;

        }

        

        /* 區間標籤美化 */

        .date-badge {

            background-color: #edf2f7;

            color: #2d3748;

            padding: 2px 8px;

            border-radius: 4px;

            font-weight: 600;

            border: 1px solid #cbd5e0;

            font-size: 0.85rem;

        }

        

        #MainMenu {visibility: hidden;}

        footer {visibility: hidden;}

    </style>

""", unsafe_allow_html=True)



# ==========================================

# 2. 獨立安全的連線與資料載入核心 (快取內絕不含 UI 元件)

# ==========================================

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

        if gc: 

            return gc.open(SHEET_NAME)

    except:

        pass

    return None



sh = init_gspread()



@st.cache_data(ttl=300)

def fetch_raw_sheet_data():

    """純粹撈取資料，若失敗回傳 None 與錯誤訊息，不呼叫任何 st.error"""

    if not sh: 

        return None, "無法連線至 Google 試算表，請檢查憑證設定。"

    try:

        ws = sh.worksheet(WORKSHEET_HISTORY)

        raw_data = ws.get_all_values()

        if not raw_data or len(raw_data) < 2:

            return None, f"工作表「{WORKSHEET_HISTORY}」內沒有足夠的數據列。"

        return raw_data, None

    except Exception as e:

        return None, f"讀取工作表「{WORKSHEET_HISTORY}」失敗: {str(e)}"



def process_and_standardize(raw_data):

    """標準化清洗資料，精準對齊使用者的實際欄位名稱"""

    df = pd.DataFrame(raw_data[1:], columns=raw_data[0])

    df.columns = [str(c).strip() for c in df.columns]

    

    # 🎯 精準對齊您的試算表欄位名稱

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

    

    # 檢查核心轉換是否成功，避免後續運算發生 KeyError

    missing = [k for k in ["etf", "date", "stock", "weight", "volume"] if k not in df.columns]

    if missing:

        return pd.DataFrame(), f"主要欄位對照失敗。請確認工作表首行是否包含您的預設欄位。缺少對應: {missing}"



    # 資料格式安全清洗

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



# ==========================================

# 3. 主 UI 執行緒面板渲染 (安全攔截機制)

# ==========================================

def main():

    # 讀取最原始試算表陣列

    raw_data, err_msg = fetch_raw_sheet_data()

    if err_msg:

        st.error(err_msg)

        return

        

    # 在非快取區清洗資料

    df, clean_err = process_and_standardize(raw_data)

    if clean_err:

        st.error(clean_err)

        return

        

    if df.empty:

        st.info("💡 試算表目前為空，或日期轉換後無有效數據。")

        return



    # 取得不重複的 ETF 清單

    etf_list = sorted(df['etf'].dropna().unique().tolist())

    if not etf_list:

        st.warning("⚠️ 未在「ETF代號」欄位中偵測到任何資料。")

        return



    # 建立左右不對稱版面佈局 (左選單 1.1 : 右主頁面 3.5)

    main_left, main_right = st.columns([1.1, 3.5])



    # ------------------------------------------

    # 左側控制台：請選擇 ETF 代號

    # ------------------------------------------

    with main_left:

        st.markdown('<div class="panel-title"><b>::: 請選擇 ETF 代號</b></div>', unsafe_allow_html=True)

        search_query = st.text_input("輸入關鍵字篩選...", placeholder="輸入關鍵字篩選...", label_visibility="collapsed", key="left_filter")

        

        filtered_etfs = [e for e in etf_list if search_query.lower() in e.lower()] if search_query else etf_list

        

        if filtered_etfs:

            selected_etf = st.radio("ETF清單列表", filtered_etfs, label_visibility="collapsed", key="left_etf_radio")

        else:

            st.write("<small style='color:gray;'>無相符結果</small>", unsafe_allow_html=True)

            selected_etf = None



    # ------------------------------------------

    # 右側主控制台：核心大數據監控

    # ------------------------------------------

    with main_right:

        if not selected_etf:

            st.info("💡 請在左側選單選擇或篩選出欲查看的 ETF 代號。")

            return

            

        df_etf = df[df['etf'] == selected_etf].copy()

        if df_etf.empty:

            st.warning(f"該 ETF ({selected_etf}) 查無關聯歷史明細。")

            return

            

        sorted_dates = sorted(df_etf['date'].unique())

        latest_date = sorted_dates[-1]



        # 頂部控制面板：籌碼比較天數 / 範圍

        st.markdown('<div class="white-panel-card">', unsafe_allow_html=True)

        st.markdown('<div class="panel-title"><b>🗃️ 籌碼比較天數 / 範圍邏輯</b></div>', unsafe_allow_html=True)

        

        ctrl_c1, ctrl_c2 = st.columns([3, 1])

        with ctrl_c1:

            comp_option = st.selectbox(

                "比較日選擇",

                ["與前 1 筆紀錄比較 (日變動)", "與前 5 筆紀錄比較", "與前 10 筆紀錄比較"],

                label_visibility="collapsed"

            )

            if "1" in comp_option: offset = 1

            elif "5" in comp_option: offset = 5

            else: offset = 10

            

            compare_index = max(0, len(sorted_dates) - 1 - offset)

            compare_date = sorted_dates[compare_index]

            

        with ctrl_c2:

            st.button("🧮 重新計算籌碼", use_container_width=True)

            

        st.markdown(f'<p style="font-size:0.85rem; color:#4a5568; margin: 6px 0 0 0;">📊 <b>籌碼分析區間：</b> 比較日 <span class="date-badge">{compare_date}</span> ➔ 基準日 <span class="date-badge">{latest_date}</span></p>', unsafe_allow_html=True)

        st.markdown('</div>', unsafe_allow_html=True)



        # 擷取最新日期的指標資料

        df_latest = df_etf[df_etf['date'] == latest_date]

        

        def fetch_meta_val(key_name):

            val_set = df_latest[df_latest['stock'] == key_name]['volume'].values

            if len(val_set) > 0 and str(val_set[0]).strip() != "":

                try:

                    return f"{int(float(val_set[0])):,}"

                except:

                    return str(val_set[0])

            return "-"



        is_stock = is_global_stock_code(df_latest)

        stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()

        assets_df = df_latest[~is_stock].copy()



        # 頂部 6 聯排獨立彩色頂邊框

        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)

        mc1.markdown(f'<div class="meta-box" style="border-top: 3px solid #718096;"><div class="meta-title">昨收價</div><div class="meta-num">{fetch_meta_val("昨收價")}</div></div>', unsafe_allow_html=True)

        mc2.markdown(f'<div class="meta-box" style="border-top: 3px solid #e53e3e;"><div class="meta-title">漲跌</div><div class="meta-num">{fetch_meta_val("漲跌")}</div></div>', unsafe_allow_html=True)

        mc3.markdown(f'<div class="meta-box" style="border-top: 3px solid #3182ce;"><div class="meta-title">市價</div><div class="meta-num">{fetch_meta_val("市價")}</div></div>', unsafe_allow_html=True)

        

        stock_vol_str = fetch_meta_val("股數") if "股數" in df_latest['stock'].values else f"{int(stocks_df['volume'].sum()):,}" if not stocks_df.empty else "-"

        mc4.markdown(f'<div class="meta-box" style="border-top: 3px solid #dd6b20;"><div class="meta-title">股數</div><div class="meta-num">{stock_vol_str}</div></div>', unsafe_allow_html=True)

        mc5.markdown(f'<div class="meta-box" style="border-top: 3px solid #805ad5;"><div class="meta-title">規模</div><div class="meta-num">{fetch_meta_val("規模")}</div></div>', unsafe_allow_html=True)

        mc6.markdown(f'<div class="meta-box" style="border-top: 3px solid #319795;"><div class="meta-title">折溢價</div><div class="meta-num">{fetch_meta_val("折溢價")}</div></div>', unsafe_allow_html=True)



        # 中層雙表格佈局

        sub_col1, sub_col2 = st.columns([2.1, 1.1])

        

        with sub_col1:

            st.markdown('<div class="panel-title"><b>📋 最新成分股持股明細</b></div>', unsafe_allow_html=True)

            st.dataframe(

                stocks_df[['stock', 'name', 'weight', 'volume']],

                use_container_width=True,

                hide_index=True,

                height=320,

                column_config={

                    "stock": st.column_config.TextColumn("股票代號"),

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



        # 底部高質感深色動態分析看板

        st.markdown(f"""

            <div class="dark-ribbon-header">

                <span>⚡ 動態籌碼異動計算與連續狀態追蹤</span>

                <span style="font-size: 0.8rem; font-weight: 400; opacity: 0.85;">基準最新日: {latest_date}</span>

            </div>

        """, unsafe_allow_html=True)



        # 提取對比歷史數據

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

            

            # 動態狀態追蹤計算

            status_map = calculate_continuous_status(df_etf[is_global_stock_code(df_etf)], sorted_dates, 'stock')

            df_change['continuousStatus'] = df_change['stock'].map(status_map)

            

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
