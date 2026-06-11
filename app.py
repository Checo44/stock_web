from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import gspread
import os
import json
import re
from collections import defaultdict

app = FastAPI()

# 設定 1: Google Sheets 認證
SHEET_NAME = "ETF daily"

def get_sheets_client():
    # 優先從 Streamlit Cloud 的 Secrets / 環境變數中讀取
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    
    # 如果是在 Streamlit Cloud 託管環境，Streamlit 會把 Secrets 注入到 st.secrets 中
    if not creds_json:
        try:
            import streamlit as st
            if "GOOGLE_CREDENTIALS" in st.secrets:
                creds_json = st.secrets["GOOGLE_CREDENTIALS"]
        except:
            pass

    # 如果有抓到雲端憑證字串
    if creds_json:
        try:
            # 有時環境變數會帶有前後單撇號，先清洗乾淨
            clean_json = creds_json.strip().strip("'").strip('"')
            creds_data = json.loads(clean_json)
            return gspread.service_account_from_dict(creds_data)
        except Exception as e:
            print(f"❌ 解析雲端 GOOGLE_CREDENTIALS 失敗: {e}")

    # 本地開發備用方案：尋找本地 credentials.json
    json_path = os.path.join(os.getcwd(), 'credentials.json')
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            creds_data = json.load(f)
        return gspread.service_account_from_dict(creds_data)
    
    # 兩者皆空時，拋出可讀性錯誤，防止 Uvicorn 啟動閃退
    raise RuntimeError("❌ 找不到任何有效的 Google Sheets 憑證！請確認 Streamlit Secrets 是否設定成功。")

# 安全初始化連線
try:
    gc = get_sheets_client()
    sh = gc.open(SHEET_NAME)
except Exception as e:
    print(f"⚠️ 試算表初始化失敗: {e}")
    sh = None

# 網頁範本設定
templates = Jinja2Templates(directory="templates")

def sheet_to_dict(ws):
    """將工作表轉換為帶有標頭的字典列表"""
    try:
        data = ws.get_all_values()
        if not data: return []
        headers = [h.strip().replace("'", "") for h in data[0]]
        rows = []
        for r in data[1:]:
            if len(r) < len(headers):
                r += [""] * (len(headers) - len(r))
            rows.append(dict(zip(headers, [cell.strip().replace("'", "") for cell in r])))
        return rows
    except:
        return []

@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    if not sh:
        return HTMLResponse(content="<h1>❌ 試算表尚未成功連線，請檢查日誌與憑證。</h1>", status_code=500)
        
    try: matrix_raw = sh.worksheet("ETF異動矩陣_純淨版").get_all_values()
    except: matrix_raw = []

    try: hero_list = sheet_to_dict(sh.worksheet("Hero_List_美化版"))
    except: hero_list = []

    try: chip_distribution = sheet_to_dict(sh.worksheet("標的籌碼分佈_美化版"))
    except: chip_distribution = []

    return templates.TemplateResponse("index.html", {
        "request": request,
        "matrix_raw": matrix_raw,
        "hero_list": hero_list,
        "chip_distribution": chip_distribution
    })

@app.get("/etf/{etf_code}", response_class=HTMLResponse)
def read_etf(request: Request, etf_code: str):
    if not sh: raise HTTPException(status_code=500, detail="試算表未連線")
    
    clean_code = etf_code.upper().strip()
    if len(clean_code) == 4 and clean_code[0].isdigit(): clean_code = f"00{clean_code}"
    elif len(clean_code) == 5 and clean_code[0].isdigit(): clean_code = f"0{clean_code}"

    try:
        etf_ws = sh.worksheet(clean_code)
        etf_data = sheet_to_dict(etf_ws)
        return templates.TemplateResponse("etf_detail.html", {
            "request": request,
            "etf_code": clean_code,
            "etf_data": etf_data
        })
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(status_code=404, detail=f"找不到 ETF 工作表: {clean_code}")

@app.get("/history", response_class=HTMLResponse)
def read_history(request: Request):
    if not sh: raise HTTPException(status_code=500, detail="試算表未連線")
    
    try:
        history_ws = sh.worksheet("ETF History")
        raw_data = history_ws.get_all_values()
        headers = [h.replace("'", "") for h in raw_data[0]] if raw_data else []
        rows = []
        for r in reversed(raw_data[1:]):
            if len(rows) >= 500: break
            rows.append(dict(zip(headers, [cell.replace("'", "") for cell in r])))
    except:
        headers, rows = [], []

    return templates.TemplateResponse("history.html", {
        "request": request,
        "headers": headers,
        "history_data": rows
    })

# 配合 Streamlit Cloud ASGI 轉發埠口啟動
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8501))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
