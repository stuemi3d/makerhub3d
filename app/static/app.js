// Suggests the number of a new variant: template number + abbreviations of the chosen values
// (issue #62). Stops as soon as the person has typed in the number field themselves. The form
// may also arrive later in the pop-up (issue #87), so every new form is set up once it is shown.
(() => {
  const setUp = (root) => {
    const input = root.querySelector("input[data-number-prefix]");
    if (!input || input.dataset.suggesting) return;
    input.dataset.suggesting = "1";
    const selects = Array.from(root.querySelectorAll("select[name^='attr_']"));
    const suggestion = () => {
      const parts = [input.dataset.numberPrefix];
      for (const select of selects) {
        const option = select.options[select.selectedIndex];
        if (option && option.dataset.abbreviation) parts.push(option.dataset.abbreviation);
      }
      return parts.join("-");
    };
    // A number that is already there counts as typed, unless it is just the earlier suggestion
    // (the form comes back with it after an error).
    let touched = input.value !== "" && input.value !== suggestion();
    input.addEventListener("input", () => {
      touched = true;
    });
    const suggest = () => {
      if (!touched) input.value = suggestion();
    };
    for (const select of selects) select.addEventListener("change", suggest);
  };
  setUp(document);
  document.body.addEventListener("htmx:afterSettle", (event) => {
    const popupBody = document.getElementById("popup-body");
    if (popupBody && event.detail.target === popupBody) setUp(popupBody);
  });
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
    // Empty it right now, not only in the "close" handler (which runs a moment later): a late
    // "settled" message in between would otherwise find content and open the pop-up again.
    body.replaceChildren();
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
    // "Settled" arrives a few milliseconds after the swap. When the pop-up was closed in between
    // (Cancel, which empties it), it stays closed instead of opening again without content.
    if (!body.firstElementChild) return;
    if (!dialog.open) {
      dialog.showModal();
      document.documentElement.classList.add("modal-is-open");
    }
    const field =
      body.querySelector("[aria-invalid='true']:not(.combo-native)") || body.querySelector("[autofocus]");
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

// Page Production (issue #132): the button "create production" works once a print is chosen,
// and the number of chosen prints is shown. Without this script the button stays clickable and
// the server answers "no print chosen" itself.
(() => {
  const button = document.querySelector("[data-requires-checked]");
  if (!button) return;
  const boxes = Array.from(document.querySelectorAll("input[name='prints'][form='select-form']"));
  const label = document.querySelector("[data-selected-text]");
  const update = () => {
    const count = boxes.filter((box) => box.checked).length;
    button.disabled = count === 0;
    if (label) label.textContent = count ? `${count} ${label.dataset.selectedText}` : "";
  };
  for (const box of boxes) box.addEventListener("change", update);
  // Also when the browser brings the page back with the boxes as they were (back button)
  window.addEventListener("pageshow", update);
  update();
})();

// Embedded in another page (issue #152, e.g. in Bambuddy): the outer address never changes, so a
// reload of the outer page starts this app on its start page again. Remember the last page for
// this browser tab only (sessionStorage: gone when the tab or app is closed) and go back to it
// when the app starts on the dashboard from outside. Nothing is sent to the server. Not embedded,
// the address in the browser already keeps the page, so nothing happens.
(() => {
  const KEY = "makerhub.lastPage";
  // Set while a return to the remembered page is under way and removed by that page itself. When
  // it is still set at the next start, that page never showed up (e.g. it was deleted and the
  // server answered with a bare error): then stay on the dashboard instead of trying it again.
  const PENDING = "makerhub.returning";
  let embedded = false;
  try {
    embedded = window.self !== window.top;
  } catch (error) {
    embedded = true;
  }
  if (!embedded) return;
  const store = (() => {
    try {
      return window.sessionStorage;
    } catch (error) {
      return null; // storage blocked: simply no remembering
    }
  })();
  if (!store) return;

  // Only a path of this app: starts with one slash, no second slash or backslash, no control
  // characters, and it stays on this origin when resolved.
  const isOwnPath = (value) => {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return false;
    if (value.includes("\\") || /[\u0000- \u007f]/.test(value)) return false;
    try {
      return new URL(value, window.location.origin).origin === window.location.origin;
    } catch (error) {
      return false;
    }
  };

  const path = window.location.pathname;
  const here = path + window.location.search;
  const entry = performance.getEntriesByType("navigation")[0];
  const status = entry && entry.responseStatus ? entry.responseStatus : 200;
  const noPage = ["/login", "/login/code", "/setup", "/logout"];

  // Login pages and error pages are no place to come back to; logging out forgets everything.
  if (noPage.includes(path) || status >= 400) {
    store.removeItem(KEY);
    store.removeItem(PENDING);
    return;
  }

  // Started from outside (the referrer is another origin or empty) on the dashboard: return.
  // Coming from a link of this app (same origin, e.g. the brand link) stays on the dashboard.
  let fromOutside = true;
  try {
    fromOutside = !document.referrer || new URL(document.referrer).origin !== window.location.origin;
  } catch (error) {
    fromOutside = true;
  }
  const saved = store.getItem(KEY);
  // The back button keeps the old referrer, so it must not count as "started from outside".
  const viaBackButton = entry && entry.type === "back_forward";
  if (here === "/" && fromOutside && !viaBackButton && saved && saved !== "/") {
    if (isOwnPath(saved) && !store.getItem(PENDING)) {
      store.setItem(PENDING, "1");
      window.location.replace(saved);
      return;
    }
    store.removeItem(KEY);
  }
  store.removeItem(PENDING);
  store.setItem(KEY, here);
})();

// Power table of a printer model (issue #177): add, remove and move rows. Moving works with the
// arrow buttons (phone, keyboard) and by dragging a row at its grip. The rows are numbered in
// the order they stand on the page (material_0, kwh_0, material_1 ...), so the server saves
// exactly that order. The server checks everything again; this only handles the form. The
// pop-up content arrives later with HTMX, so the clicks are handled on the document.
(() => {
  const rowsOf = (table) =>
    Array.from(table.querySelectorAll("[data-power-rows] > [data-power-row]"));

  const renumber = (table) => {
    const rows = rowsOf(table);
    rows.forEach((row, index) => {
      for (const input of row.querySelectorAll("input")) {
        const base = input.name.replace(/_[^_]*$/, "");
        input.name = `${base}_${index}`;
        input.id = `${base}_${index}`;
        if (input.hasAttribute("aria-describedby")) {
          input.setAttribute("aria-describedby", `${base}_${index}-error`);
        }
        const message = input.parentElement.querySelector("small");
        if (message) message.id = `${base}_${index}-error`;
      }
      row.querySelector("[data-power-up]").disabled = index === 0;
      row.querySelector("[data-power-down]").disabled = index === rows.length - 1;
    });
    // The server allows 100 rows; the button stops there instead of the save failing later.
    const add = table.querySelector("[data-power-add]");
    if (add) add.disabled = rows.length >= 100;
  };

  const move = (row, direction) => {
    const neighbour =
      direction < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!neighbour) return;
    if (direction < 0) neighbour.before(row);
    else neighbour.after(row);
    const table = row.closest("[data-power-table]");
    renumber(table);
    // The button that was used keeps the focus; at the end of the list the other arrow does.
    const used = row.querySelector(direction < 0 ? "[data-power-up]" : "[data-power-down]");
    const other = row.querySelector(direction < 0 ? "[data-power-down]" : "[data-power-up]");
    (used.disabled ? other : used).focus();
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest(
      "[data-power-add], [data-power-up], [data-power-down], [data-power-remove]"
    );
    const table = button && button.closest("[data-power-table]");
    if (!table) return;
    const row = button.closest("[data-power-row]");
    if (button.hasAttribute("data-power-add")) {
      const pattern = table.querySelector("template");
      const fresh = pattern.content.firstElementChild.cloneNode(true);
      table.querySelector("[data-power-rows]").append(fresh);
      renumber(table);
      fresh.querySelector("input").focus();
    } else if (button.hasAttribute("data-power-remove")) {
      row.remove();
      renumber(table);
    } else {
      move(row, button.hasAttribute("data-power-up") ? -1 : 1);
    }
  });

  let dragged = null;

  document.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest("[data-power-handle]");
    if (!handle || (event.pointerType === "mouse" && event.button !== 0)) return;
    const row = handle.closest("[data-power-row]");
    dragged = { row, table: row.closest("[data-power-table]") };
    row.classList.add("is-dragging");
    event.preventDefault();
  });

  document.addEventListener("pointermove", (event) => {
    if (!dragged) return;
    for (const other of rowsOf(dragged.table)) {
      if (other === dragged.row) continue;
      const box = other.getBoundingClientRect();
      if (event.clientY < box.top || event.clientY > box.bottom) continue;
      const middle = box.top + box.height / 2;
      const below = Boolean(
        dragged.row.compareDocumentPosition(other) & Node.DOCUMENT_POSITION_FOLLOWING
      );
      // Passing the middle of a row swaps the places; the row under the pointer moves aside.
      if (below && event.clientY > middle) other.after(dragged.row);
      else if (!below && event.clientY < middle) other.before(dragged.row);
      break;
    }
  });

  const finishDrag = () => {
    if (!dragged) return;
    dragged.row.classList.remove("is-dragging");
    renumber(dragged.table);
    dragged = null;
  };
  document.addEventListener("pointerup", finishDrag);
  document.addEventListener("pointercancel", finishDrag);
})();

// Combobox (issue #191): a select with data-search becomes a text field with a list. Typing in
// the field filters the list live; choosing an entry sets the real select (which stays in the
// form, hidden, and is what is sent) and tells the form with a normal "change" event. The
// typing itself must not draw the form again, so its events stop at the field. Without
// JavaScript the plain select stays. A form that is drawn again brings fresh selects, which
// are set up again.
(() => {
  const setUp = (root) => {
    for (const select of root.querySelectorAll("select[data-search]")) {
      if (select.dataset.searchReady) continue;
      select.dataset.searchReady = "1";
      const options = Array.from(select.options);
      const placeholder = options.find((option) => option.value === "");
      const textOf = (option) => (option && option.value !== "" ? option.text : "");

      const wrap = document.createElement("div");
      wrap.className = "combo";
      const input = document.createElement("input");
      input.type = "text";
      input.id = `${select.id}-combo`;
      input.dataset.for = select.id;
      input.autocomplete = "off";
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-controls", `${select.id}-list`);
      input.placeholder = placeholder ? placeholder.text : select.dataset.search;
      for (const name of ["aria-invalid", "aria-describedby"]) {
        if (select.hasAttribute(name)) input.setAttribute(name, select.getAttribute(name));
      }
      const list = document.createElement("ul");
      list.id = `${select.id}-list`;
      list.className = "combo-list";
      list.setAttribute("role", "listbox");
      list.hidden = true;
      wrap.append(input, list);
      select.before(wrap);
      // The select stays in the form but out of sight and out of the tab order.
      select.classList.add("combo-native");
      select.tabIndex = -1;
      select.setAttribute("aria-hidden", "true");
      const label = document.querySelector(`label[for="${CSS.escape(select.id)}"]`);
      if (label) label.htmlFor = input.id;

      let shown = [];
      let active = -1;
      const showChosen = () => {
        input.value = textOf(select.selectedOptions[0]);
      };
      const mark = (index) => {
        active = index;
        Array.from(list.children).forEach((item, position) => {
          item.classList.toggle("active", position === index);
          item.setAttribute("aria-selected", String(position === index));
        });
        const item = list.children[index];
        if (item) {
          item.scrollIntoView({ block: "nearest" });
          input.setAttribute("aria-activedescendant", item.id);
        } else {
          input.removeAttribute("aria-activedescendant");
        }
      };
      const render = (text) => {
        const needle = text.trim().toLowerCase();
        shown = options.filter(
          (option) => option.value === "" || option.text.toLowerCase().includes(needle)
        );
        list.replaceChildren();
        shown.forEach((option, position) => {
          const item = document.createElement("li");
          item.id = `${select.id}-opt-${position}`;
          item.setAttribute("role", "option");
          item.textContent = option.text;
          if (option.value === "") item.classList.add("combo-empty-choice");
          if (option.value === select.value) item.classList.add("chosen");
          item.dataset.value = option.value;
          list.append(item);
        });
        if (needle && !shown.some((option) => option.value !== "")) {
          const note = document.createElement("li");
          note.className = "combo-none";
          note.setAttribute("role", "option");
          note.setAttribute("aria-disabled", "true");
          note.textContent = select.dataset.searchEmpty;
          list.append(note);
        }
        mark(-1);
      };
      const open = (text) => {
        render(text);
        list.hidden = false;
        input.setAttribute("aria-expanded", "true");
      };
      const close = () => {
        list.hidden = true;
        input.setAttribute("aria-expanded", "false");
        mark(-1);
      };
      const choose = (value) => {
        const changed = select.value !== value;
        select.value = value;
        showChosen();
        close();
        if (changed) select.dispatchEvent(new Event("change", { bubbles: true }));
      };

      showChosen();
      input.addEventListener("focus", () => {
        input.select();
        open("");
      });
      input.addEventListener("input", () => open(input.value));
      // After Escape the field keeps the focus, so a click opens the list again
      input.addEventListener("click", () => {
        if (list.hidden) open("");
      });
      input.addEventListener("blur", () => {
        close();
        showChosen();
      });
      input.addEventListener("keydown", (event) => {
        const last = shown.length - 1;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (list.hidden) open("");
          const step = event.key === "ArrowDown" ? 1 : -1;
          mark(Math.min(last, Math.max(0, active + step)));
        } else if (event.key === "Enter") {
          event.preventDefault();
          // Without an arrow key the first real match is taken, so typing and Enter is enough
          const first = shown.findIndex((option) => option.value !== "");
          const index = active >= 0 ? active : input.value.trim() ? first : -1;
          if (!list.hidden && index >= 0) choose(shown[index].value);
        } else if (event.key === "Escape" && !list.hidden) {
          // Closes the list only, not the pop-up around the form
          event.preventDefault();
          event.stopPropagation();
          close();
          showChosen();
        }
      });
      // The list entries take the click without the field losing its focus first
      list.addEventListener("mousedown", (event) => event.preventDefault());
      list.addEventListener("click", (event) => {
        const item = event.target.closest("li[data-value]");
        if (item) choose(item.dataset.value);
      });
      for (const type of ["input", "change", "keyup"]) {
        input.addEventListener(type, (event) => event.stopPropagation());
      }
    }
  };
  setUp(document);
  document.body.addEventListener("htmx:afterSettle", () => setUp(document));
})();
