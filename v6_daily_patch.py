"""v6_daily_patch.py - 在每日工作流程加入 V6 步驟 + 決策引擎加 cooldown 檢查"""
import subprocess

# ── 1. 決策引擎加入 cooldown 檢查 ──
with open("backend/v5/decision_engine.py") as f:
    de = f.read()

old_cooldown_check = "            # 現金太少直接 SKIP"
new_cooldown_check = """            # 停損冷卻期檢查
            if not blocked:
                try:
                    cd_row = db.execute(text("""
                        SELECT id FROM strategy_cooldowns
                        WHERE account_id=:aid AND code=:c AND is_active=1
                        AND cooldown_until >= :d
                    """), {"aid": account_id, "c": code, "d": str(signal_date)}).fetchone()
                    if cd_row:
                        action = "SKIP"
                        blocked = True
                        blocked_reason = "STOP_LOSS_COOLDOWN（冷卻期未結束）"
                except: pass

            # 現金太少直接 SKIP"""

if old_cooldown_check in de and "STOP_LOSS_COOLDOWN" not in de:
    de = de.replace(old_cooldown_check, new_cooldown_check)
    with open("backend/v5/decision_engine.py","w") as f:
        f.write(de)
    print("✓ 決策引擎加入 cooldown 檢查")
else:
    print("- cooldown 已存在或未找到")

# ── 2. paper_engine 停損時建立 cooldown ──
with open("backend/v5/paper_engine.py") as f:
    pe = f.read()

old_sell_log = """                logger.info(f"[PAPER] A{aid} SELL {code} {sell_lots:.0f}股 @{fill_price:.2f} PnL={pnl:+.0f}")"""
new_sell_log = """                logger.info(f"[PAPER] A{aid} SELL {code} {sell_lots:.0f}股 @{fill_price:.2f} PnL={pnl:+.0f}")
                # 停損時建立冷卻期
                if "停損" in (note or ""):
                    try:
                        from datetime import timedelta
                        cooldown_until = str(execution_date + timedelta(days=5))
                        db.execute(text("""
                            INSERT OR IGNORE INTO strategy_cooldowns
                                (account_id, strategy_name, code, triggered_date,
                                 exit_price, cooldown_days, cooldown_until, reason, is_active)
                            VALUES (:aid,:sn,:c,:d,:ep,5,:cu,'STOP_LOSS',1)
                        """), {"aid": aid, "sn": sname, "c": code,
                               "d": str(execution_date), "ep": fill_price,
                               "cu": cooldown_until})
                    except: pass"""

if old_sell_log in pe and "cooldown_until" not in pe:
    pe = pe.replace(old_sell_log, new_sell_log)
    with open("backend/v5/paper_engine.py","w") as f:
        f.write(pe)
    print("✓ paper_engine 停損時建立 cooldown")
else:
    print("- cooldown 建立已存在或未找到")

# ── 3. 每日工作流程加入 V6 步驟 ──
with open("backend/v4/daily_workflow.py") as f:
    wf = f.read()

if "v6_chip_anomalies" not in wf:
    old_v5 = "    # Step 10c: V5 Paper Pipeline"
    new_v6 = """    # Step 10d: V6 籌碼異動 + 冷卻期更新
    def _v6_daily():
        try:
            from scripts.v6_detect_chip_anomalies import detect_chip_anomalies
            from scripts.v6_update_cooldowns import update_cooldowns
            from scripts.v6_update_strategy_health_scores import update_health_scores
            n = detect_chip_anomalies(target_date)
            update_cooldowns(target_date)
            update_health_scores()
            return {"status": "PASS", "message": f"V6: {n}個籌碼異動"}
        except Exception as e:
            return {"status": "WARN", "message": f"V6 失敗: {e}"}
    step("10d_v6_daily", _v6_daily)

    # Step 10c: V5 Paper Pipeline"""

    wf = wf.replace(old_v5, new_v6)
    with open("backend/v4/daily_workflow.py","w") as f:
        f.write(wf)
    print("✓ V6 加入每日工作流程")
else:
    print("- V6 已在工作流程")

# 語法檢查
for f in ["backend/v5/decision_engine.py","backend/v5/paper_engine.py","backend/v4/daily_workflow.py"]:
    r = subprocess.run(["python3","-m","py_compile",f], capture_output=True)
    print(f"{'✓' if r.returncode==0 else '❌'} {f}")
