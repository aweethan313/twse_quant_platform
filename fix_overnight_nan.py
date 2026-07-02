import math, sys
path = 'main.py'
with open(path) as f:
    c = f.read()
if 'sanitize_nan' in c:
    print("✓ 已修，跳過")
    sys.exit()
old = '''        return {
            "date": str(row[0]) if row else None,
            "overnight_score": float(row[2] or 50) if row else 50,
            "summary": us_summary,
            "market_regime": row[5] if row else "—",
            "taiex_close": taiex_close,
            "taiex_change": taiex_change,
            "tw_futures_close": tw_fut_close,
            "tw_futures_change": tw_fut_change,
            "mu_ret": mu_ret,
            "twse_proxy": {
                "code": "0050",
                "date": str(idx_row[0]) if idx_row else None,
                "close": float(idx_row[1] or 0) if idx_row else 0,
                "change_pct": float(idx_row[2] or 0) if idx_row else 0,
            },
            "breadth": {
                "up": int(mkt[0] or 0) if mkt else 0,
                "down": int(mkt[1] or 0) if mkt else 0,
                "avg_change": float(mkt[2] or 0) if mkt else 0,
                "total_value_b": round(float(mkt[3] or 0)/1e8, 0) if mkt else 0,
            }
        }'''
new = '''        def sanitize_nan(v, default=None):
            import math
            if v is None:
                return default
            try:
                f = float(v)
                return default if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return v

        return {
            "date": str(row[0]) if row else None,
            "overnight_score": sanitize_nan(row[2] if row else None, 50) or 50,
            "summary": us_summary,
            "market_regime": row[5] if row else "—",
            "taiex_close": sanitize_nan(taiex_close),
            "taiex_change": sanitize_nan(taiex_change),
            "tw_futures_close": sanitize_nan(tw_fut_close),
            "tw_futures_change": sanitize_nan(tw_fut_change),
            "mu_ret": sanitize_nan(mu_ret, 0) or 0,
            "twse_proxy": {
                "code": "0050",
                "date": str(idx_row[0]) if idx_row else None,
                "close": sanitize_nan(idx_row[1] if idx_row else None, 0) or 0,
                "change_pct": sanitize_nan(idx_row[2] if idx_row else None, 0) or 0,
            },
            "breadth": {
                "up": int(mkt[0] or 0) if mkt else 0,
                "down": int(mkt[1] or 0) if mkt else 0,
                "avg_change": sanitize_nan(mkt[2] if mkt else None, 0) or 0,
                "total_value_b": round((sanitize_nan(mkt[3] if mkt else None, 0) or 0) / 1e8, 0),
            }
        }'''
if old in c:
    c = c.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ 已修好 nan 問題")
else:
    print("❌ 找不到目標，請把 main.py 2450-2475 行貼給 Claude")
