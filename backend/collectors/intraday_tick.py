"""
backend/collectors/intraday_tick.py
盤中每分鐘收集（09:00–13:35）
排程由 APScheduler 呼叫 run_tick()
"""
from datetime import datetime, date
from loguru import logger
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, OHLCV1Min
from backend.utils.twse_client import twse_client
from config.stock_universe import UNIVERSE_CODES


def run_tick():
    """每分鐘呼叫一次，批次抓取全股票池"""
    now = datetime.now()
    trade_date = now.date()

    # 非交易時間保護
    t = now.time()
    from datetime import time as dtime
    if not (dtime(9, 0) <= t <= dtime(13, 35)):
        return

    logger.debug(f"[TICK] {now.strftime('%H:%M')} 開始")

    # 分批抓（每批 50 檔）
    codes = UNIVERSE_CODES
    batch_size = 50
    all_rows = []

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        df = twse_client.fetch_intraday_snapshot(batch)
        if df is None or df.empty:
            continue
        ts = now.replace(second=0, microsecond=0)
        for _, r in df.iterrows():
            all_rows.append({
                "code":     r["code"],
                "ts":       ts,
                "open":     r.get("open"),
                "high":     r.get("high"),
                "low":      r.get("low"),
                "close":    r.get("close"),
                "volume":   r.get("volume"),
                "buy_vol":  r.get("buy_vol"),
                "sell_vol": r.get("sell_vol"),
            })

    if not all_rows:
        logger.warning("[TICK] 本次無資料（盤中休市？）")
        return

    db = SessionLocal()
    try:
        stmt = sqlite_insert(OHLCV1Min).values(all_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "ts"],
            set_={c: stmt.excluded[c] for c in
                  ["open","high","low","close","volume","buy_vol","sell_vol"]}
        )
        db.execute(stmt)
        db.commit()
        logger.info(f"[TICK] {now.strftime('%H:%M')} 寫入 {len(all_rows)} 筆")
    except Exception as e:
        db.rollback()
        logger.error(f"[TICK] 寫入失敗: {e}")
    finally:
        db.close()
