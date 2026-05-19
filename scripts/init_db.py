"""
scripts/init_db.py
初始化資料庫 + 建立三個示範策略帳戶
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from rich.console import Console
from backend.models.database import init_db, SessionLocal, StrategyAccount
from config.settings import settings

console = Console()

DEMO_ACCOUNTS = [
    {
        "name": "動能突破策略",
        "description": "追蹤高動能、高綜合分標的，短線 7% 停損 / 15% 停利",
        "strategy_class": "MomentumBreakout",
        "strategy_type": "rule_based",
        "params": {
            "entry_composite": 70,
            "entry_momentum": 65,
            "stop_loss": -0.07,
            "take_profit": 0.15,
            "weak_signal": 40,
            "max_pct": 0.10,
            "max_lots": 10,
        },
    },
    {
        "name": "價值回歸策略",
        "description": "找低估值 + 強基本面標的，中期持有最多 20 個交易日",
        "strategy_class": "ValueReversion",
        "strategy_type": "rule_based",
        "params": {
            "max_pct": 0.12,
            "max_lots": 15,
            "max_hold_days": 20,
        },
    },
    {
        "name": "籌碼跟蹤策略",
        "description": "外資 + 投信連續買超，新聞正面，嚴格 5% 停損",
        "strategy_class": "ChipFollow",
        "strategy_type": "rule_based",
        "params": {
            "max_pct": 0.08,
            "max_lots": 8,
        },
    },
]


def main():
    console.rule("[bold cyan]初始化資料庫")
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    init_db()
    console.print(f"  DB: {settings.DB_PATH} [green]✓")

    db = SessionLocal()
    try:
        today = date.today()
        end   = today + timedelta(days=settings.COMPETITION_DAYS)
        created = 0
        for cfg in DEMO_ACCOUNTS:
            existing = db.query(StrategyAccount).filter_by(name=cfg["name"]).first()
            if existing:
                console.print(f"  [yellow]已存在[/yellow] {cfg['name']}")
                continue
            acc = StrategyAccount(
                name=cfg["name"],
                description=cfg["description"],
                strategy_class=cfg["strategy_class"],
                strategy_type=cfg["strategy_type"],
                params=cfg["params"],
                weights={},
                initial_cash=settings.INITIAL_CASH,
                cash=settings.INITIAL_CASH,
                start_date=today,
                end_date=end,
                is_active=True,
            )
            db.add(acc)
            created += 1
            console.print(f"  [green]建立[/green] {cfg['name']}")
        db.commit()
        console.print(f"\n  共建立 {created} 個策略帳戶")
    finally:
        db.close()

    console.rule("[bold green]初始化完成")
    console.print("\n下一步：")
    console.print("  [cyan]python scripts/backfill_year.py[/cyan]   # 補抓近一年歷史資料")
    console.print("  [cyan]uvicorn main:app --reload[/cyan]          # 啟動 Web\n")


if __name__ == "__main__":
    main()
