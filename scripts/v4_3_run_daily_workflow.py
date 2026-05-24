"""scripts/v4_3_run_daily_workflow.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v4.daily_workflow import run_daily_workflow

if __name__ == "__main__":
    r = run_daily_workflow(date.today())
    print(f"完成 | PASS={r['pass']} WARN={r['warn']} FAIL={r['fail']} 耗時={r['duration']}秒")
