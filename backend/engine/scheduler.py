"""
backend/engine/scheduler.py
APScheduler 排程中樞
"""
from datetime import date, datetime
from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from config.stock_universe import UNIVERSE_CODES


scheduler = BlockingScheduler(timezone="Asia/Taipei")


def job_eod():
    """21:00 每日收盤資料收集"""
    logger.info("=== [SCHED] EOD 開始 ===")
    from backend.collectors.daily_eod import run_eod
    run_eod()


def job_scores():
    """21:30 分數計算（EOD 完成後）"""
    logger.info("=== [SCHED] 分數計算 ===")
    from backend.signals.scorer import compute_scores
    compute_scores(UNIVERSE_CODES)


def job_strategies():
    """22:00 策略帳戶執行"""
    logger.info("=== [SCHED] 策略執行 ===")
    from backend.engine.strategy_runner import run_all_strategies
    run_all_strategies(date.today())


def job_equity_snapshot():
    """22:30 全帳戶權益快照"""
    logger.info("=== [SCHED] 權益快照 ===")
    from backend.engine.strategy_runner import snapshot_all_equity
    snapshot_all_equity()


def job_intraday_tick():
    """盤中每分鐘行情收集"""
    from backend.collectors.intraday_tick import run_tick
    run_tick()


def job_fundamental():
    """每月 10 日 08:00 補抓月營收"""
    logger.info("=== [SCHED] 月營收收集 ===")
    from backend.collectors.fundamental import run_monthly_revenue
    run_monthly_revenue()


def job_news():
    """每天 08:00 抓新聞事件"""
    logger.info("=== [SCHED] 新聞收集 ===")
    from backend.collectors.news_events import run_news
    run_news()


def register_jobs():
    # ── 收盤後任務 ─────────────────────────────────
    scheduler.add_job(
        job_eod, CronTrigger(hour=21, minute=0, day_of_week="mon-fri"),
        id="eod", name="日K收集", replace_existing=True
    )
    scheduler.add_job(
        job_scores, CronTrigger(hour=21, minute=30, day_of_week="mon-fri"),
        id="scores", name="分數計算", replace_existing=True
    )
    scheduler.add_job(
        job_strategies, CronTrigger(hour=22, minute=0, day_of_week="mon-fri"),
        id="strategies", name="策略執行", replace_existing=True
    )
    scheduler.add_job(
        job_equity_snapshot, CronTrigger(hour=22, minute=30, day_of_week="mon-fri"),
        id="equity", name="權益快照", replace_existing=True
    )

    # ── 盤中每分鐘（交易時段由 run_tick 內部保護）──
    scheduler.add_job(
        job_intraday_tick, IntervalTrigger(seconds=60),
        id="intraday", name="盤中分K", replace_existing=True
    )

    # ── 月任務 ─────────────────────────────────────
    scheduler.add_job(
        job_fundamental, CronTrigger(day=10, hour=8, minute=0),
        id="fundamental", name="月營收", replace_existing=True
    )

    # ── 每日新聞 ───────────────────────────────────
    scheduler.add_job(
        job_news, CronTrigger(hour=8, minute=0, day_of_week="mon-fri"),
        id="news", name="新聞事件", replace_existing=True
    )

    logger.info(f"已登錄 {len(scheduler.get_jobs())} 個排程任務")


def start():
    register_jobs()
    logger.info("排程啟動中... (Ctrl+C 停止)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("排程已停止")
