"""除權息處理:每日更新 corporate_actions + 除息日現金入帳(冪等)"""
from datetime import date, timedelta
from loguru import logger
from sqlalchemy import text
from backend.models.database import SessionLocal

FORWARD_START = "2026-05-25"


def refresh_corporate_actions(as_of: date = None) -> dict:
    """每日重抓 過去45天~未來90天 的除權息(涵蓋金額後補公告的情況)"""
    from backend.utils.twse_client import twse_client
    as_of = as_of or date.today()
    df = twse_client.fetch_ex_rights(as_of - timedelta(days=45), as_of + timedelta(days=90))
    if df is None or df.empty:
        logger.warning("[DIV] 除權息資料抓取失敗/為空")
        return {"ok": False, "updated": 0}
    db = SessionLocal()
    try:
        n = 0
        for _, r in df.iterrows():
            db.execute(text("""
                INSERT INTO corporate_actions(code, ex_date, action_type, cash_dividend, stock_dividend_ratio)
                VALUES(:c,:d,:t,:cd,:sr)
                ON CONFLICT(code, ex_date) DO UPDATE SET
                  action_type=excluded.action_type,
                  cash_dividend=excluded.cash_dividend,
                  stock_dividend_ratio=excluded.stock_dividend_ratio,
                  fetched_at=datetime('now','localtime')
            """), {"c": r["code"], "d": r["ex_date"], "t": r["action_type"],
                   "cd": r["cash_dividend"], "sr": r["stock_dividend_ratio"]})
            n += 1
        db.commit()
        logger.info(f"[DIV] corporate_actions 更新 {n} 筆")
        return {"ok": True, "updated": n}
    finally:
        db.close()


def _entitled_shares(db, account_id: int, code: str, ex_date: str) -> float:
    """除息日前一天收盤應持股數 = 重播 ex_date 之前的所有 fills"""
    row = db.execute(text("""
        SELECT COALESCE(SUM(CASE WHEN action='BUY' THEN shares
                                 WHEN action='SELL' THEN -shares END), 0)
        FROM paper_fills
        WHERE account_id=:a AND code=:c AND execution_date < :d
          AND COALESCE(is_blocked,0)=0
    """), {"a": account_id, "c": code, "d": ex_date}).scalar()
    return float(row or 0)


def credit_dividends(as_of: date = None) -> dict:
    """
    對所有帳戶結算「已到除息日且金額已公告」的現金股利(冪等:dividend_income 主鍵防重)。
    含自動補記:過去漏掉或金額後補公告的,每天都會被撈到並入帳一次。
    股票股利(權/權息的配股部分)僅記警告,需人工確認。
    """
    as_of = as_of or date.today()
    db = SessionLocal()
    credited = []
    try:
        accounts = [r[0] for r in db.execute(text(
            "SELECT id FROM strategy_accounts WHERE id >= 11")).fetchall()]
        cas = db.execute(text("""
            SELECT code, ex_date, action_type, cash_dividend, COALESCE(stock_dividend_ratio,0)
            FROM corporate_actions
            WHERE ex_date >= :fs AND ex_date <= :d
        """), {"fs": FORWARD_START, "d": str(as_of)}).fetchall()

        for code, ex_date, atype, cash_div, stock_ratio in cas:
            for aid in accounts:
                sh = _entitled_shares(db, aid, code, ex_date)
                if sh <= 0:
                    continue
                if stock_ratio and float(stock_ratio) > 0:
                    logger.warning(f"[DIV] A{aid} {code} {ex_date} 含配股(率={stock_ratio}),股數調整需人工處理")
                if cash_div is None:
                    logger.info(f"[DIV] A{aid} {code} {ex_date} 持有{sh:.0f}股,金額待公告,暫不入帳")
                    continue
                amt = round(sh * float(cash_div), 2)
                if amt <= 0:
                    continue
                dup = db.execute(text("""
                    SELECT 1 FROM dividend_income WHERE account_id=:a AND code=:c AND ex_date=:d
                """), {"a": aid, "c": code, "d": ex_date}).scalar()
                if dup:
                    continue
                db.execute(text("""
                    INSERT INTO dividend_income(account_id, code, ex_date, shares, cash_dividend, amount)
                    VALUES(:a,:c,:d,:s,:cd,:amt)
                """), {"a": aid, "c": code, "d": ex_date, "s": sh, "cd": float(cash_div), "amt": amt})
                db.execute(text("""
                    UPDATE strategy_accounts SET cash = cash + :amt WHERE id=:a
                """), {"amt": amt, "a": aid})
                credited.append((aid, code, ex_date, sh, amt))
                logger.success(f"[DIV] A{aid} {code} 除息{ex_date} {sh:.0f}股 × {cash_div} = +{amt} 已入帳")
        db.commit()
        return {"ok": True, "credited": len(credited), "detail": credited}
    finally:
        db.close()
