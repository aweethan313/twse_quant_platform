"""scripts/v5_setup.py - V5 完整初始化腳本"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=== V5 初始化 ===\n")

    # 1. Migration
    print("Step 1: 資料庫 migration...")
    from scripts.v5_migrate import migrate
    migrate()

    # 2. 建立 6 個策略帳戶
    print("\nStep 2: 建立 V5 策略帳戶...")
    from backend.v5.strategy_configs import setup_v5_accounts
    r = setup_v5_accounts()
    print(f"  建立 {r['created']} 個新帳戶，共 {r['total']} 個")

    # 3. 建立 0050 Benchmark
    print("\nStep 3: 建立 0050 Benchmark...")
    from backend.v5.benchmark import rebuild_0050_benchmark
    n = rebuild_0050_benchmark(start_date="2025-01-01")
    print(f"  0050 benchmark {n} 筆")

    # 4. 跑今日決策
    print("\nStep 4: 產生今日決策...")
    from backend.v5.decision_engine import generate_strategy_decisions
    from datetime import date
    r = generate_strategy_decisions(date.today())
    print(f"  {r}")

    # 5. 驗收
    print("\n=== 驗收 ===")
    from backend.models.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    tables = ["strategy_account_configs","benchmark_daily_equity",
              "paper_fills","strategy_decision_logs"]
    for t in tables:
        cnt = db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        print(f"  {t}: {cnt:,} 筆")
    accts = db.execute(text("SELECT id, name FROM strategy_accounts WHERE id >= 11")).fetchall()
    print(f"  V5 帳戶: {[r[1] for r in accts]}")
    db.close()
    print("\n✅ V5 初始化完成")

if __name__ == "__main__":
    main()
