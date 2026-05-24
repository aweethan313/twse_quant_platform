"""scripts/v4_1_run_data_quality_check.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v4.data_quality import run_data_quality_checks, get_quality_report
import csv
from pathlib import Path

def main():
    td = date.today()
    print(f"=== V4-1 資料品質檢查 {td} ===\n")
    r = run_data_quality_checks(td)
    print(f"\n整體健康分: {r.get('overall_health', 0):.0f}/100")
    print(f"PASS={r.get('pass',0)} WARN={r.get('warn',0)} FAIL={r.get('fail',0)}")

    # 輸出報告
    checks = get_quality_report(str(td))
    path_md = Path(f"data/reports/v4_1_data_quality_report_{td}.md")
    path_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# 資料品質報告 {td}", f"整體健康分: {r.get('overall_health',0):.0f}/100", ""]
    for c in checks:
        icon = {"PASS":"✅","WARN":"⚠️","FAIL":"❌","SKIPPED":"⏭"}.get(c["status"],"•")
        lines.append(f"- {icon} [{c['severity']}] {c['check_type']}: {c['message']}")
    path_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✓ 報告: {path_md}")

if __name__ == "__main__":
    main()
