import argparse, subprocess, sys
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal

def _pending_dates():
    db = SessionLocal()
    try:
        db.execute(text("""CREATE TABLE IF NOT EXISTS pending_backfill(
            trade_date TEXT PRIMARY KEY, reason TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)"""))
        rows = db.execute(text(
            "SELECT trade_date FROM pending_backfill WHERE resolved_at IS NULL ORDER BY trade_date"
        )).fetchall()
        return [r[0] for r in rows]
    finally:
        db.close()

def _mark_resolved(d):
    db = SessionLocal()
    try:
        db.execute(text("UPDATE pending_backfill SET resolved_at=datetime('now','localtime') WHERE trade_date=:d"), {"d": str(d)})
        db.commit()
    finally:
        db.close()

def _ohlcv_count(d):
    db = SessionLocal()
    try:
        return db.execute(text("SELECT COUNT(*) FROM ohlcv_daily WHERE trade_date=:d"), {"d": str(d)}).scalar() or 0
    finally:
        db.close()

def _rerun_steps(d: date):
    from backend.services.technical_features import build_technical_features
    from backend.services.latest_update import recompute_scores_for_date
    from backend.v5.decision_engine import generate_strategy_decisions
    from backend.v5.paper_engine import simulate_paper_fills, update_v5_equity
    build_technical_features(d)
    recompute_scores_for_date(d)
    subprocess.run([sys.executable, "twse_ml_eval/ml_scorer.py",
                    "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1",
                    "--date", str(d)],
                   capture_output=True, text=True, cwd=str(PROJECT))
    generate_strategy_decisions(d)
    simulate_paper_fills(d)
    from backend.v5.dividends import credit_dividends
    credit_dividends(d)
    update_v5_equity(d)
    # FULL_RERUN:檢討書/ML檢討/主題/品質也一併補齊(7/17事件的教訓)
    try:
        from backend.services.daily_review import generate_daily_review
        from backend.services.ml_review import generate_ml_review
        from backend.services.latest_update import update_theme_trends
        from backend.v4.data_quality import run_data_quality_checks
        db2 = SessionLocal()
        prev = db2.execute(text("""SELECT MAX(trade_date) FROM ohlcv_daily
            WHERE trade_date < :d AND code GLOB '[0-9][0-9][0-9][0-9]'"""), {"d": str(d)}).scalar()
        past = db2.execute(text("""SELECT trade_date FROM (SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date < :d AND code GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY trade_date DESC LIMIT 5) ORDER BY trade_date ASC LIMIT 1"""), {"d": str(d)}).scalar()
        db2.close()
        if prev:
            generate_daily_review(date.fromisoformat(str(prev)), d)
        if past:
            generate_ml_review(date.fromisoformat(str(past)), top_n=10, hold_days=5)
        update_theme_trends(d)
        run_data_quality_checks(d)
    except Exception as e:
        print(f"[{d}] 檢討/主題/品質補齊部分失敗(不影響核心資料): {e}")

def _rebuild_benchmarks():
    from backend.v5.benchmark import rebuild_0050_benchmark
    db = SessionLocal()
    try:
        fs = db.execute(text("SELECT MIN(start_date) FROM strategy_accounts WHERE id BETWEEN 11 AND 17")).scalar()
    finally:
        db.close()
    fs = str(fs)[:10] if fs else "2026-05-25"
    rebuild_0050_benchmark(start_date=fs)
    rebuild_0050_benchmark(start_date=fs, benchmark_code="00981A")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates")
    args = ap.parse_args()
    targets = sorted(args.dates.split(",")) if args.dates else _pending_dates()
    if not targets:
        print("沒有待補日期，結束")
        return
    from backend.collectors.daily_eod import run_eod
    fixed = []
    for ds in targets:
        d = date.fromisoformat(ds.strip())
        if d.weekday() >= 5:
            print(f"[{ds}] 週末，標記 resolved"); _mark_resolved(ds); continue
        print(f"[{ds}] run_eod（MI_INDEX 歷史抓取）...")
        run_eod(d)
        n = _ohlcv_count(ds)
        if n > 800:
            print(f"[{ds}] OHLCV={n}，重跑 pipeline...")
            _rerun_steps(d); _mark_resolved(ds); fixed.append(ds)
            print(f"[{ds}] ✓ 完成")
        else:
            print(f"[{ds}] OHLCV 仍只有 {n} 筆，保留 pending")
    if fixed:
        print("重建 benchmark..."); _rebuild_benchmarks()
    print(f"完成：本次修復 {len(fixed)} 天 {fixed}")

if __name__ == "__main__":
    main()
