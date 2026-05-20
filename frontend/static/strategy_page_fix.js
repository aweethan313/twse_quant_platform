(function () {
  function money(x) {
    return Number(x || 0).toLocaleString("zh-TW", { maximumFractionDigits: 0 });
  }

  function pct(x) {
    x = Number(x || 0);
    return (x >= 0 ? "+" : "") + x.toFixed(2) + "%";
  }

  function pn(x) {
    x = Number(x || 0);
    if (x > 0) return "text-up";
    if (x < 0) return "text-dn";
    return "text-gray-400";
  }

  async function apiJson(url, options) {
    const res = await fetch(url, options || {});
    if (!res.ok) throw new Error(await res.text());
    return await res.json();
  }

  window.loadAccounts = async function () {
    const cards = document.getElementById("account_cards");
    const sel = document.getElementById("sel_account");
    const mo = document.getElementById("mo_account");

    if (cards) cards.innerHTML = '<div class="text-sm text-gray-500">載入中...</div>';

    try {
      const accounts = await apiJson("/api/strategies");

      if (!accounts.length) {
        if (cards) cards.innerHTML = '<div class="text-sm text-gray-500">目前沒有策略帳戶</div>';
        return;
      }

      if (cards) {
        cards.innerHTML = accounts.map(a => `
          <div class="bg-surface-2 border border-border rounded-lg p-4">
            <div class="flex items-start justify-between mb-3">
              <div>
                <div class="font-medium text-sm">${a.name}</div>
                <div class="text-xs text-gray-500">${a.strategy_type || "rule_based"}</div>
              </div>
              <button onclick="deleteAccount(${a.account_id})" class="text-gray-500 hover:text-dn">×</button>
            </div>

            <div class="space-y-1.5 text-xs">
              <div class="flex justify-between"><span class="text-gray-500">現金</span><span class="font-mono">${money(a.cash)}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">持股市值</span><span class="font-mono">${money(a.market_value)}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">總資產</span><span class="font-mono font-bold">${money(a.total_equity)}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">損益</span><span class="font-mono ${pn(a.pnl)}">${money(a.pnl)}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">報酬率</span><span class="font-mono font-bold ${pn(a.return_pct)}">${pct(a.return_pct)}</span></div>
              <div class="flex justify-between"><span class="text-gray-500">持股數</span><span class="font-mono">${a.position_count || 0} 檔</span></div>
                            <div class="grid grid-cols-2 gap-2 mt-3 pt-3 border-t border-border">
                <div>
                  <div class="text-[10px] text-gray-500">交易次數</div>
                  <div id="m_trades_${a.account_id}" class="font-mono text-xs">載入中</div>
                </div>
                <div>
                  <div class="text-[10px] text-gray-500">勝率</div>
                  <div id="m_win_${a.account_id}" class="font-mono text-xs">載入中</div>
                </div>
                <div>
                  <div class="text-[10px] text-gray-500">最大回撤</div>
                  <div id="m_dd_${a.account_id}" class="font-mono text-xs text-dn">載入中</div>
                </div>
                <div>
                  <div class="text-[10px] text-gray-500">Profit Factor</div>
                  <div id="m_pf_${a.account_id}" class="font-mono text-xs">載入中</div>
                </div>
              </div>
            </div>

            <button onclick="selectAccount(${a.account_id})"
              class="mt-4 w-full border border-border rounded py-1.5 text-xs hover:bg-surface-3">
              查看持倉 / 交易
            </button>
          </div>
        `).join("");
      }

      const options = '<option value="">選擇帳戶</option>' +
        accounts.map(a => `<option value="${a.account_id}">${a.name}</option>`).join("");

      if (sel) sel.innerHTML = options;
      if (mo) mo.innerHTML = options;

            function refreshMetrics() {
        accounts.forEach(a => loadMetricsIntoCard(a.account_id));
      }

      refreshMetrics();
      setTimeout(refreshMetrics, 300);
      setTimeout(refreshMetrics, 1000);
    } catch (e) {
      if (cards) cards.innerHTML = `<div class="text-dn text-sm">載入失敗：${e.message}</div>`;
    }
  };

    async function loadMetricsIntoCard(accountId) {
    const trades = document.getElementById(`m_trades_${accountId}`);
    const win = document.getElementById(`m_win_${accountId}`);
    const dd = document.getElementById(`m_dd_${accountId}`);
    const pf = document.getElementById(`m_pf_${accountId}`);

    function num(x, fallback = 0) {
      const v = Number(x);
      return Number.isFinite(v) ? v : fallback;
    }

    try {
      const m = await apiJson(`/api/strategies/${accountId}/metrics`);

      const tradeCount = num(m.trade_count ?? m.total_trades, 0);
      const winRate = num(m.win_rate, 0);
      const maxDrawdown = num(m.max_drawdown, 0);
      const profitFactor = m.profit_factor == null ? null : num(m.profit_factor, null);

      if (trades) trades.textContent = tradeCount;
      if (win) win.textContent = winRate.toFixed(2) + "%";
      if (dd) dd.textContent = "-" + maxDrawdown.toFixed(2) + "%";
      if (pf) pf.textContent = profitFactor == null ? "∞" : profitFactor.toFixed(2);

      if (win) {
        win.className = "font-mono text-xs " + (winRate >= 50 ? "text-up" : "text-dn");
      }

      if (dd) {
        dd.className = "font-mono text-xs text-dn";
      }

      if (pf) {
        pf.className = "font-mono text-xs " + (profitFactor == null || profitFactor >= 1 ? "text-up" : "text-dn");
      }
    } catch (e) {
      console.warn("metrics load failed", accountId, e);

      if (trades) trades.textContent = "錯誤";
      if (win) win.textContent = "錯誤";
      if (dd) dd.textContent = "錯誤";
      if (pf) pf.textContent = "錯誤";
    }
  }
  
  window.selectAccount = function (id) {
    const sel = document.getElementById("sel_account");
    if (sel) {
      sel.value = String(id);
      loadDetail();
    }
  };

  window.loadDetail = async function () {
    const id = document.getElementById("sel_account")?.value;
    const posBody = document.getElementById("pos_body");
    const tradeBody = document.getElementById("trade_body");

    if (!id) return;

    const positions = await apiJson(`/api/strategies/${id}/positions`);
    const trades = await apiJson(`/api/strategies/${id}/trades`);

    if (posBody) {
      posBody.innerHTML = positions.length ? positions.map(p => `
        <tr>
          <td class="px-4 py-2 font-mono">${p.code}</td>
          <td class="px-4 py-2 text-right font-mono">${p.lots}</td>
          <td class="px-4 py-2 text-right font-mono">${Number(p.avg_cost || 0).toFixed(2)}</td>
          <td class="px-4 py-2 text-right text-gray-500">—</td>
          <td class="px-4 py-2 text-right text-gray-500">—</td>
        </tr>
      `).join("") : '<tr><td colspan="5" class="text-center py-6 text-gray-500 text-xs">目前沒有持倉</td></tr>';
    }

    if (tradeBody) {
      tradeBody.innerHTML = trades.length ? trades.map(t => `
        <tr>
          <td class="px-3 py-2">${t.date || ""}</td>
          <td class="px-3 py-2 font-mono">${t.code || ""}</td>
          <td class="px-3 py-2 text-center">${t.direction || ""}</td>
          <td class="px-3 py-2 text-right font-mono">${t.lots || 0}</td>
          <td class="px-3 py-2 text-right font-mono">${Number(t.price || 0).toFixed(2)}</td>
          <td class="px-3 py-2 text-right font-mono">${Number(t.pnl || 0).toFixed(0)}</td>
          <td class="px-3 py-2 text-gray-500">${t.trigger || ""}</td>
        </tr>
      `).join("") : '<tr><td colspan="7" class="text-center py-6 text-gray-500 text-xs">目前沒有交易紀錄</td></tr>';
    }
  };

  window.createAccount = async function () {
    const name = document.getElementById("f_name")?.value.trim();
    const strategyClass = document.getElementById("f_class")?.value || "MomentumBreakout";
    const cash = Number(document.getElementById("f_cash")?.value || 200000);

    if (!name) {
      alert("請輸入帳戶名稱");
      return;
    }

    await apiJson("/api/strategies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        strategy_class: strategyClass,
        strategy_type: "rule_based",
        initial_cash: cash
      })
    });

    document.getElementById("f_name").value = "";
    await loadAccounts();
  };

  window.deleteAccount = async function (id) {
    if (!confirm("確定刪除此策略帳戶？")) return;
    await apiJson(`/api/strategies/${id}`, { method: "DELETE" });
    await loadAccounts();
  };

  document.addEventListener("DOMContentLoaded", function () {
    if (typeof renderParamForm === "function") renderParamForm();
    loadAccounts();
  });
})();

