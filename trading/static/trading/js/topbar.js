(function () {
  function setupMenu() {
    var toggle = document.getElementById("topbar-menu-toggle");
    var menu = document.getElementById("topbar-menu");
    if (!toggle || !menu) return;

    function close() {
      menu.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }

    function open() {
      menu.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    }

    toggle.addEventListener("click", function (e) {
      e.stopPropagation();
      if (menu.hidden) open();
      else close();
    });

    document.addEventListener("click", function (e) {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== toggle) close();
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !menu.hidden) {
        close();
        toggle.focus();
      }
    });
  }

  function setupShareTracking() {
    document.querySelectorAll("[data-share-platform]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var platform = btn.getAttribute("data-share-platform");
        if (!platform || typeof fetch !== "function") return;
        fetch("/api/share-click/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ platform: platform, lang: document.documentElement.lang || "" }),
          keepalive: true,
          credentials: "same-origin",
        }).catch(function () {});
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupMenu();
    setupShareTracking();
  });
})();
