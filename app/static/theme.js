/* Переключатель темы для страниц без app.js */
(function () {
  const saved = localStorage.getItem("emk-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const btn = document.getElementById("theme");
  btn && btn.addEventListener("click", () => {
    const h = document.documentElement;
    h.dataset.theme = h.dataset.theme === "light" ? "dark" : "light";
    localStorage.setItem("emk-theme", h.dataset.theme);
  });
})();
