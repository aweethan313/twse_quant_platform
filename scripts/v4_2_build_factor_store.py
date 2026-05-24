"""scripts/v4_2_build_factor_store.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from pathlib import Path
from backend.v4.factor_store import build_factor_store, get_factors, check_no_lookahead

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-2 Factor Store {td} ===\n")
    n = build_factor_store(td)
    print(f"✓ 寫入 {n} 個因子\n")
    sample = get_factors(factor_date=str(td), limit=5)
    print("因子樣本:")
    for f in sample:
        print(f"  {f['code']:6} {f['factor_name']:20} = {f['factor_value']:8.2f}  available_at={f['available_at']}")
    violations = check_no_lookahead(str(td) + " 15:00:00")
    print(f"\n偷看未來檢查: {'✅ 無違規' if not violations else '❌ ' + str(violations[:3])}")
    path = Path(f"data/reports/v4_2_factor_store_build_report_{td}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Factor Store Report {td}\n\n共寫入 {n} 個因子\n", encoding="utf-8")
    print(f"✓ {path}")
