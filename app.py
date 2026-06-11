import pandas as pd
import gspread  # 確保您已安裝並設定好 gspread
import streamlit as st


# 輔助函式：將 Google Sheets 的 get_all_values() 轉為乾淨的 DataFrame
def sheets_to_df(rows):
    if not rows or len(rows) < 1:
        return pd.DataFrame()
    # 以第一列作為欄位名稱（Header）
    df = pd.DataFrame(rows[1:], columns=rows[0])
    return df


# 假設您前面已經完成了 gspread 的認證，並取得 client 物件
# gc = gspread.service_account(filename='您的憑證路徑.json')
# sh = gc.open("您的試算表名稱")

# ---- 以下為 Streamlit 介面與 Tab1 的完整實作 ----

st.title("ETF 投資數據儀表板")

# 建立分頁（以您的情境為例，這裡建立 Tab1）
tab1, tab2 = st.tabs(["ETF 異動矩陣", "其他數據"])

with tab1:
    st.subheader("每日各 ETF 增減倉純淨異動矩陣")
    try:
        # 1. 從 Google 試算表讀取原始資料
        # 注意：此處 sh 變數需為您在前面已定義的 gspread Spreadsheet 物件
        matrix_rows = sh.worksheet("ETF異動矩陣_純淨版").get_all_values()
        df_matrix = sheets_to_df(matrix_rows)

        if not df_matrix.empty:
            # 2. 安全地將數值欄位轉為 numeric 型態，並防止「重複欄位標頭」引發的二維 DataFrame 錯誤
            for col in df_matrix.columns:
                col_data = df_matrix[col]

                # 【防呆機制】如果 col_data 的型態是 DataFrame，代表 Excel/Google 試算表中有重複的欄位名稱
                if isinstance(col_data, pd.DataFrame):
                    # 走訪所有名稱重複的子欄位，逐一轉換為一維數值
                    for sub_col in col_data.columns:
                        df_matrix.loc[:, sub_col] = pd.to_numeric(
                            col_data[sub_col], errors="coerce"
                        )
                else:
                    # 正常情況：欄位名稱不重複，屬於一維 Series
                    df_matrix[col] = pd.to_numeric(
                        col_data, errors="coerce"
                    )

            # 3. 檢查是否有欄位「原本全是文字」而被誤轉為全 NaN。若是，則維持或還原（可選）
            for col in df_matrix.columns:
                if (
                    df_matrix[col].isna().all()
                    and not df_matrix[col].empty
                ):
                    # 如果整欄轉換後全部變成 NaN，代表它本來就是文字欄位（例如：股票名稱、日期文字）
                    # 這裡保持原樣，讓 Streamlit 以文字顯示
                    pass

            # 4. 自動篩選出成功轉換為「純數字」的欄位名稱，用來做漸層上色
            # 這樣可以避免文字欄位（如股票代號、名稱）被強行上色而導致程式碼崩潰
            numeric_cols = df_matrix.select_dtypes(
                include=["number"]
            ).columns.tolist()

            # 5. 根據是否有數值欄位，決定是否套用 Style 漸層
            if numeric_cols:
                # cmap='RdYlGn' 代表 紅(Red)-黃(Yellow)-綠(Green) 漸層
                # axis=0 代表以「直欄」為單位進行每欄的獨立大小比較
                styled_df = df_matrix.style.background_gradient(
                    cmap="RdYlGn", axis=0, subset=numeric_cols
                )
            else:
                styled_df = df_matrix

            # 6. 將樣式化後的 DataFrame 渲染到 Streamlit 網頁上
            st.dataframe(
                styled_df,
                use_container_width=True,  # 自動填滿容器寬度
                hide_index=True,  # 隱藏 pandas 預設的流水號索引
            )
        else:
            st.info("工作表『ETF異動矩陣_純淨版』目前無資料")

    except Exception as e:
        # 捕捉所有讀取或轉換過程中發生的異常，並優雅地呈現在畫面上，避免網頁整頁當掉
        st.error(f"❌ 讀取『ETF異動矩陣_純淨版』工作表發生錯誤: {e}")

with tab2:
    st.write("其他功能的實作區塊...")
