// Light or dark, shared by the app and the help page. Loaded in <head> so the
// choice is applied before anything paints.
//
// data-theme on <html> always holds the theme in effect: the reader's own
// choice once they have made one, otherwise whatever the system prefers.
(function () {
  var KEY = "wikindle-theme";
  var root = document.documentElement;
  var system = window.matchMedia("(prefers-color-scheme: dark)");

  function stored() {
    try {
      var value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch (error) {
      return null; // storage blocked: fall back to the system, every visit
    }
  }

  function apply() {
    root.dataset.theme = stored() || (system.matches ? "dark" : "light");
  }

  apply();
  system.addEventListener("change", apply);

  // Delegated, because the app renders its button after this has run.
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!(target instanceof Element) || !target.closest("[data-theme-toggle]")) return;
    var next = root.dataset.theme === "dark" ? "light" : "dark";
    try {
      localStorage.setItem(KEY, next);
    } catch (error) {
      // Still switch for this page view.
    }
    root.dataset.theme = next;
  });
})();
