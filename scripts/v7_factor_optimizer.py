"""scripts/v7_factor_optimizer.py
多因子選股權重優化（Walk-Forward，防止過度擬合）
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal


FACTORS = ["momentum_score", "chip_score", "risk_score", "core_score", "final_score"]
WEIGHT_SETS = [
    {"momentum_score": 0.30, "chip_score": 0.35, "risk_score": 0.15, "core_score": 0.20},
    {"momentum_score": 0.20, "chip_score": 0.40, "risk_score": 0.15, "core_score": 0.25},
    {"momentum_score": 0.40, "chip_score": 0.25, "risk_score": 0.15, "core_score": 0.20},
    {"momentum_score": 0.25, "chip_score": 0.30, "risk_score": 0.20, "core_score": 0.25},
]


def backtest_weights(db, weights: dict, start: str, end: str) -> dict:
    """用特定權重回測一段期間，計算 top-N 選股的平均報酬"""
    w_parts = " + ".join(f"{v}*COALESCE({k},0)" for k, v in weights.items())

    rows = db.execute(text(f"""
        SELECT ds.code, ds.score_date,
               ({w_parts}) as composite,
               cfr.return_5d, cfr.alpha_5d_vs_0050
        FROM daily_scores ds
        JOIN candidate_forward_returns cfr
            ON cfr.code=ds.code AND cfr.signal_date=ds.score_date
        WHERE ds.score_date >= :s AND ds.score_date <= :e
          AND cfr.return_5d IS NOT NULL
    """), {"s": start, "e": end}).fetchall()

    if not rows:
        return {"avg_5d": 0, "win_rate": 0, "n": 0}

    # 每日取 top-5
    by_date = {}
    for code, d, comp, r5, alpha in rows:
        if d not in by_date: by_date[d] = []
        by_date[d].append((float(comp or 0), float(r5 or 0), float(alpha or 0)))

    all_rets = []
    for d, stocks in by_date.items():
        top5 = sorted(stocks, key=lambda x: -x[0])[:5]
        all_rets.extend(r for _, r, _ in top5)

    if not all_rets: return {"avg_5d": 0, "win_rate": 0, "n": 0}
    return {
        "avg_5d": round(sum(all_rets)/len(all_rets), 3),
        "win_rate": round(sum(1 for r in all_rets if r > 0)/len(all_rets)*100, 1),
        "n": len(all_rets),
    }


def walk_forward_optimization(db, n_splits: int = 3):
    """Walk-forward 測試各權重組合"""
    # 取可用日期範圍
    date_range = db.execute(text("""
        SELECT MIN(signal_date), MAX(signal_date) FROM candidate_forward_returns
    """)).fetchone()

    if not date_range[0]:
        print("⚠️ candidate_forward_returns 無資料，請先執行 v6_build_candidate_forward_returns")
        return []

    start = date.fromisoformat(str(date_range[0]))
    end   = date.fromisoformat(str(date_range[1]))
    total_days = (end - start).days
    split_days = total_days // n_splits

    print(f"Walk-Forward: {start} ~ {end}，{n_splits} 個 fold\n")

    results = {str(i): {"train": [], "valid": []} for i in range(len(WEIGHT_SETS))}

    for fold in range(n_splits):
        fold_start = start + timedelta(days=fold * split_days)
        fold_end   = fold_start + timedelta(days=split_days - 1)
        train_end  = fold_start + timedelta(days=split_days * 2 // 3)

        print(f"Fold {fold+1}: train={fold_start}~{train_end} valid={train_end}~{fold_end}")

        for i, weights in enumerate(WEIGHT_SETS):
            train_r = backtest_weights(db, weights, str(fold_start), str(train_end))
            valid_r = backtest_weights(db, weights, str(train_end), str(fold_end))
            results[str(i)]["train"].append(train_r["avg_5d"])
            results[str(i)]["valid"].append(valid_r["avg_5d"])
            print(f"  權重{i+1}: train={train_r['avg_5d']:+.2f}% valid={valid_r['avg_5d']:+.2f}%")

    # 找最穩定的權重（train/valid 差距最小 + valid 最高）
    best_idx = 0
    best_score = float('-inf')
    summary = []
    for i, w in enumerate(WEIGHT_SETS):
        r = results[str(i)]
        avg_valid = sum(r["valid"])/len(r["valid"]) if r["valid"] else 0
        stability = 1 / (1 + abs(sum(r["train"])/len(r["train"]) - avg_valid)) if r["train"] else 0
        score = avg_valid * 0.7 + stability * 0.3
        summary.append({"idx": i, "weights": w, "avg_valid": avg_valid, "stability": stability, "score": score})
        if score > best_score:
            best_score = score
            best_idx = i

    print(f"\n🏆 最佳權重組合（#{best_idx+1}）：")
    for k, v in WEIGHT_SETS[best_idx].items():
        print(f"  {k}: {v*100:.0f}%")

    # 儲存結果
    path = Path("data/reports/v7_factor_optimizer.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "best_weights": WEIGHT_SETS[best_idx],
        "summary": summary,
        "note": "walk-forward 結果，請勿直接替換正式策略，需進一步驗證"
    }, ensure_ascii=False, indent=2))
    print(f"\n  報告：{path}")
    return summary


if __name__ == "__main__":
    db = SessionLocal()
    try:
        walk_forward_optimization(db)
    finally:
        db.close()
