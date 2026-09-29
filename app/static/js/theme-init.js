// Apply the saved theme before the stylesheet paints, so the page never
// flashes the wrong one. Loaded synchronously from <head> (base.html).
(function () {
  var pref = localStorage.getItem("theme") || "light";
  var systemLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  var light = pref === "light" || (pref === "system" && systemLight);
  if (pref === "professional") {
    document.documentElement.classList.add("professional-mode");
  } else if (light) {
    document.documentElement.classList.add("light-mode");
  }
  var meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) {
    meta.setAttribute("content", light && pref !== "professional" ? "light" : "dark");
  }
})();
