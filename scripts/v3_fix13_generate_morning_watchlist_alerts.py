"""scripts/v3_fix13_generate_morning_watchlist_alerts.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from backend.v3.candidate_trade_plans import generate_daily_plans
from backend.v3.watchlist_alerts import generate_morning_alerts

def main():
    today = date.today()
    print(f"=== 早晨候選清單提醒 {today} ===\n")
    print("Step 1: 生成交易計畫...")
    plans = generate_daily_plans(today)
    print(f"  → {len(plans)} 個交易計畫")

    print("Step 2: 生成看盤提醒...")
    alerts = generate_morning_alerts(today)
    print(f"  → {len(alerts)} 個提醒\n")

    buy   = [a for a in alerts if a["alert_type"]=="BUY_WATCH"]
    watch = [a for a in alerts if a["alert_type"]=="HOLD_WATCH"]
    avoid = [a for a in alerts if a["alert_type"]=="DO_NOT_CHASE"]

    print(f"✅ 買入觀察: {len(buy)} 檔")
    for a in buy[:5]:
        print(f"   {a['code']} {a['name']} 進場{a['entry_price_low']}~{a['entry_price_high']} "
              f"停損{a['stop_loss_price']} 目標{a['target_price_1']} 風報{a['risk_reward_ratio']}")

    print(f"👀 持續觀察: {len(watch)} 檔")
    print(f"⚠️  不可追高: {len(avoid)} 檔")
    print(f"\n📄 報告輸出至 data/reports/morning_watchlist_alerts_{today}.md")
    print(f"⚠️  本報告僅供輔助看盤，請自行確認後再下單")

if __name__ == "__main__":
    main()
