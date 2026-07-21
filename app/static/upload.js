/* Страница обновления данных: полный пересчёт и быстрая замена parquet. */
const logBox = document.getElementById("log");
const qlog = document.getElementById("qlog");
let poll = null;

function show(box, text) { box.hidden = false; box.textContent = text; }

document.getElementById("full").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  show(logBox, "Загружаю файлы на сервер...");
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const d = await r.json();
    if (!r.ok) { show(logBox, "Ошибка: " + (d.error || r.status)); btn.disabled = false; return; }
    show(logBox, "Файлы приняты. Идёт пересборка базы...");
    poll = setInterval(checkStatus, 1500);
  } catch (err) {
    show(logBox, "Ошибка отправки: " + err);
    btn.disabled = false;
  }
});

async function checkStatus() {
  const r = await fetch("/api/status");
  if (!r.ok) return;
  const d = await r.json();
  const job = d.job || {};
  if (job.log && job.log.length) logBox.textContent = job.log.join("\n");
  if (!job.running && job.finished_at) {
    clearInterval(poll);
    document.querySelector("#full button").disabled = false;
    logBox.textContent += job.ok
      ? `\n\nБаза обновлена: ${d.rows.toLocaleString("ru-RU")} строк. Откройте отчёт.`
      : "\n\nПересборка не удалась — база осталась прежней.";
    logBox.scrollTop = logBox.scrollHeight;
  }
}

document.getElementById("quick").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  show(qlog, "Загружаю и проверяю файл...");
  const r = await fetch("/api/upload-parquet", { method: "POST", body: fd });
  const d = await r.json();
  show(qlog, r.ok ? `Готово. В базе ${d.rows.toLocaleString("ru-RU")} строк.` : "Ошибка: " + d.error);
});
