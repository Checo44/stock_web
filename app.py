import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import os
import requests

# ==========================================
# 1. 網頁基本設定
# ==========================================
st.set_page_config(page_title="ETF 籌碼監控面板", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
        #MainMenu {visibility: hidden;} header {visibility: hidden;} footer {visibility: hidden;}
        .block-container { padding-top: 1rem !important; }
        .card { border: none; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 1.5rem; background-color: #fff; }
        .card-header { background-color: #fff; border-bottom: 1px solid #edf2f9; font-weight: 700; padding: 1rem 1.25rem; }
        .price-up { color: #dc2626 !important; font-weight: 700; }
        .price-down { color: #0f766e !important; font-weight: 700; }
    </style>
""", unsafe_allow_html=True)

# 試算表設定維持不變
SHEET_NAME = "ETF daily"
WORKSHEET_HISTORY = "ETF History"
WORKSHEET_TICKER = "代號"
WORKSHEET_ETF_NAME = "名稱"

# ==========================================
# [請在此處保留/填入您原有的 gspread 連線與資料獲取函式]
# 例如：get_sheets_client, init_gspread, fetch_raw_sheet_data 等
# ==========================================

def fetch_twse_live_data(etf_list):
    """獲取 ETF 即時價格與漲跌幅百分比"""
    if not etf_list:
        return {}
    ch_elements = [f"tse_{code}.tw" for code in etf_list] + [f"otc_{code}.tw" for code in etf_list]
    api_url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={'|'.join(ch_elements)}"
    try:
        res = requests.get(api_url, timeout=5)
        data = res.json().get("msgArray", [])
        
        result = {}
        for item in data:
            code = item.get("c")
            try:
                z = item.get("z", "-")  # 當盤成交價
                y = item.get("y", "-")  # 昨收價
                
                # 如果 z 是 '-' 則嘗試取最高買進價或昨收
                if z == "-" or not z:
                    price = "-"
                    change_pct = 0.0
                else:
                    price = float(z)
                    ref_price = float(y) if (y and y != "-") else price
                    change_pct = ((price - ref_price) / ref_price) * 100 if ref_price else 0.0
                
                result[code] = {
                    "price": f"{price:.2f}" if isinstance(price, float) else "-",
                    "change": round(change_pct, 2)
                }
            except:
                result[code] = {"price": "-", "change": 0.0}
        return result
    except: 
        return {}

# ==========================================
# 2. 渲染邏輯
# ==========================================
def main():
    # ----------------------------------------------------
    # 模擬或讀取您的 ETF 列表數據 (請確保此處與您的 Google 試算表對接)
    # 這裡預設填入您圖片中的主動型 ETF 範例資料，若已有試算表連線請替換為讀取試算表的邏輯
    # ----------------------------------------------------
    try:
        # 範例：若您有 fetch_raw_sheet_data 函數，取消註釋下方：
        # df = fetch_raw_sheet_data()
        # etf_data_list = df[['代號', '名稱']].to_dict(orient='records')
        
        # 以下為對應圖片內容的預設資料結構：
        etf_data_list = [
            {"代號": "00400A", "名稱": "主動國泰動能高息"},
            {"代號": "00401A", "名稱": "主動摩根台灣鑫收"},
            {"代號": "00402A", "名稱": "主動安聯美國科技"},
            {"代號": "00403A", "名稱": "主動統一升級50"},
            {"代號": "00404A", "名稱": "主動聯博動能50"},
            {"代號": "00405A", "名稱": "主動富邦台灣龍耀"},
            {"代號": "00406A", "名稱": "中信台灣收益成長"},
            {"代號": "00980A", "名稱": "主動野村臺灣優選"},
            {"代號": "00981A", "名稱": "主動統一台股增長"},
            {"代號": "00982A", "名稱": "主動群益台灣強棒"},
            {"代號": "00983A", "名稱": "主動中信ARK創新"},
            {"代號": "00984A", "名稱": "主動安聯台灣高息"},
            {"代號": "00985A", "名稱": "主動野村台灣50"},
            {"代號": "00986A", "名稱": "主動台新龍頭成長"},
            {"代號": "00987A", "名稱": "主動群益美國增長"}
        ]
    except Exception as e:
        st.error(f"資料讀取失敗: {e}")
        etf_data_list = []

    # 提取代號列表並獲取交易所即時報價
    etf_tickers = [item["代號"] for item in etf_data_list]
    live_market_data = fetch_twse_live_data(etf_tickers)

    # 整合名稱與即時報價
    full_table_data = []
    for item in etf_data_list:
        ticker = item["代號"]
        name = item["名稱"]
        live_info = live_market_data.get(ticker, {"price": "-", "change": 0.0})
        
        full_table_data.append({
            "ticker": ticker,
            "name": name,
            "price": live_info["price"],
            "change": live_info["change"]
        })

    # 將 Python 資料轉為 JSON 供前端 JavaScript 讀取
    json_data = json.dumps(full_table_data, ensure_ascii=False)

    html_template = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
      <style>
        body {{
            background-color: #f8f9fa;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }}
        .nav-tabs .nav-link {{
            color: #495057;
            font-weight: 500;
            border: none;
            padding: 0.75rem 1.25rem;
        }}
        .nav-tabs .nav-link.active {{
            color: #0d6efd;
            background-color: transparent;
            border-bottom: 3px solid #0d6efd;
            font-weight: bold;
        }}
        .card {{
            border: none;
            border-radius: 8px;
            box-shadow: 0 0.125rem 0.25rem rgba(0, 0, 0, 0.075);
            background-color: #ffffff;
        }}
        .card-header {{
            background-color: #ffffff;
            border-bottom: 1px solid #dee2e6;
            font-weight: bold;
            font-size: 1.1rem;
            padding: 1rem 1.25rem;
        }}
        .table {{
            margin-bottom: 0;
            vertical-align: middle;
        }}
        .table th {{
            font-weight: 500;
            color: #6c757d;
            border-top: none;
            background-color: #fafafa;
            padding: 0.75rem 1rem;
        }}
        .table td {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid #f1f1f1;
            font-size: 0.95rem;
        }}
        /* 紅漲綠跌與平盤顏色設定 */
        .text-up {{
            color: #dc2626 !important;
            font-weight: 600;
        }}
        .text-down {{
            color: #0f766e !important;
            font-weight: 600;
        }}
        .text-even {{
            color: #212529 !important;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
      </style>
    </head>
    <body>
      <div class="container-fluid py-3">
        <ul class="nav nav-tabs mb-4">
          <li class="nav-item">
            <button class="nav-link active" onclick="switchTab(this, 'content-home')">首頁總覽</button>
          </li>
          <li class="nav-item">
            <button class="nav-link" onclick="switchTab(this, 'content-a')">籌碼與持股</button>
          </li>
        </ul>

        <div id="content-home" class="tab-content active">
            <div class="card">
                <div class="card-header">ETF 即時行情總覽</div>
                <div class="table-responsive">
                    <table class="table table-hover">
                        <thead>
                            <tr>
                                <th>ETF代號</th>
                                <th>ETF名稱</th>
                                <th>現價</th>
                                <th>漲跌幅</th>
                            </tr>
                        </thead>
                        <tbody id="homeTableBody">
                            </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="content-a" class="tab-content">
            <div class="card p-4">
                <h5>籌碼與持股詳細數據</h5>
                <p class="text-muted">此處為籌碼分頁內容位置...</p>
            </div>
        </div>
      </div>

      <script>
        // 接收來自 Python 端的資料
        const tableData = {json_data};

        function switchTab(button, id) {{ 
            // 切換分頁內容顯示
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.getElementById(id).classList.add('active');
            
            // 切換按鈕 active 樣式
            document.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            button.classList.add('active');
        }}

        function renderTable() {{
            const tbody = document.getElementById('homeTableBody');
            tbody.innerHTML = '';

            tableData.forEach(item => {{
                const tr = document.createElement('tr');
                
                // 判斷漲跌幅正負號並給予相對應的顏色 Class 與 符號
                let changeClass = 'text-even';
                let changeText = item.change;

                if (item.change > 0) {{
                    changeClass = 'text-up';
                    changeText = '+' + item.change + '%';
                }} else if (item.change < 0) {{
                    changeClass = 'text-down';
                    changeText = item.change + '%';
                }} else {{
                    changeText = item.change === '-' ? '-' : '0.00%';
                }}

                tr.innerHTML = `
                    <td>${{item.ticker}}</td>
                    <td>${{item.name}}</td>
                    <td>${{item.price}}</td>
                    <td class="${{changeClass}}">${{changeText}}</td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        // 初始化表格渲染
        document.addEventListener('DOMContentLoaded', renderTable);
      </script>
    </body>
    </html>
    """
    
    # 渲染 HTML 組件
    components.html(html_template, height=1200, scroller=True)

if __name__ == "__main__":
    main()
