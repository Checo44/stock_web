import streamlit as st
import gspread
import os
import json

# 1. 網頁基本設定 (全寬佈局、暗色科技風)
st.set_page_config(page_title="ETF 籌碼監控儀表板", layout="wide")

SHEET_NAME = "ETF daily"

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

# 初始化 Google Sheets (使用 st.cache_resource 避免重複連線被 Google 封鎖)
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

if not sh:
    st.warning("⏳ 等待 Google Sheets 憑證正確注入... 請確保 Secrets 已正確填寫 `GOOGLE_CREDENTIALS`。")
else:
    st.title("📊 ETF 籌碼動態監控中心")
    
    # 側邊欄：點擊查詢單檔 ETF 明細 (如 00981A)
    st.sidebar.header("🔍 個別 ETF 明細查詢")
    etf_input = st.sidebar.text_input("輸入 ETF 代號 (如: 00981A):", "").upper().strip()
    if etf_input:
        # 自動補零防呆機制
        if len(etf_input) == 4 and etf_input[0].isdigit(): etf_input = f"00{etf_input}"
        elif len(etf_input) == 5 and etf_input[0].isdigit(): etf_input = f"0{etf_input}"
            
        try:
            etf_rows = sh.worksheet(etf_input).get_all_values()
            st.sidebar.success(f"成功開啟 {etf_input} 工作表")
            st.markdown(f"### 📈 {etf_input} 完整持股明細")
            st.dataframe(etf_rows, use_container_width=True)
        except gspread.exceptions.WorksheetNotFound:
            st.sidebar.error(f"❌ 找不到工作表: '{etf_input}'")
        except Exception as e:
            st.sidebar.error(f"❌ 讀取發生錯誤: {e}")

    # 使用 4 個 Tab 頁籤切換你指定的 5 類工作表內容
    tab1, tab2, tab3, tab4 = st.tabs([
        "🎨 ETF 異動矩陣", 
        "🔥 今日英雄榜", 
        "🎯 籌碼分佈", 
        "📚 歷史紀錄 (ETF History)"
    ])
    
    with tab1:
        st.subheader("每日各 ETF 增減倉純淨異動矩陣")
        try:
            matrix_data = sh.worksheet("ETF異動矩陣_純淨版").get_all_values()
            st.table(matrix_data) # Streamlit 會把你的畫布矩陣完美對齊輸出
        except Exception as e:
            st.info("暫時無法讀取『ETF異動矩陣_純淨版』工作表")

    with tab2:
        st.subheader("今日異動英雄榜 (Hero_List)")
        try:
            hero_data = sh.worksheet("Hero_List_美化版").get_all_values()
            st.dataframe(hero_data, use_container_width=True)
        except Exception as e:
            st.info("暫時無法讀取『Hero_List_美化版』工作表")

    with tab3:
        st.subheader("關鍵標的籌碼分佈庫")
        try:
            chip_data = sh.worksheet("標的籌碼分佈_美化版").get_all_values()
            st.dataframe(chip_data, use_container_width=True)
        except Exception as e:
            st.info("暫時無法讀取『標的籌碼分佈_美化版』工作表")

    with tab4:
        st.subheader("ETF History 歷史資料庫 (最新 500 筆)")
        try:
            history_data = sh.worksheet("ETF History").get_all_values()
            if len(history_data) > 1:
                header = history_data[0]
                rows = history_data[1:]
                # 倒序排列，讓最新追加的日期在最上面
                display_rows = [header] + list(reversed(rows[-500:]))
                st.dataframe(display_rows, use_container_width=True)
            else:
                st.info("目前歷史紀錄無資料")
        except Exception as e:
            st.info("暫時無法讀取『ETF History』工作表")
