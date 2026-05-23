"""
config/capital_config.py
V3-FIX-15：資金設定 + V3-FIX-10：輔助看盤模式
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class CapitalConfig:
    # ── 總資金分配 ──
    total_capital: float = 200_000
    core_etf_ratio: float = 0.50          # 50% = 10萬放 0050
    active_trading_ratio: float = 0.50    # 50% = 10萬短線
    core_etf_codes: List[str] = field(default_factory=lambda: ["0050"])

    # ── 目標 ──
    monthly_total_return_target: float = 0.10        # 月總報酬目標 10%
    active_monthly_return_target: float = 0.20       # 短線部位月報酬目標 20%

    # ── 風控 ──
    max_single_trade_loss_ratio: float = 0.01        # 單筆最大虧損佔總資產 1%
    max_single_trade_loss_amount: float = 2_000      # 單筆最大虧損金額 2000元
    max_active_position_ratio: float = 0.50          # 短線總部位上限 50%
    min_cash_ratio: float = 0.10                     # 最低現金比例 10%
    high_risk_max_loss_amount: float = 1_000         # 高風險市場單筆上限降至 1000元

    # ── 預設進出場參數 ──
    default_stop_loss_pct: float = 0.08              # 預設停損 -8%
    default_target_pct_1: float = 0.10               # 目標價1 +10%
    default_target_pct_2: float = 0.15               # 目標價2 +15%
    min_risk_reward_ratio: float = 1.5               # 最低風報比 1.5

    # ── 交易模式 ──
    mode: str = "assistive"                          # 輔助看盤模式
    future_mode: str = "semi_auto_confirm_before_order"
    allow_auto_order: bool = False                   # 不允許自動下單
    require_user_confirmation: bool = True           # 必須使用者確認

    @property
    def core_capital(self) -> float:
        return self.total_capital * self.core_etf_ratio

    @property
    def active_capital(self) -> float:
        return self.total_capital * self.active_trading_ratio

    def max_loss_for_market(self, risk_level: str = "medium") -> float:
        if risk_level == "high":
            return self.high_risk_max_loss_amount
        return self.max_single_trade_loss_amount

    def calc_suggested_shares(
        self,
        entry_price: float,
        stop_loss_price: float,
        risk_level: str = "medium",
        lot_size: int = 1,
    ) -> dict:
        """
        根據停損距離反推建議股數（以單筆最大虧損為上限）
        """
        if entry_price <= 0 or stop_loss_price <= 0:
            return {"shares": 0, "amount": 0, "max_loss": 0, "reason": "價格異常"}

        loss_per_share = entry_price - stop_loss_price
        if loss_per_share <= 0:
            return {"shares": 0, "amount": 0, "max_loss": 0, "reason": "停損價高於進場價"}

        max_loss = self.max_loss_for_market(risk_level)
        max_shares_by_risk = int(max_loss / loss_per_share)

        # 短線資金上限
        active = self.active_capital * (1 - self.min_cash_ratio)
        max_shares_by_capital = int(active / entry_price)

        suggested_shares = min(max_shares_by_risk, max_shares_by_capital)
        suggested_shares = max(1, suggested_shares)

        amount = suggested_shares * entry_price
        actual_max_loss = suggested_shares * loss_per_share

        reason = f"停損距離 {loss_per_share:.2f}，最大虧損 {actual_max_loss:.0f} 元"
        if actual_max_loss > max_loss * 1.05:
            reason += "（超過上限，已縮減）"

        return {
            "shares": suggested_shares,
            "amount": round(amount, 0),
            "max_loss": round(actual_max_loss, 0),
            "loss_per_share": round(loss_per_share, 2),
            "reason": reason,
        }

    def is_core_etf(self, code: str) -> bool:
        return code in self.core_etf_codes

    def summary(self) -> dict:
        return {
            "mode": self.mode,
            "allow_auto_order": self.allow_auto_order,
            "require_user_confirmation": self.require_user_confirmation,
            "total_capital": self.total_capital,
            "core_capital": self.core_capital,
            "active_capital": self.active_capital,
            "core_etf_codes": self.core_etf_codes,
            "monthly_target": f"{self.monthly_total_return_target*100:.0f}%",
            "active_monthly_target": f"{self.active_monthly_return_target*100:.0f}%",
            "max_single_loss": self.max_single_trade_loss_amount,
            "stop_loss_pct": f"{self.default_stop_loss_pct*100:.0f}%",
            "target_pct_1": f"{self.default_target_pct_1*100:.0f}%",
            "target_pct_2": f"{self.default_target_pct_2*100:.0f}%",
            "warning": "月報酬10%是高風險目標，短線部位需達20%才能達成，風控優先於追求報酬",
        }


# 全域單例
CAPITAL_CONFIG = CapitalConfig()
