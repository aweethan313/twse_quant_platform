"""S8 v2-5A 回測 v3：大盤 MA60 保護 + 停損"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from sqlalchemy import text
from backend.models.database import SessionLocal

START=date(2025,2,3); END=date(2026,5,21)
INITIAL_CASH=1_000_000; MAX_POS=8
MIN_SCORE=62; MAX_RISK=45
STOP_LOSS=-0.08
COMMISSION=0.001425; TAX=0.003

def run():
    db=SessionLocal()
    trade_dates=[r[0] for r in db.execute(text("""
        SELECT DISTINCT score_date FROM daily_scores
        WHERE score_date BETWEEN :s AND :e ORDER BY score_date
    """),{"s":START,"e":END}).fetchall()]

    # 預載 0050 收盤價
    etf_prices={r[0]:float(r[1]) for r in db.execute(text("""
        SELECT trade_date,close FROM ohlcv_daily
        WHERE code='0050' AND trade_date BETWEEN :s AND :e AND close IS NOT NULL
    """),{"s":START,"e":END}).fetchall()}

    cash=float(INITIAL_CASH); holdings={}; equity=[]; trades=[]; monthly_pnl={}

    for td in trade_dates:
        prices={r[0]:float(r[1]) for r in db.execute(text("""
            SELECT code,close FROM ohlcv_daily
            WHERE trade_date=:d AND close IS NOT NULL AND close>0
        """),{"d":td}).fetchall()}
        if not prices: continue

        # 大盤判斷：0050 vs MA60
        etf_hist=sorted([(d,p) for d,p in etf_prices.items() if d<=td])[-60:]
        etf_ma60=sum(p for _,p in etf_hist)/len(etf_hist) if etf_hist else None
        etf_now=etf_prices.get(td,0)
        bear_market=(etf_ma60 is not None and etf_now < etf_ma60*0.99)

        # 停損
        for code in [c for c in list(holdings) if c in prices]:
            ret=(prices[code]-holdings[code]["cost"])/holdings[code]["cost"]
            if ret<=STOP_LOSS or bear_market:
                h=holdings[code]; price=prices[code]
                proc=h["lots"]*1000*price; fee=proc*COMMISSION; tax_=proc*TAX
                pnl=proc-fee-tax_-h["lots"]*1000*h["cost"]
                cash+=proc-fee-tax_
                m=str(td)[:7]; monthly_pnl[m]=monthly_pnl.get(m,0)+pnl
                reason="STOP" if ret<=STOP_LOSS else "BEAR"
                trades.append({"date":str(td),"code":code,"action":"SELL",
                               "lots":h["lots"],"price":price,"pnl":round(pnl),"reason":reason})
                del holdings[code]

        # 熊市不買
        if bear_market:
            mkt=sum(holdings[c]["lots"]*1000*prices.get(c,holdings[c]["cost"]) for c in holdings)
            equity.append({"date":str(td),"total":round(cash+mkt)})
            continue

        # 選股
        cands=db.execute(text("""
            SELECT code,COALESCE(final_score,composite_score) as score
            FROM daily_scores WHERE score_date=:d
              AND (final_action='BUY'
                OR (final_action IS NULL AND signal='BUY' AND composite_score>=:ms))
              AND COALESCE(risk_score,30)<=:mr
              AND COALESCE(stock_class,'NORMAL') NOT IN ('ETF_INCOME','ILLIQUID_RISK')
            ORDER BY score DESC LIMIT :n
        """),{"d":td,"ms":MIN_SCORE,"mr":MAX_RISK,"n":MAX_POS}).fetchall()
        targets={r[0] for r in cands if r[0] in prices}

        # 賣出不在目標
        for code in [c for c in list(holdings) if c not in targets and c in prices]:
            h=holdings[code]; price=prices[code]
            proc=h["lots"]*1000*price; fee=proc*COMMISSION; tax_=proc*TAX
            pnl=proc-fee-tax_-h["lots"]*1000*h["cost"]
            cash+=proc-fee-tax_
            m=str(td)[:7]; monthly_pnl[m]=monthly_pnl.get(m,0)+pnl
            trades.append({"date":str(td),"code":code,"action":"SELL",
                           "lots":h["lots"],"price":price,"pnl":round(pnl),"reason":"EXIT"})
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
                cash-=cost+fee
                holdings[code]={"lots":lots,"cost":price,"buy_date":str(td)}
                trades.append({"date":str(td),"code":code,"action":"BUY",
                               "lots":lots,"price":price,"pnl":0,"reason":"BUY"})

        mkt=sum(holdings[c]["lots"]*1000*prices.get(c,holdings[c]["cost"]) for c in holdings)
        equity.append({"date":str(td),"total":round(cash+mkt)})

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
    print(f"S8 v2-5A 回測 v3  {START}~{END}")
    print(f"{'='*45}")
    print(f"初始資金   {INITIAL_CASH:>12,.0f}")
    print(f"最終資產   {final:>12,.0f}")
    print(f"總報酬     {ret:>11.2f}%")
    print(f"年化報酬   {ann:>11.2f}%")
    print(f"最大回撤   {mdd:>11.2f}%")
    print(f"交易次數   {len(sells):>12}")
    print(f"勝率       {len(wins)/len(sells)*100 if sells else 0:>11.1f}%")
    bear_days=sum(1 for e in equity if e==equity[0] or True)
    print(f"{'='*45}")
    print("\n每月損益:")
    for m in sorted(monthly_pnl):
        v=monthly_pnl[m]; s="+" if v>0 else ""
        print(f"  {m}: {s}{v:>9,.0f}")

    import csv
    with open("data/reports/s8_v2_equity.csv","w",newline="") as f:
        csv.DictWriter(f,["date","total"]).writeheader()
        w=csv.DictWriter(f,["date","total"]); w.writerows(equity)
    with open("data/reports/s8_v2_trades.csv","w",newline="") as f:
        w=csv.DictWriter(f,["date","code","action","lots","price","pnl","reason"])
        w.writeheader(); w.writerows(trades)
    print("\n✓ CSV 輸出完成")

if __name__=="__main__": run()
