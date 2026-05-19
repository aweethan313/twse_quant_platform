"""
backend/engine/paper_account.py
紙上交易帳戶 + 模擬撮合引擎

V4.5 修正：
- 20 萬帳戶不適合用整張 1000 股回測，否則多數股票永遠買不起。
- 為了不改既有資料庫欄位，本版把 Position.lots / TradeLog.lots 視為「股數 shares」。
- 請在套用本版後重設策略帳戶，避免舊版「張數」資料和新版「股數」混在一起。
"""
from datetime import date, datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.models.database import (
    SessionLocal, StrategyAccount, Position, TradeLog, EquityCurve
)
from config.settings import settings


class OrderResult:
    def __init__(self, ok: bool, msg: str, pnl: float = 0.0):
        self.ok = ok
        self.msg = msg
        self.pnl = pnl


class PaperAccount:
    """
    單一策略帳戶的撮合 + 持倉管理。

    注意：資料表欄位仍叫 lots，但 V4.5 起策略帳戶一律以「股」為單位，
    也就是 lots 欄位實際儲存 shares。這樣 20 萬帳戶才能做零股回測。
    """

    def __init__(self, account_id: int):
        self.account_id = account_id

    def _get_account(self, db: Session) -> StrategyAccount:
        acc = db.query(StrategyAccount).filter_by(id=self.account_id).first()
        if acc is None:
            raise ValueError(f"Account {self.account_id} not found")
        return acc

    def _get_position(self, db: Session, code: str) -> Optional[Position]:
        return db.query(Position).filter_by(
            account_id=self.account_id, code=code
        ).first()

    @staticmethod
    def _to_trade_date(x) -> date:
        if isinstance(x, datetime):
            return x.date()
        if isinstance(x, date):
            return x
        if x is None:
            return date.today()
        return date.fromisoformat(str(x)[:10])

    def buy(self, code: str, lots: int, price: float,
            trigger: str = "", trade_date: date = None) -> OrderResult:
        """買入 shares 股。參數名 lots 是舊 schema 名稱，實際代表股數。"""
        shares = int(lots or 0)
        if shares <= 0 or price <= 0:
            return OrderResult(False, "參數錯誤")

        trade_date = self._to_trade_date(trade_date)
        gross = price * shares
        fee = max(1, round(gross * settings.TRADE_FEE_RATE)) if gross > 0 else 0
        net = gross + fee

        db = SessionLocal()
        try:
            acc = self._get_account(db)
            if acc.cash < net:
                return OrderResult(False, f"現金不足（需 {net:,.0f}，餘 {acc.cash:,.0f}）")

            acc.cash -= net

            pos = self._get_position(db, code)
            if pos is None:
                pos = Position(account_id=self.account_id, code=code,
                               lots=0, avg_cost=0.0)
                db.add(pos)

            old_shares = int(pos.lots or 0)
            old_cost = (pos.avg_cost or 0.0) * old_shares
            pos.lots = old_shares + shares
            pos.avg_cost = (old_cost + gross) / pos.lots if pos.lots else 0.0

            log = TradeLog(
                account_id=self.account_id, code=code,
                direction="BUY", lots=shares, price=price,
                fee=fee, tax=0, net_amount=round(net, 2), pnl=0,
                trigger=trigger, trade_date=trade_date,
            )
            db.add(log)
            db.commit()
            logger.info(f"[PAPER][{self.account_id}] BUY {code} {shares}股 @{price} fee={fee}")
            return OrderResult(True, f"買入 {code} {shares}股 @{price:.2f}")
        except Exception as e:
            db.rollback()
            logger.error(f"[PAPER] buy error: {e}")
            return OrderResult(False, str(e))
        finally:
            db.close()

    def sell(self, code: str, lots: int, price: float,
             trigger: str = "", trade_date: date = None) -> OrderResult:
        """賣出 shares 股。參數名 lots 是舊 schema 名稱，實際代表股數。"""
        shares = int(lots or 0)
        if shares <= 0 or price <= 0:
            return OrderResult(False, "參數錯誤")

        trade_date = self._to_trade_date(trade_date)
        db = SessionLocal()
        try:
            acc = self._get_account(db)
            pos = self._get_position(db, code)
            held = int(pos.lots or 0) if pos else 0
            if pos is None or held < shares:
                return OrderResult(False, f"持股不足（持 {held}股，賣 {shares}股）")

            gross = price * shares
            fee = max(1, round(gross * settings.TRADE_FEE_RATE)) if gross > 0 else 0
            tax = round(gross * settings.TRADE_TAX_RATE)
            net_in = gross - fee - tax
            pnl = (price - (pos.avg_cost or 0.0)) * shares - fee - tax

            acc.cash += net_in
            pos.lots = held - shares
            if pos.lots == 0:
                db.delete(pos)

            log = TradeLog(
                account_id=self.account_id, code=code,
                direction="SELL", lots=shares, price=price,
                fee=fee, tax=tax, net_amount=round(net_in, 2), pnl=round(pnl, 2),
                trigger=trigger, trade_date=trade_date,
            )
            db.add(log)
            db.commit()
            logger.info(f"[PAPER][{self.account_id}] SELL {code} {shares}股 @{price} PnL={pnl:.0f}")
            return OrderResult(True, f"賣出 {code} {shares}股 @{price:.2f}，損益 {pnl:+,.0f}", pnl)
        except Exception as e:
            db.rollback()
            logger.error(f"[PAPER] sell error: {e}")
            return OrderResult(False, str(e))
        finally:
            db.close()

    def snapshot_equity(self, price_map: dict[str, float], snap_date: date = None):
        """每日收盤後快照權益曲線。"""
        snap_date = self._to_trade_date(snap_date)
        db = SessionLocal()
        try:
            acc = self._get_account(db)
            positions = db.query(Position).filter_by(account_id=self.account_id).all()

            mktval = sum(
                int(pos.lots or 0) * price_map.get(pos.code, pos.avg_cost or 0.0)
                for pos in positions
            )
            total = acc.cash + mktval

            prev = db.query(EquityCurve).filter_by(
                account_id=self.account_id
            ).order_by(EquityCurve.snap_date.desc()).first()
            prev_total = prev.total_equity if prev else acc.initial_cash
            daily_ret = (total - prev_total) / prev_total if prev_total else 0

            stmt = sqlite_insert(EquityCurve).values(
                account_id=self.account_id,
                snap_date=snap_date,
                cash=round(acc.cash, 2),
                market_value=round(mktval, 2),
                total_equity=round(total, 2),
                daily_return=round(daily_ret, 6),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["account_id", "snap_date"],
                set_={"cash": stmt.excluded.cash,
                      "market_value": stmt.excluded.market_value,
                      "total_equity": stmt.excluded.total_equity,
                      "daily_return": stmt.excluded.daily_return}
            )
            db.execute(stmt)
            db.commit()
        finally:
            db.close()

    def get_summary(self) -> dict:
        """帳戶摘要（REST API 用）。"""
        db = SessionLocal()
        try:
            acc = self._get_account(db)
            positions = db.query(Position).filter_by(account_id=self.account_id).all()
            curve = db.query(EquityCurve).filter_by(
                account_id=self.account_id
            ).order_by(EquityCurve.snap_date.desc()).first()

            total = curve.total_equity if curve else acc.cash
            pnl = total - acc.initial_cash
            ret = pnl / acc.initial_cash if acc.initial_cash else 0

            recent = db.query(EquityCurve.daily_return).filter_by(
                account_id=self.account_id
            ).order_by(EquityCurve.snap_date.desc()).limit(30).all()
            rets = [r[0] for r in recent if r[0] is not None]
            import numpy as np
            sharpe = (np.mean(rets) / (np.std(rets) + 1e-9) * (252**0.5)) if rets else 0

            return {
                "account_id": acc.id,
                "name": acc.name,
                "strategy_type": acc.strategy_type,
                "cash": round(acc.cash, 2),
                "market_value": round(total - acc.cash, 2),
                "total_equity": round(total, 2),
                "pnl": round(pnl, 2),
                "return_pct": round(ret * 100, 4),
                "sharpe": round(sharpe, 4),
                "position_count": len(positions),
                "start_date": str(acc.start_date),
                "unit": "shares",
            }
        finally:
            db.close()
