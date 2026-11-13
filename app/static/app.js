// Asks before sending a form that has a data-confirm text (issue #39).
// Without JavaScript the form is simply sent; the server never relies on this question.
document.addEventListener("submit", (event) => {
  const message = event.target.dataset ? event.target.dataset.confirm : undefined;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});
