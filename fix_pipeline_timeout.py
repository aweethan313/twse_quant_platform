"""
工程債 #6:pipeline 單步超時保護

背景(2026-08-14):
  1_ohlcv_eod 步驟跑了 11,838 秒(3.3 小時),18:30 開始 22:11 才結束。
  log 中無任何 timeout/retry 記錄 —— 程式在安靜地等待,無從得知原因。

修法:
  在 step() 包裝函式加 signal.alarm 超時保護(macOS/Unix 有效)。
  超時後該步驟標記 FAIL 並記錄,但**繼續執行後續步驟**
  (不因單一步驟卡住而整條中斷)。

預設上限:
  - 1_ohlcv_eod / 4_ml_score:900 秒(15 分鐘,這兩步本來就較慢)
  - 其他步驟:300 秒(5 分鐘)

用法:python3 fix_pipeline_timeout.py
"""
path = 'scripts/daily_pipeline.py'
with open(path) as f:
    src = f.read()

if 'STEP_TIMEOUT' in src:
    print("✓ 已修,跳過")
    raise SystemExit

old = '''    def step(name, fn):
        t0 = datetime.now()
        try:
            r = fn()'''

new = '''    # STEP_TIMEOUT:單步超時上限(秒)
    # (2026-08-14:1_ohlcv_eod 安靜卡住 3.3 小時,無 log 可追)
    _TIMEOUTS = {
        "1_ohlcv_eod": 900,
        "4_ml_score": 900,
    }
    _DEFAULT_TIMEOUT = 300

    def step(name, fn):
        import signal as _sig
        t0 = datetime.now()
        _limit = _TIMEOUTS.get(name, _DEFAULT_TIMEOUT)

        def _on_timeout(signum, frame):
            raise TimeoutError(f"步驟超過 {_limit} 秒上限,已中止")

        _prev = None
        try:
            _prev = _sig.signal(_sig.SIGALRM, _on_timeout)
            _sig.alarm(_limit)
        except (ValueError, AttributeError):
            _prev = None   # 非主執行緒或不支援,略過保護

        try:
            r = fn()'''

if old not in src:
    print("❌ 錨點失敗(step 函式開頭)")
    raise SystemExit
src = src.replace(old, new, 1)

# 在 finally 取消 alarm:找 step 內的 except 區塊,改為 except + finally
old2 = '''        except Exception as e:
            steps.append({"name": name, "ok": False, "message": str(e)[:200],
                          "sec": round((datetime.now() - t0).total_seconds(), 1)})
            logger.error(f"❌ {name}: {e}")
            return None'''

new2 = '''        except Exception as e:
            steps.append({"name": name, "ok": False, "message": str(e)[:200],
                          "sec": round((datetime.now() - t0).total_seconds(), 1)})
            logger.error(f"❌ {name}: {e}")
            return None
        finally:
            try:
                _sig.alarm(0)
                if _prev is not None:
                    _sig.signal(_sig.SIGALRM, _prev)
            except (ValueError, AttributeError):
                pass'''

if old2 not in src:
    print("❌ 錨點失敗(except 區塊)")
    raise SystemExit
src = src.replace(old2, new2, 1)

with open(path, 'w') as f:
    f.write(src)

print("✓ 單步超時保護已加入")
print("  1_ohlcv_eod / 4_ml_score: 900 秒")
print("  其他步驟: 300 秒")
print("\n請執行驗證:")
print("  grep -c 'STEP_TIMEOUT' scripts/daily_pipeline.py")
print("  python3 -m py_compile scripts/daily_pipeline.py")
