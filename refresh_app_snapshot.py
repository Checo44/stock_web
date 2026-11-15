"""
更新 ETF 網頁快照。

執行方式：
    python refresh_app_snapshot.py

功能：
1. 讀取 Google Sheets 的 ETF History 與相關資料
2. 執行 app.py 原本的資料整理邏輯
3. 取得 FinMind、Yahoo Finance 等資料
4. 只對代號以 A 結尾的 ETF 呼叫 Gemini
5. 產生 app_snapshot.json
"""

import json
import os
from datetime import datetime
from pathlib import Path


# 告訴 app.py：目前是更新快照模式，可以連線 Google Sheets。
os.environ["APP_REFRESH_MODE"] = "1"


# app.py 與本檔案位於同一層，因此可以直接 import app。
import app


def main():
    print("開始產生 ETF 網頁快照...")

    (
        json_data,
        _,
        twse_live_market,
        ticker_map,
        etf_name_map,
        price_history,
    ) = app.fetch_backend_data_to_json()

    if not json_data or json_data == "[]":
        raise RuntimeError(
            "ETF History 沒有可用資料，拒絕產生空白快照。"
        )

    (
        discount_data,
        dividend_data,
        institutional_data,
        shares_flow_data,
    ) = app.load_crawler_data()

    gemini_insight = ""
    gemini_by_etf = {}

    try:
        import pandas as pd

        analysis_df = pd.DataFrame(
            json.loads(json_data)
        )

        # Gemini 只分析代號以 A 結尾的 ETF。
        # 例如：00400A、00413A。
        # 0050、0056、00919 等一般 ETF 不會送到 Gemini。
        if "etf" in analysis_df.columns:
            gemini_df = analysis_df[
                analysis_df["etf"]
                .astype(str)
                .str.upper()
                .str.endswith("A")
            ].copy()
        else:
            gemini_df = analysis_df.iloc[0:0]

        api_key = app.get_gemini_api_key()

        if api_key and not gemini_df.empty:
            print("開始產生 Gemini 整體摘要...")

            gemini_summary = app.build_gemini_summary(
                gemini_df
            )

            gemini_insight = app.fetch_gemini_insight(
                gemini_summary,
                api_key,
            )

            etf_codes = sorted(
                gemini_df["etf"]
                .dropna()
                .astype(str)
                .unique()
            )

            for code in etf_codes:
                if not code.upper().endswith("A"):
                    continue

                print(
                    f"產生 Gemini 單檔摘要：{code}"
                )

                summary = app.build_gemini_etf_summary(
                    gemini_df,
                    code,
                )

                gemini_by_etf[code] = (
                    app.fetch_gemini_insight(
                        summary,
                        api_key,
                    )
                )

        else:
            if not api_key:
                print(
                    "未設定 GEMINI_API_KEY，略過 Gemini 摘要。"
                )
            else:
                print(
                    "沒有 A 結尾 ETF 資料，略過 Gemini 摘要。"
                )

    except Exception as exc:
        print(
            "Gemini 摘要產生失敗，保留其他資料："
            f"{type(exc).__name__}: {exc}"
        )
        gemini_insight = (
            f"Gemini 摘要暫時無法取得：{exc}"
        )
        gemini_by_etf = {}

    snapshot = {
        "updated_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "json_data": json_data,
        "twse_live_market": twse_live_market,
        "ticker_map": ticker_map,
        "etf_name_map": etf_name_map,
        "price_history": price_history,
        "gemini_insight": gemini_insight,
        "gemini_by_etf": gemini_by_etf,
        "crawler": {
            "discount": discount_data,
            "dividend": dividend_data,
            "institutional": institutional_data,
            "shares_flow": shares_flow_data,
        },
    }

    target = (
        Path(__file__)
        .resolve()
        .with_name("app_snapshot.json")
    )

    temporary = target.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            snapshot,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(target)

    print("ETF 網頁快照更新完成：")
    print(target)
    print(f"快照時間：{snapshot['updated_at']}")
    print(
        "ETF History 筆數：",
        len(json.loads(json_data)),
    )
    print(
        "Gemini 單檔摘要數：",
        len(gemini_by_etf),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            "快照更新失敗："
            f"{type(exc).__name__}: {exc}"
        )
        raise
