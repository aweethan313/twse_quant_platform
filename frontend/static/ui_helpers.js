/**
 * frontend/static/ui_helpers.js
 * 共用 UI 狀態管理：loading / empty / error
 */

window.UI = {
  loading(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `
      <div class="flex items-center gap-2 text-gray-500 text-xs py-3">
        <div style="width:14px;height:14px;border:2px solid #f0b429;border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite"></div>
        ${msg || '載入中...'}
      </div>`;
  },

  empty(id, msg, hint) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `
      <div class="text-gray-600 text-xs py-3">
        <div>${msg || '尚無資料'}</div>
        ${hint ? `<div class="text-gray-700 mt-1 font-mono">${hint}</div>` : ''}
      </div>`;
  },

  error(id, err, apiPath) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `
      <div class="text-xs py-3">
        <div class="text-dn">⚠️ 載入失敗：${err}</div>
        ${apiPath ? `<div class="text-gray-600 mt-1 font-mono text-xs">API: ${apiPath}</div>` : ''}
        <button onclick="location.reload()" class="mt-2 text-xs px-2 py-1 rounded border border-gray-700 text-gray-400 hover:text-white">重新載入</button>
      </div>`;
  },

  html(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  },

  /** 安全 fetch：自動處理錯誤狀態 */
  async fetch(url, options) {
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(`HTTP ${r.status} ${r.statusText}`);
    return r.json();
  }
};

// 全域 spin keyframe
const style = document.createElement('style');
style.textContent = '@keyframes spin{to{transform:rotate(360deg)}}';
document.head.appendChild(style);
