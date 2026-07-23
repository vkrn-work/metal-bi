/* Дашборд «Конверсия КП → Заказ». */
const $ = (id) => document.getElementById(id);
const ROW1 = ["company", "source"];        // компания, источник
const ROW2 = ["tenure", "maturity", "scale", "result"];  // категории клиента
const FILTER_IDS = [...ROW1, ...ROW2];
const widgets = {};                        // key -> MultiSelect
let datePick = null;
const PALETTE = ["#2f6df6","#0ea5e9","#0f9d58","#e0a13a","#8b5cf6","#06b6d4",
                 "#ec4899","#f97316","#1e3a8a","#94a3b8","#64748b"];

/* Методика показателей. Тот же текст — на странице /metrics. */
const METRIC_HELP = {
  kp_docs: ["КП, документов",
    "Сколько разных коммерческих предложений содержали эту категорию. Единица счёта — документ, а не строка: тридцать позиций труб в одном КП это один коммерческий шанс, а не тридцать.",
    "COUNT(DISTINCT номер КП)"],
  zk_docs: ["Заказов, документов",
    "Сколько разных заказов клиента содержали эту категорию.",
    "COUNT(DISTINCT номер ЗК)"],
  lines: ["Позиции",
    "Число строк — упоминаний категории в документах. Мера объёма спроса. В знаменателе конверсии НЕ используется: один документ на 903 строки перекосил бы всю категорию.",
    "COUNT(*)"],
  margin: ["Маржа",
    "Сумма маржи по всем строкам категории, всегда в евро. Чувствительна к единичным крупным сделкам — для приоритета смотрите медиану.",
    "SUM(маржа)"],
  cr: ["Конверсия КП→ЗК",
    "С какой вероятностью категория, попавшая в предложение, дойдёт до заказа. Основной показатель шанса.",
    "уникальных ЗК / уникальных КП"],
  delta: ["Δ к предыдущему периоду",
    "Изменение конверсии по сравнению с предыдущим отрезком той же длины. Зелёный — рост, красный — падение. Пусто, если за предыдущий период данных в базе нет.",
    "CR сейчас − CR раньше, в п.п."],
  median: ["Медиана маржи заказа",
    "Типичный размер маржи одного заказа по категории. Медиана, а не среднее: единичные сделки на десятки миллионов ломают среднее. Заказы без маржи не учитываются.",
    "MEDIAN(маржа одного ЗК)"],
  expected: ["Ожидаемая маржа на 1 КП",
    "Сколько евро приносит одно предложение с этой категорией. Объединяет шанс закрытия и типичный размер сделки — показатель приоритета направления.",
    "конверсия × медиана заказа"],
  reliable: ["Мало данных",
    "Заказов меньше 30: доверительный интервал конверсии слишком широк, различия с соседними категориями недостоверны. Принимать решения по такой строке нельзя.",
    "число заказов < 30"],
  top: ["Топы номенклатуры",
    "Позиции ранжируются по конверсии КП→ЗК. В расчёт попадают только те, что встретились минимум в N предложениях — иначе в топ выходят позиции с одним КП и одним заказом, то есть конверсией 100%. Порог задаётся селектором «Мин. КП».",
    "CR позиции, при КП ≥ порога"],
  monthly: ["Помесячная динамика",
    "Точки — значения показателя по месяцам внутри выбранного периода. Линия показывает направление тренда.",
    "тот же расчёт, разрез по месяцу документа"],
};

let charts = {};
let expanded = new Set();
let lastTree = [];
let lastTotals = null;

/* ---------------------------------------------------------- формат */
const nf = new Intl.NumberFormat("ru-RU");
const money = (v) => nf.format(Math.round(v || 0));
const eur = (v) => (v === null || v === undefined ? "—" : nf.format(Math.round(v)) + " €");
const pct = (v, d = 2) =>
  v === null || v === undefined ? "—" : (v * 100).toFixed(d).replace(".", ",") + "%";
const ruShort = (iso) => { const [y, m, d] = iso.split("-"); return `${d}.${m}.${y}`; };

/* ---------------------------------------------------------- тема */
(function theme() {
  const saved = localStorage.getItem("emk-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = $("theme");
  btn && btn.addEventListener("click", () => {
    const h = document.documentElement;
    h.dataset.theme = h.dataset.theme === "light" ? "dark" : "light";
    localStorage.setItem("emk-theme", h.dataset.theme);
    if (lastTotals) loadReport();
  });
})();

/* ---------------------------------------------------------- фильтры */
async function loadFilters() {
  const r = await fetch("/api/filters");
  if (r.status === 401) return (location.href = "/login");
  const data = await r.json();
  if (!data.ready) return;

  $("dataPeriod").textContent =
    `данные за ${ruShort(data.min_date)} – ${ruShort(data.max_date)} · ${nf.format(data.rows)} строк`;

  // календарь (строится один раз)
  if (!datePick) {
    datePick = new DateRange(document.getElementById("datePick"),
      { min: data.min_date, max: data.max_date, onChange: () => {} });
  }

  // мультиселекты (строятся один раз, дальше только обновляем значения)
  FILTER_IDS.forEach((k) => {
    const meta = data.filters[k];
    if (!meta) return;
    const lbl = $("lb_" + k);
    if (lbl) lbl.textContent = meta.label;
    if (widgets[k]) { widgets[k].setValues(meta.values); return; }
    const mount = document.getElementById("ms_" + k);
    if (mount) widgets[k] = new MultiSelect(mount, { values: meta.values, placeholder: "Все" });
  });

  const mk = $("min_kp");
  mk.innerHTML = "";
  (data.min_kp_options || [100]).forEach((v) => mk.add(new Option(v, v)));
  mk.value = data.min_kp_default || 100;
}

function currentFilters() {
  const dr = datePick ? datePick.get() : { from: "", to: "" };
  const f = { date_from: dr.from, date_to: dr.to, min_kp: +$("min_kp").value };
  FILTER_IDS.forEach((k) => (f[k] = widgets[k] ? widgets[k].getSelected() : []));
  return f;
}

/* ---------------------------------------------------------- загрузка */
function showProblem(text) {
  $("empty").hidden = false;
  $("empty").textContent = text;
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
      const t = await r.text();
      return showProblem(`Ошибка сервера (${r.status}). ${t.slice(0, 300)}`);
    }
    const d = await r.json();
    if (!d.ready) return showProblem("База пуста. Загрузите выгрузки на странице «Данные».");
    $("empty").hidden = true;
    $("app").hidden = false;
    lastTotals = d.totals;
    lastTree = d.tree;
    renderKpis(d);
    renderTable(d);
    renderDonut("Kp", d.shares_kp, d.totals.kp_docs);
    renderDonut("Zk", d.shares_zk, d.totals.zk_docs);
    renderTops(d.products);
  } catch (err) {
    showProblem("Не удалось построить отчёт: " + err);
  }
}

/* ---------------------------------------------------------- показатели */
const KPI_DEFS = [
  { key: "kp_docs", label: "КП, документов", help: "kp_docs",
    val: (t) => nf.format(t.kp_docs), series: (m) => m.kp_docs, fmt: (v) => nf.format(v) },
  { key: "zk_docs", label: "Заказов", help: "zk_docs",
    val: (t) => nf.format(t.zk_docs), series: (m) => m.zk_docs, fmt: (v) => nf.format(v) },
  { key: "cr", label: "CR КП→ЗК", help: "cr",
    val: (t) => pct(t.cr_count), series: (m) => (m.cr_count ?? 0) * 100,
    fmt: (v) => v.toFixed(2).replace(".", ",") + "%" },
  { key: "median", label: "Медиана маржи ЗК", help: "median",
    val: (t) => eur(t.zk_median), series: (m) => m.zk_median ?? 0, fmt: (v) => money(v) + " €" },
];

function renderKpis(d) {
  $("kpis").innerHTML = KPI_DEFS.map((k) => `
    <div class="kpi card">
      <div class="top">${k.label} <i class="help" data-metric="${k.help}">?</i></div>
      <div class="val">${k.val(d.totals)}</div>
      <div class="sub">по месяцам <i class="help" data-metric="monthly">?</i></div>
      <div class="spark-box"><canvas id="sp_${k.key}"></canvas></div>
    </div>`).join("");

  const months = d.monthly || [];
  KPI_DEFS.forEach((k) => {
    charts[k.key] && charts[k.key].destroy();
    charts[k.key] = new Chart($("sp_" + k.key), {
      type: "line",
      data: {
        labels: months.map((m) => m.label),
        datasets: [{
          data: months.map(k.series),
          borderColor: "#2f6df6", backgroundColor: "#2f6df6",
          pointRadius: 3.5, pointHoverRadius: 5, borderWidth: 1.5, tension: .25,
          showLine: months.length > 1,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (c) => " " + k.fmt(c.raw) } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 9 }, color: "#8593a8" }, border: { display: false } },
          y: { display: false, beginAtZero: false },
        },
      },
    });
  });
}

/* ---------------------------------------------------------- таблица */
function deltaCell(node) {
  if (node.delta_pp === null || node.delta_pp === undefined) return "—";
  const v = node.delta_pp;
  const cls = v > 0.05 ? "b-up" : v < -0.05 ? "b-down" : "b-flat";
  const sign = v > 0 ? "+" : "";
  return `<span class="badge ${cls}">${sign}${v.toFixed(2).replace(".", ",")} п.п.</span>`;
}

function renderTable(d) {
  const tb = $("tbody");
  tb.innerHTML = "";
  lastTree.forEach((n, i) => addRow(tb, n, [], 1, PALETTE[i % PALETTE.length]));

  const t = d.totals;
  $("tfoot").innerHTML =
    `<td>Итого · ${lastTree.length} категорий</td>
     <td class="sep">${nf.format(t.kp_docs)}</td><td>${nf.format(t.kp_lines)}</td><td>${money(t.kp_margin)}</td>
     <td class="sep">${nf.format(t.zk_docs)}</td><td>${nf.format(t.zk_lines)}</td><td>${money(t.zk_margin)}</td>
     <td class="sep">${pct(t.cr_count)}</td><td>—</td><td>${eur(t.zk_median)}</td><td>${eur(t.expected)}</td>`;
}

function addRow(tb, node, parentPath, depth, color) {
  const path = parentPath.concat(node.name);
  const key = path.join(" / ");
  const has = node.children && node.children.length > 0;
  const open = expanded.has(key);

  const tr = document.createElement("tr");
  tr.className = "lvl" + depth + (node.reliable ? "" : " weak");
  const pad = (depth - 1) * 18;
  const dot = depth === 1 ? `<span class="dot" style="background:${color}"></span>` : "";
  const warn = node.reliable ? "" : `<span class="badge b-warn" data-metric="reliable">мало данных</span>`;
  tr.innerHTML =
    `<td><div class="cell-name" style="padding-left:${pad}px">
       <button class="tog ${has ? "" : "empty"}">${has ? (open ? "−" : "+") : ""}</button>
       ${dot}<span>${escapeHtml(node.name)}</span>${warn}</div></td>
     <td class="sep">${nf.format(node.kp_docs)}</td><td>${nf.format(node.kp_lines)}</td><td>${money(node.kp_margin)}</td>
     <td class="sep">${nf.format(node.zk_docs)}</td><td>${nf.format(node.zk_lines)}</td><td>${money(node.zk_margin)}</td>
     <td class="sep">${pct(node.cr_count)}</td><td>${deltaCell(node)}</td>
     <td>${eur(node.zk_median)}</td><td>${eur(node.expected)}</td>`;
  tb.appendChild(tr);

  if (has) {
    tr.querySelector(".tog").addEventListener("click", () => {
      expanded.has(key) ? expanded.delete(key) : expanded.add(key);
      renderTable({ totals: lastTotals });
    });
    if (open) node.children.forEach((c) => addRow(tb, c, path, depth + 1, color));
  }
}

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------------------------------------------------- кольца */
function renderDonut(suffix, shares, total) {
  $("total" + suffix).textContent = nf.format(total);
  const colors = shares.map((_, i) => PALETTE[i % PALETTE.length]);
  charts[suffix] && charts[suffix].destroy();
  charts[suffix] = new Chart($(suffix === "Kp" ? "donutKp" : "donutZk"), {
    type: "doughnut",
    data: {
      labels: shares.map((s) => s.name),
      datasets: [{ data: shares.map((s) => s.value), backgroundColor: colors,
                   borderWidth: 2, borderColor: getComputedStyle(document.body).backgroundColor }],
    },
    options: {
      cutout: "64%", responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          label: (c) => ` ${c.label}: ${nf.format(c.raw)} (${(shares[c.dataIndex].share * 100).toFixed(1)}%)` } },
      },
    },
  });
  $("legend" + suffix).innerHTML = shares.map((s, i) =>
    `<div><i style="background:${colors[i]}"></i>
      <span title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
      <b>${(s.share * 100).toFixed(1)}%</b></div>`).join("");
}

/* ---------------------------------------------------------- топы позиций */
function renderTops(p) {
  if (!p) return;
  $("qualified").textContent = `${p.qualified} позиций прошли порог ${p.min_kp} КП`;
  const row = (x, i) =>
    `<div class="toprow">
       <span class="rk">${i + 1}</span>
       <span class="nm" title="${escapeHtml(x.name)}">${escapeHtml(x.name)}</span>
       <span class="kp">${x.kp_docs} КП · ${x.zk_docs} ЗК</span>
       <span class="cr">${pct(x.cr_count, 1)}</span>
     </div>`;
  const none = `<div class="empty-note">Нет позиций, прошедших порог. Понизьте «Мин. КП».</div>`;
  $("topBest").innerHTML = p.best.length ? p.best.map(row).join("") : none;
  $("topWorst").innerHTML = p.worst.length ? p.worst.map(row).join("") : none;
}

/* ---------------------------------------------------------- подсказки */
function initTooltips() {
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-metric]");
    if (!el) return;
    const m = METRIC_HELP[el.dataset.metric];
    if (!m) return;
    tip.innerHTML = `<b>${escapeHtml(m[0])}</b><p>${escapeHtml(m[1])}</p><code>${escapeHtml(m[2])}</code>`;
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const w = 330;
    tip.style.left = Math.max(10, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - 10)) + "px";
    const below = r.bottom + 10;
    tip.style.top = (below + tip.offsetHeight > innerHeight ? r.top - tip.offsetHeight - 10 : below) + "px";
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest("[data-metric]")) tip.hidden = true;
  });
}

/* ---------------------------------------------------------- экспорт */
function exportCsv() {
  const head = ["Категория","КП док","КП позиций","КП маржа","ЗК док","ЗК позиций","ЗК маржа",
                "CR %","Дельта п.п.","Медиана маржи","Ожид. на КП","Достоверно"];
  const rows = [];
  const walk = (nodes, prefix) => nodes.forEach((n) => {
    rows.push([prefix + n.name, n.kp_docs, n.kp_lines, Math.round(n.kp_margin),
      n.zk_docs, n.zk_lines, Math.round(n.zk_margin),
      n.cr_count === null ? "" : (n.cr_count * 100).toFixed(2),
      n.delta_pp === null || n.delta_pp === undefined ? "" : n.delta_pp.toFixed(2),
      n.zk_median === null ? "" : Math.round(n.zk_median),
      n.expected === null ? "" : Math.round(n.expected),
      n.reliable ? "да" : "нет"]);
    walk(n.children || [], prefix + "— ");
  });
  walk(lastTree, "");
  const csv = "﻿" + [head, ...rows]
    .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(";")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const dr = datePick ? datePick.get() : { from: "", to: "" };
  a.download = `emk-categories-${dr.from}_${dr.to}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------------------------------------------------------- события */
$("apply").addEventListener("click", loadReport);
$("reset").addEventListener("click", () => {
  FILTER_IDS.forEach((k) => widgets[k] && widgets[k].clear());
  datePick && datePick.reset();
  expanded.clear();
  loadReport();
});
$("exportBtn") && $("exportBtn").addEventListener("click", exportCsv);

(async () => {
  initTooltips();
  await loadFilters();
  await loadReport();
})();
