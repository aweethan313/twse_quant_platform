"""scripts/v4_13_build_market_sector_classification.py"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.market_sector import build_classification, get_classification

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-13 股市分類系統 {td} ===\n")
    n = build_classification(td)
    print(f"✓ 分類 {n} 檔\n")
    all_cls = get_classification(limit=2000)
    cats = {}
    for c in all_cls:
        cats[c["primary_category"]] = cats.get(c["primary_category"], 0) + 1
    for cat, cnt in sorted(cats.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:20} {cnt} 檔")

    path = Path(f"data/reports/v4_13_market_sector_classification_{td}.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["code","name","primary_category","secondary_category","theme_tags_json","risk_type","is_core_etf","theme_heat_score","is_defensive","classification_confidence"]
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader(); w.writerows(all_cls)
    print(f"\n驗收:")
    for code in ["0050","2330","2324","6271","2882"]:
        c = get_classification(code=code)
        if c: print(f"  {code} {c[0]['name']:8} → {c[0]['primary_category']} / {c[0]['secondary_category']}")
    print(f"\n✓ {path}")
