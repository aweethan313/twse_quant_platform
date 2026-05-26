"""backend/v5/strategy_configs.py
6 個 V5 Forward Paper Strategy Accounts 定義與初始化
"""
from __future__ import annotations
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

# ── 6 個策略帳戶定義 ──
V5_ACCOUNTS = [
    {
        "id": 11,
        "name": "A1 HighScore Top3",
        "strategy_type": "HighScoreTop3",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "HighScoreTop3",
            "candidate_rank_limit": 3,
            "min_score": 80.0,
            "max_positions": 5,
            "max_position_pct": 0.20,
            "stop_loss_pct": 0.08,
            "take_profit_pct": 0.15,
            "no_chase_enabled": 0,
            "max_rsi14": 85.0,
            "description": "每日候選股分數前3，最低分80，不特別限制RSI",
        }
    },
    {
        "id": 12,
        "name": "A2 HighScore NoChase",
        "strategy_type": "HighScoreNoChase",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "HighScoreNoChase",
            "candidate_rank_limit": 5,
            "min_score": 78.0,
            "max_positions": 5,
            "max_position_pct": 0.20,
            "stop_loss_pct": 0.07,
            "take_profit_pct": 0.12,
            "no_chase_enabled": 1,
            "max_rsi14": 75.0,
            "max_distance_ma20_pct": 12.0,
            "description": "分數前5，RSI<75，不追高",
        }
    },
    {
        "id": 13,
        "name": "A3 LargeCap Stable",
        "strategy_type": "LargeCapStable",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "LargeCapStable",
            "min_score": 70.0,
            "max_positions": 6,
            "max_position_pct": 0.15,
            "stop_loss_pct": 0.06,
            "take_profit_pct": 0.10,
            "large_cap_only": 1,
            "max_rsi14": 80.0,
            "description": "只買核心大型股/LARGE_LIQUID，穩定優先",
        }
    },
    {
        "id": 14,
        "name": "A4 Theme Semiconductor",
        "strategy_type": "ThemeSemiconductor",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "ThemeSemiconductor",
            "min_score": 75.0,
            "max_positions": 5,
            "max_position_pct": 0.20,
            "stop_loss_pct": 0.08,
            "take_profit_pct": 0.15,
            "theme_filter": "AI/伺服器,半導體,PCB/載板,電源/散熱",
            "description": "只買AI/半導體/PCB/電源散熱主題股",
        }
    },
    {
        "id": 15,
        "name": "A5 Pullback Quality",
        "strategy_type": "PullbackQuality",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "PullbackQuality",
            "min_score": 75.0,
            "max_positions": 5,
            "max_position_pct": 0.20,
            "stop_loss_pct": 0.06,
            "take_profit_pct": 0.12,
            "min_rsi14": 40.0,
            "max_rsi14": 65.0,
            "max_distance_ma20_pct": 8.0,
            "no_chase_enabled": 1,
            "description": "高分但短線回落，RSI 40~65，接近MA20",
        }
    },
    {
        "id": 16,
        "name": "A6 0050 Core+Satellite",
        "strategy_type": "CoreSatellite",
        "mode": "forward_paper",
        "initial_cash": 200000,
        "config": {
            "strategy_name": "CoreSatellite",
            "min_score": 75.0,
            "max_positions": 4,
            "max_position_pct": 0.15,
            "stop_loss_pct": 0.07,
            "take_profit_pct": 0.12,
            "target_0050_pct": 0.50,
            "target_satellite_pct": 0.50,
            "description": "50% 持有0050，50% 買每日候選股",
        }
    },
]


def setup_v5_accounts():
    """建立 6 個 V5 Forward Paper Strategy Accounts"""
    db = SessionLocal()
    try:
        created = 0
        for acct in V5_ACCOUNTS:
            # 建立 strategy_accounts
            existing = db.execute(text(
                "SELECT id FROM strategy_accounts WHERE id=:id"
            ), {"id": acct["id"]}).fetchone()

            if not existing:
                db.execute(text("""
                    INSERT INTO strategy_accounts
                        (id, name, strategy_type, initial_cash, cash, mode, start_date)
                    VALUES (:id, :name, :st, :ic, :ic, :mode, date('now','localtime'))
                """), {
                    "id": acct["id"],
                    "name": acct["name"],
                    "st": acct["strategy_type"],
                    "ic": acct["initial_cash"],
                    "mode": acct["mode"],
                })
                created += 1
                logger.info(f"[V5] 建立帳戶 {acct['name']}")

            # 建立 strategy_account_configs
            db.execute(text("""
                INSERT OR REPLACE INTO strategy_account_configs
                    (account_id, strategy_name, mode,
                     candidate_rank_limit, min_score, max_positions,
                     max_position_pct, stop_loss_pct, take_profit_pct,
                     large_cap_only, no_chase_enabled,
                     max_rsi14, min_rsi14, max_distance_ma20_pct,
                     theme_filter, target_0050_pct, target_satellite_pct,
                     description, is_active, updated_at)
                VALUES
                    (:aid, :sn, :mode,
                     :crl, :ms, :mp,
                     :mpp, :sl, :tp,
                     :lco, :nc,
                     :max_rsi, :min_rsi, :max_ma,
                     :tf, :t0050, :tsat,
                     :desc, 1, datetime('now','localtime'))
            """), {
                "aid": acct["id"],
                "sn": acct["config"]["strategy_name"],
                "mode": acct["config"].get("mode", "forward_paper"),
                "crl": acct["config"].get("candidate_rank_limit", 5),
                "ms": acct["config"].get("min_score", 75.0),
                "mp": acct["config"].get("max_positions", 5),
                "mpp": acct["config"].get("max_position_pct", 0.20),
                "sl": acct["config"].get("stop_loss_pct", 0.08),
                "tp": acct["config"].get("take_profit_pct", 0.15),
                "lco": acct["config"].get("large_cap_only", 0),
                "nc": acct["config"].get("no_chase_enabled", 0),
                "max_rsi": acct["config"].get("max_rsi14", 80.0),
                "min_rsi": acct["config"].get("min_rsi14", 30.0),
                "max_ma": acct["config"].get("max_distance_ma20_pct", 12.0),
                "tf": acct["config"].get("theme_filter"),
                "t0050": acct["config"].get("target_0050_pct", 0.0),
                "tsat": acct["config"].get("target_satellite_pct", 1.0),
                "desc": acct["config"].get("description", ""),
            })

        db.commit()
        logger.success(f"[V5] 建立 {created} 個新帳戶，更新 {len(V5_ACCOUNTS)} 個設定")
        return {"created": created, "total": len(V5_ACCOUNTS)}

    except Exception as e:
        db.rollback()
        logger.error(f"[V5] 建立帳戶失敗: {e}")
        raise
    finally:
        db.close()


def get_account_config(account_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT * FROM strategy_account_configs WHERE account_id=:id"
        ), {"id": account_id}).fetchone()
        if not row:
            return None
        cols = ["id","account_id","strategy_name","mode",
                "candidate_rank_limit","min_score","max_positions",
                "max_position_pct","stop_loss_pct","take_profit_pct",
                "min_hold_days","max_hold_days","allow_core_stocks","allow_small_caps",
                "theme_filter","large_cap_only","no_chase_enabled",
                "max_rsi14","min_rsi14","max_distance_ma20_pct",
                "target_0050_pct","target_satellite_pct",
                "market_risk_position_multiplier","description","is_active",
                "created_at","updated_at"]
        return dict(zip(cols, row))
    finally:
        db.close()
