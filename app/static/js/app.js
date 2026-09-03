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
  return THEME_OPTIONS.includes(stored) ? stored : "light";
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

const NAV_TREE_KEY = "nav-tree";

function navTreeState() {
  try {
    const raw = localStorage.getItem(NAV_TREE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function setNavBranchOpen(branch, open, persist) {
  const input = branch.querySelector(":scope > .nav-fold-check");
  if (input instanceof HTMLInputElement) {
    input.checked = open;
  }
  branch.classList.toggle("is-open", open);
  if (!persist) {
    return;
  }
  const id = branch.getAttribute("data-nav-id");
  if (!id) {
    return;
  }
  const stored = navTreeState();
  stored[id] = open;
  localStorage.setItem(NAV_TREE_KEY, JSON.stringify(stored));
}

function initNavTree() {
  const stored = navTreeState();
  document.querySelectorAll(".nav-branch[data-nav-id]").forEach((branch) => {
    const id = branch.getAttribute("data-nav-id");
    const input = branch.querySelector(":scope > .nav-fold-check");
    const hasActive = Boolean(branch.querySelector("a.active"));
    if (hasActive) {
      setNavBranchOpen(branch, true);
    } else if (id && stored[id] === true) {
      setNavBranchOpen(branch, true);
    } else if (id && stored[id] === false && !hasActive) {
      setNavBranchOpen(branch, false);
    } else if (input instanceof HTMLInputElement) {
      branch.classList.toggle("is-open", input.checked);
    }
    if (!(input instanceof HTMLInputElement) || input.dataset.navReady === "1") {
      return;
    }
    input.dataset.navReady = "1";
    input.addEventListener("change", () => {
      setNavBranchOpen(branch, input.checked, true);
    });
  });
}

function randomPatientToken() {
  try {
    const bytes = new Uint8Array(4);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("").toUpperCase();
  } catch {
    return Date.now().toString(16).toUpperCase().slice(-8).padStart(8, "0");
  }
}

function generatedPatientName() {
  return `ARNPRO^PDF${randomPatientToken().slice(0, 4)}`;
}

function generatedPatientId() {
  return `PDF${randomPatientToken()}`;
}

function initPdfStoreForm(scope) {
  const root = scope instanceof Element ? scope : document;
  const form = root.querySelector?.("[data-pdf-store]") || document.querySelector("[data-pdf-store]");
  if (!(form instanceof HTMLFormElement) || form.dataset.pdfReady === "1") {
    return;
  }
  form.dataset.pdfReady = "1";
  const nameInput = form.querySelector('[name="patient_name"]');
  const idInput = form.querySelector('[name="patient_id"]');
  const generateName = form.querySelector("[data-generate-name]");
  const generateId = form.querySelector("[data-generate-id]");
  const uniquePatient = form.querySelector("[data-unique-patient]");
  const sameStudy = form.querySelector("[data-same-study]");
  const directoryInput = form.querySelector("[data-directory-input]");
  const browseBtn = form.querySelector("[data-browse-directory]");
  const scanBtn = form.querySelector("[data-scan-directory]");
  const scanTarget = document.getElementById("pdf-scan");
  const browseStatus = document.getElementById("pdf-browse-status");
  const fileStatus = document.getElementById("pdf-file-status");
  let wasUnique = uniquePatient instanceof HTMLInputElement && uniquePatient.checked;
  let wasGenName = generateName instanceof HTMLInputElement && generateName.checked;
  let wasGenId = generateId instanceof HTMLInputElement && generateId.checked;

  function setBrowseStatus(message) {
    if (!(browseStatus instanceof HTMLElement)) {
      return;
    }
    if (!message) {
      browseStatus.hidden = true;
      browseStatus.innerHTML = "";
      return;
    }
    browseStatus.hidden = false;
    browseStatus.innerHTML = `<p class="scan-result fail">${message}</p>`;
  }

  function setFileStatus(message) {
    if (!(fileStatus instanceof HTMLElement)) {
      return;
    }
    if (!message) {
      fileStatus.hidden = true;
      fileStatus.innerHTML = "";
      return;
    }
    fileStatus.hidden = false;
    fileStatus.innerHTML = `<p class="scan-result fail">${message}</p>`;
  }

  function applyPatientMode() {
    const unique = uniquePatient instanceof HTMLInputElement && uniquePatient.checked;
    const wantName = generateName instanceof HTMLInputElement && generateName.checked;
    const wantId = generateId instanceof HTMLInputElement && generateId.checked;
    const genName = unique || wantName;
    const genId = unique || wantId;
    if (unique && generateName instanceof HTMLInputElement) {
      generateName.checked = true;
    }
    if (unique && generateId instanceof HTMLInputElement) {
      generateId.checked = true;
    }
    if (unique && sameStudy instanceof HTMLInputElement) {
      sameStudy.checked = false;
    }
    if (unique && !wasUnique) {
      if (nameInput instanceof HTMLInputElement) {
        nameInput.value = "";
      }
      if (idInput instanceof HTMLInputElement) {
        idInput.value = "";
      }
    }
    const nameLabel = form.querySelector('[data-patient-field="name"]');
    const idLabel = form.querySelector('[data-patient-field="id"]');
    nameLabel?.classList.toggle("is-optional", genName);
    idLabel?.classList.toggle("is-optional", genId);
    if (nameInput instanceof HTMLInputElement) {
      nameInput.required = !genName;
      nameInput.placeholder = unique ? "From each file name" : genName ? "ARNPRO^PDFxxxx" : "DOE^JANE";
    }
    if (idInput instanceof HTMLInputElement) {
      idInput.required = !genId;
      idInput.placeholder = unique ? "From each file name" : genId ? "PDFxxxxxxxx" : "1001";
    }
    if (!unique && nameInput instanceof HTMLInputElement && wantName && (!wasGenName || !nameInput.value.trim())) {
      nameInput.value = generatedPatientName();
    }
    if (!unique && idInput instanceof HTMLInputElement && wantId && (!wasGenId || !idInput.value.trim())) {
      idInput.value = generatedPatientId();
    }
    wasUnique = unique;
    wasGenName = wantName && !unique;
    wasGenId = wantId && !unique;
  }

  function keepPdfFiles(input) {
    if (!(input instanceof HTMLInputElement) || !input.files || input.files.length === 0) {
      return;
    }
    const kept = [];
    let dropped = 0;
    for (const file of input.files) {
      if ((file.name || "").toLowerCase().endsWith(".pdf")) {
        kept.push(file);
      } else {
        dropped += 1;
      }
    }
    if (!dropped) {
      return;
    }
    try {
      const transfer = new DataTransfer();
      kept.forEach((file) => transfer.items.add(file));
      input.files = transfer.files;
    } catch {
      input.value = "";
      setFileStatus("Only .pdf files are allowed. Choose PDFs again.");
      return;
    }
    setFileStatus(
      dropped === 1
        ? "1 file was not a PDF and was removed. Only .pdf is allowed."
        : `${dropped} files were not PDFs and were removed. Only .pdf is allowed.`
    );
  }

  async function scanDirectory() {
    if (!(directoryInput instanceof HTMLInputElement) || !scanTarget) {
      return;
    }
    const directory = directoryInput.value.trim();
    if (!directory) {
      setBrowseStatus("");
      scanTarget.innerHTML = '<p class="scan-result fail">Type a directory or use … to browse.</p>';
      return;
    }
    setBrowseStatus("");
    scanTarget.innerHTML = '<p class="scan-result">Scanning…</p>';
    const body = new FormData();
    body.set("directory", directory);
    try {
      const response = await fetch("/tools/pdf-store/scan", {
        method: "POST",
        body,
        headers: { "HX-Request": "true" },
      });
      scanTarget.innerHTML = await response.text();
    } catch {
      scanTarget.innerHTML = '<p class="scan-result fail">Scan failed.</p>';
    }
  }

  [generateName, generateId, uniquePatient].forEach((node) => {
    node?.addEventListener("change", applyPatientMode);
    node?.addEventListener("click", () => {
      window.setTimeout(applyPatientMode, 0);
    });
  });
  applyPatientMode();

  form.querySelectorAll('input[type="file"][name="pdfs"], input[type="file"][name="folder"]').forEach((input) => {
    input.addEventListener("change", () => keepPdfFiles(input));
  });
  const zipInput = form.querySelector('input[type="file"][name="zip_file"]');
  zipInput?.addEventListener("change", () => {
    const file = zipInput.files?.[0];
    if (!file) {
      return;
    }
    if (!(file.name || "").toLowerCase().endsWith(".zip")) {
      zipInput.value = "";
      setFileStatus("Only a .zip of PDF files is allowed here.");
    }
  });

  browseBtn?.addEventListener("click", async () => {
    browseBtn.disabled = true;
    try {
      const response = await fetch("/api/fs/pick-directory", { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.path && directoryInput instanceof HTMLInputElement) {
        directoryInput.value = payload.path;
        setBrowseStatus("");
        await scanDirectory();
        return;
      }
      setBrowseStatus("No folder dialog on this session (needs a desktop display). Type the path, or use Folder of PDFs above.");
    } catch {
      setBrowseStatus("Could not open a folder dialog. Type the path instead.");
    } finally {
      browseBtn.disabled = false;
    }
  });

  scanBtn?.addEventListener("click", (event) => {
    event.preventDefault();
    scanDirectory();
  });

  directoryInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      scanDirectory();
    }
  });
}

function initAnonymizeForm(scope) {
  const root = scope instanceof Element ? scope : document;
  const form = root.querySelector?.("[data-anon-form]") || document.querySelector("[data-anon-form]");
  if (!(form instanceof HTMLFormElement) || form.dataset.anonReady === "1") {
    return;
  }
  form.dataset.anonReady = "1";

  const removePatientOptions = form.querySelector("[data-anon-remove-patient-options]");
  const customTable = form.querySelector("[data-anon-custom-table]");

  function syncModeVisibility() {
    const checked = form.querySelector('[data-anon-mode-radio]:checked');
    const mode = checked ? checked.value : "";
    if (removePatientOptions instanceof HTMLElement) {
      removePatientOptions.hidden = mode !== "remove_patient";
    }
    if (customTable instanceof HTMLElement) {
      customTable.hidden = mode !== "custom";
    }
  }
  form.querySelectorAll("[data-anon-mode-radio]").forEach((radio) => {
    radio.addEventListener("change", syncModeVisibility);
  });
  syncModeVisibility();

  const browseBtn = form.querySelector("[data-anon-browse]");
  const directoryInput = form.querySelector("[data-anon-output-dir]");
  browseBtn?.addEventListener("click", async () => {
    browseBtn.disabled = true;
    try {
      const response = await fetch("/api/fs/pick-directory", { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (response.ok && payload.path && directoryInput instanceof HTMLInputElement) {
        directoryInput.value = payload.path;
      }
    } catch {
      // No dialog available in this session — the text field still works.
    } finally {
      browseBtn.disabled = false;
    }
  });
}

initNavTree();
initPdfStoreForm(document);
initAnonymizeForm(document);
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

function splitHl7Lines(text) {
  const normalized = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  if (!normalized) {
    return [""];
  }
  return normalized.split("\n");
}

function parseHl7Segment(line) {
  const value = String(line || "").replace(/\r/g, "");
  if (value.length >= 3) {
    return { id: value.slice(0, 3), rest: value.slice(3) };
  }
  return { id: value, rest: "" };
}

function composeHl7Segment(id, rest) {
  const type = String(id || "")
    .replace(/[\r\n]/g, "")
    .slice(0, 3)
    .toUpperCase();
  return type + String(rest || "").replace(/\r/g, "");
}

function resizeHl7Field(node) {
  if (!(node instanceof HTMLTextAreaElement)) {
    return;
  }
  node.style.height = "auto";
  node.style.height = `${Math.max(node.scrollHeight, 42)}px`;
}

function initHl7Editor(root) {
  if (!(root instanceof HTMLElement) || root.dataset.hl7Ready === "1") {
    return;
  }
  const raw = root.querySelector(".hl7-body");
  const segments = root.querySelector("[data-hl7-segments]");
  const addBtn = root.querySelector("[data-hl7-add-segment]");
  const form = root.closest("form");
  if (!(raw instanceof HTMLTextAreaElement) || !(segments instanceof HTMLElement)) {
    return;
  }
  root.dataset.hl7Ready = "1";
  root.classList.add("is-ready");
  let view = "segments";

  function setView(next) {
    view = next === "raw" ? "raw" : "segments";
    root.classList.toggle("is-raw", view === "raw");
    root.querySelectorAll("[data-hl7-view]").forEach((btn) => {
      const active = btn.getAttribute("data-hl7-view") === view;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (view === "segments") {
      rebuildRows();
    }
  }

  function syncRawFromRows() {
    const lines = [...segments.querySelectorAll(".hl7-seg-row")].map((row) => {
      const id = row.querySelector(".hl7-seg-id");
      const fields = row.querySelector(".hl7-seg-fields");
      const type = id instanceof HTMLInputElement ? id.value : "";
      const rest = fields instanceof HTMLTextAreaElement ? fields.value : "";
      return composeHl7Segment(type, rest);
    });
    raw.value = lines.join("\n");
  }

  function addRow(line) {
    const parsed = parseHl7Segment(line);
    const row = document.createElement("div");
    row.className = "hl7-seg-row";

    const id = document.createElement("input");
    id.className = "hl7-seg-id";
    id.type = "text";
    id.maxLength = 3;
    id.spellcheck = false;
    id.autocomplete = "off";
    id.setAttribute("aria-label", "Segment ID");
    id.value = parsed.id;

    const fields = document.createElement("textarea");
    fields.className = "hl7-seg-fields";
    fields.rows = 1;
    fields.spellcheck = false;
    fields.wrap = "soft";
    fields.setAttribute("aria-label", "Segment fields");
    fields.value = parsed.rest;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "link-button danger hl7-seg-remove";
    remove.setAttribute("aria-label", "Remove segment");
    remove.textContent = "Remove";

    id.addEventListener("input", () => {
      id.value = id.value.replace(/[\r\n]/g, "").slice(0, 3).toUpperCase();
      syncRawFromRows();
    });
    fields.addEventListener("input", () => {
      resizeHl7Field(fields);
      syncRawFromRows();
    });
    remove.addEventListener("click", () => {
      const rows = segments.querySelectorAll(".hl7-seg-row");
      if (rows.length <= 1) {
        id.value = "";
        fields.value = "";
        resizeHl7Field(fields);
      } else {
        row.remove();
      }
      syncRawFromRows();
    });

    row.append(id, fields, remove);
    segments.append(row);
    resizeHl7Field(fields);
  }

  function rebuildRows() {
    segments.replaceChildren();
    splitHl7Lines(raw.value).forEach(addRow);
  }

  root.querySelectorAll("[data-hl7-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (view === "segments") {
        syncRawFromRows();
      }
      setView(btn.getAttribute("data-hl7-view") || "segments");
    });
  });

  if (addBtn) {
    addBtn.addEventListener("click", () => {
      addRow("");
      syncRawFromRows();
      const lastId = segments.querySelector(".hl7-seg-row:last-child .hl7-seg-id");
      if (lastId instanceof HTMLInputElement) {
        lastId.focus();
      }
    });
  }

  if (form) {
    form.addEventListener("submit", () => {
      if (view === "segments") {
        syncRawFromRows();
      }
    });
  }

  setView("segments");
}

function initHl7Editors(scope) {
  const root = scope instanceof Element ? scope : document;
  if (root instanceof HTMLElement && root.matches("[data-hl7-editor]")) {
    initHl7Editor(root);
  }
  root.querySelectorAll("[data-hl7-editor]").forEach(initHl7Editor);
}

initHl7Editors(document);
window.addEventListener("resize", () => {
  document.querySelectorAll(".hl7-seg-fields").forEach(resizeHl7Field);
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail.target;
  if (target instanceof HTMLElement) {
    initHl7Editors(target);
    initPdfStoreForm(document);
  }
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

function csvCell(value) {
  const text = value == null ? "" : String(value);
  if (/[",\n\r]/.test(text)) {
    return `"${text.replaceAll('"', '""')}"`;
  }
  return text;
}

function downloadText(filename, text, type) {
  const blob = new Blob([text], { type });
  if (window.navigator && typeof window.navigator.msSaveOrOpenBlob === "function") {
    window.navigator.msSaveOrOpenBlob(blob, filename);
    return true;
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  window.setTimeout(() => {
    link.remove();
    URL.revokeObjectURL(url);
  }, 1500);
  return true;
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
  return Promise.resolve();
}

function findAdvancedForm() {
  return document.querySelector("[data-find-advanced]");
}

function findValueInput(form, keyword) {
  return form.querySelector(`[data-find-value="${keyword}"]`);
}

function selectedFindLevel(form) {
  const checked = form.querySelector("[data-find-level]:checked");
  return checked ? checked.value : "STUDY";
}

function collectFindValues(form) {
  const values = {};
  form.querySelectorAll("[data-find-value]").forEach((input) => {
    values[input.getAttribute("data-find-value")] = input.value.trim();
  });
  return values;
}

function setFindValue(form, keyword, value) {
  const input = findValueInput(form, keyword);
  if (input) {
    input.value = value || "";
  }
}

function setFindLevel(form, level) {
  const radio = form.querySelector(`[data-find-level][value="${level}"]`);
  if (radio) {
    radio.checked = true;
  }
}

const MODALITY_CHIP_CODES = ["CR", "CT", "DX", "MG", "MR", "NM", "OT", "PT", "RF", "SC", "SR", "US", "XA"];

function syncModalityChipsFromInput(form) {
  const input = findValueInput(form, "ModalitiesInStudy");
  const chipsBox = form.querySelector("[data-find-modality-chips]");
  if (!input || !chipsBox) {
    return;
  }
  const tokens = input.value
    .split("\\")
    .map((token) => token.trim().toUpperCase())
    .filter(Boolean);
  chipsBox.querySelectorAll("[data-modality-chip]").forEach((box) => {
    box.checked = tokens.includes(box.value);
  });
}

function syncModalityInputFromChips(form) {
  const input = findValueInput(form, "ModalitiesInStudy");
  const chipsBox = form.querySelector("[data-find-modality-chips]");
  if (!input || !chipsBox) {
    return;
  }
  const known = new Set(MODALITY_CHIP_CODES);
  const extras = input.value
    .split("\\")
    .map((token) => token.trim().toUpperCase())
    .filter((token) => token && !known.has(token));
  const checked = [...chipsBox.querySelectorAll("[data-modality-chip]:checked")].map((box) => box.value);
  input.value = [...checked, ...extras].join("\\");
}

function isoDateFromDicomToken(token) {
  return /^\d{8}$/.test(token) ? `${token.slice(0, 4)}-${token.slice(4, 6)}-${token.slice(6, 8)}` : token;
}

function syncDatePickersFromInput(form) {
  const input = findValueInput(form, "StudyDate");
  const from = form.querySelector("[data-date-from]");
  const to = form.querySelector("[data-date-to]");
  if (!input || !from || !to) {
    return;
  }
  const text = input.value.trim();
  const datePart = "\\d{4}-\\d{2}-\\d{2}|\\d{8}";
  const rangeMatch = text.match(new RegExp(`^(${datePart})\\s*-\\s*(${datePart})$`));
  if (rangeMatch) {
    from.value = isoDateFromDicomToken(rangeMatch[1]);
    to.value = isoDateFromDicomToken(rangeMatch[2]);
  } else if (new RegExp(`^(${datePart})$`).test(text)) {
    from.value = isoDateFromDicomToken(text);
    to.value = "";
  } else {
    // Open-ended range, wildcard, or other raw DICOM syntax the pickers can't represent.
    from.value = "";
    to.value = "";
  }
}

function syncDateInputFromPickers(form) {
  const input = findValueInput(form, "StudyDate");
  const from = form.querySelector("[data-date-from]");
  const to = form.querySelector("[data-date-to]");
  if (!input || !from || !to) {
    return;
  }
  if (from.value && to.value) {
    input.value = `${from.value}-${to.value}`;
  } else {
    input.value = from.value || to.value || "";
  }
}

function applyDatePreset(form, preset) {
  const from = form.querySelector("[data-date-from]");
  const to = form.querySelector("[data-date-to]");
  if (!from || !to) {
    return;
  }
  const fmt = (date) => date.toISOString().slice(0, 10);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (preset === "clear") {
    from.value = "";
    to.value = "";
  } else if (preset === "today") {
    from.value = fmt(today);
    to.value = "";
  } else if (preset === "yesterday") {
    const day = new Date(today);
    day.setDate(day.getDate() - 1);
    from.value = fmt(day);
    to.value = "";
  } else if (preset === "7" || preset === "30") {
    const start = new Date(today);
    start.setDate(start.getDate() - (Number(preset) - 1));
    from.value = fmt(start);
    to.value = fmt(today);
  } else if (preset === "month") {
    from.value = fmt(new Date(today.getFullYear(), today.getMonth(), 1));
    to.value = fmt(today);
  }
  syncDateInputFromPickers(form);
  syncFindAdvanced(form);
}

function syncFindAdvanced(form) {
  if (!form) {
    return;
  }
  const level = selectedFindLevel(form);
  const values = collectFindValues(form);
  const hint = form.querySelector("[data-find-level-hint]");
  const hints = {
    STUDY:
      "Study Root FIND at Study level (patient + study keys). Empty checked fields are return columns. This is not Modality Worklist. Vue does not offer relational C-FIND or C-GET.",
    SERIES:
      "Series keys unlock after Study Instance UID is filled. Hierarchical FIND cannot search series across the archive without that parent Unique key.",
    IMAGE:
      "Image keys unlock after Study Instance UID and Series Instance UID are filled. Retrieve of SR text is C-MOVE of the SR series, not C-GET.",
  };
  if (hint) {
    hint.textContent = hints[level] || hints.STUDY;
  }
  form.querySelectorAll("[data-find-group]").forEach((group) => {
    const visible = [...group.querySelectorAll("[data-find-key]")].some((node) =>
      (node.getAttribute("data-levels") || "").split(/\s+/).includes(level)
    );
    group.hidden = !visible;
  });
  form.querySelectorAll("[data-find-key]").forEach((node) => {
    const levels = (node.getAttribute("data-levels") || "").split(/\s+/).filter(Boolean);
    const onLevel = levels.includes(level);
    node.hidden = !onLevel;
    const include = node.querySelector("[data-find-include]");
    const input = node.querySelector("[data-find-value]");
    const hintNode = node.querySelector("[data-find-hint]");
    const requires = (node.getAttribute("data-requires") || "").split(/\s+/).filter(Boolean);
    const modalityIn = (node.getAttribute("data-modality-in") || "")
      .split(/\s+/)
      .filter(Boolean)
      .map((item) => item.toUpperCase());
    const missing = requires.filter((name) => !values[name]);
    const modality = (values.Modality || "").toUpperCase();
    const modalityBlocked = modalityIn.length > 0 && modality && !modalityIn.includes(modality);
    const locked = !onLevel || missing.length > 0 || modalityBlocked;
    node.classList.toggle("is-locked", onLevel && locked);
    if (include) {
      include.disabled = locked;
    }
    if (input) {
      input.disabled = locked;
      input.required = node.getAttribute("data-match-required") === "1" && onLevel && !locked;
    }
    if (hintNode) {
      const original = node.getAttribute("data-hint-original");
      if (original == null) {
        node.setAttribute("data-hint-original", hintNode.textContent || "");
      }
      if (!onLevel) {
        hintNode.textContent = node.getAttribute("data-hint-original") || "";
      } else if (missing.length) {
        hintNode.textContent = `Enter ${missing.join(", ")} first`;
      } else if (modalityBlocked) {
        hintNode.textContent = `Available when Modality is ${modalityIn.join(", ")} (or left empty)`;
      } else {
        hintNode.textContent = node.getAttribute("data-hint-original") || "";
      }
    }
  });
  syncModalityChipsFromInput(form);
  syncDatePickersFromInput(form);
}

function applyFindSelection(form, mode) {
  if (mode === "sr") {
    const uid = findValueInput(form, "StudyInstanceUID");
    if (uid && uid.value.trim()) {
      setFindLevel(form, "SERIES");
      syncFindAdvanced(form);
      setFindValue(form, "Modality", "SR");
      const include = form.querySelector('[data-find-key="Modality"] [data-find-include]');
      if (include) {
        include.checked = true;
      }
    }
    syncFindAdvanced(form);
    return;
  }
  form.querySelectorAll("[data-find-key]").forEach((node) => {
    if (node.hidden) {
      return;
    }
    const include = node.querySelector("[data-find-include]");
    const input = node.querySelector("[data-find-value]");
    if (mode === "clear" && input && !input.disabled) {
      input.value = "";
    }
    if (!include || include.disabled) {
      return;
    }
    if (mode === "all") {
      include.checked = true;
    } else if (mode === "defaults") {
      const role = node.getAttribute("data-role");
      include.checked =
        node.getAttribute("data-default-return") === "1" || role === "unique" || role === "required";
    }
  });
  syncFindAdvanced(form);
}

function parseFindExport(root) {
  const node = root.querySelector("[data-find-export]");
  if (!node) {
    return null;
  }
  const raw = node.tagName === "TEXTAREA" ? node.value : node.textContent;
  try {
    return JSON.parse(raw || "null");
  } catch {
    return null;
  }
}

function flashFindExport(root, message, ok) {
  const bar = root.querySelector("[data-find-export-hint]") || root.querySelector(".find-export-bar .hint");
  if (!bar) {
    if (!ok) {
      window.alert(message);
    }
    return;
  }
  if (!bar.getAttribute("data-find-hint-original")) {
    bar.setAttribute("data-find-hint-original", bar.textContent || "");
  }
  bar.textContent = message;
  bar.classList.toggle("find-copy-flash", Boolean(ok));
  bar.classList.toggle("find-export-error", !ok);
  window.setTimeout(() => {
    bar.classList.remove("find-copy-flash");
    bar.classList.remove("find-export-error");
    bar.textContent = bar.getAttribute("data-find-hint-original") || "";
  }, 2200);
}

function findExportFilename(payload, extension) {
  const level = (payload && payload.level ? payload.level : "STUDY").toLowerCase();
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  return `c-find-advanced-${level}-${stamp}.${extension}`;
}

function copyFindTable(root) {
  const payload = parseFindExport(root);
  const table = root.querySelector("[data-find-table]");
  let text = "";
  if (payload && payload.records && payload.columns) {
    const labels = payload.columns.map((column) => (payload.labels && payload.labels[column]) || column);
    const lines = [labels.join("\t")];
    payload.records.forEach((row) => {
      lines.push(payload.columns.map((column) => String(row[column] ?? "").replaceAll("\t", " ")).join("\t"));
    });
    text = lines.join("\n");
  } else if (table) {
    text = [...table.rows]
      .map((row) => [...row.cells].map((cell) => cell.innerText.replaceAll("\t", " ")).join("\t"))
      .join("\n");
  }
  if (!text) {
    flashFindExport(root, "Nothing to copy.", false);
    return;
  }
  copyText(text)
    .then(() => flashFindExport(root, "Copied as tab-separated rows.", true))
    .catch(() => {
      window.prompt("Copy this table", text);
    });
}

function downloadFindCsv(root) {
  const payload = parseFindExport(root);
  if (!payload || !payload.records || !payload.columns) {
    flashFindExport(root, "Nothing to download.", false);
    return;
  }
  const labels = payload.columns.map((column) => (payload.labels && payload.labels[column]) || column);
  const lines = [labels.map(csvCell).join(",")];
  payload.records.forEach((row) => {
    lines.push(payload.columns.map((column) => csvCell(row[column])).join(","));
  });
  downloadText(findExportFilename(payload, "csv"), `${lines.join("\n")}\n`, "text/csv;charset=utf-8");
  flashFindExport(root, "Downloading CSV…", true);
}

function downloadFindJson(root) {
  const payload = parseFindExport(root);
  if (!payload) {
    flashFindExport(root, "Nothing to download.", false);
    return;
  }
  downloadText(
    findExportFilename(payload, "json"),
    `${JSON.stringify(payload, null, 2)}\n`,
    "application/json;charset=utf-8"
  );
  flashFindExport(root, "Downloading JSON…", true);
}

function patientNameLetter(name) {
  const family = String(name || "").split("^")[0].trim();
  const letter = family.charAt(0).toUpperCase();
  return /[A-Z]/.test(letter) ? letter : "#";
}

function buildFindPatientSummary(payload) {
  const seen = new Map(); // dedupe key -> letter
  payload.records.forEach((row) => {
    const id = String(row.PatientID || "").trim();
    const name = String(row.PatientName || "").trim();
    const key = id || name;
    if (!key || seen.has(key)) {
      return;
    }
    seen.set(key, patientNameLetter(name));
  });
  const counts = new Map();
  seen.forEach((letter) => {
    counts.set(letter, (counts.get(letter) || 0) + 1);
  });
  const letters = [...counts.keys()].filter((l) => l !== "#").sort();
  if (counts.has("#")) {
    letters.push("#");
  }
  return { total: seen.size, studyRows: payload.records.length, letters, counts };
}

function renderFindPatientSummary(box, summary, note) {
  const rows = summary.letters
    .map((letter) => `<tr><td>${letter}</td><td>${summary.counts.get(letter)}</td></tr>`)
    .join("");
  const dupeNote =
    summary.total < summary.studyRows
      ? `${summary.studyRows} matching studies, ${summary.total} distinct patients (deduplicated by Patient ID, or Patient Name where ID was blank).`
      : `${summary.total} distinct patients.`;
  box.innerHTML = `
    <h3 class="result-subhead">Patients by name</h3>
    <p class="hint">${dupeNote}</p>
    ${note || ""}
    <div class="table-wrap find-table-wrap">
      <table class="find-table">
        <thead><tr><th>Starts with</th><th>Patients</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
  box.hidden = false;
}

function collectFindQueryPayload() {
  const form = document.querySelector("[data-find-advanced]");
  if (!form) {
    return null;
  }
  const remoteId = form.querySelector('select[name="remote_id"]')?.value;
  const level = form.querySelector("[data-find-level]:checked")?.value || "STUDY";
  const identityId = form.querySelector('select[name="identity_id"]')?.value || "";
  if (!remoteId) {
    return null;
  }
  const values = {};
  form.querySelectorAll("[data-find-value]").forEach((input) => {
    const keyword = input.getAttribute("data-find-value");
    const value = (input.value || "").trim();
    if (keyword && value) {
      values[keyword] = value;
    }
  });
  return { remoteId, level, identityId, values };
}

async function fetchExactPatientRecords(query) {
  const response = await fetch("/api/tools/c-find-advanced/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      remote_id: query.remoteId,
      identity_id: query.identityId || null,
      options: {
        level: query.level,
        values: query.values,
        return_keys: ["PatientName", "PatientID"],
        max_records: 50000,
      },
    }),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const body = await response.json();
  if (!body.ok) {
    throw new Error(body.summary || "Query failed");
  }
  const stillTruncated = (body.steps || []).some(
    (step) => step.name === "C-FIND" && step.details && step.details.truncated
  );
  return { records: body.records || [], truncated: stillTruncated };
}

async function recountFindPatientsExact(box, capNote) {
  const query = collectFindQueryPayload();
  if (!query) {
    box.insertAdjacentHTML(
      "beforeend",
      `<p class="hint find-export-error">Could not find the query form to re-run without the row cap — ${capNote}</p>`
    );
    return;
  }
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = "Re-querying without the row limit for an exact count…";
  box.appendChild(note);
  try {
    const { records, truncated } = await fetchExactPatientRecords(query);
    const summary = buildFindPatientSummary({ records });
    const truncNote = truncated
      ? `<p class="hint find-export-error">Even the uncapped re-query hit its own safety limit (50000 studies) — narrow Study Date (or another filter) for an exact count.</p>`
      : `<p class="hint">Recomputed without the display row cap — this count covers every matching study.</p>`;
    renderFindPatientSummary(box, summary, truncNote);
  } catch (err) {
    note.remove();
    box.insertAdjacentHTML(
      "beforeend",
      `<p class="hint find-export-error">Automatic re-query failed (${err.message}) — ${capNote}</p>`
    );
  }
}

function toggleFindPatientSummary(root) {
  const box = root.querySelector("[data-find-patient-summary-result]");
  if (!box) {
    return;
  }
  if (!box.hidden) {
    box.hidden = true;
    return;
  }
  const payload = parseFindExport(root);
  if (!payload || !payload.records || !payload.records.length) {
    flashFindExport(root, "Nothing to count.", false);
    return;
  }
  const summary = buildFindPatientSummary(payload);
  renderFindPatientSummary(box, summary, "");
  if (payload.truncated) {
    offerFindPatientsRecount(box);
  }
}

function offerFindPatientsRecount(box) {
  const capNote = "narrow Study Date (or other filters) and re-run to get a complete count.";
  const prompt = document.createElement("div");
  prompt.className = "find-cap-confirm";
  prompt.innerHTML =
    "<p>These counts only cover the first 2000 studies in the table. Getting an exact count re-queries the PACS without that limit, which could have a significant impact on the performance of the PACS.</p>" +
    '<button class="button compact" type="button" data-find-recount-confirm>Get exact count anyway</button>';
  box.appendChild(prompt);
  prompt.querySelector("[data-find-recount-confirm]").addEventListener("click", () => {
    prompt.remove();
    recountFindPatientsExact(box, capNote);
  });
}

function clearFindFollow(form) {
  const follow = form.querySelector("[data-find-follow-field]");
  const studies = form.querySelector("[data-find-studies-field]");
  const cap = form.querySelector("[data-find-cap-field]");
  if (follow) {
    follow.value = "";
  }
  if (studies) {
    studies.value = "";
  }
  if (cap) {
    cap.value = "";
  }
}

const FOLLOW_RECORD_KEYS = {
  sr_series: [
    "StudyInstanceUID",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
    "ModalitiesInStudy",
  ],
  retrieve_sr: [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "Modality",
    "PatientName",
    "PatientID",
    "StudyDate",
    "AccessionNumber",
    "StudyDescription",
  ],
};

function compactFollowRecords(kind, records) {
  const keys = FOLLOW_RECORD_KEYS[kind] || FOLLOW_RECORD_KEYS.sr_series;
  return (records || [])
    .map((row) => {
      const out = {};
      keys.forEach((key) => {
        const value = row[key];
        if (value !== undefined && value !== null && String(value).trim() !== "") {
          out[key] = value;
        }
      });
      return out;
    })
    .filter((row) => row.StudyInstanceUID);
}

function clearFindParentUids(form) {
  ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"].forEach((keyword) => {
    const input = findValueInput(form, keyword);
    if (input) {
      input.value = "";
    }
  });
  syncFindAdvanced(form);
}

let findResultHistory = [];

function resetFindHistory() {
  findResultHistory = [];
}

function pushFindHistory() {
  const result = document.getElementById("result");
  if (result && result.querySelector("[data-find-result]")) {
    findResultHistory.push(result.innerHTML);
  }
}

function popFindHistory() {
  const result = document.getElementById("result");
  const html = findResultHistory.pop();
  if (result && html !== undefined) {
    result.innerHTML = html;
  }
}

function continueFindWithHigherCap(cap) {
  const form = findAdvancedForm();
  const capField = form && form.querySelector("[data-find-cap-field]");
  if (!form || !capField) {
    return;
  }
  resetFindHistory();
  capField.value = cap || "50000";
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.submit();
  }
}

function startFindFollow(kind, resultRoot) {
  const form = findAdvancedForm();
  const payload = parseFindExport(resultRoot);
  const records = compactFollowRecords(kind, payload && payload.records);
  if (!form || !records.length) {
    if (resultRoot) {
      flashFindExport(resultRoot, "No result rows to follow up.", false);
    }
    return;
  }
  const follow = form.querySelector("[data-find-follow-field]");
  const studies = form.querySelector("[data-find-studies-field]");
  if (!follow || !studies) {
    return;
  }
  pushFindHistory();
  clearFindParentUids(form);
  follow.value = kind;
  studies.value = JSON.stringify(records);
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.submit();
  }
}

function useFindRow(row) {
  const form = findAdvancedForm();
  if (!form || !row) {
    return;
  }
  const values = {};
  row.querySelectorAll("td[data-key]").forEach((cell) => {
    values[cell.getAttribute("data-key")] = (cell.textContent || "").trim();
  });
  row.closest("tbody")?.querySelectorAll("tr.is-selected").forEach((item) => {
    item.classList.remove("is-selected");
  });
  row.classList.add("is-selected");
  if (values.StudyInstanceUID) {
    setFindValue(form, "StudyInstanceUID", values.StudyInstanceUID);
  }
  if (values.SeriesInstanceUID) {
    setFindValue(form, "SeriesInstanceUID", values.SeriesInstanceUID);
  }
  const level = selectedFindLevel(form);
  if (values.SeriesInstanceUID && level !== "IMAGE") {
    setFindLevel(form, "IMAGE");
  } else if (values.StudyInstanceUID && !values.SeriesInstanceUID && level === "STUDY") {
    setFindLevel(form, "SERIES");
  }
  syncFindAdvanced(form);
  form.querySelector("[data-find-value='StudyInstanceUID']")?.closest("details")?.setAttribute("open", "");
  form.querySelector("[data-find-value='StudyInstanceUID']")?.scrollIntoView({
    block: "center",
    behavior: "smooth",
  });
}

function bindFindAdvanced(root) {
  const scope = root || document;
  const form = scope.querySelector ? scope.querySelector("[data-find-advanced]") : null;
  const target = form || findAdvancedForm();
  if (target && !target.dataset.findBound) {
    target.dataset.findBound = "1";
    target.addEventListener("change", (event) => {
      if (event.target.matches("[data-modality-chip]")) {
        syncModalityInputFromChips(target);
        syncFindAdvanced(target);
        return;
      }
      if (event.target.matches("[data-date-from], [data-date-to]")) {
        syncDateInputFromPickers(target);
        syncFindAdvanced(target);
        return;
      }
      if (event.target.closest("[data-find-level], [data-find-value], [data-find-include]")) {
        syncFindAdvanced(target);
      }
    });
    target.addEventListener("input", (event) => {
      if (event.target.matches("[data-find-value]")) {
        syncFindAdvanced(target);
      }
    });
    target.addEventListener("click", (event) => {
      const select = event.target.closest("[data-find-select]");
      if (select) {
        applyFindSelection(target, select.getAttribute("data-find-select"));
      }
      const run = event.target.closest("[data-find-run]");
      if (run) {
        clearFindFollow(target);
        resetFindHistory();
      }
      const stop = event.target.closest("[data-find-stop]");
      if (stop) {
        stopFindQuery(target);
      }
    });
    target.querySelectorAll("[data-find-modality-chips], [data-find-date-tools]").forEach((box) => {
      // These sit inside the <label class="find-key"> row; without this, a click on
      // blank space between chips/buttons would bubble to the label and toggle its
      // "include as return column" checkbox instead of doing nothing. Handle the date
      // presets here before stopping propagation, since stopPropagation also keeps the
      // click from ever reaching the delegated listener on `target` above.
      box.addEventListener("click", (event) => {
        const preset = event.target.closest("[data-date-preset]");
        if (preset) {
          applyDatePreset(target, preset.getAttribute("data-date-preset"));
        }
        event.stopPropagation();
      });
    });
    target.addEventListener("htmx:beforeRequest", () => {
      delete target.dataset.findAborted;
      target.dataset.findBusy = "1";
    });
    target.addEventListener("htmx:afterRequest", () => {
      delete target.dataset.findBusy;
      clearFindFollow(target);
    });
    syncFindAdvanced(target);
  }
}

function pageProductName() {
  return (document.body && document.body.dataset.productName) || "Dicomtag Analytics";
}

function findStoppedMarkup() {
  return (
    '<article class="result fail" data-find-result>' +
    '<header><span class="badge fail">Fail</span>' +
    "<div><strong>" +
    pageProductName() +
    "</strong>" +
    "<p>Query stopped.</p></div></header></article>"
  );
}

function findQueryInFlight(form) {
  return form.dataset.findBusy === "1" || Boolean(form.querySelector(".htmx-request"));
}

function stopFindQuery(form) {
  if (!form || !window.htmx) {
    return;
  }
  if (!findQueryInFlight(form)) {
    return;
  }
  form.dataset.findAborted = "1";
  delete form.dataset.findBusy;
  htmx.trigger(form, "htmx:abort");
  const result = document.getElementById("result");
  if (result) {
    result.innerHTML = findStoppedMarkup();
  }
}

document.addEventListener("click", (event) => {
  const copy = event.target.closest("[data-find-copy]");
  const csv = event.target.closest("[data-find-csv]");
  const json = event.target.closest("[data-find-json]");
  const follow = event.target.closest("[data-find-follow]");
  const back = event.target.closest("[data-find-back]");
  const continueCap = event.target.closest("[data-find-continue-cap]");
  const patientSummary = event.target.closest("[data-find-patient-summary]");
  const row = event.target.closest("[data-find-row]");
  const result = event.target.closest("[data-find-result]");
  if (copy && result) {
    copyFindTable(result);
  } else if (csv && result) {
    downloadFindCsv(result);
  } else if (json && result) {
    downloadFindJson(result);
  } else if (back && result) {
    popFindHistory();
  } else if (continueCap && result) {
    continueFindWithHigherCap(continueCap.getAttribute("data-find-continue-cap"));
  } else if (patientSummary && result) {
    toggleFindPatientSummary(result);
  } else if (follow && result) {
    event.preventDefault();
    startFindFollow(follow.getAttribute("data-find-follow"), result);
  } else if (row && result) {
    useFindRow(row);
  }
});

function filterFindSrResults(result, query) {
  const term = query.trim().toLowerCase();
  const cards = Array.from(result.querySelectorAll(".find-sr-card"));
  let visible = 0;
  cards.forEach((card) => {
    const index = card.getAttribute("data-sr-index");
    const row = result.querySelector(`tr[data-sr-index="${CSS.escape(index)}"]`);
    const match = !term || card.textContent.toLowerCase().includes(term);
    card.hidden = !match;
    if (row) {
      row.hidden = !match;
    }
    if (match) {
      visible += 1;
    }
  });
  const count = result.querySelector("[data-find-sr-search-count]");
  if (count) {
    count.textContent = term ? `${visible} of ${cards.length} shown` : "";
  }
}

document.addEventListener("input", (event) => {
  const search = event.target.closest("[data-find-sr-search]");
  if (!search) {
    return;
  }
  const result = event.target.closest("[data-find-result]");
  if (result) {
    filterFindSrResults(result, search.value);
  }
});

bindFindAdvanced(document);
document.body.addEventListener("htmx:afterSwap", (event) => {
  bindFindAdvanced(event.target);
});

document.body.addEventListener("htmx:beforeSwap", (event) => {
  const source = event.detail && event.detail.elt;
  if (source && source.dataset && source.dataset.findAborted) {
    event.detail.shouldSwap = false;
    delete source.dataset.findAborted;
  }
});

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
