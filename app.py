import gspread
import pandas as pd
import streamlit as st

# ==================== 1. 初始化 gspread (免實體 JSON 檔案版) ====================
try:
    # 直接從 st.secrets 中讀取名為 "gcp_service_account" 的設定項目
    # 這免去了管理本機 'xxx.json' 檔案路徑的困擾，且利於雲端部署
    gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])

    # 【注意】請將下方改為你雲端硬碟上那份試算表的「確切名稱」
    sh = gc.open("您的試算表名稱")

except Exception as e:
    st.error(
        f"❌ Google Sheets 認證或打開檔案失敗。\n"
        f"請檢查 .streamlit/secrets.toml 設定或試算表名稱是否正確。\n"
        f"錯誤訊息: {e}"
    )
    st.stop()
# ==============================================================================


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
