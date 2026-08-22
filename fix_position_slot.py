"""
第13個bug:換股日的可用部位計算未扣除待賣部位

症狀:
  有賣出決策的日子,買入決策幾乎全被跳過(「已達最大持股數」),
  導致賣出後資金空窗一至數日。

實測影響(2026-05-25 ~ 08-20):
  A1:62 天中 54 天現金 >30%,**從未滿倉**,SKIP 有 168 筆是「持股滿」
  A7:30 天現金 >50%(換手最快,空窗最頻繁)
  十天中有八天是「賣了但沒買」

  → 所有策略長期只投入約六成資金,而基準指數是 100% 投入。
    這可能是策略普遍跑輸大盤的重要原因之一。

根因:
  pos_count = len(pos_map) 在買入判斷之前計算(第 97 行),
  而賣出判斷在買入之後(第 215 行起) → 用「賣出前」的持股數判斷可用部位。

修法:
  將賣出條件抽成 _will_sell_today(),買入判斷前先預估今日將賣出的檔數,
  從 pos_count 扣除。賣出邏輯本身改為呼叫同一函式,確保兩邊條件一致。

注意:現金不預支(賣出 T+1 才入帳),所以買單仍可能因現金不足而部分成交
      —— 這符合真實交易情況。

用法:python3 fix_position_slot.py
"""
path = 'backend/v5/decision_engine.py'
with open(path) as f:
    src = f.read()

if 'POSITION_SLOT_FIX' in src:
    print("✓ 已修,跳過")
    raise SystemExit

# ── 1. 新增共用的賣出判斷函式(插在 _get_candidates 之前)──
helper = '''

def _sell_signal_for(db, cfg: dict, code: str, pos: dict, signal_date) -> tuple:
    """
    POSITION_SLOT_FIX:賣出條件的單一判斷來源。
    回傳 (sell_action, sell_reason);不賣則為 (None, None)。
    買入判斷與賣出判斷共用此函式,確保「預估將賣出的檔數」與實際一致。
    """
    row = db.execute(text("""
        SELECT close FROM ohlcv_daily WHERE code=:c AND trade_date=:d
    """), {"c": code, "d": str(signal_date)}).fetchone()
    if not row:
        return (None, None)
    sell_price = float(row[0])
    avg_cost = pos.get("avg_cost") or 0
    if avg_cost <= 0:
        return (None, None)
    pnl_pct = (sell_price / avg_cost - 1) * 100

    max_hold = cfg.get("max_hold_days")
    opened_at = pos.get("opened_at")
    if max_hold and opened_at:
        held = db.execute(text("""
            SELECT COUNT(*) FROM trading_calendar
            WHERE is_open=1 AND trade_date > :o AND trade_date <= :d
        """), {"o": str(opened_at)[:10], "d": str(signal_date)}).scalar() or 0
        if held >= max_hold:
            return ("SELL", f"max_hold_days 到期（持有{held}交易日 >= {max_hold}），換股")

    if pnl_pct <= -cfg["stop_loss_pct"] * 100:
        return ("SELL", f"觸發停損（-{cfg['stop_loss_pct']*100:.0f}%），目前虧損{pnl_pct:.1f}%")
    if pnl_pct >= cfg["take_profit_pct"] * 100:
        return ("SELL", f"達到停利（+{cfg['take_profit_pct']*100:.0f}%），目前獲利{pnl_pct:.1f}%")
    return (None, None)

'''

anchor_fn = 'def _get_candidates(db, cfg: dict, signal_date: date) -> list[dict]:'
if anchor_fn not in src:
    print("❌ 找不到 _get_candidates")
    raise SystemExit
src = src.replace(anchor_fn, helper + anchor_fn, 1)

# ── 2. pos_count 扣除待賣部位 ──
old_pc = '''            pos_count = len(pos_map)'''
new_pc = '''            # POSITION_SLOT_FIX:扣除今日將觸發賣出的部位,
            # 否則換股日的買單會全被「已達最大持股數」擋掉,造成資金空窗
            _will_sell = 0
            for _c, _p in pos_map.items():
                _act, _ = _sell_signal_for(db, cfg, _c, _p, signal_date)
                if _act == "SELL":
                    _will_sell += 1
            pos_count = max(0, len(pos_map) - _will_sell)'''
if old_pc in src:
    src = src.replace(old_pc, new_pc, 1)
else:
    print("❌ 找不到 pos_count 定義")
    raise SystemExit

with open(path, 'w') as f:
    f.write(src)

print("✓ 已加入 POSITION_SLOT_FIX")
print("  - 新增 _sell_signal_for() 作為賣出條件的單一來源")
print("  - pos_count 扣除今日將賣出的檔數")
print()
print("⚠️ 注意:原本的賣出判斷區(211 行起)仍是獨立實作,")
print("   兩者條件已對齊但未合併。若未來修改賣出條件,兩處都要改。")
print()
print("驗證:")
print("  grep -c 'POSITION_SLOT_FIX' backend/v5/decision_engine.py")
print("  python3 -m py_compile backend/v5/decision_engine.py")
