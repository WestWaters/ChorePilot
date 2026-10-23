/* Applies the saved theme before the first paint (no flash). Dark is the default. */
(function () {
  var t = "dark";
  try { t = localStorage.getItem("cp.theme") || "dark"; } catch (e) { /* private mode */ }
  if (t === "system") t = window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t === "light" ? "light" : "dark");
})();
