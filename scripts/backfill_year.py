"""
scripts/backfill_year.py
補抓近一年全台股歷史日K + 籌碼資料
執行一次即可，約需 15~30 分鐘（受 TWSE 限流）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from loguru import logger
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.console import Console

from backend.models.database import init_db
from backend.collectors.daily_eod import backfill

console = Console()

def main():
    init_db()
    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=365)

    console.rule("[bold yellow]TWSE 歷史資料補抓")
    console.print(f"  範圍：{start_date} → {end_date}")
    console.print(f"  預計天數：~{(end_date - start_date).days} 天（跳過假日）")
    console.print(f"  每次請求間隔：1.5 秒（官方限流保護）\n")

    # 計算工作日數
    total_days = sum(
        1 for i in range((end_date - start_date).days + 1)
        if (start_date + timedelta(days=i)).weekday() < 5
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("補抓日K資料", total=total_days)

        d = start_date
        while d <= end_date:
            if d.weekday() < 5:
                try:
                    from backend.collectors.daily_eod import run_eod
                    run_eod(d)
                    progress.advance(task)
                    progress.update(task, description=f"[green]{d}")
                except Exception as e:
                    logger.warning(f"  {d} 失敗: {e}")
                    progress.advance(task)
            d += timedelta(days=1)

    console.rule("[bold green]補抓完成")
    console.print("\n下一步：")
    console.print("  python scripts/run_scheduler.py &  # 啟動排程")
    console.print("  uvicorn main:app --reload           # 啟動 API\n")


if __name__ == "__main__":
    main()
