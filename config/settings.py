"""
config/settings.py
所有設定集中管理，支援 .env 覆蓋
"""
from pydantic_settings import BaseSettings
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    # ── App ────────────────────────────────────
    APP_NAME: str = "TWSE Quant Platform"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── Database ───────────────────────────────
    DB_PATH: str = str(BASE_DIR / "data" / "db" / "quant.db")
    DB_URL: str = ""  # auto-built from DB_PATH if empty

    def model_post_init(self, __context):
        if not self.DB_URL:
            object.__setattr__(self, "DB_URL", f"sqlite:///{self.DB_PATH}")

    # ── Data paths ─────────────────────────────
    RAW_DATA_DIR: str = str(BASE_DIR / "data" / "raw")
    PROCESSED_DATA_DIR: str = str(BASE_DIR / "data" / "processed")

    # ── TWSE API ───────────────────────────────
    TWSE_BASE_URL: str = "https://www.twse.com.tw/exchangeReport"
    MOPS_BASE_URL: str = "https://mops.twse.com.tw/mops/web"
    TWSE_INTRADAY_URL: str = "https://mis.twse.com.tw/stock/api"
    REQUEST_DELAY_SEC: float = 1.5   # 官方限流保護，請勿低於 1.0

    # ── Scheduler ──────────────────────────────
    EOD_CRON_HOUR: int = 21
    EOD_CRON_MINUTE: int = 0
    INTRADAY_START: str = "09:00"
    INTRADAY_END: str = "13:35"
    INTRADAY_INTERVAL_SEC: int = 60  # 每分鐘

    # ── Signal weights (各策略可覆蓋) ──────────
    DEFAULT_WEIGHTS: dict = {
        "fundamental": 0.20,
        "valuation":   0.15,
        "chip":        0.25,
        "momentum":    0.20,
        "macro":       0.10,
        "news":        0.10,
    }

    # ── Paper trading defaults ─────────────────
    INITIAL_CASH: float = 1_000_000.0
    TRADE_FEE_RATE: float = 0.001425   # 買入手續費 0.1425%
    TRADE_TAX_RATE: float = 0.003      # 賣出證交稅 0.3%
    MIN_LOT: int = 1                   # 最小單位：1張 = 1000股

    # ── Competition ────────────────────────────
    COMPETITION_DAYS: int = 30

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"


settings = Settings()
