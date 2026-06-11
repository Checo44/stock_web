import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials  # 如果您是用這個方式認證

# ==================== 1. 初始化 gspread (加入這段) ====================
try:
    # 請將 '您的憑證路徑.json' 替換為您真正的 Google Cloud 金鑰 JSON 檔案路徑
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(
        "您的憑證路徑.json", scopes=scope
    )
    gc = gspread.authorize(creds)

    # 或者是您原本如果使用 st.secrets 的話：
    # gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])

    # 將 '您的試算表名稱' 替換為雲端硬碟上那份 Excel/試算表的完整名稱
    sh = gc.open("您的試算表名稱")  # <--- 就是這裡定義了 sh！

except Exception as e:
    st.error(f"❌ Google Sheets 認證或打開檔案失敗: {e}")
    st.stop()  # 認證失敗就停止往下執行，避免後面報錯
# =====================================================================


# 輔助函式：將 Google Sheets 的 get_all_values() 轉為乾淨的 DataFrame
def sheets_to_df(rows):
    if not rows or len(rows) < 1:
        return pd.DataFrame()
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df


# ---- 以下為原本的 Streamlit 介面與 Tab1 ----
st.title("ETF 投資數據儀表板")
tab1, tab2 = st.tabs(["ETF 異動矩陣", "其他數據"])

with tab1:
    st.subheader("每日各 ETF 增減倉純淨異動矩陣")
    try:
        # 現在這裡就能正常抓到 sh 了
        matrix_rows = sh.worksheet("ETF異動矩陣_純淨版").get_all_values()
        df_matrix = sheets_to_df(matrix_rows)

        if not df_matrix.empty:
            for col in df_matrix.columns:
                col_data = df_matrix[col]
                if isinstance(col_data, pd.DataFrame):
                    for sub_col in col_data.columns:
                        df_matrix.loc[:, sub_col] = pd.to_numeric(
                            col_data[sub_col], errors="coerce"
                        )
                else:
                    df_matrix[col] = pd.to_numeric(
                        col_data, errors="coerce"
                    )

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
                styled_df, use_container_width=True, hide_index=True
            )
        else:
            st.info("工作表『ETF異動矩陣_純淨版』目前無資料")
    except Exception as e:
        st.error(f"❌ 讀取『ETF異動矩陣_純淨版』工作表發生錯誤: {e}")
