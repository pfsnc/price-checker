"use strict";

const CATEGORIES = ["全部", "特", "紀", "文", "編號", "J", "T", "生肖", "年冊", "其他"];
const CHART_RANGE_DAYS = 30;
const UTC8_OFFSET_MS = 8 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const SERIES_CONFIG = [
  { key: "site_a_mint", cond: "mint", label: "新票 (金投網)", color: "#047857", bg: "rgba(4, 120, 87, 0.12)", dash: [] },
  { key: "site_b_mint", cond: "mint", label: "新票 (點購網)", color: "#34d399", bg: "rgba(52, 211, 153, 0.12)", dash: [4, 3] },
  { key: "site_a_cto",  cond: "cto",  label: "蓋銷 (金投網)", color: "#b45309", bg: "rgba(180, 83, 9, 0.12)", dash: [] },
  { key: "site_b_cto",  cond: "cto",  label: "蓋銷 (點購網)", color: "#fbbf24", bg: "rgba(251, 191, 36, 0.12)", dash: [4, 3] },
  { key: "site_a_used", cond: "used", label: "信銷 (金投網)", color: "#be123c", bg: "rgba(190, 18, 60, 0.12)", dash: [] },
  { key: "site_b_used", cond: "used", label: "信銷 (點購網)", color: "#fb7185", bg: "rgba(251, 113, 133, 0.12)", dash: [4, 3] },
];

let storeData = null;
let stampList = [];
let filteredList = [];
let currentCategory = "全部";
let activeCols = { mint: true, cto: true, used: true };

const sparklineCharts = new Map();
const expandedCharts = new Map();
let sparkObserver = null;

// ==========================================
// 穩定日期計算（預設 UTC+8，避免瀏覽器時區偏移）
// ==========================================
function getUtc8TodayParts() {
  const utc8 = new Date(Date.now() + UTC8_OFFSET_MS);
  return {
    year: utc8.getUTCFullYear(),
    month: utc8.getUTCMonth() + 1,
    day: utc8.getUTCDate(),
  };
}

function parseYmdUtcMs(dateStr) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr || "").trim());
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const utcMs = Date.UTC(year, month - 1, day);
  const check = new Date(utcMs);
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() + 1 !== month ||
    check.getUTCDate() !== day
  ) {
    return null;
  }
  return utcMs;
}

function filterHistoryByDays(history, days) {
  if (!Array.isArray(history)) return [];
  if (!days || days <= 0) return history;
  const { year, month, day } = getUtc8TodayParts();
  const cutoffMs = Date.UTC(year, month - 1, day) - days * DAY_MS;
  return history.filter((pt) => {
    const ptMs = parseYmdUtcMs(pt?.date);
    return ptMs !== null && ptMs >= cutoffMs;
  });
}

function getDisplayDate(item) {
  const latest = item.history?.[item.history.length - 1] || {};
  return item.latest_source_date || latest.date || "-";
}

// ==========================================
// 自然排序 (Catalog Number 優先，其次 Name)
// ==========================================
function naturalTokenize(str) {
  return String(str || "").match(/\d+|\D+/g) || [];
}

function naturalCompare(a, b) {
  const ta = naturalTokenize(a);
  const tb = naturalTokenize(b);
  const len = Math.max(ta.length, tb.length);
  for (let i = 0; i < len; i++) {
    if (i >= ta.length) return -1;
    if (i >= tb.length) return 1;
    const pa = ta[i];
    const pb = tb[i];
    const na = /^\d+$/.test(pa);
    const nb = /^\d+$/.test(pb);
    if (na && nb) {
      const diff = Number(pa) - Number(pb);
      if (diff !== 0) return diff;
    } else {
      const cmp = pa.localeCompare(pb, "zh-Hans-CN");
      if (cmp !== 0) return cmp;
    }
  }
  return 0;
}

function compareStamps(a, b) {
  const ca = (a.code || "").trim();
  const cb = (b.code || "").trim();
  if (ca && !cb) return -1;
  if (!ca && cb) return 1;
  if (ca && cb) {
    const codeCmp = naturalCompare(ca, cb);
    if (codeCmp !== 0) return codeCmp;
  }
  return naturalCompare(a.name, b.name);
}

// ==========================================
// AES-256-GCM 解密
// ==========================================
function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function decryptDataEnc(envelope, password) {
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    enc.encode(password),
    "PBKDF2",
    false,
    ["deriveKey"]
  );
  const aesKey = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: b64ToBytes(envelope.salt),
      iterations: Number(envelope.iter || 100000),
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );
  const plainBuffer = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: b64ToBytes(envelope.iv) },
    aesKey,
    b64ToBytes(envelope.ct)
  );
  return JSON.parse(new TextDecoder().decode(plainBuffer));
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function hasPositivePrice(val) {
  return val !== null && val !== undefined && Number.isFinite(Number(val)) && Number(val) > 0;
}

function fmtNum(val) {
  if (!hasPositivePrice(val)) {
    return '<span class="text-slate-300">--</span>';
  }
  return Number(val).toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

function fmtDualPair(aVal, bVal, darkCls, lightCls) {
  return `
    <span class="${darkCls}">${fmtNum(aVal)}</span>
    <span class="text-slate-300 mx-0.5">/</span>
    <span class="${lightCls}">${fmtNum(bVal)}</span>
  `;
}

function cleanupCharts() {
  if (sparkObserver) {
    sparkObserver.disconnect();
    sparkObserver = null;
  }
  sparklineCharts.forEach((c) => c.destroy());
  sparklineCharts.clear();
  expandedCharts.forEach((c) => c.destroy());
  expandedCharts.clear();
}

function renderCategoryTabs() {
  const container = document.getElementById("categoryTabs");
  const counts = { "全部": stampList.length };
  for (const s of stampList) counts[s.category] = (counts[s.category] || 0) + 1;

  container.innerHTML = CATEGORIES.map((cat) => {
    const active = cat === currentCategory;
    const cnt = counts[cat] || 0;
    const cls = active
      ? "bg-indigo-600 text-white shadow-sm"
      : "bg-slate-100 text-slate-700 hover:bg-slate-200";
    return `
      <button type="button" data-cat="${escapeHtml(cat)}"
        class="cat-btn shrink-0 whitespace-nowrap px-2.5 sm:px-3 py-1 sm:py-1.5 rounded-full text-xs font-medium transition ${cls}">
        ${escapeHtml(cat)}<span class="hidden sm:inline opacity-75 ml-1">(${cnt})</span>
      </button>`;
  }).join("");

  container.querySelectorAll(".cat-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentCategory = btn.dataset.cat;
      renderCategoryTabs();
      renderTable();
    });
  });
}

function renderDashboardSummary() {
  const totalChanges = stampList.reduce((acc, s) => acc + (s.history?.length || 0), 0);
  document.getElementById("statStamps").textContent = `${filteredList.length} / ${stampList.length}`;
  document.getElementById("statChanges").textContent = String(totalChanges);
  document.getElementById("statUpdated").textContent = storeData?.updated_at || "-";
  document.getElementById("headerLastChecked").textContent = storeData?.last_checked || "-";
}

function buildDatasets(hist, isSparkline) {
  const isSingle = hist.length === 1;
  const activeSeries = SERIES_CONFIG.filter((cfg) =>
    hist.some((h) => hasPositivePrice(h[cfg.key]))
  );
  return activeSeries.map((cfg) => ({
    label: cfg.label,
    data: hist.map((h) => (hasPositivePrice(h[cfg.key]) ? Number(h[cfg.key]) : null)),
    borderColor: cfg.color,
    backgroundColor: cfg.bg,
    borderDash: cfg.dash,
    borderWidth: isSparkline ? 1.75 : 2.2,
    tension: 0.22,
    spanGaps: true,
    pointRadius: isSparkline ? (isSingle ? 2.5 : 0) : 3.5,
    pointHoverRadius: isSparkline ? 3 : 5.5,
    pointBackgroundColor: cfg.color,
  }));
}

function drawSparkline(canvasEl, idx) {
  if (sparklineCharts.has(idx) || typeof window.Chart !== "function") return;
  const item = filteredList[idx];
  if (!item || !item.history?.length) return;

  // 只顯示最近 30 天日期範圍內的資料（非最後 30 筆）
  const hist30 = filterHistoryByDays(item.history, CHART_RANGE_DAYS);
  const datasets = buildDatasets(hist30, true);
  if (!hist30.length || !datasets.length) {
    const emptyEl = document.getElementById(`spark-empty-${idx}`);
    if (emptyEl) emptyEl.classList.remove("hidden");
    return;
  }

  const chart = new window.Chart(canvasEl.getContext("2d"), {
    type: "line",
    data: { labels: hist30.map((h) => h.date), datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          callbacks: {
            label: (c) => `${c.dataset.label}: ${c.parsed.y !== null ? c.parsed.y + "元" : "--"}`,
          },
        },
      },
      scales: {
        x: { display: false },
        y: { display: false, beginAtZero: false },
      },
    },
  });
  sparklineCharts.set(idx, chart);
}

function renderExpandedContent(idx, item, days) {
  const fullHist = item.history || [];
  const viewHist = filterHistoryByDays(fullHist, days);
  const reversed = [...viewHist].reverse();

  const itemHasMint = fullHist.some((h) => hasPositivePrice(h.site_a_mint) || hasPositivePrice(h.site_b_mint));
  const itemHasCto = fullHist.some((h) => hasPositivePrice(h.site_a_cto) || hasPositivePrice(h.site_b_cto));
  const itemHasUsed = fullHist.some((h) => hasPositivePrice(h.site_a_used) || hasPositivePrice(h.site_b_used));

  const tbodyEl = document.getElementById(`detail-tbody-${idx}`);
  if (tbodyEl) {
    if (!reversed.length) {
      const cols = 1 + (itemHasMint ? 2 : 0) + (itemHasCto ? 2 : 0) + (itemHasUsed ? 2 : 0);
      tbodyEl.innerHTML = `<tr><td colspan="${cols}" class="px-3 py-6 text-center text-slate-400">近 ${CHART_RANGE_DAYS} 天內無價格變動紀錄</td></tr>`;
    } else {
      tbodyEl.innerHTML = reversed.map((r) => `
        <tr class="hover:bg-slate-50">
          <td class="px-2.5 py-1.5 text-slate-700 whitespace-nowrap">${escapeHtml(r.date)}</td>
          ${itemHasMint ? `<td class="px-2 py-1.5 text-right font-medium text-emerald-700">${fmtNum(r.site_a_mint)}</td><td class="px-2 py-1.5 text-right font-medium text-emerald-500">${fmtNum(r.site_b_mint)}</td>` : ""}
          ${itemHasCto ? `<td class="px-2 py-1.5 text-right font-medium text-amber-700">${fmtNum(r.site_a_cto)}</td><td class="px-2 py-1.5 text-right font-medium text-amber-500">${fmtNum(r.site_b_cto)}</td>` : ""}
          ${itemHasUsed ? `<td class="px-2 py-1.5 text-right font-medium text-rose-700">${fmtNum(r.site_a_used)}</td><td class="px-2 py-1.5 text-right font-medium text-rose-400">${fmtNum(r.site_b_used)}</td>` : ""}
        </tr>
      `).join("");
    }
  }

  if (expandedCharts.has(idx)) {
    expandedCharts.get(idx).destroy();
    expandedCharts.delete(idx);
  }

  const emptyHint = document.getElementById(`expanded-empty-${idx}`);
  const expCanvas = document.getElementById(`expanded-chart-${idx}`);
  const datasets = buildDatasets(viewHist, false);

  if (emptyHint) {
    if (!viewHist.length || !datasets.length) {
      emptyHint.classList.remove("hidden");
    } else {
      emptyHint.classList.add("hidden");
    }
  }

  if (expCanvas && typeof window.Chart === "function") {
    const expChart = new window.Chart(expCanvas.getContext("2d"), {
      type: "line",
      data: {
        labels: viewHist.map((h) => h.date),
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "top", labels: { boxWidth: 14, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              afterTitle: (items) => {
                const i = items[0]?.dataIndex;
                return i !== undefined && viewHist[i]?.ts ? `抓取時間：${viewHist[i].ts}` : "";
              },
              label: (c) => `${c.dataset.label}: ${c.parsed.y !== null ? c.parsed.y + " 元" : "--"}`,
            },
          },
        },
        scales: {
          y: { beginAtZero: false, ticks: { callback: (v) => v + "元" } },
        },
      },
    });
    expandedCharts.set(idx, expChart);
  }
}

function toggleDetailRow(idx, colSpan) {
  const detailRow = document.getElementById(`detail-row-${idx}`);
  const arrowEl = document.getElementById(`arrow-${idx}`);
  if (!detailRow) return;

  if (!detailRow.classList.contains("hidden")) {
    detailRow.classList.add("hidden");
    if (arrowEl) arrowEl.textContent = "▸";
    if (expandedCharts.has(idx)) {
      expandedCharts.get(idx).destroy();
      expandedCharts.delete(idx);
    }
    return;
  }

  detailRow.classList.remove("hidden");
  if (arrowEl) arrowEl.textContent = "▾";

  const item = filteredList[idx];
  const fullHist = item.history || [];
  const last = fullHist[fullHist.length - 1] || {};

  const itemHasMint = fullHist.some((h) => hasPositivePrice(h.site_a_mint) || hasPositivePrice(h.site_b_mint));
  const itemHasCto = fullHist.some((h) => hasPositivePrice(h.site_a_cto) || hasPositivePrice(h.site_b_cto));
  const itemHasUsed = fullHist.some((h) => hasPositivePrice(h.site_a_used) || hasPositivePrice(h.site_b_used));

  const cardsHtml = [
    itemHasMint ? `
      <div class="bg-white rounded-lg border border-emerald-200 p-3">
        <div class="text-xs text-emerald-800 font-medium">新票比價 (金投 vs 點購)</div>
        <div class="text-base font-bold mt-1 flex items-center gap-2">
          <span class="text-emerald-700">金投 ${fmtNum(last.site_a_mint)}</span>
          <span class="text-slate-300">|</span>
          <span class="text-emerald-500">點購 ${fmtNum(last.site_b_mint)}</span>
        </div>
      </div>` : "",
    itemHasCto ? `
      <div class="bg-white rounded-lg border border-amber-200 p-3">
        <div class="text-xs text-amber-800 font-medium">蓋銷比價 (金投 vs 點購)</div>
        <div class="text-base font-bold mt-1 flex items-center gap-2">
          <span class="text-amber-700">金投 ${fmtNum(last.site_a_cto)}</span>
          <span class="text-slate-300">|</span>
          <span class="text-amber-500">點購 ${fmtNum(last.site_b_cto)}</span>
        </div>
      </div>` : "",
    itemHasUsed ? `
      <div class="bg-white rounded-lg border border-rose-200 p-3">
        <div class="text-xs text-rose-800 font-medium">信銷比價 (金投 vs 點購)</div>
        <div class="text-base font-bold mt-1 flex items-center gap-2">
          <span class="text-rose-700">金投 ${fmtNum(last.site_a_used)}</span>
          <span class="text-slate-300">|</span>
          <span class="text-rose-400">點購 ${fmtNum(last.site_b_used)}</span>
        </div>
      </div>` : "",
  ].filter(Boolean).join("");

  const panel = document.getElementById(`detail-panel-${idx}`);
  panel.colSpan = colSpan;
  panel.innerHTML = `
    <div class="p-3.5 sm:p-5 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 pb-3">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-bold text-slate-900 text-sm sm:text-base">${escapeHtml(item.name)}</span>
          <span class="px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 text-xs font-semibold">${escapeHtml(item.category)}</span>
          ${item.code ? `<span class="px-2 py-0.5 rounded bg-slate-200 text-slate-700 font-mono text-xs">${escapeHtml(item.code)}</span>` : ""}
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-xs text-slate-500 mr-1">
            更新日：${escapeHtml(getDisplayDate(item))} · 全年保留 ${fullHist.length} 筆
          </span>
          <button type="button" data-days="30"
            class="detail-range-btn-${idx} px-2.5 py-1 rounded text-xs font-medium bg-indigo-600 text-white">
            近 30 天
          </button>
          <button type="button" data-days="0"
            class="detail-range-btn-${idx} px-2.5 py-1 rounded text-xs font-medium bg-slate-200 text-slate-700 hover:bg-slate-300">
            全部歷史
          </button>
        </div>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">${cardsHtml}</div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div class="bg-white rounded-lg border border-slate-200 p-3">
          <div class="text-xs font-semibold text-slate-600 mb-2">雙站同色系深淺走勢對比圖（元）</div>
          <div class="relative h-56 sm:h-60">
            <canvas id="expanded-chart-${idx}"></canvas>
            <div id="expanded-empty-${idx}"
              class="hidden absolute inset-0 flex items-center justify-center text-xs text-slate-400 bg-white/80 pointer-events-none">
              近 ${CHART_RANGE_DAYS} 天內無價格變動紀錄，可切換「全部歷史」檢視
            </div>
          </div>
        </div>

        <div class="bg-white rounded-lg border border-slate-200 overflow-hidden">
          <div class="px-3 py-2 bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600">
            雙站歷史報價明細表
          </div>
          <div class="max-h-60 overflow-auto">
            <table class="min-w-full divide-y divide-slate-100 text-xs">
              <thead class="bg-white text-slate-500 sticky top-0">
                <tr>
                  <th class="px-2.5 py-2 text-left font-medium">更新日</th>
                  ${itemHasMint ? `<th class="px-2 py-2 text-right font-medium text-emerald-700">金投新</th><th class="px-2 py-2 text-right font-medium text-emerald-500">點購新</th>` : ""}
                  ${itemHasCto ? `<th class="px-2 py-2 text-right font-medium text-amber-700">金投蓋</th><th class="px-2 py-2 text-right font-medium text-amber-500">點購蓋</th>` : ""}
                  ${itemHasUsed ? `<th class="px-2 py-2 text-right font-medium text-rose-700">金投信</th><th class="px-2 py-2 text-right font-medium text-rose-400">點購信</th>` : ""}
                </tr>
              </thead>
              <tbody id="detail-tbody-${idx}" class="divide-y divide-slate-100"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  `;

  panel.querySelectorAll(`.detail-range-btn-${idx}`).forEach((btn) => {
    btn.addEventListener("click", () => {
      panel.querySelectorAll(`.detail-range-btn-${idx}`).forEach((b) => {
        b.className = `detail-range-btn-${idx} px-2.5 py-1 rounded text-xs font-medium bg-slate-200 text-slate-700 hover:bg-slate-300`;
      });
      btn.className = `detail-range-btn-${idx} px-2.5 py-1 rounded text-xs font-medium bg-indigo-600 text-white`;
      renderExpandedContent(idx, item, Number(btn.dataset.days));
    });
  });

  renderExpandedContent(idx, item, CHART_RANGE_DAYS);
}

function renderTable() {
  cleanupCharts();
  const kw = document.getElementById("searchInput").value.trim().toLowerCase();

  filteredList = stampList
    .filter((item) => {
      if (currentCategory !== "全部" && item.category !== currentCategory) return false;
      if (kw && !`${item.name} ${item.code}`.toLowerCase().includes(kw)) return false;
      return true;
    })
    .sort(compareStamps);

  renderDashboardSummary();

  activeCols = {
    mint: filteredList.some((item) => {
      const l = item.history?.[item.history.length - 1] || {};
      return hasPositivePrice(l.site_a_mint) || hasPositivePrice(l.site_b_mint);
    }),
    cto: filteredList.some((item) => {
      const l = item.history?.[item.history.length - 1] || {};
      return hasPositivePrice(l.site_a_cto) || hasPositivePrice(l.site_b_cto);
    }),
    used: filteredList.some((item) => {
      const l = item.history?.[item.history.length - 1] || {};
      return hasPositivePrice(l.site_a_used) || hasPositivePrice(l.site_b_used);
    }),
  };

  const thead = document.getElementById("stampTableHead");
  thead.innerHTML = `
    <tr>
      <th class="px-2.5 sm:px-3 py-3 text-left font-semibold w-16 sm:w-20">志號</th>
      <th class="px-2.5 sm:px-3 py-3 text-left font-semibold">郵票名稱</th>
      <th class="px-2 py-3 text-center font-semibold w-14">分類</th>
      ${activeCols.mint ? `<th class="px-2.5 sm:px-3 py-3 text-right font-semibold text-emerald-700 whitespace-nowrap">新票 (金投/點購)</th>` : ""}
      ${activeCols.cto ? `<th class="px-2.5 sm:px-3 py-3 text-right font-semibold text-amber-700 whitespace-nowrap">蓋銷 (金投/點購)</th>` : ""}
      ${activeCols.used ? `<th class="px-2.5 sm:px-3 py-3 text-right font-semibold text-rose-700 whitespace-nowrap">信銷 (金投/點購)</th>` : ""}
      <th class="px-2.5 sm:px-3 py-3 text-center font-semibold w-36 sm:w-44">近30天走勢</th>
      <th class="px-2.5 sm:px-3 py-3 text-center font-semibold w-24">更新日</th>
      <th class="px-2 py-3 text-center font-semibold w-10">明細</th>
    </tr>
  `;

  const colSpan = 6 + (activeCols.mint ? 1 : 0) + (activeCols.cto ? 1 : 0) + (activeCols.used ? 1 : 0);
  const tbody = document.getElementById("stampTableBody");

  if (!filteredList.length) {
    tbody.innerHTML = `<tr><td colspan="${colSpan}" class="px-4 py-10 text-center text-slate-400">查無符合條件的郵票資料</td></tr>`;
    return;
  }

  tbody.innerHTML = filteredList.map((item, idx) => {
    const latest = item.history?.[item.history.length - 1] || {};
    return `
      <tr data-idx="${idx}" class="stamp-row hover:bg-indigo-50/40 cursor-pointer transition h-[54px]">
        <td class="px-2.5 sm:px-3 py-1.5 font-mono text-xs text-slate-600 font-semibold whitespace-nowrap">${escapeHtml(item.code || "-")}</td>
        <td class="px-2.5 sm:px-3 py-1.5 font-medium text-slate-900 min-w-[120px]">${escapeHtml(item.name)}</td>
        <td class="px-2 py-1.5 text-center">
          <span class="px-1.5 sm:px-2 py-0.5 rounded bg-slate-100 text-slate-700 text-xs font-medium">${escapeHtml(item.category)}</span>
        </td>
        ${activeCols.mint ? `
          <td class="px-2.5 sm:px-3 py-1.5 text-right font-semibold whitespace-nowrap text-xs">
            ${fmtDualPair(latest.site_a_mint, latest.site_b_mint, "text-emerald-700", "text-emerald-500")}
          </td>` : ""}
        ${activeCols.cto ? `
          <td class="px-2.5 sm:px-3 py-1.5 text-right font-semibold whitespace-nowrap text-xs">
            ${fmtDualPair(latest.site_a_cto, latest.site_b_cto, "text-amber-700", "text-amber-500")}
          </td>` : ""}
        ${activeCols.used ? `
          <td class="px-2.5 sm:px-3 py-1.5 text-right font-semibold whitespace-nowrap text-xs">
            ${fmtDualPair(latest.site_a_used, latest.site_b_used, "text-rose-700", "text-rose-400")}
          </td>` : ""}
        <td class="px-2 py-1">
          <div class="w-32 sm:w-40 h-[42px] mx-auto relative">
            <canvas class="sparkline-canvas" data-idx="${idx}"></canvas>
            <div id="spark-empty-${idx}"
              class="hidden absolute inset-0 flex items-center justify-center text-[10px] text-slate-300 pointer-events-none">
              近30天無異動
            </div>
          </div>
        </td>
        <td class="px-2.5 sm:px-3 py-1.5 text-center text-xs text-slate-500 whitespace-nowrap">${escapeHtml(getDisplayDate(item))}</td>
        <td class="px-2 py-1.5 text-center text-indigo-600 font-bold select-none">
          <span id="arrow-${idx}">▸</span>
        </td>
      </tr>
      <tr id="detail-row-${idx}" class="hidden bg-slate-50/90">
        <td id="detail-panel-${idx}" colspan="${colSpan}" class="p-0 border-t border-indigo-100"></td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll(".stamp-row").forEach((row) => {
    row.addEventListener("click", () => toggleDetailRow(Number(row.dataset.idx), colSpan));
  });

  sparkObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const canvas = entry.target;
          drawSparkline(canvas, Number(canvas.dataset.idx));
          sparkObserver.unobserve(canvas);
        }
      }
    },
    { rootMargin: "150px 0px" }
  );

  tbody.querySelectorAll(".sparkline-canvas").forEach((canvas) => sparkObserver.observe(canvas));
}

function getDataSourceUrl() {
  const configured = (document.body?.dataset?.apiUrl || "").trim();
  if (configured && !configured.includes("你的")) {
    return configured;
  }
  return "./data.enc";
}

async function decryptResponsePayload(payload, password) {
  if (payload && Array.isArray(payload.chunks)) {
    if (!payload.chunks.length) {
      throw new Error("雲端資料庫目前為空，請先執行爬蟲或遷移工作流程。");
    }
    const merged = { updated_at: "", last_checked: "", latest_source_date: "", stamps: {} };
    for (const chunk of payload.chunks) {
      const envelope = JSON.parse(chunk.envelope_json);
      const obj = await decryptDataEnc(envelope, password);
      if (chunk.chunk_key === "__meta__") {
        merged.updated_at = obj.updated_at || "";
        merged.last_checked = obj.last_checked || "";
        merged.latest_source_date = obj.latest_source_date || "";
      } else if (obj && typeof obj.stamps === "object") {
        Object.assign(merged.stamps, obj.stamps);
      }
    }
    return merged;
  }
  return decryptDataEnc(payload, password);
}

function initApp() {
  const unlockForm = document.getElementById("unlockForm");
  const searchInput = document.getElementById("searchInput");
  const lockBtn = document.getElementById("lockBtn");
  if (!unlockForm || !searchInput || !lockBtn) return;

  unlockForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pwInput = document.getElementById("passwordInput");
    const errBox = document.getElementById("authError");
    const btn = document.getElementById("unlockBtn");
    const password = pwInput.value;

    errBox.classList.add("hidden");
    btn.disabled = true;
    btn.textContent = "正在下載並解密資料...";

    try {
      if (typeof window.Chart !== "function") {
        throw new Error("本機圖表套件尚未載入，請確認建置資產是否完整。");
      }

      const sourceUrl = getDataSourceUrl();
      const sep = sourceUrl.includes("?") ? "&" : "?";
      const res = await fetch(`${sourceUrl}${sep}t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`無法載入加密資料 (HTTP ${res.status})`);
      const payload = await res.json();

      storeData = await decryptResponsePayload(payload, password);
      stampList = Object.values(storeData.stamps || {}).sort(compareStamps);

      pwInput.value = "";
      document.getElementById("lockScreen").classList.add("hidden");
      document.getElementById("appScreen").classList.remove("hidden");

      renderCategoryTabs();
      renderTable();
    } catch (err) {
      console.error("解密／載入失敗類型:", err?.name, err?.message);
      const msg = err?.message || "";
      if (err?.name === "OperationError") {
        errBox.textContent = "密碼錯誤（與 GitHub Secrets 的 ENCRYPT_PASSWORD 不符），請檢查大小寫與空白。";
      } else if (err?.name === "TypeError") {
        errBox.textContent = "連線或 CORS 失敗，請按 F12 查看 Network 分頁。";
      } else if (msg.includes("HTTP") || msg.includes("圖表套件") || msg.includes("雲端資料庫")) {
        errBox.textContent = msg;
      } else {
        errBox.textContent = `資料格式異常（${err?.name || "Error"}），請打開 F12 Console 確認。`;
      }
      errBox.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "解密並載入儀表板";
    }
  });

  let searchTimer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderTable, 180);
  });

  lockBtn.addEventListener("click", () => {
    cleanupCharts();
    storeData = null;
    stampList = [];
    filteredList = [];
    document.getElementById("stampTableHead").innerHTML = "";
    document.getElementById("stampTableBody").innerHTML = "";
    document.getElementById("appScreen").classList.add("hidden");
    document.getElementById("lockScreen").classList.remove("hidden");
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}
