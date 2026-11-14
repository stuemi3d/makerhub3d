// Asks before sending a form that has a data-confirm text (issue #39).
// Without JavaScript the form is simply sent; the server never relies on this question.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset ? event.target.dataset.confirm : undefined;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

// Suggests the number of a new variant: template number + abbreviations of the chosen values
// (issue #62). Stops as soon as the person has typed in the number field themselves.
(() => {
  const input = document.querySelector("input[data-number-prefix]");
  if (!input) return;
  const selects = Array.from(document.querySelectorAll("select[name^='attr_']"));
  let touched = input.value !== "";
  input.addEventListener("input", () => {
    touched = true;
  });
  const suggest = () => {
    if (touched) return;
    const parts = [input.dataset.numberPrefix];
    for (const select of selects) {
      const option = select.options[select.selectedIndex];
      if (option && option.dataset.abbreviation) parts.push(option.dataset.abbreviation);
    }
    input.value = parts.join("-");
  };
  for (const select of selects) select.addEventListener("change", suggest);
})();

// Phone menu: the button folds the navigation entries in and out (issue #74).
(() => {
  const toggle = document.querySelector(".nav-toggle");
  const menu = document.getElementById("main-menu");
  if (!toggle || !menu) return;
  toggle.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    menu.classList.toggle("open", open);
  });
})();

// Pop-ups (issue #76): a link with data-popup loads its page into the dialog with HTMX. The
// link keeps its normal address, so without JavaScript it simply opens the page. The server
// stays the judge: it checks login, role, form token and input; this file only shows the result.
(() => {
  const dialog = document.getElementById("popup");
  const body = document.getElementById("popup-body");
  if (!dialog || !body || typeof dialog.showModal !== "function") return;
  let openerHref = null;

  const focusOpener = () => {
    if (!openerHref) return;
    const link = document.querySelector(`[data-popup][href="${CSS.escape(openerHref)}"]`);
    if (link) link.focus();
  };
  // The pop-up closes only on purpose: after saving or with the Cancel button. Escape and a
  // click on the background do nothing, so typed input is not lost by accident.
  let closeOnPurpose = false;
  const closeDialog = () => {
    closeOnPurpose = true;
    if (dialog.open) dialog.close();
  };
  // A page behind a redirect (e.g. the login page after the session ended) is no form: go there.
  const leavesToOtherPage = (detail) => {
    const asked = new URL(detail.pathInfo.finalRequestPath, window.location.href).pathname;
    const got = new URL(detail.xhr.responseURL || asked, window.location.href).pathname;
    return got !== asked;
  };
  const isPopupRequest = (target) =>
    target === body || (target.hasAttribute && target.hasAttribute("data-popup-refresh"));

  // "Open in new tab" and the like (Ctrl, Shift, Cmd, Alt + click) must reach the browser, not HTMX.
  document.addEventListener(
    "click",
    (event) => {
      const link = event.target.closest && event.target.closest("a[data-popup]");
      if (link && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)) {
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const element = event.detail.elt;
    if (element.matches && element.matches("[data-popup]")) {
      openerHref = element.getAttribute("href");
    }
  });

  document.body.addEventListener("htmx:beforeSwap", (event) => {
    const detail = event.detail;
    if (!isPopupRequest(detail.target)) return;
    if (leavesToOtherPage(detail)) {
      detail.shouldSwap = false;
      detail.isError = false;
      window.location.assign(openerHref || window.location.href);
    } else if (detail.xhr.status === 422) {
      // The form with its error messages.
      detail.shouldSwap = true;
      detail.isError = false;
    }
  });

  // Any other failure (no right, no such article, server error): show the normal page instead.
  document.body.addEventListener("htmx:responseError", (event) => {
    if (isPopupRequest(event.detail.target)) {
      window.location.assign(openerHref || window.location.href);
    }
  });

  // After "settle", not after the swap: only then HTMX has wired up the new form. Showing the
  // pop-up earlier would let a quick Enter send the form as a normal page.
  document.body.addEventListener("htmx:afterSettle", (event) => {
    if (event.detail.target !== body) return;
    if (!dialog.open) {
      dialog.showModal();
      document.documentElement.classList.add("modal-is-open");
    }
    const field =
      body.querySelector("[aria-invalid='true']") || body.querySelector("[autofocus]");
    if (field) field.focus();
  });

  // Saved: close, then load the list again (same address, so sorting and filter stay).
  document.body.addEventListener("popup-saved", () => {
    closeDialog();
    const region = document.querySelector("[data-popup-refresh]");
    if (!region || !region.id || !window.htmx) {
      window.location.reload();
      return;
    }
    window.htmx
      .ajax("GET", window.location.href, {
        target: region,
        swap: "outerHTML",
        select: `#${region.id}`,
      })
      .then(focusOpener)
      .catch(() => window.location.reload());
  });

  dialog.addEventListener("cancel", (event) => event.preventDefault());
  dialog.addEventListener("close", () => {
    // Some browsers close on a second Escape despite the block above: open it again.
    if (!closeOnPurpose) {
      dialog.showModal();
      return;
    }
    closeOnPurpose = false;
    document.documentElement.classList.remove("modal-is-open");
    body.replaceChildren();
    focusOpener();
  });
  // Only a Cancel button closes the dialog without saving.
  dialog.addEventListener("click", (event) => {
    if (event.target.closest && event.target.closest("[data-popup-close]")) closeDialog();
  });
})();

// HTMX (issue #76) only talks to this server, runs no code from answers and keeps no page copies.
if (window.htmx) {
  Object.assign(window.htmx.config, {
    allowEval: false,
    allowScriptTags: false,
    selfRequestsOnly: true,
    historyCacheSize: 0,
    includeIndicatorStyles: false,
  });
}
