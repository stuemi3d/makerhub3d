// Period choice (issues #206, #207): the two dates only count for "custom". Typing a date
// switches the choice to "custom", and picking a named period clears the dates, so the form
// never says one thing and does another.
(() => {
  const form = document.querySelector("form.period-form");
  if (!form) return;
  const select = form.querySelector("select[name=period]");
  const dates = form.querySelectorAll("input[type=date]");
  dates.forEach((input) =>
    input.addEventListener("change", () => {
      if (input.value) select.value = "custom";
    })
  );
  select.addEventListener("change", () => {
    if (select.value !== "custom") dates.forEach((input) => (input.value = ""));
  });
})();
