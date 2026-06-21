import streamlit as st
import pandas as pd
import numpy as np
import gspread
import json
import os

# ==========================================
# 1. 網頁基本設定與全真 UI 視覺美化 CSS 注入
# ==========================================
st.set_page_config(page_title="ETF 籌碼大數據監控面板", layout="wide")

SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"

# 全方位注入對齊截圖質感的客製化 CSS 樣式
st.markdown("""
    <style>
        /* 全域清爽底色與字體規範 */
        html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans TC", sans-serif !important;
            background-color: #f3f4f6 !important;
            color: #1f2937 !important;
        }
        
        /* 區塊高質感白底外殼 */
        .white-panel-card {
            background-color: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 18px;
            margin-bottom: 1.2rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.02);
        }
        
        /* 區塊標題樣式 */
        .panel-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: #1e293b;
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        /* 頂部 6 聯排獨立彩色頂條指標卡片 */
        .meta-box {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            padding: 12px 8px;
            text-align: center;
            margin-bottom: 1.2rem;
            box-shadow: 0 1px 2px rgba(0,0,0,0.01);
        }
        .meta-title {
            font-size: 0.8rem;
            color: #64748b;
            margin-bottom: 6px;
            font-weight: 500;
        }
        .meta-num {
            font-size: 1.25rem;
            font-weight: 700;
            color: #0f172a;
            min-height: 30px;
        }
        
        /* 底部深色高質感 Banner */
        .dark-ribbon-header {
            background-color: #1e293b;
            color: #ffffff;
            padding: 12px 18px;
            font-weight: 700;
            font-size: 0.92rem;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        /* 區間分析標籤 */
        .date-badge {
            background-color: #f1f5f9;
            color: #334155;
            padding: 3px 10px;
            border-radius: 5px;
            font-weight: 600;
            border: 1px solid #cbd5e1;
            font-size: 0.85rem;
        }

        /* 完美還原圖二：左側單選清單卡片化美化 */
        div[data-testid="stRadio"] > div[role="radiogroup"] {
            gap: 6px !important;
            background: transparent !important;
        }
        div[data-testid="stRadio"] [data-testid="stWidgetLabel"] {
            display: none !important;
        }
        div[data-testid="stRadio"] label {
            background-color: #ffffff !important;
            border: 1px solid #e2e8f0 !important;
            border-radius: 6px !important;
            padding: 10px 14px !important;
            margin: 0 !important;
            width: 100% !important;
            box-shadow: 0 1px 2px rgba(0,0,0,0.02) !important;
            cursor: pointer !important;
            display: flex !important;
            align-items: center !important;
        }
        div[data-testid="stRadio"] label:hover {
            background-color: #f8fafc !important;
            border-color: #cbd5e1 !important;
        }
        /* 被選中的藍底高質感狀態 */
        div[data-testid="stRadio"] label[data-checked="true"] {
            background-color: #1e3a8a !important; 
            border-color: #1e3a8a !important;
        }
        div[data-testid="stRadio"] label[data-checked="true"] span {
            color: #ffffff !important;
            font-weight: 700 !important;
        }
        /* 隱藏原生的醜圓圈單選鈕 */
        div[data-testid="stRadio"] label > div:first-child {
            display: none !important;
        }
        
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 獨立安全的連線與資料載入核心 (快取安全防護)
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

# ==========================================
# 3. 主 UI 執行緒渲染
# ==========================================
def main():
    raw_data, err_msg = fetch_raw_sheet_data()
    if err_msg:
        st.error(err_msg)
        return
        
    df, clean_err = process_and_standardize(raw_data)
    if clean_err:
        st.error(clean_err)
        return
        
    if df.empty:
        st.info("💡 試算表中目前無有效數據。")
        return

    etf_list = sorted(df['etf'].dropna().unique().tolist())

    # 建立左右不對稱版面佈局 (左選單 1.1 : 右主頁面 3.5)
    main_left, main_right = st.columns([1.1, 3.5])

    # ------------------------------------------
    # 左側控制台：圖二高質感單選列表
    # ------------------------------------------
    with main_left:
        st.markdown('<div class="panel-title"><b>📋 請選擇 ETF 代號</b></div>', unsafe_allow_html=True)
        search_query = st.text_input("輸入關鍵字篩選...", placeholder="輸入關鍵字篩選...", label_visibility="collapsed", key="left_filter")
        
        # 篩選相符的 ETF
        matched_etfs = [e for e in etf_list if search_query.lower() in e.lower()] if search_query else etf_list
        
        if matched_etfs:
            # 加上書籤小圖示供卡片顯示
            display_list = [f"📄 {e}" for e in matched_etfs]
            selected_display = st.radio("ETF清單列表", display_list, label_visibility="collapsed", key="left_etf_radio")
            selected_etf = selected_display.replace("📄 ", "")
        else:
            st.write("<small style='color:gray;'>無相符結果</small>", unsafe_allow_html=True)
            selected_etf = None

    # ------------------------------------------
    # 右側主控制台：核心大數據監控看板
    # ------------------------------------------
    with main_right:
        if not selected_etf:
            st.info("💡 請在左側選單選擇欲查看的 ETF 代號。")
            return
            
        df_etf = df[df['etf'] == selected_etf].copy()
        if df_etf.empty:
            st.warning(f"該 ETF ({selected_etf}) 查無關聯歷史明細。")
            return
            
        sorted_dates = sorted(df_etf['date'].unique())
        latest_date = sorted_dates[-1]
        df_latest = df_etf[df_etf['date'] == latest_date]

        # 1. 頂部 6 聯排獨立精緻指標卡片
        def fetch_meta_val(key_name):
            val_set = df_latest[df_latest['stock'] == key_name]['volume'].values
            if len(val_set) > 0 and str(val_set[0]).strip() != "":
                try: return f"{int(float(val_set[0])):,}"
                except: return str(val_set[0])
            return "-"

        is_stock = is_global_stock_code(df_latest)
        stocks_df = df_latest[is_stock].sort_values(by='weight', ascending=False).copy()
        assets_df = df_latest[~is_stock].copy()

        mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
        mc1.markdown(f'<div class="meta-box" style="border-top: 3px solid #64748b;"><div class="meta-title">昨收價</div><div class="meta-num">{fetch_meta_val("昨收價")}</div></div>', unsafe_allow_html=True)
        mc2.markdown(f'<div class="meta-box" style="border-top: 3px solid #ef4444;"><div class="meta-title">漲跌</div><div class="meta-num">{fetch_meta_val("漲跌")}</div></div>', unsafe_allow_html=True)
        mc3.markdown(f'<div class="meta-box" style="border-top: 3px solid #3b82f6;"><div class="meta-title">市價</div><div class="meta-num">{fetch_meta_val("市價")}</div></div>', unsafe_allow_html=True)
        
        stock_vol_str = fetch_meta_val("股數") if "股數" in df_latest['stock'].values else f"{int(stocks_df['volume'].sum()):,}" if not stocks_df.empty else "-"
        mc4.markdown(f'<div class="meta-box" style="border-top: 3px solid #f97316;"><div class="meta-title">股數</div><div class="meta-num">{stock_vol_str}</div></div>', unsafe_allow_html=True)
        mc5.markdown(f'<div class="meta-box" style="border-top: 3px solid #a855f7;"><div class="meta-title">規模</div><div class="meta-num">{fetch_meta_val("規模")}</div></div>', unsafe_allow_html=True)
        mc6.markdown(f'<div class="meta-box" style="border-top: 3px solid #14b8a6;"><div class="meta-title">折溢價</div><div class="meta-num">{fetch_meta_val("折溢價")}</div></div>', unsafe_allow_html=True)

        # 2. 中層雙表格佈局 (還原圖一：內嵌小白色代號藥丸外殼)
        sub_col1, sub_col2 = st.columns([2.1, 1.1])
        
        with sub_col1:
            st.markdown('<div class="panel-title"><b>📋 最新成分股持股明細</b></div>', unsafe_allow_html=True)
            left_table_html = """
            <div style="max-height:330px; overflow-y:auto; border:1px solid #e5e7eb; border-radius:6px; background:white;">
            <table style="width:100%; border-collapse:collapse; font-size:0.88rem; text-align:left;">
                <thead>
                    <tr style="border-bottom:2px solid #e2e8f0; color:#64748b; font-weight:600; background:#f8fafc; position:sticky; top:0;">
                        <th style="padding:10px 14px;">股票代號</th>
                        <th style="padding:10px 14px;">股票名稱</th>
                        <th style="padding:10px 14px; text-align:right;">持股權重</th>
                        <th style="padding:10px 14px; text-align:right;">最新持股(股)</th>
                    </tr>
                </thead>
                <tbody>
            """
            for _, row in stocks_df.iterrows():
                left_table_html += f"""
                    <tr style="border-bottom:1px solid #f1f5f9; color:#334155;">
                        <td style="padding:10px 14px;"><span style="border:1px solid #cbd5e1; background:#ffffff; border-radius:4px; padding:2px 7px; font-family:monospace; font-size:0.82rem; color:#475569; font-weight:500;">{row['stock']}</span></td>
                        <td style="padding:10px 14px; font-weight:500; color:#0f172a;">{row['name']}</td>
                        <td style="padding:10px 14px; text-align:right; font-weight:500;">{row['weight']:.2f}%</td>
                        <td style="padding:10px 14px; text-align:right; color:#475569;">{int(row['volume']):,} 股</td>
                    </tr>
                """
            left_table_html += "</tbody></table></div>"
            st.markdown(left_table_html, unsafe_allow_html=True)
            
        with sub_col2:
            st.markdown('<div class="panel-title"><b>🔒 非股票資產項目</b></div>', unsafe_allow_html=True)
            right_table_html = """
            <div style="max-height:330px; overflow-y:auto; border:1px solid #e5e7eb; border-radius:6px; background:white;">
            <table style="width:100%; border-collapse:collapse; font-size:0.88rem; text-align:left;">
                <thead>
                    <tr style="border-bottom:2px solid #e2e8f0; color:#64748b; font-weight:600; background:#f8fafc; position:sticky; top:0;">
                        <th style="padding:10px 14px;">資產代號</th>
                        <th style="padding:10px 14px;">資產項目</th>
                        <th style="padding:10px 14px; text-align:right;">權重</th>
                        <th style="padding:10px 14px; text-align:right;">資產價值(股)</th>
                    </tr>
                </thead>
                <tbody>
            """
            for _, row in assets_df.iterrows():
                vol_str = f"{int(row['volume']):,}" if row['volume'] != 0 else "-"
                weight_str = f"{row['weight']:.2f}%" if row['weight'] != 0 else "%"
                right_table_html += f"""
                    <tr style="border-bottom:1px solid #f1f5f9; color:#334155;">
                        <td style="padding:10px 14px;"><span style="border:1px solid #cbd5e1; background:#ffffff; border-radius:4px; padding:2px 7px; font-family:monospace; font-size:0.82rem; color:#475569;">{row['stock']}</span></td>
                        <td style="padding:10px 14px; font-size:0.82rem; color:#64748b; max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">{row['name']}</td>
                        <td style="padding:10px 14px; text-align:right;">{weight_str}</td>
                        <td style="padding:10px 14px; text-align:right; color:#475569;">{vol_str}</td>
                    </tr>
                """
            right_table_html += "</tbody></table></div>"
            st.markdown(right_table_html, unsafe_allow_html=True)

        st.write("<div style='margin-bottom: 25px;'></div>", unsafe_allow_html=True)

        # 3. 🎯 【重組移位】圖四：籌碼比較天數/範圍控制區（精準插入至正中間）
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
            st.button("🗓️ 重新計算籌碼", use_container_width=True)
            
        st.markdown(f'<p style="font-size:0.85rem; color:#475569; margin: 8px 0 0 0;">📊 <b>籌碼分析區間：</b> 比較日 <span class="date-badge">{compare_date}</span> ➔ 基準日 <span class="date-badge">{latest_date}</span></p>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        # 4. 底層高質感深色動態籌碼分析看板（還原圖三：綠底減少標籤與淡黃連續買賣狀態）
        st.markdown(f"""
            <div class="dark-ribbon-header">
                <span>⚡ 動態籌碼異動計算與連續狀態追蹤</span>
                <span style="font-size: 0.8rem; font-weight: 400; opacity: 0.85;">基準最新日: {latest_date}</span>
            </div>
        """, unsafe_allow_html=True)

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
            
            status_map = calculate_continuous_status(df_etf[is_global_stock_code(df_etf)], sorted_dates, 'stock')
            df_change['continuousStatus'] = df_change['stock'].map(status_map)
            
            # 使用高階 HTML 渲染完美契合圖三外觀
            bottom_table_html = """
            <div style="border:1px solid #e5e7eb; border-top:none; border-bottom-left-radius:8px; border-bottom-right-radius:8px; background:white; overflow:hidden;">
            <table style="width:100%; border-collapse:collapse; font-size:0.88rem; text-align:left;">
                <thead>
                    <tr style="border-bottom:2px solid #e2e8f0; color:#64748b; font-weight:600; background:#f8fafc;">
                        <th style="padding:12px 16px;">成分股</th>
                        <th style="padding:12px 16px;">異動性質</th>
                        <th style="padding:12px 16px; text-align:right;">區間增減股數</th>
                        <th style="padding:12px 16px;">核心歷史連續買賣狀態</th>
                    </tr>
                </thead>
                <tbody>
            """
            for _, row in df_change.iterrows():
                nature = row['nature']
                # 依據性質配置顏色 (圖三中 減少為深綠/藍綠色標籤與字體)
                if nature == "減少":
                    badge_style = "background-color:#0f766e; color:white; padding:3px 8px; border-radius:4px; font-size:0.78rem; font-weight:600;"
                    diff_style = "color:#0f766e; text-align:right; font-weight:600;"
                else:
                    badge_style = "background-color:#dc2626; color:white; padding:3px 8px; border-radius:4px; font-size:0.78rem; font-weight:600;"
                    diff_style = "color:#dc2626; text-align:right; font-weight:600;"
                
                # 處理狀態淡黃色標籤
                status_str = row['continuousStatus']
                if "賣" in status_str:
                    status_html = f'<span style="background-color:#fef3c7; color:#92400e; padding:4px 12px; border-radius:4px; font-weight:600; font-size:0.82rem; border:1px solid #fde68a;">📉 {status_str}</span>'
                elif "買" in status_str:
                    status_html = f'<span style="background-color:#dcfce7; color:#166534; padding:4px 12px; border-radius:4px; font-weight:600; font-size:0.82rem; border:1px solid #bbf7d0;">📈 {status_str}</span>'
                else:
                    status_html = f'<span style="color:#64748b;">{status_str}</span>'
                    
                bottom_table_html += f"""
                    <tr style="border-bottom:1px solid #f1f5f9; color:#334155;">
                        <td style="padding:14px 16px; font-weight:700; color:#0f172a;">{row['stock']} <span style="font-weight:400; color:#475569; margin-left:6px;">{row['name']}</span></td>
                        <td style="padding:14px 16px;"><span style="{badge_style}">{nature}</span></td>
                        <td style="padding:14px 16px; {diff_style}">{int(row['diff']):,} 股</td>
                        <td style="padding:14px 16px;">{status_html}</td>
                    </tr>
                """
            bottom_table_html += "</tbody></table></div>"
            st.markdown(bottom_table_html, unsafe_allow_html=True)
        else:
            st.markdown('<div style="padding:20px; border:1px solid #e5e7eb; border-top:none; background:white; text-align:center; color:gray;">💡 該對比區間內，此 ETF 成分股持倉數量未發生任何變動。</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
