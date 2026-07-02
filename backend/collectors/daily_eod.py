"""
backend/collectors/daily_eod.py
每日收盤後執行：抓取當日日 K + 三大法人。

V4.6.2 重要修正：
- run_eod(trade_date) 仍可用於「當日 / 最近交易日」收盤資料更新。
- 禁止再用 STOCK_DAY_ALL 做歷史區間 backfill。
  原因：STOCK_DAY_ALL 不適合拿來補歷史全市場日 K；在本專案資料中已觀察到
  2026-04-16 ~ 2026-05-15 大量股票被寫入完全相同 OHLCV。
- 歷史修復請改用：
  python -m scripts.repair_stale_ohlcv_from_stock_day --start-date YYYY-MM-DD --end-date YYYY-MM-DD --codes ... --apply
"""
from datetime import date, timedelta
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import SessionLocal, OHLCVDaily, ChipDaily, StockMeta
from backend.utils.twse_client import twse_client
from backend.utils.trading_day import is_trading_day


def run_eod(trade_date: date = None):
    """
    主入口：收盤後抓指定日期資料。

    注意：
    - 適合每日收盤後跑「當日資料」。
    - 不建議拿來補很久以前的歷史日 K。
    """
    if trade_date is None:
        trade_date = date.today()

    logger.info(f"[EOD] 開始收集 {trade_date} 資料")
    db = SessionLocal()
    try:
        if not is_trading_day(trade_date, db=db):
            logger.info(f"[EOD] {trade_date} 非交易日，略過 OHLCV / 法人寫入")
            return {"ok": True, "skipped": True, "reason": "non_trading_day", "trade_date": str(trade_date)}
        _collect_ohlcv(db, trade_date)
        _collect_chips(db, trade_date)
        db.commit()
        logger.success(f"[EOD] {trade_date} 資料收集完成")
    except Exception as e:
        db.rollback()
        logger.error(f"[EOD] {trade_date} 失敗: {e}")
        raise
    finally:
        db.close()


def _collect_ohlcv(db: Session, trade_date: date):
    df = twse_client.fetch_daily_all(trade_date)
    if df is None or df.empty:
        logger.warning(f"[EOD] OHLCV 無資料 {trade_date}（可能為假日）")
        _register_pending_backfill(db, trade_date, "fetch_empty")
        return

    # 假日污染防護：STOCK_DAY_ALL 在假日會回傳「最近交易日」的舊資料，
    # 若指標股收盤與資料庫最新交易日完全相同，代表今天非交易日，跳過寫入。
    try:
        from backend.utils.trading_day import is_fetched_data_stale
        if is_fetched_data_stale(df, trade_date, db):
            logger.warning(f"[EOD] {trade_date} 判定為非交易日（假日舊資料），跳過 OHLCV 寫入")
            return
    except Exception as e:
        logger.warning(f"[EOD] 假日防護檢查失敗（放行）：{e}")

    _upsert_stock_meta_from_daily_df(db, df)

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code":       str(r["code"]).strip(),
            "trade_date": trade_date,
            "open":       r.get("open"),
            "high":       r.get("high"),
            "low":        r.get("low"),
            "close":      r.get("close"),
            "volume":     r.get("volume"),
            "value":      r.get("value"),
            "change":     r.get("change"),
            "change_pct": r.get("change_pct"),
        })

    if rows:
        stmt = sqlite_insert(OHLCVDaily).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code", "trade_date"],
            set_={c: stmt.excluded[c] for c in
                  ["open", "high", "low", "close", "volume", "value", "change", "change_pct"]}
        )
        db.execute(stmt)
        logger.info(f"[EOD] OHLCV upsert {len(rows)} 筆")


def _upsert_stock_meta_from_daily_df(db: Session, df):
    """用 TWSE STOCK_DAY_ALL 回傳的 code/name 更新 stock_meta。"""
    if df is None or df.empty or "code" not in df.columns or "name" not in df.columns:
        return

    rows = []
    seen = set()
    for _, r in df.iterrows():
        code = str(r.get("code", "")).strip()
        name = str(r.get("name", "")).strip()
        if not code or not name or code in seen:
            continue
        if name.lower() in ("nan", "none"):
            continue
        seen.add(code)
        rows.append({
            "code": code,
            "name": name,
            "market": "TWSE",
            "is_active": True,
        })

    if not rows:
        return

    stmt = sqlite_insert(StockMeta).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": stmt.excluded.name,
            "market": stmt.excluded.market,
            "is_active": True,
        },
    )
    db.execute(stmt)
    logger.info(f"[EOD] stock_meta upsert {len(rows)} 筆")


def _collect_chips(db: Session, trade_date: date):
    df = twse_client.fetch_institutional(trade_date)

    if df is None or df.empty:
        logger.warning(f"[EOD] 法人資料無 {trade_date}")
        return

    rows = []

    for _, r in df.iterrows():
        code = str(r.get("code", "")).strip()

        if not code:
            continue

        rows.append({
            "code":        code,
            "trade_date":  trade_date,
            "foreign_net": float(r.get("foreign_net", 0) or 0),
            "trust_net":   float(r.get("trust_net", 0) or 0),
            "dealer_net":  float(r.get("dealer_net", 0) or 0),
        })

    if not rows:
        logger.warning(f"[EOD] 法人資料解析後無可寫入資料 {trade_date}")
        return

    stmt = sqlite_insert(ChipDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code", "trade_date"],
        set_={
            "foreign_net": stmt.excluded.foreign_net,
            "trust_net":   stmt.excluded.trust_net,
            "dealer_net":  stmt.excluded.dealer_net,
        }
    )

    db.execute(stmt)
    logger.info(f"[EOD] 法人 upsert {len(rows)} 筆")


def backfill(start_date: date, end_date: date = None):
    """
    V4.6.2：停用不安全的歷史全市場 backfill。

    原本這裡逐日呼叫 STOCK_DAY_ALL，但本專案已發現歷史區間大量 OHLCV 被複製成同一天資料。
    為避免再次污染 data/db/quant.db，這裡直接阻擋。

    歷史修復請改用：
      python -m scripts.repair_stale_ohlcv_from_stock_day --start-date 2026-04-16 --end-date 2026-05-18 --codes 2330,2454 --apply

    或先用 --dry-run / 不加 --apply 測試。
    """
    raise RuntimeError(
        "V4.6.2 已停用 daily_eod.backfill()：不要用 STOCK_DAY_ALL 補歷史全市場日 K。"
        "請改用 scripts.repair_stale_ohlcv_from_stock_day 逐股逐月修復。"
    )


def _register_pending_backfill(db: Session, trade_date, reason: str):
    from sqlalchemy import text as _t
    try:
        db.execute(_t('''CREATE TABLE IF NOT EXISTS pending_backfill(
          trade_date TEXT PRIMARY KEY, reason TEXT,
          created_at TEXT DEFAULT (datetime('now','localtime')), resolved_at TEXT)'''))
        db.execute(_t(
            "INSERT OR IGNORE INTO pending_backfill(trade_date, reason) VALUES(:d,:r)"
        ), {"d": str(trade_date), "r": reason})
        db.commit()
        logger.warning(f"[EOD] {trade_date} 已登記 pending_backfill（{reason}）")
    except Exception as e:
        logger.warning(f"[EOD] pending_backfill 登記失敗: {e}")
