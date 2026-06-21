import streamlit as st
import streamlit.components.v1 as components
import os
import json

# ==============================================================================
# 1. 初始化 Streamlit 頁面設定（開啟寬螢幕模式、預設收起側邊欄）
# ==============================================================================
st.set_page_config(
    page_title="ETF 籌碼大數據監控面板",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==============================================================================
# 2. 強大隱藏術：注入 CSS 拔除 Streamlit 所有原生邊框與元件
#    讓你的 Bootstrap 5 網頁能夠 100% 滿版無縫填滿整個螢幕！
# ==============================================================================
st.markdown("""
    <style>
    /* 隱藏頂部主選單與裝飾條 */
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    
    /* 隱藏底部浮水印 footer */
    footer {visibility: hidden;}
    
    /* 將 Streamlit 預設的主容器邊距（Padding）歸零 */
    .block-container {
        padding-top: 0rem !important;
        padding-bottom: 0rem !important;
        padding-left: 0rem !important;
        padding-right: 0rem !important;
    }
    
    /* 確保內嵌的 iframe 底部沒有奇異的空白間距 */
    iframe {
        display: block;
        border: none;
    }
    </style>
""", unsafe_allow_html=True)


# ==============================================================================
# 3. 【進階資料對接功能】（可選）
#    如果你希望未來由 Python 來抓取資料（例如從資料庫、Pandas 或 CSV），
#    可以在這裡把資料打包成 JSON，直接空投注入給 index.html 的 JavaScript 接收！
# ==============================================================================
def fetch_backend_data_from_python():
    # 這裡未來可以放你的 Pandas 運算邏輯，目前先留空
    # sample_df = pd.read_csv("your_data.csv")
    # return sample_df.to_json(orient="records")
    return None


# ==============================================================================
# 4. 讀取 index.html 並透過 Streamlit Component 渲染渲染上線
# ==============================================================================
def main():
    html_filename = "index.html"
    
    if os.path.exists(html_filename):
        with open(html_filename, "r", encoding="utf-8") as f:
            html_content = f.read()
        
        # 💡 資料對接小技巧：
        # 如果你想把 Python 資料塞進前端，可以解開下方這兩行，並在 index.html 裡留一個引子接收
        # python_data = fetch_backend_data_from_python()
        # if python_data:
        #     html_content = html_content.replace('let globalRawData = [];', f'let globalRawData = {python_data};')

        # 渲染 HTML。將 height 設為 1600 或更高，確保垂直頁面有充足的發揮空間，並開啟 scrolling
        components.html(html_content, height=1500, scrolling=True)
        
    else:
        # 防呆機制
        st.error(f"❌ 找不到 `{html_filename}` 檔案！")
        st.info("請確認你的資料夾結構，`app.py` 與 `index.html` 必須放在同一個目錄階層下喔！")

if __name__ == "__main__":
    main()
