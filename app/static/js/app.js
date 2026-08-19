async function refreshSansationClips() {
  try {
    await document.fonts.load("700 58px Sansation");
    await document.fonts.ready;
  } catch {
    /* font loading is best-effort */
  }
  document.querySelectorAll(".brand-watermark-svg clipPath text").forEach((node) => {
    node.setAttribute("font-family", "Sansation, sans-serif");
    node.setAttribute("font-weight", "700");
    node.style.fontFamily = "Sansation, sans-serif";
    node.style.fontWeight = "700";
  });
  document.querySelectorAll(".brand-watermark-svg foreignObject[clip-path]").forEach((node) => {
    const value = node.getAttribute("clip-path");
    if (!value || value === "none") {
      return;
    }
    node.setAttribute("clip-path", "none");
    node.getBoundingClientRect();
    node.setAttribute("clip-path", value);
  });
}
refreshSansationClips();

const THEME_STORAGE_KEY = "theme";
const THEME_OPTIONS = ["light", "dark", "system", "professional"];
const THEME_LABELS = {
  light: "Light mode",
  dark: "Dark mode",
  system: "Follows device setting",
  professional: "Professional dark",
  button: {
    light: "Light",
    dark: "Dark",
    system: "Auto",
    professional: "Pro",
  },
};

function storedThemePreference() {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  return THEME_OPTIONS.includes(stored) ? stored : "system";
}

function themeButtonLabel(preference) {
  return THEME_LABELS.button[preference] || THEME_LABELS.button.system;
}

function applyThemePreference(preference) {
  const root = document.documentElement;
  const systemLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  const light =
    preference === "light" || ((preference === "system" || !preference) && systemLight);
  root.classList.remove("light-mode", "professional-mode");
  if (preference === "professional") {
    root.classList.add("professional-mode");
  } else if (light) {
    root.classList.add("light-mode");
  }
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) {
    meta.setAttribute("content", light && preference !== "professional" ? "light" : "dark");
  }

  const iconSun = document.getElementById("icon-sun");
  const iconMoon = document.getElementById("icon-moon");
  const iconAuto = document.getElementById("icon-auto");
  const iconPro = document.getElementById("icon-pro");
  iconSun?.classList.toggle("hidden", preference !== "light");
  iconMoon?.classList.toggle("hidden", preference !== "dark");
  iconAuto?.classList.toggle("hidden", preference !== "system");
  iconPro?.classList.toggle("hidden", preference !== "professional");

  const toggle = document.getElementById("theme-toggle");
  const longLabel = THEME_LABELS[preference] || THEME_LABELS.system;
  toggle?.setAttribute("aria-label", longLabel);
  toggle?.setAttribute("title", longLabel);
  const labelEl = document.getElementById("theme-toggle-label");
  if (labelEl) {
    labelEl.textContent = themeButtonLabel(preference);
  }

  document.querySelectorAll("[data-theme-option]").forEach((button) => {
    const selected = button.getAttribute("data-theme-option") === preference;
    button.classList.toggle("is-active", selected);
    button.setAttribute("aria-checked", selected ? "true" : "false");
  });
}

function setThemeMenuOpen(open) {
  const toggle = document.getElementById("theme-toggle");
  const panel = document.getElementById("theme-menu-panel");
  panel?.classList.toggle("is-closed", !open);
  panel?.setAttribute("aria-hidden", open ? "false" : "true");
  toggle?.setAttribute("aria-expanded", open ? "true" : "false");
}

function initThemePreference() {
  const root = document.getElementById("theme-menu");
  const toggle = document.getElementById("theme-toggle");
  const panel = document.getElementById("theme-menu-panel");
  const lightQuery = window.matchMedia("(prefers-color-scheme: light)");

  applyThemePreference(storedThemePreference());

  toggle?.addEventListener("click", (event) => {
    event.stopPropagation();
    const open = toggle.getAttribute("aria-expanded") === "true";
    setThemeMenuOpen(!open);
  });

  document.querySelectorAll("[data-theme-option]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = button.getAttribute("data-theme-option");
      if (!THEME_OPTIONS.includes(next)) {
        return;
      }
      localStorage.setItem(THEME_STORAGE_KEY, next);
      applyThemePreference(next);
      setThemeMenuOpen(false);
    });
  });

  document.addEventListener("click", (event) => {
    if (!panel || panel.classList.contains("is-closed")) {
      return;
    }
    if (root?.contains(event.target)) {
      return;
    }
    setThemeMenuOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !panel || panel.classList.contains("is-closed")) {
      return;
    }
    setThemeMenuOpen(false);
    toggle?.focus();
  });

  const onSystemChange = () => {
    if (storedThemePreference() === "system") {
      applyThemePreference("system");
    }
  };
  if (typeof lightQuery.addEventListener === "function") {
    lightQuery.addEventListener("change", onSystemChange);
  } else if (typeof lightQuery.addListener === "function") {
    lightQuery.addListener(onSystemChange);
  }
}

const CHEVRON_SVG =
  '<svg class="theme-toggle-chevron" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" /></svg>';

function selectedOptionLabel(select) {
  const option = select.selectedOptions[0];
  return option ? option.textContent.trim() : "";
}

function closeSelectMenus(except) {
  document.querySelectorAll(".choice-menu").forEach((menu) => {
    if (except && menu === except) {
      return;
    }
    menu.classList.add("is-closed");
    menu.setAttribute("aria-hidden", "true");
    const toggle = menu.parentElement?.querySelector(".choice-toggle");
    toggle?.setAttribute("aria-expanded", "false");
  });
}

function enhanceSelectMenus(root = document) {
  root.querySelectorAll("select").forEach((select) => {
    if (!(select instanceof HTMLSelectElement) || select.multiple || select.size > 1) {
      return;
    }
    if (select.closest(".choice") || select.dataset.choice === "1") {
      return;
    }
    const wrap = document.createElement("div");
    wrap.className = "choice";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("choice-native");
    select.dataset.choice = "1";
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "choice-toggle";
    toggle.setAttribute("aria-haspopup", "listbox");
    toggle.setAttribute("aria-expanded", "false");
    const label = document.createElement("span");
    label.className = "choice-toggle-label";
    label.textContent = selectedOptionLabel(select);
    toggle.append(label);
    toggle.insertAdjacentHTML("beforeend", CHEVRON_SVG);
    wrap.insertBefore(toggle, select);

    const menu = document.createElement("div");
    menu.className = "choice-menu is-closed";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-hidden", "true");
    const panel = document.createElement("div");
    panel.className = "theme-menu-panel";
    menu.appendChild(panel);
    wrap.appendChild(menu);

    const renderItems = () => {
      panel.replaceChildren();
      Array.from(select.options).forEach((option, index) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "theme-menu-item";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", option.selected ? "true" : "false");
        item.classList.toggle("is-active", option.selected);
        item.textContent = option.textContent.trim();
        item.disabled = option.disabled;
        item.addEventListener("click", () => {
          select.selectedIndex = index;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          label.textContent = selectedOptionLabel(select);
          renderItems();
          closeSelectMenus();
          toggle.focus();
        });
        panel.appendChild(item);
      });
    };
    renderItems();

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = toggle.getAttribute("aria-expanded") === "true";
      closeSelectMenus(open ? null : menu);
      if (!open) {
        renderItems();
        menu.classList.remove("is-closed");
        menu.setAttribute("aria-hidden", "false");
        toggle.setAttribute("aria-expanded", "true");
      }
    });

    select.addEventListener("change", () => {
      label.textContent = selectedOptionLabel(select);
      renderItems();
    });
  });
}

document.addEventListener("click", (event) => {
  if (event.target instanceof Element && event.target.closest(".choice")) {
    return;
  }
  closeSelectMenus();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") {
    return;
  }
  closeSelectMenus();
});

initThemePreference();
enhanceSelectMenus();

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

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail.target;
  if (target && target.id === "log-view-panel") {
    const view = document.getElementById("log-view");
    const follow = document.getElementById("log-follow");
    if (view instanceof HTMLElement && follow instanceof HTMLInputElement && follow.checked) {
      view.scrollTop = view.scrollHeight;
    }
  }
});

const initialLogView = document.getElementById("log-view");
if (initialLogView) {
  initialLogView.scrollTop = initialLogView.scrollHeight;
}

document.body.addEventListener("htmx:responseError", (event) => {
  const target = event.detail.target;
  if (target && target.id === "log-view-panel") {
    return;
  }
  if (target) {
    target.innerHTML =
      '<article class="result fail"><header><span class="badge fail">Fail</span><div><strong>Request failed</strong><p>The tool could not be started. Check the application logs.</p></div></header></article>';
  }
});
