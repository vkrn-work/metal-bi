/* Дашборд «Конверсия КП → Заказ»: фильтры, таблица с раскрытием, кольца, динамика. */
const $ = (id) => document.getElementById(id);
const FILTER_IDS = ["client_1", "client_2", "client_3", "client_4", "client_5", "selection"];
const PALETTE = ["#2563eb", "#7db3f7", "#22c55e", "#f5b301", "#8b5cf6", "#06b6d4",
                 "#ec4899", "#fb923c", "#1e3a8a", "#93a4c8", "#9aa5b8"];

/* Методика показателей. Тот же текст — на странице /metrics. */
const METRIC_HELP = {
  kp_docs: ["Документов КП",
    "Сколько разных коммерческих предложений содержали эту категорию. Единица счёта — документ, а не строка: тридцать позиций труб в одном КП это один коммерческий шанс, а не тридцать.",
    "COUNT(DISTINCT номер КП)"],
  zk_docs: ["Документов Заказ",
    "Сколько разных заказов содержали эту категорию.",
    "COUNT(DISTINCT номер ЗК)"],
  lines: ["Позиции",
    "Число строк — упоминаний категории в документах. Мера объёма спроса. В знаменателе конверсии НЕ используется: один документ на 903 строки перекосил бы всю категорию.",
    "COUNT(*)"],
  margin: ["Маржа",
    "Сумма маржи по всем строкам категории, всегда в евро. Сумма чувствительна к единичным крупным сделкам, поэтому для приоритета используйте медиану и ожидаемую маржу.",
    "SUM(маржа)"],
  cr: ["Конверсия КП-ЗК",
    "С какой вероятностью категория, попавшая в предложение, дойдёт до заказа. Основной показатель шанса.",
    "уникальных ЗК / уникальных КП"],
  median: ["Медиана заказа",
    "Типичный размер маржи одного заказа по категории. Медиана, а не среднее: единичные сделки на десятки миллионов ломают среднее. Заказы без маржи не учитываются.",
    "MEDIAN(маржа одного ЗК)"],
  expected: ["Ожидаемая маржа на одно КП",
    "Сколько евро маржи приносит одно предложение с этой категорией. Объединяет шанс закрытия и типичный размер сделки — это и есть показатель приоритета направления.",
    "конверсия × медиана заказа"],
  reliable: ["Достоверность",
    "Заказов меньше 30: доверительный интервал конверсии слишком широк, различия с соседними категориями недостоверны. Принимать решения по такой строке нельзя.",
    "число заказов < 30"],
};

let charts = {};
let expanded = new Set();   // раскрытые узлы дерева (по пути)
let lastTree = [];

/* ---------------------------------------------------------- форматирование */
const nf = new Intl.NumberFormat("ru-RU");
const money = (v) => nf.format(Math.round(v || 0));
const eur = (v) => (v === null || v === undefined ? "—" : nf.format(Math.round(v)) + " €");
const pct = (v, d = 2) =>
  v === null || v === undefined ? "—" : (v * 100).toFixed(d).replace(".", ",") + "%";

/* ---------------------------------------------------------- фильтры */
async function loadFilters() {
  const r = await fetch("/api/filters");
  if (r.status === 401) return (location.href = "/login");
  const data = await r.json();
  if (!data.ready) return;

  $("dateFrom").value = data.min_date;
  $("dateTo").value = data.max_date;
  $("dateFrom").min = $("dateTo").min = data.min_date;
  $("dateFrom").max = $("dateTo").max = data.max_date;
  $("dataPeriod").textContent =
    `Период данных в отчёте: ${fmtDate(data.min_date)} – ${fmtDate(data.max_date)}`;

  fill($("source"), data.source);
  FILTER_IDS.forEach((k) => data.filters[k] && fill($(k), data.filters[k].values));
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function fill(sel, values) {
  values.forEach((v) => {
    const o = document.createElement("option");
    o.value = o.textContent = v;
    sel.appendChild(o);
  });
}

function currentFilters() {
  const f = {
    date_from: $("dateFrom").value,
    date_to: $("dateTo").value,
    source: $("source").value ? [$("source").value] : [],
  };
  FILTER_IDS.forEach((k) => (f[k] = $(k).value ? [$(k).value] : []));
  return f;
}

/* ---------------------------------------------------------- загрузка отчёта */
function showProblem(text) {
  const box = $("empty");
  box.hidden = false;
  box.textContent = text;
  $("app").hidden = true;
}

async function loadReport() {
  try {
    const r = await fetch("/api/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentFilters()),
    });
    if (r.status === 401) return (location.href = "/login");
    if (!r.ok) {
      const text = await r.text();
      return showProblem(`Ошибка сервера (${r.status}). ${text.slice(0, 400)}`);
    }
    const d = await r.json();
    if (!d.ready) {
      return showProblem("База пуста. Загрузите выгрузки на странице «Данные».");
    }
    $("empty").hidden = true;
    $("app").hidden = false;
    renderKpi(d);
    lastTree = d.tree;
    renderTable(d);
    renderDonut("Kp", d.shares_kp, d.totals.kp_docs);
    renderDonut("Zk", d.shares_zk, d.totals.zk_docs);
    renderHalf(d.halfyears);
  } catch (err) {
    showProblem("Не удалось построить отчёт: " + err);
  }
}

/* ---------------------------------------------------------- KPI */
function renderKpi(d) {
  $("crCount").textContent = pct(d.totals.cr_count);
  $("expected").textContent = eur(d.totals.expected);
  delta("crCountDelta", d.totals.cr_count, d.prev_totals && d.prev_totals.cr_count, "pp");
  delta("expectedDelta", d.totals.expected, d.prev_totals && d.prev_totals.expected, "eur");
}

function delta(id, now, before, kind) {
  const el = $(id);
  if (now === null || now === undefined || before === null || before === undefined) {
    el.innerHTML = "";
    return;
  }
  const raw = kind === "pp" ? (now - before) * 100 : now - before;
  const eps = kind === "pp" ? 0.004 : 0.5;
  const cls = raw > eps ? "up" : raw < -eps ? "down" : "flat";
  const arrow = cls === "up" ? "↑" : cls === "down" ? "↓" : "→";
  const val = kind === "pp"
    ? Math.abs(raw).toFixed(2).replace(".", ",") + " п.п."
    : nf.format(Math.round(Math.abs(raw))) + " €";
  el.innerHTML = `<b class="${cls}">${arrow} ${val}</b>
                  <span class="flat">к периоду ранее</span>`;
}

/* ---------------------------------------------------------- таблица */
function renderTable(d) {
  const tb = $("tbody");
  tb.innerHTML = "";
  lastTree.forEach((node) => addRow(tb, node, [], 1));

  const t = d.totals;
  $("tfoot").innerHTML =
    `<td>Итого</td>
     <td class="sep">${nf.format(t.kp_docs)}</td><td>${nf.format(t.kp_lines)}</td><td>${money(t.kp_margin)}</td>
     <td class="sep">${nf.format(t.zk_docs)}</td><td>${nf.format(t.zk_lines)}</td><td>${money(t.zk_margin)}</td>
     <td class="sep">${pct(t.cr_count)}</td><td>${eur(t.zk_median)}</td><td>${eur(t.expected)}</td>`;
}

function addRow(tb, node, parentPath, depth) {
  const path = parentPath.concat(node.name);
  const key = path.join(" / ");
  const has = node.children && node.children.length > 0;
  const open = expanded.has(key);

  const tr = document.createElement("tr");
  tr.className = "lvl" + depth + (node.reliable ? "" : " weak");
  const pad = 6 + (depth - 1) * 20;
  const flag = node.reliable
    ? ""
    : `<span class="help warn-flag" data-metric="reliable">!</span>`;
  tr.innerHTML =
    `<td><div class="cell-name" style="padding-left:${pad}px">
       <button class="tog ${has ? "" : "empty"}" data-key="${escapeAttr(key)}">${has ? (open ? "−" : "+") : ""}</button>
       <span>${escapeHtml(node.name)}</span>${flag}</div></td>
     <td class="sep">${nf.format(node.kp_docs)}</td><td>${nf.format(node.kp_lines)}</td><td>${money(node.kp_margin)}</td>
     <td class="sep">${nf.format(node.zk_docs)}</td><td>${nf.format(node.zk_lines)}</td><td>${money(node.zk_margin)}</td>
     <td class="sep">${pct(node.cr_count)}</td><td>${eur(node.zk_median)}</td><td>${eur(node.expected)}</td>`;
  tb.appendChild(tr);

  if (has) {
    tr.querySelector(".tog").addEventListener("click", () => {
      expanded.has(key) ? expanded.delete(key) : expanded.add(key);
      const tbody = $("tbody");
      tbody.innerHTML = "";
      lastTree.forEach((n) => addRow(tbody, n, [], 1));
    });
    if (open) node.children.forEach((c) => addRow(tb, c, path, depth + 1));
  }
}

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escapeAttr = escapeHtml;

/* ---------------------------------------------------------- кольца */
function renderDonut(suffix, shares, total) {
  $("total" + suffix).textContent = nf.format(total);
  const canvas = $(suffix === "Kp" ? "donutKp" : "donutZk");
  const colors = shares.map((_, i) => PALETTE[i % PALETTE.length]);

  charts[suffix] && charts[suffix].destroy();
  charts[suffix] = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: shares.map((s) => s.name),
      datasets: [{ data: shares.map((s) => s.value), backgroundColor: colors,
                   borderWidth: 2, borderColor: "#fff" }],
    },
    options: {
      cutout: "62%", responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => ` ${c.label}: ${nf.format(c.raw)} (${(shares[c.dataIndex].share * 100).toFixed(1)}%)`,
          },
        },
      },
    },
  });

  $("legend" + suffix).innerHTML = shares
    .map((s, i) => `<div><i style="background:${colors[i]}"></i>
        <span title="${escapeAttr(s.name)}">${escapeHtml(s.name)}</span>
        <b>${(s.share * 100).toFixed(1)}%</b></div>`)
    .join("");
}

/* ---------------------------------------------------------- динамика */
function renderHalf(rows) {
  charts.half && charts.half.destroy();
  charts.half = new Chart($("halfChart"), {
    type: "line",
    data: {
      labels: rows.map((r) => r.label),
      datasets: [
        { label: "Конверсия КП-ЗК", data: rows.map((r) => (r.cr_count ?? 0) * 100),
          borderColor: "#2563eb", backgroundColor: "#2563eb", yAxisID: "y", tension: .1,
          pointRadius: 5, borderWidth: 2 },
        { label: "Ожидаемая маржа на КП", data: rows.map((r) => r.expected ?? 0),
          borderColor: "#16a34a", backgroundColor: "#16a34a", yAxisID: "y1", tension: .1,
          pointRadius: 5, borderWidth: 2 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: "top", labels: { usePointStyle: true, boxWidth: 8, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            label: (c) => c.datasetIndex === 0
              ? ` Конверсия: ${c.raw.toFixed(2).replace(".", ",")}%`
              : ` Ожидаемая маржа: ${nf.format(Math.round(c.raw))} €`,
          },
        },
      },
      scales: {
        y: { position: "left", ticks: { color: "#2563eb", callback: (v) => v + "%" },
             grid: { color: "#eef1f6" }, beginAtZero: true },
        y1: { position: "right", ticks: { color: "#16a34a", callback: (v) => nf.format(v) + " €" },
              grid: { drawOnChartArea: false }, beginAtZero: true },
        x: { grid: { display: false } },
      },
    },
  });
}

/* ---------------------------------------------------------- всплывающие подсказки */
function initTooltips() {
  let tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-metric]");
    if (!el) return;
    const meta = METRIC_HELP[el.dataset.metric];
    if (!meta) return;
    tip.innerHTML = `<b>${escapeHtml(meta[0])}</b><p>${escapeHtml(meta[1])}</p>
                     <code>${escapeHtml(meta[2])}</code>`;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = 320;
    let left = r.left + r.width / 2 - w / 2;
    left = Math.max(10, Math.min(left, window.innerWidth - w - 10));
    tip.style.left = left + "px";
    const below = r.bottom + 10;
    tip.style.top = (below + 160 > window.innerHeight ? r.top - tip.offsetHeight - 10 : below) + "px";
  });

  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-metric]")) tip.hidden = true;
  });
}

/* ---------------------------------------------------------- события */
$("apply").addEventListener("click", loadReport);
$("reset").addEventListener("click", async () => {
  ["source", ...FILTER_IDS].forEach((k) => ($(k).value = ""));
  expanded.clear();
  await loadFilters();
  loadReport();
});

(async () => {
  initTooltips();
  await loadFilters();
  await loadReport();
})();
