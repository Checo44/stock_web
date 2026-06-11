import json
import os
import gspread
import pandas as pd
import streamlit as st

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
    json_path = os.path.join(os.getcwd(), "credentials.json")
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
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


# 輔助函式：將 Google Sheets 的二維陣列安全轉換為帶有正確 Header 的 Pandas DataFrame
def sheets_to_df(rows):
    if not rows or len(rows) == 0:
        return pd.DataFrame()
    # 第一列作為欄位名稱，其餘為資料
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df


if not sh:
    st.warning(
        "⏳ 等待 Google Sheets 憑證正確注入... 請確保 Secrets 已正確填寫 `GOOGLE_CREDENTIALS`。"
    )
else:
    st.title("📊 ETF 籌碼動態監控中心")

    # 側邊欄：點擊查詢單檔 ETF 明細 (如 00981A)
    st.sidebar.header("🔍 個別 ETF 明細查詢")
    etf_input = (
        st.sidebar.text_input("輸入 ETF 代號 (如: 00981A):", "").upper().strip()
    )
    if etf_input:
        # 自動補零防呆機制
        if len(etf_input) == 4 and etf_input[0].isdigit():
            etf_input = f"00{etf_input}"
        elif len(etf_input) == 5 and etf_input[0].isdigit():
            etf_input = f"0{etf_input}"

        try:
            raw_etf_rows = sh.worksheet(etf_input).get_all_values()
            df_etf = sheets_to_df(raw_etf_rows)

            st.sidebar.success(f"成功開啟 {etf_input} 工作表")
            st.markdown(f"### 📈 {etf_input} 完整持股明細")
            # 修正：Streamlit 1.x+ 滿寬度請改用 use_container_width=True，避免使用不支援的 "stretch"
            st.dataframe(df_etf, use_container_width=True, hide_index=True)
        except gspread.exceptions.WorksheetNotFound:
            st.sidebar.error(f"❌ 找不到工作表: '{etf_input}'")
        except Exception as e:
            st.sidebar.error(f"❌ 讀取發生錯誤: {e}")

    # 使用 4 個 Tab 頁籤切換指定的工作表內容
    tab1, tab2, tab3, tab4 = st.tabs(
        [
            "🎨 ETF 異動矩陣",
            "🔥 今日英雄榜",
            "🎯 籌碼分佈",
            "📚 歷史紀錄 (ETF History)",
        ]
    )

    with tab1:
        st.subheader("每日各 ETF 增減倉純淨異動矩陣")
        try:
            matrix_rows = sh.worksheet("ETF異動矩陣_純淨版").get_all_values()
            df_matrix = sheets_to_df(matrix_rows)

            if not df_matrix.empty:
                # 修正與優化：走訪所有欄位，嘗試將能轉成數字的欄位進行轉換
                for col in df_matrix.columns:
                    # 使用 pd.to_numeric 並設定 errors='coerce'（無法轉換則留空）或 'ignore'
                    # 這裡用 to_numeric 轉換後，如果整欄能轉成數字，Style 渐变色才能正確呈現
                    converted = pd.to_numeric(df_matrix[col], errors="coerce")
                    if not converted.isna().all():  # 如果這欄包含數字，則更新該欄位
                        df_matrix[col] = converted.fillna(df_matrix[col])

                # 修正：寬度設定改用 use_container_width=True
                # 加上防呆：如果遇到非數值資料，Style 漸層可能會報錯，透過指定數值型欄位或 subset 來確保安全
                numeric_cols = df_matrix.select_dtypes(
                    include=["number"]
                ).columns.tolist()
                if numeric_cols:
                    styled_df = df_matrix.style.background_gradient(
                        cmap="RdYlGn", axis=0, subset=numeric_cols
                    )
                else:
                    styled_df = df_matrix

                st.dataframe(
                    styled_df,
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.info("工作表目前無資料")
        except Exception as e:
            st.info(f"暫時無法讀取『ETF異動矩陣_純淨版』工作表。錯誤: {e}")

    with tab2:
        st.subheader("今日異動英雄榜 (Hero_List)")
        try:
            hero_rows = sh.worksheet("Hero_List_美化版").get_all_values()
            df_hero = sheets_to_df(hero_rows)
            st.dataframe(df_hero, use_container_width=True, hide_index=True)
        except Exception as e:
            st.info("暫時無法讀取『Hero_List_美化版』工作表")

    with tab3:
        st.subheader("關鍵標的籌碼分佈庫")
        try:
            chip_rows = sh.worksheet("標的籌碼分佈_美化版").get_all_values()
            df_chip = sheets_to_df(chip_rows)
            st.dataframe(df_chip, use_container_width=True, hide_index=True)
        except Exception as e:
            st.info("暫時無法讀取『標的籌碼分佈_美化版』工作表")

    with tab4:
        st.subheader("ETF History 歷史資料庫 (最新 500 筆)")
        try:
            history_rows = sh.worksheet("ETF History").get_all_values()
            if len(history_rows) > 1:
                df_history = sheets_to_df(history_rows)
                # 優化：取出最後 500 筆並倒序，建議加上 reset_index(drop=True) 確保索引純淨
                df_latest_500 = (
                    df_history.tail(500).iloc[::-1].reset_index(drop=True)
                )

                st.dataframe(
                    df_latest_500, use_container_width=True, hide_index=True
                )
            else:
                st.info("目前歷史紀錄無資料")
        except Exception as e:
            st.info("暫時無法讀取『ETF History』工作表")
