// Status dashboard: render one tile per rule from /lite/api/status, every 10 s.
(function () {
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function ago(s) {
    if (s < 90) return s + " s ago";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    return Math.round(s / 3600) + " h ago";
  }
  function tile(x) {
    var t = el("div", "tile " + x.state);
    t.appendChild(el("div", "name", x.rule_name));
    if (x.state === "waiting") {
      t.appendChild(el("div", "msg mute", "Waiting for the first check"));
      return t;
    }
    t.appendChild(el("div", "meta", "Checked " + ago(x.age_seconds) + " · " + x.total + " exams"));
    if (x.state === "error") {
      t.appendChild(el("div", "msg", x.error || "Query failed"));
      return t;
    }
    var entries = Object.keys(x.by_status || {});
    if (!entries.length) entries = null;
    (entries || ["No exams"]).forEach(function (k) {
      var row = el("div", "row");
      row.appendChild(el("span", "", k === "(none)" ? "No status returned" : k));
      row.appendChild(el("b", "", entries ? x.by_status[k] : 0));
      t.appendChild(row);
    });
    if (x.state === "stale") {
      t.appendChild(el("div", "msg mute", "No new check since " + ago(x.age_seconds)));
    }
    if (x.rule_status && x.rule_status !== "active") {
      t.appendChild(el("div", "msg mute", "Rule is " + x.rule_status));
    }
    return t;
  }
  function render(tiles) {
    var bad = tiles.filter(function (x) { return x.state === "error"; }).length;
    var quiet = tiles.filter(function (x) { return x.state === "stale"; }).length;
    document.body.className = bad ? "error" : (quiet || !tiles.length || tiles.some(function (x) { return x.state === "waiting"; }) ? "" : "ok");
    document.getElementById("summary").textContent = !tiles.length ? "No Dicom Router rules yet" :
      bad ? bad + " failing" : quiet ? quiet + " without recent data" : "All checks running";
    var main = document.getElementById("tiles");
    main.replaceChildren();
    if (!tiles.length) {
      main.appendChild(el("div", "empty", "Add a Dicom Router rule to see its latest result here."));
    }
    tiles.forEach(function (x) { main.appendChild(tile(x)); });
  }
  function load() {
    fetch("/lite/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) { render(d.tiles); })
      .catch(function () {
        document.getElementById("summary").textContent = "App unreachable";
        document.body.className = "";
      });
  }
  load();
  setInterval(load, 10000);
})();
