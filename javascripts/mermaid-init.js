(function () {
  var currentTheme = "";

  function theme() {
    return document.body.getAttribute("data-md-color-scheme") === "slate"
      ? "dark"
      : "default";
  }

  function restoreSource(el) {
    if (!el.dataset.mermaidSource) {
      el.dataset.mermaidSource = el.textContent.trim();
    }

    if (el.dataset.processed) {
      el.removeAttribute("data-processed");
      el.textContent = el.dataset.mermaidSource;
    }
  }

  function configure() {
    var nextTheme = theme();

    if (currentTheme !== nextTheme) {
      mermaid.initialize({
        startOnLoad: false,
        theme: nextTheme,
        securityLevel: "loose"
      });
      currentTheme = nextTheme;
    }
  }

  function renderMermaid() {
    var diagrams = Array.prototype.slice.call(
      document.querySelectorAll(".mermaid")
    );

    if (!diagrams.length || typeof mermaid === "undefined") {
      return;
    }

    configure();
    diagrams.forEach(restoreSource);

    mermaid.run({ nodes: diagrams }).catch(function (error) {
      console.error("Mermaid render failed", error);
    });
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(renderMermaid);
  } else {
    document.addEventListener("DOMContentLoaded", renderMermaid);
  }

  new MutationObserver(function (mutations) {
    if (
      mutations.some(function (mutation) {
        return mutation.attributeName === "data-md-color-scheme";
      })
    ) {
      renderMermaid();
    }
  }).observe(document.body, {
    attributes: true,
    attributeFilter: ["data-md-color-scheme"]
  });
})();
