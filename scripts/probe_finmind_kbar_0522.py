import os
import requests
import pandas as pd

DATE = "2026-05-22"
STOCK_ID = "2330"

token = os.getenv("FINMIND_TOKEN", "").strip()

url = "https://api.finmindtrade.com/api/v4/data"
headers = {}
if token:
    headers["Authorization"] = f"Bearer {token}"

params = {
    "dataset": "TaiwanStockKBar",
    "data_id": STOCK_ID,
    "start_date": DATE,
}

print("=" * 80)
print(f"測試 FinMind TaiwanStockKBar")
print(f"stock_id = {STOCK_ID}")
print(f"date     = {DATE}")
print(f"token    = {'YES' if token else 'NO'}")
print("=" * 80)

resp = requests.get(url, headers=headers, params=params, timeout=30)

print("HTTP status:", resp.status_code)
print("URL:", resp.url)

try:
    data = resp.json()
except Exception:
    print("不是 JSON 回應：")
    print(resp.text[:1000])
    raise SystemExit(1)

print("API status:", data.get("status"))
print("API msg   :", data.get("msg"))

rows = data.get("data", [])
print("row count :", len(rows))

if not rows:
    print("\n❌ 沒有抓到資料。可能原因：")
    print("1. TaiwanStockKBar 需要 sponsor 權限")
    print("2. token 沒設或權限不足")
    print("3. 2026-05-22 資料尚未更新或該 API 沒資料")
    raise SystemExit(0)

df = pd.DataFrame(rows)

print("\n✅ 抓到資料")
print(df.head())
print(df.tail())

out_dir = "data/raw/minute/2026-05-22"
os.makedirs(out_dir, exist_ok=True)

out_path = f"{out_dir}/{STOCK_ID}_2026-05-22_finmind_kbar.csv"
df.to_csv(out_path, index=False, encoding="utf-8-sig")

print(f"\n已輸出：{out_path}")
