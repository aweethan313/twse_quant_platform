"""v5d_patch.py - V5D 完整修復"""
import subprocess

# ════════════════════════════════════
# 1. 刪除 S1~S5 舊策略帳戶
# ════════════════════════════════════
print("=== Step 1: 刪除舊策略帳戶 S1~S5 ===")
from backend.models.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()
try:
    # 列出要刪的帳戶
    old_accts = db.execute(text(
        "SELECT id, name FROM strategy_accounts WHERE id <= 7"
    )).fetchall()
    print(f"找到舊帳戶: {[(r[0], r[1]) for r in old_accts]}")

    for aid, aname in old_accts:
        # 刪除相關資料
        db.execute(text("DELETE FROM equity_curve WHERE account_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM positions WHERE account_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM strategy_decision_logs WHERE account_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM strategy_leaderboard WHERE account_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM strategy_kill_switch_status WHERE strategy_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM strategy_router_decisions WHERE strategy_id=:id"), {"id": aid})
        db.execute(text("DELETE FROM strategy_accounts WHERE id=:id"), {"id": aid})
        print(f"  ✓ 刪除 S{aid} {aname}")

    db.commit()
    print(f"✅ 刪除完成")
except Exception as e:
    db.rollback()
    print(f"❌ 刪除失敗: {e}")
finally:
    db.close()


# ════════════════════════════════════
# 2. A16 0050 Core+Satellite 初始化買入 0050
# ════════════════════════════════════
print("\n=== Step 2: A16 初始化買入 0050 ===")
db = SessionLocal()
try:
    # 確認 A16 存在且未持有 0050
    a16 = db.execute(text("SELECT id, cash FROM strategy_accounts WHERE id=16")).fetchone()
    if a16:
        has_0050 = db.execute(text(
            "SELECT id FROM positions WHERE account_id=16 AND code='0050'"
        )).fetchone()

        if not has_0050:
            cash = float(a16[1] or 200000)
            target_amount = cash * 0.50  # 50% 買 0050
            price_row = db.execute(text(
                "SELECT close FROM ohlcv_daily WHERE code='0050' ORDER BY trade_date DESC LIMIT 1"
            )).fetchone()

            if price_row:
                price = float(price_row[0])
                shares = int(target_amount / price)
                cost = shares * price * 1.001425 * 0.38  # 含手續費
                total_cost = shares * price + max(20, round(shares * price * 0.001425 * 0.38, 0))

                db.execute(text("""
                    INSERT INTO positions (account_id, code, lots, avg_cost, opened_at)
                    VALUES (16, '0050', :shares, :price, datetime('now','localtime'))
                """), {"shares": shares, "price": price})

                db.execute(text(
                    "UPDATE strategy_accounts SET cash=cash-:cost WHERE id=16"
                ), {"cost": total_cost})

                db.execute(text("""
                    INSERT INTO paper_fills
                        (account_id, code, action, shares, fill_price, fill_time, fill_source,
                         execution_time_model, gross_amount, net_amount, note, no_lookahead_pass)
                    VALUES (16, '0050', 'BUY', :shares, :price, datetime('now','localtime'), 'init',
                            'initial_allocation', :gross, :net, 'A16 0050 核心持股初始化', 1)
                """), {
                    "shares": shares, "price": price,
                    "gross": shares * price, "net": total_cost,
                })

                db.commit()
                print(f"  ✓ A16 買入 0050 {shares}股 @{price:.2f}，花費 {total_cost:,.0f}")
            else:
                print("  ⚠️ 找不到 0050 價格")
        else:
            print("  - A16 已持有 0050")
    else:
        print("  ⚠️ A16 不存在")
except Exception as e:
    db.rollback()
    print(f"  ❌ {e}")
finally:
    db.close()


# ════════════════════════════════════
# 3. main.py 加入剩餘 API
# ════════════════════════════════════
print("\n=== Step 3: 加入剩餘 API ===")
with open("main.py") as f:
    c = f.read()

REMAINING_APIS = '''
# ── strategy_registry ──
@app.get("/api/strategies/registry")
def api_strategy_registry():
    """策略帳戶設定清單"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        rows = db.execute(_t("""
            SELECT a.id, a.name, a.mode, a.initial_cash,
                   cfg.strategy_name, cfg.min_score, cfg.max_positions,
                   cfg.stop_loss_pct, cfg.take_profit_pct,
                   cfg.large_cap_only, cfg.no_chase_enabled,
                   cfg.max_rsi14, cfg.min_rsi14, cfg.theme_filter,
                   cfg.target_0050_pct, cfg.description, cfg.is_active
            FROM strategy_accounts a
            LEFT JOIN strategy_account_configs cfg ON cfg.account_id=a.id
            WHERE a.id >= 11 ORDER BY a.id
        """)).fetchall()
        cols = ["account_id","name","mode","initial_cash","strategy_name",
                "min_score","max_positions","stop_loss_pct","take_profit_pct",
                "large_cap_only","no_chase_enabled","max_rsi14","min_rsi14",
                "theme_filter","target_0050_pct","description","is_active"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        db.close()


# ── fundamental 覆蓋率 ──
@app.get("/api/data-quality/fundamental")
def api_fundamental_coverage():
    """基本面資料覆蓋率"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    db = SessionLocal()
    try:
        total_stocks = db.execute(_t("SELECT COUNT(DISTINCT code) FROM stock_meta")).scalar() or 0
        fund_count = db.execute(_t("SELECT COUNT(DISTINCT code) FROM fundamental")).scalar() or 0
        return {
            "total_stocks": total_stocks,
            "fundamental_count": fund_count,
            "coverage_pct": round(fund_count/total_stocks*100, 1) if total_stocks else 0,
            "note": "fundamental 表目前覆蓋率低，基本面分數以預設值填充",
        }
    finally:
        db.close()


# ── RSI 計算驗證 ──
@app.get("/api/data-quality/rsi-check")
def api_rsi_check(code: str = "2330"):
    """驗證 RSI14 計算是否正確（用最近14期）"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    import math
    db = SessionLocal()
    try:
        closes = db.execute(_t("""
            SELECT close FROM ohlcv_daily
            WHERE code=:c ORDER BY trade_date DESC LIMIT 20
        """), {"c": code}).fetchall()
        if len(closes) < 15:
            return {"ok": False, "message": "資料不足15筆"}
        c_list = [float(r[0]) for r in reversed(closes)]
        gains = [max(c_list[i]-c_list[i-1], 0) for i in range(1, 15)]
        losses = [max(c_list[i-1]-c_list[i], 0) for i in range(1, 15)]
        ag, al = sum(gains)/14, sum(losses)/14
        rsi_manual = 100 - 100/(1 + ag/al) if al else 100.0
        stored = db.execute(_t("""
            SELECT rsi14 FROM technical_daily_features
            WHERE code=:c ORDER BY trade_date DESC LIMIT 1
        """), {"c": code}).scalar()
        diff = abs(float(stored or 0) - rsi_manual)
        return {
            "code": code,
            "rsi_manual_calc": round(rsi_manual, 2),
            "rsi_stored": float(stored or 0),
            "diff": round(diff, 2),
            "ok": diff < 5,
            "message": "✅ RSI 計算正確" if diff < 5 else f"⚠️ 差異 {diff:.1f}，可能需要重算",
        }
    finally:
        db.close()


# ── 月度競賽 drawdown ──
@app.get("/api/monthly/drawdown")
def api_monthly_drawdown(start_date: str = None):
    """各帳戶回撤曲線"""
    from backend.models.database import SessionLocal
    from sqlalchemy import text as _t
    from datetime import date as ddate
    if not start_date:
        today = ddate.today()
        start_date = f"{today.year}-{today.month:02d}-01"
    db = SessionLocal()
    try:
        accounts = db.execute(_t(
            "SELECT id, name FROM strategy_accounts WHERE id >= 11"
        )).fetchall()
        result = []
        for aid, aname in accounts:
            rows = db.execute(_t("""
                SELECT snap_date, total_equity FROM equity_curve
                WHERE account_id=:id AND snap_date>=:sd ORDER BY snap_date
            """), {"id": aid, "sd": start_date}).fetchall()
            if not rows: continue
            peak = float(rows[0][1] or 200000)
            curve = []
            for d, eq in rows:
                eq_f = float(eq or peak)
                if eq_f > peak: peak = eq_f
                dd = round((eq_f/peak - 1)*100, 3)
                curve.append({"date": d, "drawdown": dd})
            result.append({"account_id": aid, "name": aname, "curve": curve})
        return result
    finally:
        db.close()
'''

if "/api/strategies/registry" not in c:
    c = c + REMAINING_APIS
    print("✓ 剩餘 API 加入")
else:
    print("- API 已存在")

with open("main.py","w") as f:
    f.write(c)

r = subprocess.run(["python3","-m","py_compile","main.py"], capture_output=True)
print("✓ main.py 語法正確" if r.returncode==0 else "❌ "+r.stderr.decode())
print("\n=== V5D Patch 完成 ===")
