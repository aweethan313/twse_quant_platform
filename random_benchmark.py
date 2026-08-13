"""
實驗二:隨機選股基準(判斷選股是否真有 alpha)

設計:與 104 相同條件 —— 20 檔、250 交易日輪動、等權配置、含費稅
     但改為「隨機選股」,重複 N 次,看 104 的 +105.6% 落在分布的哪個百分位。

關鍵:用 universe_membership 過濾「當日在市」的股票(修 survivorship bias)
     —— 若只從今天還活著的股票裡隨機選,會系統性高估隨機基準。

用法:
  python3 random_benchmark.py --runs 500 --start 2023-01-01 --end 2026-05-24
"""
import argparse, random, sys, time
from datetime import date
from pathlib import Path
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
from sqlalchemy import text
from backend.models.database import SessionLocal

FEE = 0.001425
TAX = 0.003
INITIAL = 200000.0


def load_data(start, end):
    """一次載入所需資料到記憶體(避免 500 次回測反覆查 DB)"""
    db = SessionLocal()
    try:
        days = [str(r[0]) for r in db.execute(text("""
            SELECT DISTINCT trade_date FROM ohlcv_daily
            WHERE trade_date BETWEEN :s AND :e AND code GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY trade_date"""), {"s": start, "e": end}).fetchall()]

        # 價格:{(code, date): open/close}
        rows = db.execute(text("""
            SELECT code, trade_date, open, close FROM ohlcv_daily
            WHERE trade_date BETWEEN :s AND :e AND code GLOB '[0-9][0-9][0-9][0-9]'
              AND close > 10 AND open > 0"""), {"s": start, "e": end}).fetchall()
        px = {}
        for c, d, o, cl in rows:
            px[(c, str(d))] = (float(o), float(cl))

        # universe:每檔的存活區間(修 survivorship)
        uni = {r[0]: (str(r[1] or '2000-01-01'), str(r[2]) if r[2] else '2099-12-31')
               for r in db.execute(text(
                   "SELECT code, list_date, delist_date FROM universe_membership")).fetchall()}
        return days, px, uni
    finally:
        db.close()


def tradable_on(day, px, uni, all_codes):
    """當日可交易且當時在市的股票"""
    out = []
    for c in all_codes:
        if (c, day) not in px:
            continue
        ld, dd = uni.get(c, ('2000-01-01', '2099-12-31'))
        if ld <= day <= dd:
            out.append(c)
    return out


def run_once(days, px, uni, all_codes, n_hold=20, hold_days=250, seed=None):
    rng = random.Random(seed)
    cash = INITIAL
    holdings = {}          # code -> shares
    entry_day_idx = -10**9
    equity_curve = []

    for i, d in enumerate(days):
        # 到期或首日 → 全部換股
        if i - entry_day_idx >= hold_days:
            # 賣出全部(用當日開盤)
            for c, sh in list(holdings.items()):
                if (c, d) in px:
                    o = px[(c, d)][0]
                    proceeds = sh * o
                    cash += proceeds - proceeds * (FEE + TAX)
            holdings = {}
            # 隨機買入 n_hold 檔(等權)
            pool = tradable_on(d, px, uni, all_codes)
            if len(pool) >= n_hold:
                picks = rng.sample(pool, n_hold)
                per = cash / n_hold
                for c in picks:
                    o = px[(c, d)][0]
                    sh = int(per / (o * (1 + FEE)))
                    if sh > 0:
                        cost = sh * o
                        cash -= cost + max(cost * FEE, 20)
                        holdings[c] = sh
                entry_day_idx = i

        # 計算當日淨值
        mv = 0.0
        for c, sh in holdings.items():
            if (c, d) in px:
                mv += sh * px[(c, d)][1]
        equity_curve.append(cash + mv)

    # SHARPE_CALC:用日報酬序列算年化夏普(無風險利率設 0)
    import statistics as _st
    daily_rets = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i-1]
        if prev > 0:
            daily_rets.append(equity_curve[i] / prev - 1)
    if len(daily_rets) > 2:
        mu = _st.mean(daily_rets)
        sd = _st.pstdev(daily_rets)
        sharpe = (mu / sd * (252 ** 0.5)) if sd > 1e-9 else 0.0
    else:
        sharpe = 0.0

    final = equity_curve[-1] if equity_curve else INITIAL
    peak, mdd = -1e18, 0.0
    for e in equity_curve:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e / peak - 1) * 100)
    ret_pct = (final / INITIAL - 1) * 100
    rr = ret_pct / abs(mdd) if mdd < -0.01 else 0.0
    return ret_pct, mdd, rr, sharpe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=500)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-05-24")
    ap.add_argument("--hold", type=int, default=20, help="持股檔數")
    ap.add_argument("--hold-days", type=int, default=250)
    ap.add_argument("--compare", type=float, default=105.59, help="要比較的策略報酬%")
    ap.add_argument("--compare-mdd", type=float, default=-24.92, help="策略的最大回撤%")
    ap.add_argument("--compare-sharpe", type=float, default=None, help="策略的年化夏普")
    args = ap.parse_args()

    print(f"載入資料 {args.start} ~ {args.end} ...")
    t0 = time.time()
    days, px, uni = load_data(args.start, args.end)
    all_codes = sorted({c for (c, d) in px.keys()})
    print(f"  {len(days)} 個交易日、{len(all_codes)} 檔股票、{len(px)} 筆價格({time.time()-t0:.0f}s)")

    print(f"\n跑 {args.runs} 次隨機組合({args.hold} 檔、{args.hold_days} 日輪動)...")
    results, mdds, rrs, sharpes = [], [], [], []
    for k in range(args.runs):
        r, m, rr, sp = run_once(days, px, uni, all_codes, args.hold, args.hold_days, seed=k)
        results.append(r); mdds.append(m); rrs.append(rr); sharpes.append(sp)
        if (k + 1) % 50 == 0:
            print(f"  {k+1}/{args.runs} ({time.time()-t0:.0f}s)", flush=True)

    results.sort()
    n = len(results)
    def pct(p):
        return results[min(n - 1, int(n * p / 100))]

    beat = sum(1 for r in results if r < args.compare)
    percentile = beat / n * 100

    print("\n" + "=" * 55)
    print(f"隨機組合報酬分布({n} 次)")
    print("=" * 55)
    print(f"  最差    : {results[0]:8.2f}%")
    print(f"  5百分位 : {pct(5):8.2f}%")
    print(f"  25百分位: {pct(25):8.2f}%")
    print(f"  中位數  : {pct(50):8.2f}%")
    print(f"  75百分位: {pct(75):8.2f}%")
    print(f"  95百分位: {pct(95):8.2f}%")
    print(f"  最好    : {results[-1]:8.2f}%")
    print(f"  平均    : {sum(results)/n:8.2f}%")
    print(f"  平均回撤: {sum(mdds)/n:8.2f}%")
    print("-" * 55)
    print(f"  策略報酬: {args.compare:8.2f}%")
    print(f"  ★ 落在第 {percentile:.1f} 百分位")
    if percentile >= 95:
        print("  → 贏過 95% 隨機組合,選股具統計顯著的 alpha")
    elif percentile >= 75:
        print("  → 優於多數隨機組合,但未達 95% 顯著門檻")
    else:
        print("  → 與隨機選股無顯著差異,選股 alpha 存疑")
    # SHARPE_MODE:風險調整後(報酬/回撤比)的百分位
    rrs_sorted = sorted(rrs)
    strat_rr = args.compare / abs(args.compare_mdd) if args.compare_mdd else 0
    beat_rr = sum(1 for x in rrs_sorted if x < strat_rr)
    pct_rr = beat_rr / len(rrs_sorted) * 100
    print()
    print("=" * 55)
    print("風險調整後(報酬 / 最大回撤)")
    print("=" * 55)
    print(f"  隨機中位數 : {rrs_sorted[len(rrs_sorted)//2]:8.2f}")
    print(f"  隨機95百分位: {rrs_sorted[int(len(rrs_sorted)*0.95)]:8.2f}")
    print(f"  策略       : {strat_rr:8.2f}  (報酬{args.compare:.1f}% / 回撤{abs(args.compare_mdd):.1f}%)")
    print(f"  ★ 落在第 {pct_rr:.1f} 百分位")
    if pct_rr >= 95:
        print("  → 風險調整後贏過 95% 隨機組合,具統計顯著優勢")
    elif pct_rr >= 75:
        print("  → 優於多數隨機組合")
    else:
        print("  → 與隨機無顯著差異")
    print("=" * 55)

    if args.compare_sharpe:
        ss = sorted(sharpes)
        beat_s = sum(1 for x in ss if x < args.compare_sharpe)
        pct_s = beat_s / len(ss) * 100
        print()
        print("=" * 55)
        print("年化夏普比率(日報酬標準差,無風險利率=0)")
        print("=" * 55)
        print(f"  隨機中位數  : {ss[len(ss)//2]:8.2f}")
        print(f"  隨機95百分位: {ss[int(len(ss)*0.95)]:8.2f}")
        print(f"  策略        : {args.compare_sharpe:8.2f}")
        print(f"  ★ 落在第 {pct_s:.1f} 百分位")
        if pct_s >= 95:
            print("  → 夏普比率贏過 95% 隨機組合,統計顯著")
        elif pct_s >= 75:
            print("  → 優於多數隨機組合")
        else:
            print("  → 與隨機無顯著差異")
        print("=" * 55)


if __name__ == "__main__":
    main()
