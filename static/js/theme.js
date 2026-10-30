(() => {
  const toggle = document.querySelector("[data-theme-toggle]");
  if (!toggle) return;

  const label = toggle.querySelector("[data-theme-label]");

  function updateToggle() {
    const isDark = document.documentElement.dataset.theme === "dark";
    toggle.setAttribute("aria-pressed", String(isDark));
    toggle.setAttribute("aria-label", isDark ? "Aktifkan mode terang" : "Aktifkan mode gelap");
    if (label) label.textContent = isDark ? "Terang" : "Gelap";
  }

  toggle.addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme !== "dark";
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
    try {
      localStorage.setItem("savebite-theme", isDark ? "dark" : "light");
    } catch (_) {}
    updateToggle();
  });

  updateToggle();
})();
