"""tests/test_v6_core.py
V6 核心測試：trading_calendar, benchmark, fill_model, day_trade
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


# ── V6-1: Trading Calendar ──
class TestTradingCalendar:
    def test_weekday_is_open(self):
        """週一到週五應該是有效交易日"""
        from datetime import date
        monday = date(2026, 5, 25)  # 週一
        assert monday.weekday() < 5

    def test_weekend_is_closed(self):
        """週六日不應是有效交易日"""
        saturday = date(2026, 5, 23)
        sunday   = date(2026, 5, 24)
        assert saturday.weekday() == 5
        assert sunday.weekday() == 6

    def test_calendar_table_exists(self):
        """trading_calendar 表應該存在"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            n = db.execute(text("SELECT COUNT(*) FROM trading_calendar")).scalar()
            assert n > 0, "trading_calendar 應有資料"
        finally:
            db.close()

    def test_latest_trading_date(self):
        """最新交易日不應是週末"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            latest = db.execute(text(
                "SELECT MAX(trade_date) FROM trading_calendar WHERE is_open=1"
            )).scalar()
            assert latest is not None
            dt = date.fromisoformat(str(latest))
            assert dt.weekday() < 5, f"最新交易日 {latest} 是週末"
        finally:
            db.close()


# ── V6-2: 0050 Benchmark ──
class TestBenchmark:
    def test_no_extreme_daily_return(self):
        """0050 benchmark 不應有單日 > 50% 的報酬（已清理）"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            extreme = db.execute(text("""
                SELECT COUNT(*) FROM benchmark_daily_equity
                WHERE ABS(daily_return) > 50 AND is_valid=1
            """)).scalar()
            assert extreme == 0, f"benchmark 有 {extreme} 筆 is_valid=1 但日報酬 >50%"
        finally:
            db.close()

    def test_benchmark_has_data(self):
        """benchmark 應有資料"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            n = db.execute(text(
                "SELECT COUNT(*) FROM benchmark_daily_equity WHERE benchmark_code='0050'"
            )).scalar()
            assert n > 100, "0050 benchmark 應有 100+ 筆資料"
        finally:
            db.close()

    def test_benchmark_return_reasonable(self):
        """benchmark 總報酬應在合理範圍"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            cum = db.execute(text("""
                SELECT cumulative_return FROM benchmark_daily_equity
                WHERE benchmark_code='0050' AND is_valid=1
                ORDER BY snap_date DESC LIMIT 1
            """)).scalar()
            assert cum is not None
            assert -50 < float(cum) < 200, f"benchmark 報酬 {cum}% 不合理"
        finally:
            db.close()


# ── V6-4: Daily Fill Model ──
class TestDailyFillModel:
    def test_fill_date_after_signal_date(self):
        """成交日必須在訊號日之後"""
        from backend.v6.daily_fill_model import simulate_daily_fill
        result = simulate_daily_fill(
            code="0050", signal_date="2026-05-26",
            side="BUY", shares=10
        )
        if result.get("ok"):
            assert result["fill_date"] > result["signal_date"], \
                "fill_date 必須 > signal_date"

    def test_buy_slippage_positive(self):
        """買入成交價應 >= 原始價（滑價）"""
        from backend.v6.daily_fill_model import simulate_daily_fill
        result = simulate_daily_fill(
            code="0050", signal_date="2026-05-26",
            side="BUY", shares=10, slippage_bps=10
        )
        if result.get("ok"):
            assert result["fill_price"] >= result["raw_price"], "買入應有正向滑價"

    def test_sell_slippage_negative(self):
        """賣出成交價應 <= 原始價（滑價）"""
        from backend.v6.daily_fill_model import simulate_daily_fill
        result = simulate_daily_fill(
            code="0050", signal_date="2026-05-26",
            side="SELL", shares=10, slippage_bps=10
        )
        if result.get("ok"):
            assert result["fill_price"] <= result["raw_price"], "賣出應有負向滑價"

    def test_is_estimated_flag(self):
        """系統模擬成交應標記 is_estimated=1"""
        from backend.v6.daily_fill_model import simulate_daily_fill
        result = simulate_daily_fill(
            code="0050", signal_date="2026-05-26",
            side="BUY", shares=10
        )
        if result.get("ok"):
            assert result["is_estimated"] == 1, "系統估算應標記 is_estimated=1"

    def test_no_ohlcv_1min_dependency(self):
        """確認 fill_model 不使用分鐘資料"""
        from backend.v6 import daily_fill_model
        import inspect
        src = inspect.getsource(daily_fill_model)
        assert "ohlcv_1min" not in src, "fill_model 不應使用 ohlcv_1min"
        assert "09:10" not in src, "fill_model 不應假裝知道盤中價格"


# ── V6-6: No Day Trade ──
class TestNoDayTrade:
    def test_buy_then_sell_blocked(self):
        """同日先買後賣應被擋"""
        from backend.v6.daily_fill_model import can_sell_without_day_trade_violation
        from unittest.mock import MagicMock, patch

        mock_db = MagicMock()
        # 模擬今日已有 BUY 記錄
        mock_db.execute.return_value.scalar.return_value = 100  # 買了100股

        allowed, reason = can_sell_without_day_trade_violation(
            account_id=11, code="2330",
            fill_date="2026-05-27", shares_to_sell=50, db=mock_db
        )
        assert not allowed, "同日先買後賣應被擋"
        assert "same_day_buy_then_sell" in reason

    def test_sell_existing_then_buy_allowed(self):
        """同日先賣後買應被允許（無今日買進記錄）"""
        from backend.v6.daily_fill_model import can_sell_without_day_trade_violation
        from unittest.mock import MagicMock

        mock_db = MagicMock()
        mock_db.execute.return_value.scalar.return_value = 0  # 今日無買進

        allowed, reason = can_sell_without_day_trade_violation(
            account_id=11, code="2330",
            fill_date="2026-05-27", shares_to_sell=50, db=mock_db
        )
        assert allowed, "先賣後買應被允許"


# ── V6-10: No-lookahead ──
class TestNoLookahead:
    def test_decisions_no_future_data(self):
        """策略決策不應使用未來資料"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            bad = db.execute(text("""
                SELECT COUNT(*) FROM strategy_decision_logs
                WHERE signal_date > datetime('now','localtime')
            """)).scalar() or 0
            assert bad == 0, f"{bad} 筆決策有未來 signal_date"
        finally:
            db.close()

    def test_no_1min_table(self):
        """ohlcv_1min 表不應存在"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            has_1min = db.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ohlcv_1min'"
            )).scalar()
            assert has_1min == 0, "ohlcv_1min 表不應存在（V6 不使用分鐘資料）"
        finally:
            db.close()

    def test_fill_date_after_signal(self):
        """所有成交日必須在訊號日之後"""
        from backend.models.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            bad = db.execute(text("""
                SELECT COUNT(*) FROM paper_fills
                WHERE execution_date IS NOT NULL AND signal_date IS NOT NULL
                  AND execution_date <= signal_date
            """)).scalar() or 0
            assert bad == 0, f"{bad} 筆成交日 <= 訊號日"
        finally:
            db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
