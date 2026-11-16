"""更新 ETF 網頁快照。

請由排程（例如 GitHub Actions、工作排程器）執行：
    python refresh_app_snapshot.py

網頁 app.py 正常啟動時只讀取 app_snapshot.json，不會等待 Google Sheets。
"""

import json
import os
from pathlib import Path

os.environ["APP_REFRESH_MODE"] = "1"

import app


def enrich_institutional_amounts(payload, price_history, twse_live_market):
    """將法人張數配合最近收盤價轉成金額，供網頁與產業統計使用。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return payload

    for item in payload["data"]:
        code = str(item.get("code", "")).strip()
        price = float(item.get("price", 0) or 0)
        history = price_history.get(code, {})
        if price <= 0 and history:
            price = float(history[sorted(history)[-1]] or 0)
        if price <= 0:
            price = float(
                (twse_live_market.get(code) or {}).get("price", 0) or 0
            )

        item["price"] = price
        for key in ("foreign", "trust", "dealer"):
            lots = float(item.get(f"{key}_lots", 0) or 0)
            item[f"{key}_amount"] = lots * price

        item["total_amount"] = sum(
            float(item.get(f"{key}_amount", 0) or 0)
            for key in ("foreign", "trust", "dealer")
        )

    return payload


def main():
    json_data, _, twse_live_market, ticker_map, etf_name_map, price_history = app.fetch_backend_data_to_json()
    discount, dividend, institutional, shares_flow = app.load_crawler_data()
    institutional_codes = [
        str(item.get("code", "")).strip()
        for item in institutional.get("data", [])
        if str(item.get("code", "")).strip()
    ]
    institutional_quotes = app.fetch_twse_live_data(institutional_codes)
    all_quotes = {**twse_live_market, **institutional_quotes}
    institutional = enrich_institutional_amounts(
        institutional,
        price_history,
        all_quotes,
    )

    gemini_insight = ""
    gemini_by_etf = {}
    try:
        import pandas as pd
        analysis_df = pd.DataFrame(json.loads(json_data))
        # Gemini 只分析代號以 A 結尾的主動型 ETF，其他 ETF 完全不送出 API 請求。
        gemini_df = analysis_df[
            analysis_df["etf"].astype(str).str.upper().str.endswith("A")
        ].copy() if "etf" in analysis_df.columns else analysis_df.iloc[0:0]
        gemini_insight = app.fetch_gemini_insight(
            app.build_gemini_summary(gemini_df),
            app.get_gemini_api_key(),
        )
        api_key = app.get_gemini_api_key()
        if api_key and not gemini_df.empty:
            for code in sorted(gemini_df["etf"].dropna().astype(str).unique()):
                if not code.upper().endswith("A"):
                    continue
                gemini_by_etf[code] = app.fetch_gemini_insight(
                    app.build_gemini_etf_summary(gemini_df, code), api_key
                )
    except Exception as exc:
        gemini_insight = f"Gemini 快照分析失敗：{type(exc).__name__}: {exc}"

    snapshot = {
        "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "json_data": json_data,
        "twse_live_market": twse_live_market,
        "ticker_map": ticker_map,
        "etf_name_map": etf_name_map,
        "price_history": price_history,
        "gemini_insight": gemini_insight,
        "gemini_by_etf": gemini_by_etf,
        "crawler": {
            "discount": discount,
            "dividend": dividend,
            "institutional": institutional,
            "shares_flow": shares_flow,
        },
    }
    target = Path(__file__).resolve().with_name("app_snapshot.json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    print(f"快照更新完成：{target}")


if __name__ == "__main__":
    main()
