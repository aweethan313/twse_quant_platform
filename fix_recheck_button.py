"""
讓資料健康「重新檢查」按鈕有明顯的視覺回饋：
  - 按下 → 按鈕變「⏳ 檢查中…」並 disable
  - 各區塊顯示「檢查中…」
  - 完成 → 按鈕恢復、顯示「✓ 已更新 時間」
  - 加最少 600ms 可見延遲（本地太快會看不到）
idempotent。用法：python3 fix_recheck_button.py
"""
path = 'frontend/templates/data_health.html'
with open(path) as f:
    c = f.read()

if 'runAllChecks_v2' in c:
    print("✓ 已套用，跳過")
    raise SystemExit

old = '''async function runAllChecks() {
  document.getElementById('status_cards').innerHTML = '<div class="col-span-2 text-gray-500 text-xs animate-pulse">檢查中...</div>';
  await loadChecks();
}'''

new = '''async function runAllChecks() {  // runAllChecks_v2
  const btn = document.querySelector('button[onclick="runAllChecks()"]');
  const orig = btn ? btn.textContent : '';
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 檢查中…'; btn.style.opacity = '0.6'; }
  document.getElementById('status_cards').innerHTML = '<div class="col-span-2 text-gray-500 text-xs animate-pulse">檢查中…</div>';
  const check_details = document.getElementById('check_details');
  if (check_details) check_details.innerHTML = '<div class="text-gray-500 text-xs animate-pulse">重新檢查中…</div>';
  const t0 = Date.now();
  try {
    await loadChecks();
  } catch(e) {}
  // 最少可見 600ms，讓使用者看到「有在跑」
  const wait = Math.max(0, 600 - (Date.now() - t0));
  await new Promise(r => setTimeout(r, wait));
  if (btn) { btn.disabled = false; btn.textContent = orig || '▶ 重新檢查'; btn.style.opacity = '1'; }
  const lu = document.getElementById('last_update');
  if (lu) lu.textContent = '✓ 已更新：' + new Date().toLocaleString('zh-TW');
}'''

if old in c:
    c = c.replace(old, new)
    with open(path, 'w') as f:
        f.write(c)
    print("✓ 重新檢查按鈕已加上視覺回饋（檢查中→已更新）")
else:
    print("❌ 找不到 runAllChecks，可能結構已不同")
