/* Apply the saved theme before first paint (no flash). Loaded as a plain,
   render-blocking script in <head> rather than inline, so the page can run
   under a Content-Security-Policy that allows no inline script at all. */
(function () {
  try {
    var t = localStorage.getItem("helmsman_theme") || "auto";
    if (t === "auto") {
      t = matchMedia("(prefers-color-scheme: light)").matches ? "daybreak" : "deep-sea";
    }
    document.documentElement.dataset.theme = t;
  } catch (e) {}
})();
