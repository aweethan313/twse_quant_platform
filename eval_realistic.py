"""
現實化評估:T+1 開盤成交 + 交易成本,並換算年化
用於解釋「選股評估看似有超額,但 A7 實際虧損」的落差
"""
import statistics as st
from sqlalchemy import text
from backend.models.database import SessionLocal

VER = "lgbm_exp_h5"
FEE, TAX = 0.001425, 0.003
COMBOS = [(5, 5), (5, 20), (5, 60), (20, 20), (20, 60)]

db = SessionLocal()
days = [str(r[0]) for r in db.execute(text("""
    SELECT DISTINCT trade_date FROM ohlcv_daily
    WHERE trade_date >= '2024-10-01' AND code GLOB '[0-9][0-9][0-9][0-9]'
    ORDER BY trade_date""")).fetchall()]

print(f"評估區間 {days[0]} ~ {days[-1]}({len(days)} 交易日),模型 {VER}")
print("成交模型:T+1 開盤買入 → 持有 N 日後開盤賣出,含手續費 0.1425%×2 + 證交稅 0.3%\n")
print("="*88)
print(f"{'持股':>5}{'持有':>6}{'毛超額':>10}{'淨超額':>10}{'成本':>9}{'年化次數':>10}{'年化淨超額':>12}{'勝率':>8}{'樣本':>7}")
print("-"*88)

for topn, hold in COMBOS:
    gross, net, mkts = [], [], []
    step = max(hold, 5)
    for i in range(0, len(days) - hold - 1, step):
        d = days[i]
        buy_d = days[i + 1]          # T+1 開盤買
        sell_d = days[i + 1 + hold]  # 持有 hold 日後開盤賣
        rows = db.execute(text("""
            SELECT (s.open/b.open - 1)*100
            FROM ml_score_experiments e
            JOIN ohlcv_daily b ON b.code=e.code AND b.trade_date=:bd
            JOIN ohlcv_daily s ON s.code=e.code AND s.trade_date=:sd
            WHERE e.score_date=:d AND e.model_version=:v AND e.ml_rank <= :n
              AND b.open > 10 AND s.open > 0
        """), {"d": d, "bd": buy_d, "sd": sell_d, "v": VER, "n": topn}).fetchall()
        if len(rows) < max(3, topn // 2):
            continue
        g = st.mean([float(r[0]) for r in rows])
        cost_pct = (FEE * 2 + TAX) * 100     # 一買一賣的總成本
        gross.append(g)
        net.append(g - cost_pct)
        m = db.execute(text("""
            SELECT AVG((s.open/b.open - 1)*100)
            FROM ohlcv_daily b JOIN ohlcv_daily s ON s.code=b.code AND s.trade_date=:sd
            WHERE b.trade_date=:bd AND b.code GLOB '[0-9][0-9][0-9][0-9]'
              AND b.open > 10 AND s.open > 0
        """), {"bd": buy_d, "sd": sell_d}).scalar()
        mkts.append(float(m or 0))
    if not gross:
        continue
    g, n_, m = st.mean(gross), st.mean(net), st.mean(mkts)
    cost = (FEE * 2 + TAX) * 100
    cycles = 252 / hold                       # 一年輪動幾次
    ann_net = (n_ - m) * cycles               # 年化淨超額(簡化,不複利)
    win = sum(1 for a, b in zip(net, mkts) if a > b) / len(net) * 100
    print(f"{topn:>5}{hold:>4}日{g-m:>+9.2f}%{n_-m:>+9.2f}%{cost:>8.2f}%{cycles:>9.1f}{ann_net:>+11.1f}%{win:>7.0f}%{len(net):>7}")

db.close()
print("="*88)
print("\n判讀:")
print("  年化淨超額是簡化估計(不複利、不計滑價、假設每次都能滿額成交)")
print("  若某組合年化淨超額為負,代表成本吃掉全部選股優勢")
