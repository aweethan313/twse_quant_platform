"""scripts/v8_retrain_ml.py
用擴增後的訓練資料重新訓練 ML 模型
（從 746 筆擴增到幾萬筆後才跑這個）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
from pathlib import Path
from sqlalchemy import text
from backend.models.database import SessionLocal
from loguru import logger

MODEL_VERSION = "v8_rf_v2"


def retrain(start_date="2025-01-01", end_date=None):
    try:
        from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import mean_absolute_error
        import numpy as np
    except ImportError:
        print("❌ pip install scikit-learn --break-system-packages")
        return

    if not end_date:
        end_date = str(date.today() - timedelta(days=20))

    db = SessionLocal()
    try:
        # 用擴增後的資料（所有候選股，非只有被選入的）
        rows = db.execute(text("""
            SELECT
                cfr.code, cfr.signal_date,
                cfr.final_score, cfr.momentum_score, cfr.chip_score,
                cfr.risk_score, cfr.valuation_score, cfr.core_score,
                tdf.rsi14, tdf.distance_ma20, tdf.return_5d as past5, tdf.return_1d,
                COALESCE(cd.foreign_net,0), COALESCE(cd.trust_net,0),
                COALESCE(mts.position_multiplier,1.0),
                cfr.return_5d as target
            FROM candidate_forward_returns cfr
            LEFT JOIN technical_daily_features tdf ON tdf.code=cfr.code AND tdf.trade_date=cfr.signal_date
            LEFT JOIN chip_daily cd ON cd.code=cfr.code AND cd.trade_date=cfr.signal_date
            LEFT JOIN market_timing_signals mts ON mts.trade_date=cfr.signal_date
            WHERE cfr.signal_date >= :s AND cfr.signal_date <= :e
              AND cfr.return_5d IS NOT NULL
            ORDER BY cfr.signal_date
        """), {"s": start_date, "e": end_date}).fetchall()

        logger.info(f"[ML-V2] 訓練資料：{len(rows)} 筆")
        if len(rows) < 100:
            print("⚠️ 訓練資料不足，請先執行 v8_expand_training_data.py")
            return

        import numpy as np
        X = np.array([[
            float(r[2] or 50), float(r[3] or 50), float(r[4] or 50),
            float(r[5] or 50), float(r[6] or 50), float(r[7] or 50),
            float(r[8] or 50), float(r[9] or 0),  float(r[10] or 0),
            float(r[11] or 0), float(r[12] or 0), float(r[13] or 0),
            float(r[14] or 1.0),
        ] for r in rows])
        y = np.array([float(r[15]) for r in rows])

        feature_names = ["momentum","chip","risk","valuation","core","final_score",
                         "rsi14","dist_ma20","past_5d","return_1d",
                         "foreign_net","trust_net","market_mult"]

        # Walk-Forward 評估
        tscv = TimeSeriesSplit(n_splits=5)
        rf_scores, gb_scores = [], []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            rf = RandomForestRegressor(n_estimators=200, max_depth=6, random_state=42, n_jobs=-1)
            rf.fit(X[train_idx], y[train_idx])
            rf_scores.append(mean_absolute_error(y[val_idx], rf.predict(X[val_idx])))

            gb = GradientBoostingRegressor(n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42)
            gb.fit(X[train_idx], y[train_idx])
            gb_scores.append(mean_absolute_error(y[val_idx], gb.predict(X[val_idx])))

        rf_avg = np.mean(rf_scores)
        gb_avg = np.mean(gb_scores)
        print(f"Random Forest MAE:     {rf_avg:.3f}%  (folds: {[f'{s:.2f}' for s in rf_scores]})")
        print(f"Gradient Boosting MAE: {gb_avg:.3f}%  (folds: {[f'{s:.2f}' for s in gb_scores]})")

        # 選較好的模型
        best_model_type = "RF" if rf_avg <= gb_avg else "GB"
        print(f"→ 選用: {best_model_type}")

        if best_model_type == "RF":
            final_model = RandomForestRegressor(n_estimators=300, max_depth=7, random_state=42, n_jobs=-1)
        else:
            final_model = GradientBoostingRegressor(n_estimators=300, max_depth=5, learning_rate=0.05, random_state=42)

        final_model.fit(X, y)

        # 特徵重要性
        importance = sorted(zip(feature_names, final_model.feature_importances_.tolist()),
                           key=lambda x: -x[1])
        print("\n特徵重要性 (Top8)：")
        for fname, imp in importance[:8]:
            bar = "█" * int(imp * 100)
            print(f"  {fname:15} {imp:.4f} {bar}")

        # 儲存
        import pickle
        model_path = Path("data/models")
        model_path.mkdir(parents=True, exist_ok=True)
        model_file = model_path / f"{MODEL_VERSION}.pkl"
        with open(model_file, "wb") as f:
            pickle.dump({
                "model": final_model,
                "features": feature_names,
                "importance": importance,
                "train_samples": len(rows),
                "rf_mae": rf_avg,
                "gb_mae": gb_avg,
                "model_type": best_model_type,
                "train_end": end_date,
                "version": MODEL_VERSION,
            }, f)

        print(f"\n✅ 模型儲存：{model_file}")
        print(f"   訓練資料: {len(rows)} 筆（vs 舊版 746 筆）")
        print(f"   MAE: {min(rf_avg, gb_avg):.3f}% （越低越好）")
        return final_model

    finally:
        db.close()


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    retrain(start)
