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
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        json_path = os.path.join(os.getcwd(), 'credentials.json')
        with open(json_path, 'r', encoding='utf-8') as f:
            creds_data = json.load(f)
    else:
        creds_data = json.loads(creds_json)
    return gspread.service_account_from_dict(creds_data)

# 初始化 gspread
gc = get_sheets_client()
sh = gc.open(SHEET_NAME)

# 網頁範本設定
templates = Jinja2Templates(directory="templates")

def sheet_to_dict(ws):
    """將工作表轉換為帶有標頭的字典列表，方便網頁渲染"""
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
    """網頁主頁：顯示異動矩陣、Hero_List、標的籌碼分佈"""
    # 1. 抓取『ETF異動矩陣_純淨版』
    try:
        matrix_ws = sh.worksheet("ETF異動矩陣_純淨版")
        matrix_raw = matrix_ws.get_all_values()
    except:
        matrix_raw = []

    # 2. 抓取『Hero_List_美化版』
    try:
        hero_ws = sh.worksheet("Hero_List_美化版")
        hero_list = sheet_to_dict(hero_ws)
    except:
        hero_list = []

    # 3. 抓取『標的籌碼分佈_美化版』
    try:
        chip_ws = sh.worksheet("標的籌碼分佈_美化版")
        chip_distribution = sheet_to_dict(chip_ws)
    except:
        chip_distribution = []

    return templates.TemplateResponse("index.html", {
        "request": request,
        "matrix_raw": matrix_raw,
        "hero_list": hero_list,
        "chip_distribution": chip_distribution
    })

@app.get("/etf/{etf_code}", response_class=HTMLResponse)
def read_etf(request: Request, etf_code: str):
    """特定 ETF 頁面：抓取以 {ETF代碼} 為名稱的頁面（例如 00981A）"""
    # 【關鍵修正】防止 00 開頭的 ETF 代碼被去零（例如 981A 自動補回 00981A）
    clean_code = etf_code.upper().strip()
    if len(clean_code) == 4 and clean_code[0].isdigit():     # 比如 981A -> 00981A
        clean_code = f"00{clean_code}"
    elif len(clean_code) == 5 and clean_code[0].isdigit():   # 比如 0981A -> 00981A
        clean_code = f"0{clean_code}"

    try:
        # 精準對齊你的純代碼工作表名稱（如 00981A）
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
    """歷史紀錄頁面：抓取『ETF History』"""
    try:
        history_ws = sh.worksheet("ETF History")
        raw_data = history_ws.get_all_values()
        headers = [h.replace("'", "") for h in raw_data[0]] if raw_data else []
        rows = []
        for r in reversed(raw_data[1:]):
            if len(rows) >= 500: break  # 限制網頁載入量
            rows.append(dict(zip(headers, [cell.replace("'", "") for cell in r])))
    except:
        headers, rows = [], []

    return templates.TemplateResponse("history.html", {
        "request": request,
        "headers": headers,
        "history_data": rows
    })
