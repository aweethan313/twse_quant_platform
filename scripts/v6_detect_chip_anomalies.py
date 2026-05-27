"""scripts/v6_detect_chip_anomalies.py - 籌碼異動警報"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal


def detect_chip_anomalies(target_date: date = None):
    if not target_date: target_date = date.today()
    db = SessionLocal()
    total = 0

    try:
        # 取今日籌碼
        chips = db.execute(text("""
            SELECT c.code, sm.name,
                   c.foreign_buy, c.foreign_sell,
                   c.trust_buy, c.trust_sell,
                   c.dealer_buy, c.dealer_sell,
                   c.foreign_net, c.trust_net
            FROM chip_daily c
            LEFT JOIN stock_meta sm ON sm.code=c.code
            WHERE c.trade_date=:d
        """), {"d": str(target_date)}).fetchall()

        if not chips:
            print(f"[CHIP] {target_date} 無籌碼資料")
            return 0

        # 刪除今日舊警報
        db.execute(text("DELETE FROM chip_anomaly_alerts WHERE trade_date=:d"),
                   {"d": str(target_date)})

        alerts = []
        for row in chips:
            code, name = row[0], row[1]
            f_buy, f_sell = float(row[2] or 0), float(row[3] or 0)
            t_buy, t_sell = float(row[4] or 0), float(row[5] or 0)
            f_net = float(row[8] or 0)
            t_net = float(row[9] or 0)

            # 取近20日籌碼
            hist = db.execute(text("""
                SELECT trade_date, foreign_net, trust_net
                FROM chip_daily WHERE code=:c AND trade_date < :d
                ORDER BY trade_date DESC LIMIT 20
            """), {"c": code, "d": str(target_date)}).fetchall()

            f_nets = [float(r[1] or 0) for r in hist]
            t_nets = [float(r[2] or 0) for r in hist]

            # 外資連買天數
            f_streak = 0
            for fn in f_nets:
                if fn > 0: f_streak += 1
                else: break

            # 投信連買天數
            t_streak = 0
            for tn in t_nets:
                if tn > 0: t_streak += 1
                else: break

            # 外資20日最大買超
            f_max_20 = max(f_nets) if f_nets else 0

            def add_alert(alert_type, investor_type, value, streak, severity, reason):
                alerts.append({
                    "trade_date": str(target_date), "code": code, "name": name,
                    "alert_type": alert_type, "investor_type": investor_type,
                    "buy_sell_value": value, "streak_days": streak,
                    "severity": severity, "reason": reason,
                })

            # 1. 外資連買 3+ 天
            if f_net > 0 and f_streak >= 2:
                sev = "STRONG" if f_streak >= 5 else "WATCH"
                add_alert("FOREIGN_CONSECUTIVE_BUY", "外資", f_net, f_streak + 1,
                          sev, f"外資連買{f_streak+1}天 今日+{f_net:.0f}張")

            # 2. 外資由賣轉買（連賣後突然買）
            if f_net > 0 and len(f_nets) >= 3 and all(n < 0 for n in f_nets[:3]):
                add_alert("FOREIGN_REVERSAL_BUY", "外資", f_net, 0,
                          "WATCH", f"外資由連賣轉買 +{f_net:.0f}張")

            # 3. 投信連買 3+ 天
            if t_net > 0 and t_streak >= 2:
                sev = "STRONG" if t_streak >= 5 else "WATCH"
                add_alert("TRUST_CONSECUTIVE_BUY", "投信", t_net, t_streak + 1,
                          sev, f"投信連買{t_streak+1}天 今日+{t_net:.0f}張")

            # 4. 三大法人同步買超
            dealer_net = float(row[6] or 0) - float(row[7] or 0)
            if f_net > 0 and t_net > 0 and dealer_net > 0:
                add_alert("THREE_PARTY_BUY", "三大法人", f_net+t_net+dealer_net, 0,
                          "STRONG", f"三大法人同步買超 外資+{f_net:.0f} 投信+{t_net:.0f}張")

            # 5. 外資大賣 warning
            if f_net < -500:
                add_alert("FOREIGN_HEAVY_SELL", "外資", f_net, 0,
                          "RISK", f"外資大賣 {f_net:.0f}張")

            # 6. 外資買超創20日高
            if f_net > 0 and f_max_20 > 0 and f_net >= f_max_20 * 1.5:
                add_alert("FOREIGN_BUY_HIGH", "外資", f_net, 0,
                          "WATCH", f"外資買超創近20日高 +{f_net:.0f}張")

        # 批次寫入
        for a in alerts:
            db.execute(text("""
                INSERT INTO chip_anomaly_alerts
                    (trade_date, code, stock_name, alert_type, investor_type,
                     buy_sell_value, streak_days, severity, reason_summary)
                VALUES (:d,:c,:n,:at,:it,:v,:s,:sev,:r)
            """), {
                "d": a["trade_date"], "c": a["code"], "n": a["name"],
                "at": a["alert_type"], "it": a["investor_type"],
                "v": a["buy_sell_value"], "s": a["streak_days"],
                "sev": a["severity"], "r": a["reason"],
            })
            total += 1

        db.commit()
        print(f"[CHIP] {target_date} 偵測到 {total} 個籌碼異動")

        # 印出 STRONG/RISK
        strong = [a for a in alerts if a["severity"] in ("STRONG","RISK")]
        for a in strong[:10]:
            icon = "🔥" if a["severity"]=="STRONG" else "⚠️"
            print(f"  {icon} {a['code']} {a['name']:10} {a['reason']}")

        return total
    finally:
        db.close()

if __name__ == "__main__":
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    detect_chip_anomalies(d)
