"""
backend/models/database.py  –  所有 ORM 模型 + DB 初始化
"""
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Date, DateTime, Boolean, ForeignKey, Text, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from config.settings import settings
import os

os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
engine = create_engine(
    settings.DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False
)

# WAL mode：允許讀寫並發，解決 database is locked
from sqlalchemy import event
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ══════════════════════════════════════════════
# 行情資料
# ══════════════════════════════════════════════

class StockMeta(Base):
    """股票基本資料"""
    __tablename__ = "stock_meta"
    id          = Column(Integer, primary_key=True)
    code        = Column(String(10), unique=True, nullable=False, index=True)
    name        = Column(String(50), nullable=False)
    market      = Column(String(10), default="TWSE")   # TWSE / OTC
    industry    = Column(String(30))
    listing_date= Column(Date)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class OHLCVDaily(Base):
    """日K資料"""
    __tablename__ = "ohlcv_daily"
    __table_args__ = (
        UniqueConstraint("code", "trade_date", name="uq_daily"),
        Index("ix_daily_code_date", "code", "trade_date"),
    )
    id          = Column(Integer, primary_key=True)
    code        = Column(String(10), nullable=False)
    trade_date  = Column(Date, nullable=False)
    open        = Column(Float)
    high        = Column(Float)
    low         = Column(Float)
    close       = Column(Float)
    volume      = Column(Float)      # 成交量（股，TWSE 原始資料；前端顯示張數需 /1000）
    value       = Column(Float)      # 成交金額（千元）
    change      = Column(Float)      # 漲跌
    change_pct  = Column(Float)      # 漲跌幅
    turnover    = Column(Float)      # 週轉率


class OHLCV1Min(Base):
    """分K資料（盤中）"""
    __tablename__ = "ohlcv_1min"
    __table_args__ = (
        UniqueConstraint("code", "ts", name="uq_1min"),
        Index("ix_1min_code_ts", "code", "ts"),
    )
    id          = Column(Integer, primary_key=True)
    code        = Column(String(10), nullable=False)
    ts          = Column(DateTime, nullable=False)   # 2025-01-02 09:01:00
    open        = Column(Float)
    high        = Column(Float)
    low         = Column(Float)
    close       = Column(Float)
    volume      = Column(Float)
    buy_vol     = Column(Float)      # 外盤量
    sell_vol    = Column(Float)      # 內盤量


# ══════════════════════════════════════════════
# 籌碼 / 基本面資料
# ══════════════════════════════════════════════

class ChipDaily(Base):
    """三大法人 + 融資融券"""
    __tablename__ = "chip_daily"
    __table_args__ = (UniqueConstraint("code", "trade_date"),)
    id              = Column(Integer, primary_key=True)
    code            = Column(String(10), nullable=False)
    trade_date      = Column(Date, nullable=False)
    foreign_net     = Column(Float)   # 外資買超（張）
    trust_net       = Column(Float)   # 投信買超
    dealer_net      = Column(Float)   # 自營商買超
    margin_balance  = Column(Float)   # 融資餘額
    short_balance   = Column(Float)   # 融券餘額
    margin_ratio    = Column(Float)   # 融資使用率
    short_ratio     = Column(Float)   # 融券使用率
    borrow_balance  = Column(Float)   # 借券餘額


class MonthlyRevenue(Base):
    """月營收"""
    __tablename__ = "monthly_revenue"
    __table_args__ = (UniqueConstraint("code", "year", "month"),)
    id             = Column(Integer, primary_key=True)
    code           = Column(String(10), nullable=False)
    year           = Column(Integer)
    month          = Column(Integer)
    published_date = Column(Date)         # 保守估計：次月 15 日才視為可用，避免回測偷看未來
    revenue        = Column(Float)        # 千元
    mom_pct        = Column(Float)        # 月增率
    yoy_pct        = Column(Float)        # 年增率
    accumulated    = Column(Float)        # 累計營收


class Fundamental(Base):
    """季報 / 財報資料"""
    __tablename__ = "fundamental"
    __table_args__ = (UniqueConstraint("code", "year", "quarter"),)
    id          = Column(Integer, primary_key=True)
    code        = Column(String(10), nullable=False)
    year        = Column(Integer)
    quarter     = Column(Integer)
    eps         = Column(Float)
    roe         = Column(Float)
    roa         = Column(Float)
    gross_margin= Column(Float)
    op_margin   = Column(Float)
    net_margin  = Column(Float)
    debt_ratio  = Column(Float)
    current_ratio=Column(Float)
    pe          = Column(Float)       # 本益比（填入時）
    pb          = Column(Float)


class NewsEvent(Base):
    """新聞 / 法說會 / 事件"""
    __tablename__ = "news_events"
    id          = Column(Integer, primary_key=True)
    code        = Column(String(10))   # 可 NULL（大盤事件）
    event_date  = Column(Date, nullable=False)
    event_type  = Column(String(20))   # news / investor_conf / macro
    title       = Column(Text)
    sentiment   = Column(Float)        # -1~+1 情緒分
    importance  = Column(Float)        # 0~1 重要性權重
    source      = Column(String(50))
    url         = Column(String(500))


# ══════════════════════════════════════════════
# 分數體系
# ══════════════════════════════════════════════

class DailyScore(Base):
    """每日綜合分數快取"""
    __tablename__ = "daily_scores"
    __table_args__ = (UniqueConstraint("code", "score_date"),)
    id              = Column(Integer, primary_key=True)
    code            = Column(String(10), nullable=False, index=True)
    score_date      = Column(Date, nullable=False)
    fundamental_score = Column(Float)   # 0-100
    valuation_score = Column(Float)
    chip_score      = Column(Float)
    momentum_score  = Column(Float)
    macro_score     = Column(Float)
    news_score      = Column(Float)
    composite_score = Column(Float)     # 加權綜合
    signal          = Column(String(10))  # BUY / SELL / HOLD


# ══════════════════════════════════════════════
# 策略帳戶系統
# ══════════════════════════════════════════════

class StrategyAccount(Base):
    """策略帳戶"""
    __tablename__ = "strategy_accounts"
    id              = Column(Integer, primary_key=True)
    name            = Column(String(100), unique=True, nullable=False)
    description     = Column(Text)
    strategy_type   = Column(String(20))  # rule_based / ml_based
    strategy_class  = Column(String(100)) # e.g. "MomentumBreakout"
    params          = Column(JSON)         # 策略參數 JSON
    weights         = Column(JSON)         # 分數權重覆蓋
    initial_cash    = Column(Float, default=1_000_000)
    cash            = Column(Float, default=1_000_000)
    is_active       = Column(Boolean, default=True)
    start_date      = Column(Date)
    end_date        = Column(Date)          # 競賽截止日
    created_at      = Column(DateTime, default=datetime.utcnow)
    positions       = relationship("Position", back_populates="account", cascade="all, delete-orphan")
    trades          = relationship("TradeLog", back_populates="account", cascade="all, delete-orphan")


class Position(Base):
    """策略帳戶持倉"""
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "code"),)
    id          = Column(Integer, primary_key=True)
    account_id  = Column(Integer, ForeignKey("strategy_accounts.id"), nullable=False)
    code        = Column(String(10), nullable=False)
    lots        = Column(Integer, default=0)   # 持有張數
    avg_cost    = Column(Float)                # 平均成本（元/股）
    opened_at   = Column(DateTime, default=datetime.utcnow)
    account     = relationship("StrategyAccount", back_populates="positions")


class TradeLog(Base):
    """交易紀錄"""
    __tablename__ = "trade_logs"
    id          = Column(Integer, primary_key=True)
    account_id  = Column(Integer, ForeignKey("strategy_accounts.id"), nullable=False)
    code        = Column(String(10), nullable=False)
    direction   = Column(String(5))    # BUY / SELL
    lots        = Column(Integer)
    price       = Column(Float)
    fee         = Column(Float)
    tax         = Column(Float)
    net_amount  = Column(Float)        # 含手續費稅後金額
    pnl         = Column(Float)        # 賣出才有意義
    trigger     = Column(String(200))  # 觸發原因說明
    ts          = Column(DateTime, default=datetime.utcnow)
    trade_date  = Column(Date)
    account     = relationship("StrategyAccount", back_populates="trades")


class EquityCurve(Base):
    """每日權益曲線快照"""
    __tablename__ = "equity_curve"
    __table_args__ = (UniqueConstraint("account_id", "snap_date"),)
    id          = Column(Integer, primary_key=True)
    account_id  = Column(Integer, ForeignKey("strategy_accounts.id"))
    snap_date   = Column(Date, nullable=False)
    cash        = Column(Float)
    market_value= Column(Float)        # 持股市值
    total_equity= Column(Float)        # 總資產
    daily_return= Column(Float)        # 當日報酬率


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
