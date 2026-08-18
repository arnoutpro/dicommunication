document.querySelectorAll(".nav-branch > summary a").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.stopPropagation();
  });
});
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

document.body.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement) || select.name !== "identity_id") {
    return;
  }
  const option = select.selectedOptions[0];
  const form = select.form;
  if (!option || !form) {
    return;
  }
  const station = form.querySelector('[name="station_ae_title"]');
  const modality = form.querySelector('[name="modality"]');
  if (station instanceof HTMLInputElement && option.dataset.station !== undefined) {
    station.value = option.dataset.station;
  }
  if (modality instanceof HTMLInputElement && option.dataset.modality) {
    modality.value = option.dataset.modality;
  }
});

document.body.addEventListener("htmx:responseError", (event) => {
  const target = event.detail.target;
  if (target) {
    target.innerHTML =
      '<article class="result fail"><header><span class="badge fail">Fail</span><div><strong>Request failed</strong><p>The tool could not be started. Check the application logs.</p></div></header></article>';
  }
});
