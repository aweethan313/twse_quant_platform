"""
S8 v2-5A 回測：有 final_action 用它，沒有則用 composite_score>=65
"""
import sys,os; sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal

START=date(2025,2,3); END=date(2026,5,21)
INITIAL_CASH=1_000_000; MAX_POS=10
MIN_SCORE=65; MAX_RISK=50
COMMISSION=0.001425; TAX=0.003

def run():
    db=SessionLocal()
    trade_dates=[r[0] for r in db.execute(text("""
        SELECT DISTINCT score_date FROM daily_scores
        WHERE score_date BETWEEN :s AND :e ORDER BY score_date
    """),{"s":START,"e":END}).fetchall()]
    print(f"交易日數: {len(trade_dates)}")
    if not trade_dates: print("無資料"); db.close(); return

    cash=float(INITIAL_CASH); holdings={}; equity=[]; trades=[]

    for td in trade_dates:
        prices={r[0]:float(r[1]) for r in db.execute(text("""
            SELECT code,close FROM ohlcv_daily
            WHERE trade_date=:d AND close IS NOT NULL AND close>0
        """),{"d":td}).fetchall()}
        if not prices: continue

        # 選股：優先用 final_action，否則用 composite_score
        cands=db.execute(text("""
            SELECT code,
                   COALESCE(final_score, composite_score) as score,
                   COALESCE(risk_score, 30) as risk
            FROM daily_scores WHERE score_date=:d
              AND (
                final_action='BUY'
                OR (final_action IS NULL AND signal='BUY' AND composite_score>=:ms)
              )
              AND COALESCE(risk_score,30)<=:mr
            ORDER BY score DESC LIMIT :n
        """),{"d":td,"ms":MIN_SCORE,"mr":MAX_RISK,"n":MAX_POS}).fetchall()
        targets={r[0] for r in cands if r[0] in prices}

        # 賣出
        for code in list(holdings):
            if code not in targets and code in prices:
                lots=holdings[code]["lots"]; price=prices[code]
                proc=lots*1000*price; fee=proc*COMMISSION; tax=proc*TAX
                pnl=proc-fee-tax-lots*1000*holdings[code]["cost"]
                cash+=proc-fee-tax
                trades.append({"date":td,"code":code,"action":"SELL","lots":lots,"price":price,"pnl":round(pnl)})
                del holdings[code]

        # 買入
        slots=MAX_POS-len(holdings)
        buys=[r[0] for r in cands if r[0] not in holdings and r[0] in prices][:slots]
        if buys and cash>0:
            budget=cash/max(len(buys),1)
            for code in buys:
                price=prices[code]; lots=max(1,int(budget/(price*1000)))
                cost=lots*1000*price; fee=cost*COMMISSION
                if cost+fee>cash: lots=max(0,lots-1); cost=lots*1000*price; fee=cost*COMMISSION
                if lots==0: continue
                cash-=cost+fee; holdings[code]={"lots":lots,"cost":price}
                trades.append({"date":td,"code":code,"action":"BUY","lots":lots,"price":price,"pnl":0})

        mkt=sum(holdings[c]["lots"]*1000*prices.get(c,holdings[c]["cost"]) for c in holdings)
        equity.append({"date":td,"total":round(cash+mkt)})

    db.close()
    if not equity: print("無資料"); return

    final=equity[-1]["total"]; ret=(final/INITIAL_CASH-1)*100
    peak=INITIAL_CASH; mdd=0.0
    for e in equity:
        if e["total"]>peak: peak=e["total"]
        dd=(peak-e["total"])/peak*100
        if dd>mdd: mdd=dd
    sells=[t for t in trades if t["action"]=="SELL"]
    wins=[t for t in sells if t["pnl"]>0]
    days=(END-START).days
    ann=((final/INITIAL_CASH)**(365/days)-1)*100 if days>0 else 0

    print(f"\n{'='*45}")
    print(f"S8 v2-5A 回測  {START} ~ {END}")
    print(f"{'='*45}")
    print(f"初始資金   {INITIAL_CASH:>12,.0f}")
    print(f"最終資產   {final:>12,.0f}")
    print(f"總報酬     {ret:>11.2f}%")
    print(f"年化報酬   {ann:>11.2f}%")
    print(f"最大回撤   {mdd:>11.2f}%")
    print(f"交易次數   {len(sells):>12}")
    print(f"勝率       {len(wins)/len(sells)*100 if sells else 0:>11.1f}%")
    print(f"{'='*45}")

    import csv
    with open("data/reports/s8_v2_equity.csv","w",newline="") as f:
        csv.DictWriter(f,["date","total"]).writeheader() or [f for f in []]
        w=csv.DictWriter(f,["date","total"]); w.writerows(equity)
    with open("data/reports/s8_v2_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,["date","code","action","lots","price","pnl"])
        w.writeheader(); w.writerows(trades)
    print("✓ data/reports/s8_v2_equity.csv / s8_v2_trades.csv")

if __name__=="__main__": run()
