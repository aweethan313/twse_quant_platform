"""scripts/v7_score_calibration.py
V7 評分系統重新校正
分析各因子的預測力，找出最優權重
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal


def calc_ic(factor_vals, forward_returns):
    """計算 IC（Information Coefficient）= 相關係數"""
    if len(factor_vals) < 5:
        return None
    n = len(factor_vals)
    mx = sum(factor_vals) / n
    my = sum(forward_returns) / n
    num = sum((x-mx)*(y-my) for x,y in zip(factor_vals, forward_returns))
    dx = (sum((x-mx)**2 for x in factor_vals) / n) ** 0.5
    dy = (sum((y-my)**2 for y in forward_returns) / n) ** 0.5
    if dx == 0 or dy == 0:
        return 0
    return num / (n * dx * dy)


def analyze_factor(db, factor_col: str, score_table: str = "daily_scores",
                   start_date: str = "2025-01-01") -> dict:
    """分析單一因子的預測力"""
    rows = db.execute(text(f"""
        SELECT ds.{factor_col}, cfr.return_5d, cfr.return_10d, cfr.alpha_5d_vs_0050
        FROM {score_table} ds
        JOIN candidate_forward_returns cfr
            ON cfr.code=ds.code AND cfr.signal_date=ds.score_date
        WHERE ds.score_date >= :s
          AND ds.{factor_col} IS NOT NULL
          AND cfr.return_5d IS NOT NULL
        ORDER BY ds.score_date
    """), {"s": start_date}).fetchall()

    if len(rows) < 10:
        return {"factor": factor_col, "n": len(rows), "note": "樣本不足"}

    factors = [float(r[0]) for r in rows]
    ret5   = [float(r[1]) for r in rows]
    ret10  = [float(r[2] or 0) for r in rows]
    alpha5 = [float(r[3] or 0) for r in rows]

    ic5  = calc_ic(factors, ret5)
    ic10 = calc_ic(factors, ret10)
    ica  = calc_ic(factors, alpha5)

    wins = sum(1 for r, f in zip(ret5, factors) if r > 0 and f >= 65)
    total_high = sum(1 for f in factors if f >= 65)
    hit_rate = wins / total_high * 100 if total_high > 0 else 0

    return {
        "factor": factor_col,
        "n": len(rows),
        "ic_5d": round(ic5 or 0, 4),
        "ic_10d": round(ic10 or 0, 4),
        "ic_alpha": round(ica or 0, 4),
        "avg_5d": round(sum(ret5)/len(ret5), 3),
        "avg_10d": round(sum(ret10)/len(ret10), 3),
        "hit_rate_high_score": round(hit_rate, 1),
        "n_high_score": total_high,
    }


def analyze_score_buckets(db, start_date="2025-01-01"):
    """分析各分數區間的表現"""
    rows = db.execute(text("""
        SELECT
            CASE
                WHEN ds.final_score >= 80 THEN '80+'
                WHEN ds.final_score >= 70 THEN '70~80'
                WHEN ds.final_score >= 60 THEN '60~70'
                ELSE '<60'
            END as bucket,
            COUNT(*) as n,
            AVG(cfr.return_5d) as avg_5d,
            AVG(cfr.return_10d) as avg_10d,
            AVG(cfr.alpha_5d_vs_0050) as avg_alpha,
            SUM(CASE WHEN cfr.return_5d > 0 THEN 1.0 ELSE 0 END)/COUNT(*)*100 as win_rate
        FROM daily_scores ds
        JOIN candidate_forward_returns cfr
            ON cfr.code=ds.code AND cfr.signal_date=ds.score_date
        WHERE ds.score_date >= :s AND cfr.return_5d IS NOT NULL
        GROUP BY bucket ORDER BY ds.final_score DESC
    """), {"s": start_date}).fetchall()
    return [{"bucket": r[0], "n": r[1], "avg_5d": round(r[2] or 0, 3),
             "avg_10d": round(r[3] or 0, 3), "avg_alpha": round(r[4] or 0, 3),
             "win_rate": round(r[5] or 0, 1)} for r in rows]


def suggest_weights(factor_results: list) -> dict:
    """根據 IC 建議新權重"""
    # 只考慮 IC 絕對值 > 0.02 的因子
    valid = [f for f in factor_results if abs(f.get("ic_5d", 0)) > 0.02]
    if not valid:
        return {"note": "所有因子 IC 均低，建議重新收集更多資料"}

    total_ic = sum(abs(f["ic_5d"]) for f in valid)
    weights = {}
    for f in valid:
        w = abs(f["ic_5d"]) / total_ic * 100
        weights[f["factor"]] = round(w, 1)

    # 標準化
    total = sum(weights.values())
    for k in weights:
        weights[k] = round(weights[k] / total * 100, 1)

    return weights


def run():
    db = SessionLocal()
    try:
        print("=== V7 評分系統校正分析 ===\n")

        # 1. 分數區間分析
        print("📊 分數區間 vs 報酬：")
        buckets = analyze_score_buckets(db)
        if buckets:
            print(f"  {'區間':8} {'樣本':6} {'均5日':8} {'均10日':8} {'Alpha':8} {'勝率':6}")
            for b in buckets:
                flag = "❌ 逆序!" if b["bucket"] in ["80+","70~80"] and (b["avg_5d"] or 0) < 0 else ""
                print(f"  {b['bucket']:8} {b['n']:6} {b['avg_5d']:+7.2f}% {b['avg_10d']:+7.2f}% {b['avg_alpha']:+7.2f}% {b['win_rate']:5.1f}% {flag}")
        else:
            print("  ⚠️ 候選股前瞻報酬資料不足，請先執行 v6_build_candidate_forward_returns")

        # 2. 因子分析
        factors_to_test = [
            "momentum_score", "chip_score", "risk_score",
            "valuation_score", "core_score", "final_score"
        ]
        print("\n🔬 因子 IC 分析（IC > 0.02 才有預測力）：")
        results = []
        for f in factors_to_test:
            try:
                r = analyze_factor(db, f)
                results.append(r)
                if r.get("n", 0) >= 10:
                    ic = r.get("ic_5d", 0)
                    flag = "✅" if abs(ic) >= 0.02 else "⚠️"
                    print(f"  {flag} {f:20} IC={ic:+.4f} 樣本={r['n']:4} 勝率={r.get('hit_rate_high_score',0):.1f}%")
            except Exception as e:
                print(f"  - {f:20} 跳過: {e}")

        # 3. 建議權重
        print("\n💡 建議新權重（基於 IC）：")
        suggested = suggest_weights([r for r in results if r.get("n", 0) >= 10])
        if isinstance(suggested, dict) and "note" not in suggested:
            for k, v in sorted(suggested.items(), key=lambda x: -x[1]):
                print(f"  {k:25} {v:.1f}%")
        else:
            print(f"  {suggested}")

        # 4. 寫入分析結果
        today = str(date.today())
        for r in results:
            if r.get("n", 0) >= 5:
                try:
                    db.execute(text("""
                        INSERT OR REPLACE INTO factor_analysis_results
                            (analysis_date, factor_name, ic_mean, hit_rate, avg_return_5d, avg_return_10d, note)
                        VALUES (:d,:f,:ic,:hr,:r5,:r10,:note)
                    """), {"d": today, "f": r["factor"], "ic": r.get("ic_5d"),
                           "hr": r.get("hit_rate_high_score"), "r5": r.get("avg_5d"),
                           "r10": r.get("avg_10d"), "note": json.dumps(r, ensure_ascii=False)})
                except Exception:
                    pass
        db.commit()

        # 5. 核心結論
        print("\n📌 核心結論：")
        if buckets:
            sorted_b = sorted(buckets, key=lambda x: x.get("avg_5d", 0), reverse=True)
            best = sorted_b[0]["bucket"] if sorted_b else "N/A"
            worst = sorted_b[-1]["bucket"] if sorted_b else "N/A"
            print(f"  - 報酬最佳區間：{best}")
            print(f"  - 報酬最差區間：{worst}")
            if worst in ["80+", "70~80"]:
                print("  - ⚠️ 高分股表現差 → 評分系統需重新校正")
                print("  - 建議：降低動能因子權重，增加籌碼因子權重")
                print("  - 建議：考慮加入「分數連續上升」趨勢而非單日分數")

        path = Path("data/reports/v7_score_calibration.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        report = f"# V7 評分校正報告\n生成：{today}\n\n"
        report += "## 分數區間分析\n"
        for b in buckets:
            report += f"- {b['bucket']}: 均5日={b['avg_5d']:+.2f}% 勝率={b['win_rate']:.1f}%\n"
        report += "\n## 因子IC\n"
        for r in results:
            if r.get("n",0) >= 10:
                report += f"- {r['factor']}: IC={r.get('ic_5d',0):+.4f}\n"
        path.write_text(report, encoding="utf-8")
        print(f"\n  報告：{path}")

    finally:
        db.close()

if __name__ == "__main__":
    run()
