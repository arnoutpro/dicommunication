document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const message = form.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.querySelectorAll("[data-autohide]").forEach((node) => {
  window.setTimeout(() => {
    node.style.display = "none";
  }, 3500);
});

document.body.addEventListener("htmx:responseError", (event) => {
  const target = event.detail.target;
  if (target) {
    target.innerHTML =
      '<article class="result fail"><header><span class="badge fail">Fail</span><div><strong>Request failed</strong><p>The tool could not be started. Check the application logs.</p></div></header></article>';
  }
});
