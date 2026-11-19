// Period choice (issues #206, #207): the two dates only count for "custom", so they are greyed
// out (and not sent) until "custom" is chosen. Choosing another period clears them.
(() => {
  const form = document.querySelector("form.period-form");
  if (!form) return;
  const select = form.querySelector("select[name=period]");
  const dates = form.querySelectorAll("input[type=date]");
  const update = () => {
    const free = select.value === "custom";
    dates.forEach((input) => {
      input.disabled = !free;
      if (!free) input.value = "";
    });
  };
  select.addEventListener("change", update);
  update();
})();
