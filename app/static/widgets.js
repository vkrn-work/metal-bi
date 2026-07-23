/* Виджеты фильтров: мультиселект с «только»/«выбрать всё» и календарь с пресетами. */

const RU_MON = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"];
const RU_MON_FULL = ["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август",
                     "Сентябрь","Октябрь","Ноябрь","Декабрь"];
const iso = (d) => d.toISOString().slice(0, 10);
const ruDate = (s) => { const [y, m, d] = s.split("-"); return `${d}.${m}.${y}`; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* Закрывать все открытые поповеры при клике вне их */
document.addEventListener("click", (e) => {
  document.querySelectorAll(".pop.open").forEach((p) => {
    if (!p.parentElement.contains(e.target)) p.classList.remove("open");
  });
});

/* ============================ Мультиселект ============================ */
class MultiSelect {
  constructor(mount, { values = [], placeholder = "Все", onChange } = {}) {
    this.values = values;
    this.placeholder = placeholder;
    this.onChange = onChange || (() => {});
    this.selected = new Set();
    this.root = document.createElement("div");
    this.root.className = "ms";
    mount.appendChild(this.root);
    this.render();
  }

  render() {
    this.root.innerHTML = `
      <button type="button" class="ms-trigger sel"><span class="ms-lbl"></span></button>
      <div class="pop ms-pop">
        <div class="ms-head">
          <input type="text" class="ms-search" placeholder="Поиск">
          <button type="button" class="ms-all">Выбрать все</button>
        </div>
        <div class="ms-list"></div>
      </div>`;
    this.trigger = this.root.querySelector(".ms-trigger");
    this.pop = this.root.querySelector(".pop");
    this.search = this.root.querySelector(".ms-search");
    this.allBtn = this.root.querySelector(".ms-all");
    this.list = this.root.querySelector(".ms-list");

    this.trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = this.pop.classList.contains("open");
      document.querySelectorAll(".pop.open").forEach((p) => p.classList.remove("open"));
      if (!open) { this.pop.classList.add("open"); this.search.value = ""; this.paint(); this.search.focus(); }
    });
    this.search.addEventListener("input", () => this.paint());
    this.allBtn.addEventListener("click", () => {
      if (this.selected.size === this.values.length) this.selected.clear();
      else this.values.forEach((v) => this.selected.add(v));
      this.paint(); this.sync();
    });
    this.paint(); this.sync();
  }

  paint() {
    const q = (this.search.value || "").toLowerCase();
    const shown = this.values.filter((v) => v.toLowerCase().includes(q));
    this.allBtn.textContent = this.selected.size === this.values.length && this.values.length
      ? "Снять все" : "Выбрать все";
    this.list.innerHTML = shown.length
      ? shown.map((v) => `
        <div class="ms-row ${this.selected.has(v) ? "on" : ""}" data-v="${esc(v)}">
          <span class="ms-box"></span>
          <span class="ms-name" title="${esc(v)}">${esc(v)}</span>
          <button type="button" class="ms-only">только</button>
        </div>`).join("")
      : `<div class="ms-empty">ничего не найдено</div>`;

    this.list.querySelectorAll(".ms-row").forEach((row) => {
      const v = row.dataset.v;
      row.addEventListener("click", (e) => {
        if (e.target.classList.contains("ms-only")) return;
        this.selected.has(v) ? this.selected.delete(v) : this.selected.add(v);
        this.paint(); this.sync();
      });
      row.querySelector(".ms-only").addEventListener("click", (e) => {
        e.stopPropagation();
        this.selected = new Set([v]);
        this.paint(); this.sync();
      });
    });
  }

  sync() {
    const n = this.selected.size;
    const lbl = this.root.querySelector(".ms-lbl");
    if (n === 0) { lbl.textContent = this.placeholder; lbl.classList.add("muted"); }
    else if (n === 1) { lbl.textContent = [...this.selected][0]; lbl.classList.remove("muted"); }
    else { lbl.textContent = `Выбрано: ${n}`; lbl.classList.remove("muted"); }
    this.onChange();
  }

  setValues(values) {
    this.values = values;
    for (const v of [...this.selected]) if (!values.includes(v)) this.selected.delete(v);
    if (this.list) { this.paint(); this.sync(); }
  }
  getSelected() { return [...this.selected]; }
  clear() { this.selected.clear(); this.paint && this.paint(); this.sync(); }
}

/* ============================ Календарь ============================ */
class DateRange {
  constructor(mount, { min, max, onChange } = {}) {
    this.min = min; this.max = max;
    this.from = min; this.to = max;
    this.onChange = onChange || (() => {});
    this.tab = "day";
    this.pending = null;               // первый клик диапазона в сетке
    this.root = document.createElement("div");
    this.root.className = "dr";
    mount.appendChild(this.root);
    this.render();
  }

  render() {
    this.root.innerHTML = `
      <button type="button" class="dr-trigger sel"><span class="dr-lbl"></span></button>
      <div class="pop dr-pop">
        <div class="dr-tabs">
          <button data-t="day" class="on">Дни</button>
          <button data-t="month">Месяцы</button>
          <button data-t="quarter">Кварталы</button>
          <button data-t="year">Годы</button>
        </div>
        <div class="dr-quick"></div>
        <div class="dr-body"></div>
      </div>`;
    this.trigger = this.root.querySelector(".dr-trigger");
    this.pop = this.root.querySelector(".pop");
    this.quick = this.root.querySelector(".dr-quick");
    this.body = this.root.querySelector(".dr-body");

    this.trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = this.pop.classList.contains("open");
      document.querySelectorAll(".pop.open").forEach((p) => p.classList.remove("open"));
      if (!open) { this.pop.classList.add("open"); this.paint(); }
    });
    this.root.querySelectorAll(".dr-tabs button").forEach((b) =>
      b.addEventListener("click", () => {
        this.tab = b.dataset.t; this.pending = null;
        this.root.querySelectorAll(".dr-tabs button").forEach((x) => x.classList.remove("on"));
        b.classList.add("on"); this.paint();
      }));
    this.syncLabel();
  }

  syncLabel() {
    this.root.querySelector(".dr-lbl").textContent = `${ruDate(this.from)} – ${ruDate(this.to)}`;
  }
  apply(from, to) {
    if (from > to) [from, to] = [to, from];
    const clamp = (d) => (d < this.min ? this.min : d > this.max ? this.max : d);
    from = clamp(from); to = clamp(to);
    if (from > to) [from, to] = [to, from];
    this.from = from; this.to = to;
    this.syncLabel(); this.onChange();
  }

  quickRange(days) {
    const end = new Date(this.max);
    const start = new Date(end); start.setDate(start.getDate() - (days - 1));
    this.apply(iso(start), iso(end)); this.paint();
  }

  paint() {
    // быстрые пресеты относительно последней даты в данных
    this.quick.innerHTML = [
      ["7 дней", 7], ["30 дней", 30], ["90 дней", 90], ["365 дней", 365],
    ].map(([t, d]) => `<button data-d="${d}">${t}</button>`).join("")
      + `<button data-all="1">Весь период</button>`;
    this.quick.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => {
        if (b.dataset.all) { this.apply(this.min, this.max); this.paint(); }
        else this.quickRange(+b.dataset.d);
      }));

    if (this.tab === "day") return this.paintDays();
    if (this.tab === "month") return this.paintGrid("month");
    if (this.tab === "quarter") return this.paintGrid("quarter");
    if (this.tab === "year") return this.paintGrid("year");
  }

  paintDays() {
    this.body.innerHTML = `
      <div class="dr-days">
        <label>с <input type="date" class="df" min="${this.min}" max="${this.max}" value="${this.from}"></label>
        <label>по <input type="date" class="dt" min="${this.min}" max="${this.max}" value="${this.to}"></label>
      </div>
      <div class="dr-hint">Выбранное: ${ruDate(this.from)} – ${ruDate(this.to)}</div>`;
    const df = this.body.querySelector(".df"), dt = this.body.querySelector(".dt");
    const upd = () => { if (df.value && dt.value) { this.apply(df.value, dt.value);
      this.body.querySelector(".dr-hint").textContent = `Выбранное: ${ruDate(this.from)} – ${ruDate(this.to)}`; } };
    df.addEventListener("change", upd); dt.addEventListener("change", upd);
  }

  /* месяц/квартал/год: две сетки, выбор диапазона в два клика */
  paintGrid(mode) {
    const y0 = +this.min.slice(0, 4), y1 = +this.max.slice(0, 4);
    const hint = this.pending
      ? `Начало: ${ruDate(this.pending)} · выберите конец`
      : `Выбранное: ${ruDate(this.from)} – ${ruDate(this.to)}`;
    let html = `<div class="dr-hint">${hint}</div><div class="dr-grid">`;
    for (let y = y0; y <= y1; y++) {
      html += `<div class="dr-year"><div class="dr-yn">${y}</div><div class="dr-cells dr-${mode}">`;
      if (mode === "year") {
        html += `<button data-a="${y}-01-01" data-b="${y}-12-31">${y}</button>`;
      } else if (mode === "quarter") {
        for (let q = 0; q < 4; q++) {
          const m = q * 3;
          const a = `${y}-${String(m + 1).padStart(2, "0")}-01`;
          const b = iso(new Date(y, m + 3, 0));
          html += `<button data-a="${a}" data-b="${b}">Q${q + 1}</button>`;
        }
      } else {
        for (let m = 0; m < 12; m++) {
          const a = `${y}-${String(m + 1).padStart(2, "0")}-01`;
          const b = iso(new Date(y, m + 1, 0));
          html += `<button data-a="${a}" data-b="${b}">${RU_MON[m]}</button>`;
        }
      }
      html += `</div></div>`;
    }
    html += `</div>`;
    this.body.innerHTML = html;

    this.body.querySelectorAll(".dr-cells button").forEach((b) => {
      // подсветка попадания в текущий диапазон
      if (b.dataset.a >= this.from && b.dataset.b <= this.to) b.classList.add("in");
      b.addEventListener("click", () => {
        if (!this.pending) { this.pending = b.dataset.a; this._pendEnd = b.dataset.b; this.paint(); }
        else {
          const a = this.pending < b.dataset.a ? this.pending : b.dataset.a;
          const bb = this._pendEnd > b.dataset.b ? this._pendEnd : b.dataset.b;
          this.pending = null;
          this.apply(a, bb); this.paint();
        }
      });
    });
  }

  get() { return { from: this.from, to: this.to }; }
  reset() { this.from = this.min; this.to = this.max; this.pending = null; this.syncLabel(); }
}

window.MultiSelect = MultiSelect;
window.DateRange = DateRange;
