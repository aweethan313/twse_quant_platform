"""
scripts/daily_pipeline.py — V9.1-P1 每日 pipeline（單一指令）

一個指令完成每日盤後更新，並具備假日防護：
  0. 交易日判斷（週末直接跳過；平日抓到假日舊資料會在 OHLCV 步驟被擋下）
  1. 收盤 OHLCV + 法人（run_eod，內含假日 stale 防護）
  2. 技術指標（當日）
  3. daily_scores 評分
  4. ML lgbm_v9_clean 最新分數
  5. 0050 benchmark 重建
  6. V5 決策 + 模擬成交 + equity 快照
  7. 每日檢討書（昨日選股 → 今日結果）
  8. 資料品質檢查 + 驗收報告

用法：
  python3 -m scripts.daily_pipeline                # 跑今天
  python3 -m scripts.daily_pipeline 2026-06-02     # 跑指定日期
  python3 -m scripts.daily_pipeline --force        # 略過交易日判斷（debug）

設計原則：
  - 非交易日「完全不動手」：不抓、不寫、不評分、不重建 benchmark。
  - 不呼叫 run_daily_workflow（避免 step 10h 寫入舊版 v8_rf_v1，污染 ML 分數）。
"""
from __future__ import annotations
import sys
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

PROJECT = Path(__file__).resolve().parent.parent
ML_MODEL = "lgbm_v9_clean"


def _last_trade_date_before(target: date, db) -> str | None:
    return db.execute(text("""
        SELECT MAX(trade_date) FROM ohlcv_daily
        WHERE trade_date < :d AND code GLOB '[0-9][0-9][0-9][0-9]'
    """), {"d": str(target)}).scalar()


def run_pipeline(target_date: date, force: bool = False) -> dict:
    steps = []

    def step(name, fn):
        t0 = datetime.now()
        try:
            r = fn()
            ok = (r is None) or (isinstance(r, dict) and r.get("ok", True)) or (r is True)
            msg = r.get("message", "") if isinstance(r, dict) else ""
            steps.append({"name": name, "ok": bool(ok), "message": msg,
                          "sec": round((datetime.now() - t0).total_seconds(), 1)})
            mark = "✅" if ok else "⚠️"
            logger.info(f"{mark} {name}: {msg}")
            return r
        except Exception as e:
            steps.append({"name": name, "ok": False, "message": str(e)[:200],
                          "sec": round((datetime.now() - t0).total_seconds(), 1)})
            logger.error(f"❌ {name}: {e}")
            return None

    logger.info("=" * 60)
    logger.info(f"  V9.1-P1 每日 pipeline  {target_date}")
    logger.info("=" * 60)

    # ── 步驟 0：交易日判斷（週末）──
    from backend.utils.trading_day import should_run_for
    run, reason = should_run_for(target_date)
    if not run and not force:
        logger.warning(f"[SKIP] {reason}，今日不執行任何更新")
        return {"ok": True, "skipped": True, "reason": reason, "steps": []}

    # ── 步驟 1：收盤 OHLCV + 法人（內含假日 stale 防護）──
    from backend.collectors.daily_eod import run_eod
    db_check = SessionLocal()
    try:
        before_rows = db_check.execute(text(
            "SELECT COUNT(*) FROM ohlcv_daily WHERE trade_date=:d"
        ), {"d": str(target_date)}).scalar()
    finally:
        db_check.close()

    step("1_ohlcv_eod", lambda: run_eod(target_date))

    # 驗證：OHLCV 步驟後，今天是否真的有寫入資料（假日會被擋，維持 0 或不變）
    db_check = SessionLocal()
    try:
        after_rows = db_check.execute(text(
            "SELECT COUNT(*) FROM ohlcv_daily WHERE trade_date=:d"
        ), {"d": str(target_date)}).scalar()
    finally:
        db_check.close()

    has_today_data = after_rows and after_rows > 100
    if not has_today_data and not force:
        logger.warning(
            f"[SKIP] {target_date} 沒有當日 OHLCV 資料（rows={after_rows}），"
            f"判定為非交易日 / 假日，停止後續步驟"
        )
        return {"ok": True, "skipped": True,
                "reason": f"無當日資料（rows={after_rows}），非交易日",
                "steps": steps}

    # ── 步驟 2：技術指標（當日）──
    def _tech():
        from backend.services.technical_features import build_technical_features
        build_technical_features(target_date)
        return {"ok": True, "message": "技術指標完成"}
    step("2_technical_features", _tech)

    # ── 步驟 3：daily_scores 評分 ──
    def _scores():
        from backend.services.latest_update import recompute_scores_for_date
        recompute_scores_for_date(target_date)
        return {"ok": True, "message": "daily_scores 完成"}
    step("3_daily_scores", _scores)

    # ── 步驟 4：ML lgbm_v9_clean 最新分數 ──
    def _ml():
        r = subprocess.run(
            [sys.executable, "twse_ml_eval/ml_scorer.py",
             "--db", "data/db/quant.db", "--mode", "latest", "--score-days", "1"],
            capture_output=True, text=True, cwd=str(PROJECT)
        )
        ok = r.returncode == 0
        return {"ok": ok, "message": f"ML {ML_MODEL} 更新" if ok else r.stderr[-200:]}
    step("4_ml_score", _ml)

    # ── 步驟 5：0050 benchmark 重建 ──
    def _bench():
        from backend.v5.benchmark import rebuild_0050_benchmark
        # 從各帳戶起始日（forward test 從 2026-06-01）重建
        # FORWARD_START：讀實測起始日（與排行榜一致），不可寫死
        _db2 = SessionLocal()
        try:
            _fs = _db2.execute(text("SELECT MIN(start_date) FROM strategy_accounts WHERE id BETWEEN 11 AND 17")).scalar()
        finally:
            _db2.close()
        forward_start = str(_fs)[:10] if _fs else "2026-05-25"
        n = rebuild_0050_benchmark(start_date=forward_start)  # FORWARD_START
        # 00981A benchmark（all in 買進持有對照）
        rebuild_0050_benchmark(start_date=forward_start, benchmark_code="00981A")
        return {"ok": True, "message": f"benchmark {n} 筆"}
    step("5_benchmark", _bench)

    # ── 步驟 6：V5 決策 + 模擬成交 + equity 快照 ──
    def _v5():
        from backend.v5.decision_engine import generate_strategy_decisions
        from backend.v5.paper_engine import simulate_paper_fills, update_v5_equity
        r = generate_strategy_decisions(target_date)
        simulate_paper_fills(target_date)
        update_v5_equity(target_date)
        return {"ok": True, "message": f"V5 決策 {r.get('decisions',0)} 筆"}
    step("6_v5_decisions", _v5)

    # ── 步驟 7：每日檢討書 ──
    def _review():
        from backend.services.daily_review import generate_daily_review
        db = SessionLocal()
        try:
            prev = _last_trade_date_before(target_date, db)
        finally:
            db.close()
        if prev:
            p = generate_daily_review(date.fromisoformat(prev), target_date)
            return {"ok": bool(p), "message": f"檢討書 {prev}→{target_date}"}
        return {"ok": False, "message": "找不到前一交易日"}
    step("7_daily_review", _review)

    # ── 步驟 7b：ML 選股檢討（檢討 5 交易日前的選股）──
    def _ml_review():
        from backend.services.ml_review import generate_ml_review
        db = SessionLocal()
        try:
            past = db.execute(text("""
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date FROM ohlcv_daily
                    WHERE trade_date < :d AND code GLOB '[0-9][0-9][0-9][0-9]'
                    ORDER BY trade_date DESC LIMIT 5
                ) ORDER BY trade_date ASC LIMIT 1
            """), {"d": str(target_date)}).scalar()
        finally:
            db.close()
        if not past:
            return {"ok": False, "message": "無足夠歷史可檢討"}
        r = generate_ml_review(date.fromisoformat(past), top_n=10, hold_days=5)
        if r:
            return {"ok": True, "message": f"ML檢討 {past}: 命中率{r['win_rate']:.0f}% 實際{r['avg_actual_return']:+.1f}%"}
        return {"ok": False, "message": "ML檢討資料不足"}
    step("7b_ml_review", _ml_review)

    # ── 步驟 7c：主題熱度 ──
    def _themes():
        from backend.services.latest_update import update_theme_trends
        r = update_theme_trends(target_date)
        return {"ok": True, "message": f"主題 {r.get('themes_updated','?')} 個"}
    step("7c_theme_trends", _themes)

    # ── 步驟 8：資料品質檢查 ──
    def _quality():
        from backend.v4.data_quality import run_data_quality_checks
        r = run_data_quality_checks(target_date)
        return {"ok": True, "message": f"健康分={r.get('health_score','?')}"}
    step("8_data_quality", _quality)

    # ── 產生驗收報告 ──
    ok_all = all(s["ok"] for s in steps)
    n_pass = sum(1 for s in steps if s["ok"])
    n_fail = len(steps) - n_pass
    report_path = PROJECT / "data" / "reports" / f"p1_daily_pipeline_{target_date}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# P1 每日 pipeline 驗收報告 {target_date}",
        f"\n產生時間：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"\n總結：PASS={n_pass} FAIL={n_fail}  當日 OHLCV={after_rows} 筆\n",
        "| 步驟 | 結果 | 耗時(秒) | 訊息 |",
        "|------|------|---------|------|",
    ]
    for s in steps:
        lines.append(f"| {s['name']} | {'✅' if s['ok'] else '❌'} | {s['sec']} | {s['message']} |")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"📄 驗收報告：{report_path}")

    logger.info("=" * 60)
    logger.info(f"  完成 PASS={n_pass} FAIL={n_fail}")
    logger.info("=" * 60)
    return {"ok": ok_all, "skipped": False, "steps": steps, "report": str(report_path)}


def main():
    args = sys.argv[1:]
    force = "--force" in args
    args = [a for a in args if not a.startswith("--")]
    target = date.fromisoformat(args[0]) if args else date.today()
    result = run_pipeline(target, force=force)
    if result.get("skipped"):
        print(f"⏭️  跳過：{result.get('reason')}")
    else:
        n_pass = sum(1 for s in result["steps"] if s["ok"])
        n_fail = len(result["steps"]) - n_pass
        print(f"✅ 完成 PASS={n_pass} FAIL={n_fail}")
        print(f"📄 報告：{result.get('report')}")


if __name__ == "__main__":
    main()
