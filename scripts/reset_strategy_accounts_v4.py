"""
重建 / 重設策略競賽帳戶。V4.5：預設 20 萬帳戶 + 零股回測。

用法：
    python -m scripts.reset_strategy_accounts_v4
    python -m scripts.reset_strategy_accounts_v4 --initial-cash 200000

注意：V4.5 起 Position.lots / TradeLog.lots 實際代表 shares 股數。
套用後請務必重設帳戶，避免舊版整張資料混入新版零股資料。
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.database import init_db, SessionLocal, StrategyAccount, TradeLog, Position, EquityCurve
from config.settings import settings


COMMON_ODD_LOT_PARAMS = {
    "min_order_amount": 3000,
    "min_shares": 1,
    "max_shares": 300,
}


ACCOUNT_CONFIGS = [
    {
        "name": "S1 改良動能突破",
        "description": "動能 + 量價結構 + 大盤/夜盤風向；避免暴衝追高。V4.5 使用零股。",
        "strategy_class": "MomentumBreakout",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_composite": 54,
            "entry_momentum": 60,
            "entry_chip": 40,
            "min_macro": 38,
            "min_news": 38,
            "min_market_bias": 36,
            "min_close_position": 0.28,
            "min_buy_sell_ratio": 0.75,
            "max_score_day_return": 0.085,
            "max_5d_return": 0.22,
            "stop_loss": -0.05,
            "take_profit": 0.14,
            "max_pct": 0.22,
            "max_positions": 4,
            "candidate_top_n": 180,
            "candidate_min_composite": 38,
        },
    },
    {
        "name": "S2 品質價值回歸",
        "description": "基本面/估值 + 趨勢未破線；偏防守，不追高。V4.5 使用零股。",
        "strategy_class": "ValueReversion",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_fundamental": 50,
            "entry_valuation": 52,
            "entry_chip": 38,
            "entry_momentum": 26,
            "min_macro": 36,
            "min_news": 36,
            "min_market_bias": 34,
            "stop_loss": -0.07,
            "take_profit": 0.13,
            "hard_take_profit": 0.20,
            "max_pct": 0.20,
            "max_positions": 5,
            "candidate_top_n": 220,
            "candidate_min_composite": 36,
        },
    },
    {
        "name": "S3 籌碼趨勢跟隨",
        "description": "籌碼 + 量價 + 內外盤確認；修正原本 20 萬帳戶買不起、門檻過高而不動。",
        "strategy_class": "ChipFollow",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_chip": 49,
            "entry_news": 42,
            "entry_momentum": 38,
            "entry_composite": 46,
            "min_macro": 36,
            "min_news": 36,
            "min_market_bias": 34,
            "min_close_position": 0.25,
            "min_open_to_close_pct": -4.5,
            "min_buy_sell_ratio": 0.70,
            "min_vol_ratio": 0.70,
            "stop_loss": -0.05,
            "take_profit": 0.16,
            "max_pct": 0.22,
            "max_positions": 5,
            "candidate_top_n": 260,
            "candidate_min_composite": 35,
        },
    },
    {
        "name": "S4 均衡分數策略",
        "description": "避免單一分數偏科，並加入市場環境與主線加分。V4.5 使用零股。",
        "strategy_class": "BalancedScoreStrategy",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_composite": 51,
            "entry_fundamental": 42,
            "entry_valuation": 42,
            "entry_chip": 39,
            "entry_momentum": 42,
            "entry_news": 42,
            "min_macro": 36,
            "min_news": 36,
            "min_market_bias": 34,
            "stop_loss": -0.055,
            "take_profit": 0.14,
            "max_pct": 0.20,
            "max_positions": 5,
            "candidate_top_n": 220,
            "candidate_min_composite": 36,
        },
    },
    {
        "name": "S5 趨勢回檔策略",
        "description": "偏多盤低吸回檔，不買前一日暴衝；適合 4–5 月趨勢盤。V4.5 使用零股。",
        "strategy_class": "PullbackTrendStrategy",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_composite": 48,
            "entry_momentum": 39,
            "entry_chip": 36,
            "min_macro": 36,
            "min_news": 36,
            "min_market_bias": 34,
            "min_20d_return": -0.01,
            "pullback_ret1_min": -0.06,
            "pullback_ret1_max": 0.035,
            "stop_loss": -0.048,
            "take_profit": 0.11,
            "max_pct": 0.20,
            "max_positions": 5,
            "candidate_top_n": 260,
            "candidate_min_composite": 34,
        },
    },
    {
        "name": "S6 主線題材趨勢",
        "description": "根據 market_context 偵測 AI/半導體、PCB、電源散熱等主線，只買主線強勢股。",
        "strategy_class": "ThemeTrendStrategy",
        "params": {
            **COMMON_ODD_LOT_PARAMS,
            "entry_theme_score": 60,
            "entry_composite": 47,
            "entry_momentum": 40,
            "entry_chip": 34,
            "entry_news": 40,
            "min_market_bias": 38,
            "min_close_position": 0.28,
            "min_buy_sell_ratio": 0.70,
            "min_vol_ratio": 0.70,
            "stop_loss": -0.05,
            "take_profit": 0.15,
            "max_pct": 0.24,
            "max_positions": 5,
            "candidate_top_n": 300,
            "candidate_min_composite": 34,
        },
    },
]


def reset_accounts(initial_cash: float):
    init_db()
    db = SessionLocal()
    try:
        db.query(TradeLog).delete()
        db.query(Position).delete()
        db.query(EquityCurve).delete()
        db.query(StrategyAccount).delete()
        db.commit()

        today = date.today()
        for cfg in ACCOUNT_CONFIGS:
            acc = StrategyAccount(
                name=cfg["name"],
                description=cfg["description"],
                strategy_class=cfg["strategy_class"],
                strategy_type="rule_based",
                params=cfg["params"],
                weights={},
                initial_cash=initial_cash,
                cash=initial_cash,
                start_date=today,
                end_date=today + timedelta(days=settings.COMPETITION_DAYS),
                is_active=True,
            )
            db.add(acc)
        db.commit()
        print(f"已重建 {len(ACCOUNT_CONFIGS)} 個策略帳戶，initial_cash={initial_cash:,.0f}，交易單位=零股股數")
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--initial-cash", type=float, default=200_000.0)
    args = p.parse_args()
    reset_accounts(args.initial_cash)


if __name__ == "__main__":
    main()
