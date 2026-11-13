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
