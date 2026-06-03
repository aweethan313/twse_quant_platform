"""
backend/services/ml_review.py — ML 選股檢討報告

追蹤 ML 模型某個 signal 日的 Top N 選股，對照之後的實際報酬，
評估模型「預測 vs 實際」的準確度。

輸出：data/reports/ml_review_{signal_date}.md
回傳：dict（含命中率、平均實際報酬、平均預測報酬、逐檔明細）

評估方式：
  - 進場價 = signal 日收盤
  - 出場價 = signal 日之後第 5 個交易日收盤（不足 5 日則用最新可得收盤）
  - 命中 = 實際報酬 > 0
  - 預測準確度 = 預測方向與實際方向是否一致
"""
from __future__ import annotations
from pathlib import Path
from datetime import date, datetime
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

ML_MODEL = "lgbm_v9_clean"


def generate_ml_review(signal_date: date = None, top_n: int = 10,
                       hold_days: int = 5) -> dict | None:
    if signal_date is None:
        signal_date = date.today()

    db = SessionLocal()
    try:
        # 1. 取 signal 日的 Top N ML 選股
        picks = db.execute(text("""
            SELECT m.code, m.stock_name, m.ml_score, m.ml_rank, m.predicted_return_5d,
                   o.close AS entry_close
            FROM ml_score_results m
            LEFT JOIN ohlcv_daily o ON o.code=m.code AND o.trade_date=:sd
            WHERE m.score_date=:sd
              AND m.model_version=:mv
              AND m.ml_rank <= :n
              AND o.close IS NOT NULL
            ORDER BY m.ml_rank ASC
        """), {"sd": str(signal_date), "mv": ML_MODEL, "n": top_n}).fetchall()

        if not picks:
            logger.warning(f"[ML_REVIEW] {signal_date} 無 {ML_MODEL} 選股資料")
            return None

        # 2. 找出場日（signal 之後第 hold_days 個交易日）
        future_days = db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date > :sd AND code GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY trade_date LIMIT :hd
        """), {"sd": str(signal_date), "hd": hold_days}).fetchall()
        if not future_days:
            logger.warning(f"[ML_REVIEW] {signal_date} 之後尚無交易日，無法評估")
            return None
        exit_date = future_days[-1][0]
        actual_hold = len(future_days)  # 實際持有天數（可能 < hold_days）

        # 3. 逐檔計算實際報酬
        details = []
        hits = 0
        dir_correct = 0
        sum_actual = 0.0
        sum_pred = 0.0
        for code, name, ml_score, rank, pred_5d, entry in picks:
            exit_row = db.execute(text("""
                SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d
            """), {"c": code, "d": exit_date}).fetchone()
            if not exit_row or not exit_row[0] or not entry:
                continue
            exit_close = float(exit_row[0])
            entry = float(entry)
            actual_ret = (exit_close / entry - 1) * 100 if entry > 0 else 0
            pred = float(pred_5d or 0)
            is_hit = actual_ret > 0
            is_dir = (pred > 0 and actual_ret > 0) or (pred <= 0 and actual_ret <= 0)
            if is_hit:
                hits += 1
            if is_dir:
                dir_correct += 1
            sum_actual += actual_ret
            sum_pred += pred
            details.append({
                "rank": rank, "code": code, "name": name or code,
                "ml_score": round(float(ml_score or 0), 1),
                "predicted_5d": round(pred, 2),
                "entry": round(entry, 2), "exit": round(exit_close, 2),
                "actual_ret": round(actual_ret, 2),
                "hit": is_hit, "dir_correct": is_dir,
            })

        n = len(details)
        if n == 0:
            logger.warning(f"[ML_REVIEW] {signal_date} 無法計算任何個股報酬")
            return None

        win_rate = hits / n * 100
        dir_acc = dir_correct / n * 100
        avg_actual = sum_actual / n
        avg_pred = sum_pred / n

        # 0050 同期報酬（對照基準）
        bench = db.execute(text("""
            SELECT a.close AS e, b.close AS x FROM
              (SELECT close FROM ohlcv_daily WHERE code='0050' AND trade_date=:sd) a,
              (SELECT close FROM ohlcv_daily WHERE code='0050' AND trade_date=:xd) b
        """), {"sd": str(signal_date), "xd": exit_date}).fetchone()
        bench_ret = ((float(bench[1]) / float(bench[0]) - 1) * 100) if bench and bench[0] else 0.0

        # 4. 寫報告
        report = _write_report(signal_date, exit_date, actual_hold, ML_MODEL,
                               details, win_rate, dir_acc, avg_actual, avg_pred, bench_ret)

        logger.success(
            f"[ML_REVIEW] {signal_date}→{exit_date} {n}檔 "
            f"命中率{win_rate:.0f}% 方向準確{dir_acc:.0f}% "
            f"平均實際{avg_actual:+.2f}% (預測{avg_pred:+.2f}%) vs 0050 {bench_ret:+.2f}%"
        )
        return {
            "signal_date": str(signal_date), "exit_date": str(exit_date),
            "hold_days": actual_hold, "n": n,
            "win_rate": round(win_rate, 1), "direction_accuracy": round(dir_acc, 1),
            "avg_actual_return": round(avg_actual, 2),
            "avg_predicted_return": round(avg_pred, 2),
            "benchmark_return": round(bench_ret, 2),
            "alpha": round(avg_actual - bench_ret, 2),
            "details": details, "report_path": report,
        }
    finally:
        db.close()


def _write_report(signal_date, exit_date, hold_days, model, details,
                  win_rate, dir_acc, avg_actual, avg_pred, bench_ret) -> str:
    path = Path("data/reports") / f"ml_review_{signal_date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    alpha = avg_actual - bench_ret
    lines = [
        f"# ML 選股檢討報告 {signal_date} → {exit_date}",
        f"\n模型：{model}　持有：{hold_days} 個交易日　產生時間：{datetime.now():%Y-%m-%d %H:%M}",
        "\n## 總結",
        f"- 命中率（上漲檔數）：**{win_rate:.0f}%**",
        f"- 方向準確率（預測漲跌方向對）：**{dir_acc:.0f}%**",
        f"- 平均實際報酬：**{avg_actual:+.2f}%**（平均預測 {avg_pred:+.2f}%）",
        f"- 0050 同期：{bench_ret:+.2f}%　→　**Alpha {alpha:+.2f}%**",
        "\n## 逐檔明細",
        "| 排名 | 代號 | 名稱 | ML分 | 預測5日 | 進場 | 出場 | 實際報酬 | 結果 |",
        "|------|------|------|------|---------|------|------|---------|------|",
    ]
    for d in sorted(details, key=lambda x: x["rank"]):
        mark = "🔴漲" if d["hit"] else "🟢跌"
        dir_mark = "✓" if d["dir_correct"] else "✗"
        lines.append(
            f"| #{d['rank']} | {d['code']} | {d['name']} | {d['ml_score']} | "
            f"{d['predicted_5d']:+.2f}% | {d['entry']} | {d['exit']} | "
            f"{d['actual_ret']:+.2f}% | {mark} 方向{dir_mark} |"
        )
    # 最佳 / 最差
    best = max(details, key=lambda x: x["actual_ret"])
    worst = min(details, key=lambda x: x["actual_ret"])
    lines += [
        "\n## 重點",
        f"- 最佳：{best['code']} {best['name']} {best['actual_ret']:+.2f}%",
        f"- 最差：{worst['code']} {worst['name']} {worst['actual_ret']:+.2f}%",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
