"""
reset_forward_test.py — 重置策略帳戶與排行榜，從 2026-05-25 開始前向實測

流程：
  1. 自動備份資料庫（帶時間戳）
  2. 清掉殘留的 v8_rf_v1 ML 分數（只留 lgbm_v9_clean，避免 A7 誤選）
  3. 清空所有「前向實測產物」表
  4. 重置 7 個策略帳戶（A1-A7）為 200k 現金、start_date=2026-05-25
  5. 刪除 2026 以前的每日報告書檔案
  6. 重建 0050 benchmark（從 2026-05-25 起算）
  7. 重播 5/25→今天的每個交易日（用現有資料建立實測起點）

保留不動（ML 訓練用歷史資料）：
  ohlcv_daily / daily_scores / technical_daily_features / ml_score_results(lgbm)
  / chip_daily / valuation_daily

用法：
  python3 reset_forward_test.py            # 會先要你確認
  python3 reset_forward_test.py --yes      # 跳過確認
  python3 reset_forward_test.py --start 2026-05-25   # 自訂起始日
"""
from __future__ import annotations
import sys
import shutil
import sqlite3
from datetime import datetime, date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DB = PROJECT / "data" / "db" / "quant.db"
REPORTS = PROJECT / "data" / "reports"

CLEAR_TABLES = [
    "positions", "trade_logs", "paper_fills", "realistic_trade_fills",
    "equity_curve", "strategy_leaderboard", "strategy_kill_switch_status",
    "strategy_decision_logs", "strategy_health_scores", "strategy_attribution",
    "decision_explanations", "backtest_paper_gap_analysis", "benchmark_daily_equity",
]


def get_start_date() -> str:
    if "--start" in sys.argv:
        i = sys.argv.index("--start")
        return sys.argv[i + 1]
    return "2026-05-25"


def main():
    skip_confirm = "--yes" in sys.argv
    START_DATE = get_start_date()

    if not DB.exists():
        print(f"❌ 找不到資料庫：{DB}")
        sys.exit(1)

    print("=" * 60)
    print(f"  前向實測重置（從 {START_DATE} 開始）")
    print("=" * 60)
    print("\n會清空前向實測產物、重置 7 個帳戶為 200k、刪除 2026 以前報告書、")
    print("清掉 v8_rf_v1 殘留分數、重建 benchmark、重播交易日。")
    print("保留：ohlcv_daily / daily_scores / technical_daily_features / ml(lgbm)")

    if not skip_confirm:
        ans = input(f"\n確定從 {START_DATE} 重置？（會先備份）輸入 yes 繼續：").strip().lower()
        if ans != "yes":
            print("已取消。")
            sys.exit(0)

    # 1. 備份
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB.parent / f"quant_backup_before_reset_{ts}.db"
    print(f"\n[1/7] 備份 → {backup.name} ...")
    shutil.copy2(DB, backup)
    print(f"      ✅ 備份完成（{backup.stat().st_size/1e9:.2f} GB）")

    con = sqlite3.connect(str(DB))

    # 2. 清掉 v8_rf_v1 殘留（只留 lgbm_v9_clean）
    print("\n[2/7] 清掉 v8_rf_v1 殘留 ML 分數 ...")
    n_v8 = con.execute("SELECT COUNT(*) FROM ml_score_results WHERE model_version != 'lgbm_v9_clean'").fetchone()[0]
    con.execute("DELETE FROM ml_score_results WHERE model_version != 'lgbm_v9_clean'")
    print(f"      ✅ 刪除 {n_v8} 筆非 lgbm_v9_clean 分數")

    # 3. 清空前向實測表
    print("\n[3/7] 清空前向實測產物表 ...")
    for t in CLEAR_TABLES:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            con.execute(f"DELETE FROM {t}")
            print(f"      清空 {t}: {n} → 0")
        except Exception as e:
            print(f"      ⚠ {t}: {e}")

    # 4. 重置帳戶
    print("\n[4/7] 重置 7 個策略帳戶 ...")
    con.execute("""
        UPDATE strategy_accounts
        SET cash = initial_cash, realized_pnl = 0, unrealized_pnl = 0,
            latest_signal_date = NULL, latest_execution_date = NULL, start_date = ?
        WHERE id BETWEEN 11 AND 17
    """, (START_DATE,))
    con.commit()
    for r in con.execute("SELECT id, name, cash, start_date FROM strategy_accounts ORDER BY id"):
        print(f"      {r}")

    # 取出要重播的交易日（START_DATE ~ 最新）
    trade_days = [r[0] for r in con.execute("""
        SELECT DISTINCT trade_date FROM ohlcv_daily
        WHERE trade_date >= ? AND code GLOB '[0-9][0-9][0-9][0-9]'
        ORDER BY trade_date
    """, (START_DATE,)).fetchall()]
    con.close()

    # 5. 刪除 2026 以前報告書
    print("\n[5/7] 刪除 2026 以前每日報告書 ...")
    deleted = 0
    if REPORTS.exists():
        for pat in ["*.md", "*.csv"]:
            for f in REPORTS.glob(pat):
                if any(y in f.name for y in ["2023", "2024", "2025"]):
                    f.unlink(); deleted += 1
    print(f"      ✅ 刪除 {deleted} 個舊報告")

    # 6. 重建 benchmark
    print(f"\n[6/7] 重建 0050 benchmark（從 {START_DATE}）...")
    sys.path.insert(0, str(PROJECT))
    try:
        from backend.v5.benchmark import rebuild_0050_benchmark
        n = rebuild_0050_benchmark(start_date=START_DATE)
        # 00981A benchmark（all in 買進持有對照）
        rebuild_0050_benchmark(start_date=START_DATE, benchmark_code="00981A")
        print(f"      ✅ benchmark {n} 筆")
    except Exception as e:
        print(f"      ⚠ benchmark 失敗：{e}")

    # 7. 重播交易日
    print(f"\n[7/7] 重播 {len(trade_days)} 個交易日（{trade_days[0]} ~ {trade_days[-1]}）...")
    from backend.v5.decision_engine import generate_strategy_decisions
    from backend.v5.paper_engine import simulate_paper_fills, update_v5_equity
    for td in trade_days:
        d = date.fromisoformat(td)
        try:
            r = generate_strategy_decisions(d)
            simulate_paper_fills(d)
            update_v5_equity(d)
            print(f"      {td}: 決策 {r.get('decisions',0)} 筆 ✅")
        except Exception as e:
            print(f"      {td}: ⚠ {e}")

    print("\n" + "=" * 60)
    print(f"  ✅ 重置完成！7 個帳戶從 {START_DATE} 重新開始實測")
    print("=" * 60)
    print(f"\n備份檔：{backup}")
    print("之後每個交易日跑 `bash run_daily_update.sh` 持續累積績效。")


if __name__ == "__main__":
    main()
