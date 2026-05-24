"""scripts/v4_11_export_research_report.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v4.research_report import export_research_report

if __name__ == "__main__":
    td = date.today()
    print(f"=== V4-11 Research Report {td} ===\n")
    path = export_research_report(td)
    print(f"✓ 完整研究報告: {path}")
    print("\n預覽（前30行）:")
    with open(path) as f:
        for i, line in enumerate(f):
            if i >= 30: break
            print(f"  {line}", end="")
