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

# 網頁範本設定 (將 HTML 放在 templates 資料夾中)
templates = Jinja2Templates(directory="templates")

def sheet_to_dict(ws):
    """將工作表轉換為帶有標頭的字典列表，方便網頁渲染"""
    try:
        data = ws.get_all_values()
        if not data: return []
        headers = [h.strip().replace("'", "") for h in data[0]]
        rows = []
        for r in data[1:]:
            # 補齊長度
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
    """特定 ETF 頁面：抓取以 {ETF代碼} 為名稱的頁面"""
    try:
        etf_ws = sh.worksheet(etf_code.upper())
        etf_data = sheet_to_dict(etf_ws)
        return templates.TemplateResponse("etf_detail.html", {
            "request": request,
            "etf_code": etf_code.upper(),
            "etf_data": etf_data
        })
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(status_code=404, detail=f"找不到 ETF 分頁: {etf_code}") from fastapi import FastAPI, Request, HTTPException
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

# 網頁範本設定 (將 HTML 放在 templates 資料夾中)
templates = Jinja2Templates(directory="templates")

def sheet_to_dict(ws):
    """將工作表轉換為帶有標頭的字典列表，方便網頁渲染"""
    try:
        data = ws.get_all_values()
        if not data: return []
        headers = [h.strip().replace("'", "") for h in data[0]]
        rows = []
        for r in data[1:]:
            # 補齊長度
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
    """特定 ETF 頁面：抓取以 {ETF代碼} 為名稱的頁面"""
    try:
        etf_ws = sh.worksheet(etf_code.upper())
        etf_data = sheet_to_dict(etf_ws)
        return templates.TemplateResponse("etf_detail.html", {
            "request": request,
            "etf_code": etf_code.upper(),
            "etf_data": etf_data
        })
    except gspread.exceptions.WorksheetNotFound:
        raise HTTPException(status_code=404, detail=f"找不到 ETF 分頁: {etf_code}") 