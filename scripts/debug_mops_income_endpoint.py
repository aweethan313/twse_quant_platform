"""
scripts/debug_mops_income_endpoint.py

測試 MOPS 綜合損益表 ajax_t163sb04 到底哪一種請求方式有表格。
會把每個回應存到 data/debug/mops_income/。
"""

import os
import sys
from io import StringIO
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def try_read_tables(html: str):
    try:
        return pd.read_html(StringIO(html))
    except Exception:
        return []


def main():
    year = 115      # 2026
    season = "01"  # Q1
    market = "sii"

    debug_dir = Path("data/debug/mops_income")
    debug_dir.mkdir(parents=True, exist_ok=True)

    domains = [
        "https://mops.twse.com.tw",
        "https://mopsov.twse.com.tw",
        "http://mops.twse.com.tw",
    ]

    param_sets = [
        {
            "step": "1",
            "firstin": "1",
            "TYPEK": market,
            "year": str(year),
            "season": season,
        },
        {
            "step": "1",
            "firstin": "1",
            "off": "1",
            "TYPEK": market,
            "year": str(year),
            "season": season,
        },
        {
            "encodeURIComponent": "1",
            "step": "1",
            "firstin": "1",
            "off": "1",
            "TYPEK": market,
            "year": str(year),
            "season": season,
        },
        {
            "step": "1",
            "firstin": "1",
            "isnew": "false",
            "TYPEK": market,
            "year": str(year),
            "season": season,
        },
    ]

    methods = ["GET", "POST"]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://mops.twse.com.tw/mops/web/t163sb04",
        "Origin": "https://mops.twse.com.tw",
    }

    best = None

    for domain in domains:
        url = f"{domain}/mops/web/ajax_t163sb04"

        for method in methods:
            for i, params in enumerate(param_sets, start=1):
                name = f"{domain.replace('https://', '').replace('http://', '').replace('.', '_')}_{method}_{i}.html"
                out_path = debug_dir / name

                try:
                    with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as client:
                        if method == "GET":
                            r = client.get(url, params=params)
                        else:
                            r = client.post(url, data=params)

                    html = r.content.decode("utf-8", errors="ignore")
                    out_path.write_text(html, encoding="utf-8", errors="ignore")

                    tables = try_read_tables(html)

                    print("=" * 80)
                    print(f"url       : {url}")
                    print(f"method    : {method}")
                    print(f"params    : {params}")
                    print(f"status    : {r.status_code}")
                    print(f"length    : {len(html)}")
                    print(f"tables    : {len(tables)}")
                    print(f"saved     : {out_path}")

                    if tables:
                        print("first table shape:", tables[0].shape)
                        print("first table columns:")
                        print(list(tables[0].columns)[:20])

                        if best is None:
                            best = (url, method, params, len(tables), out_path)

                except Exception as e:
                    print("=" * 80)
                    print(f"FAIL {url} {method} {params}")
                    print(e)

    print("=" * 80)
    print("BEST:")
    print(best)


if __name__ == "__main__":
    main()
