// Colour theme (issue #73): dark by default, light on request. The choice lives in the browser
// only (no cookie, no request to the server). This file is loaded in <head> without defer, so
// the theme is set before the first paint and the page does not flash.
(() => {
  const KEY = "makerhub-theme";
  const THEMES = ["dark", "light"];
  const root = document.documentElement;

  const stored = () => {
    try {
      const value = window.localStorage.getItem(KEY);
      return THEMES.includes(value) ? value : "dark";
    } catch (error) {
      // The browser does not allow storing: stay with the default.
      return "dark";
    }
  };

  root.setAttribute("data-theme", stored());

  document.addEventListener("DOMContentLoaded", () => {
    const current = root.getAttribute("data-theme");
    for (const radio of document.querySelectorAll("input[name='theme']")) {
      radio.checked = radio.value === current;
      radio.addEventListener("change", () => {
        if (!radio.checked || !THEMES.includes(radio.value)) return;
        root.setAttribute("data-theme", radio.value);
        try {
          window.localStorage.setItem(KEY, radio.value);
        } catch (error) {
          // Not stored; the theme still applies until the page is closed.
        }
      });
    }
  });
})();
