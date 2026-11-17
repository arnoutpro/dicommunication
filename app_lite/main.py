"""Dicommunication Lite: read the *.status.json files a Router rule writes and
show them as one tile per rule. Nothing is stored; every request re-reads the
folder. Counts only, so there is no patient data to protect here."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

STATUS_GLOB = "*.status.json"
DEFAULT_PORT = 8081
STALE_AFTER_INTERVALS = 2.5
DEFAULT_INTERVAL_MINUTES = 5


def read_statuses(folder: Path, now: datetime | None = None) -> list[dict]:
    """One dict per status file, with a computed ``state``: ok / error / stale / unreadable."""
    now = now or datetime.now(timezone.utc)
    tiles: list[dict] = []
    for path in sorted(folder.glob(STATUS_GLOB)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            checked = datetime.fromisoformat(data["checked_at"])
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
        except (OSError, ValueError, KeyError, TypeError):
            tiles.append({"rule_name": path.name.removesuffix(".status.json"), "state": "unreadable"})
            continue
        interval = data.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES
        age = (now - checked).total_seconds()
        if age > interval * 60 * STALE_AFTER_INTERVALS:
            state = "stale"
        else:
            state = "ok" if data.get("ok") else "error"
        tiles.append(
            {
                "rule_name": data.get("rule_name", path.name),
                "status_key": data.get("status_key", ""),
                "state": state,
                "checked_at": data["checked_at"],
                "age_seconds": int(age),
                "total": data.get("total", 0),
                "by_status": data.get("by_status", {}),
                "error": data.get("error", ""),
            }
        )
    return tiles


def create_app(folder: Path) -> FastAPI:
    app = FastAPI(title="Dicommunication Lite")

    @app.get("/api/status")
    def status() -> JSONResponse:
        return JSONResponse({"folder": str(folder), "tiles": read_statuses(folder)})

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    return app


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dicommunication Lite</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1c1b22;--mute:#6b6a73;--ok:#1f8a4c;--err:#c0392b;--stale:#8a8a92;--line:#e3e1db}
@media(prefers-color-scheme:dark){:root{--bg:#15151a;--card:#1f1f26;--ink:#ececf1;--mute:#9a99a4;--ok:#3ec27a;--err:#ef6b5c;--stale:#7b7b86;--line:#32323c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,sans-serif}
header{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:6px solid var(--stale)}
body.ok header{border-color:var(--ok)}body.error header{border-color:var(--err)}
h1{font-size:18px;margin:0}#summary{font-weight:600}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;padding:16px}
.tile{background:var(--card);border:1px solid var(--line);border-left:8px solid var(--stale);border-radius:8px;padding:14px}
.tile.ok{border-left-color:var(--ok)}.tile.error{border-left-color:var(--err)}
.name{font-weight:600}.meta{color:var(--mute);font-size:13px;margin-bottom:8px}
.row{display:flex;justify-content:space-between;align-items:baseline;padding:4px 0;border-top:1px solid var(--line)}
.row b{font-size:28px;font-variant-numeric:tabular-nums}.msg{color:var(--err);font-size:13px;margin-top:6px}
.empty{padding:40px 16px;color:var(--mute)}
</style></head><body>
<header><h1>Dicommunication Lite</h1><span id="summary">…</span></header>
<main id="tiles"></main>
<script>
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function ago(s){if(s<90)return s+" s ago";if(s<5400)return Math.round(s/60)+" min ago";return Math.round(s/3600)+" h ago"}
async function load(){
  let d;try{d=await (await fetch("/api/status",{cache:"no-store"})).json()}catch(e){document.getElementById("summary").textContent="Lite server unreachable";document.body.className="";return}
  const t=d.tiles,bad=t.filter(x=>x.state==="error").length,stale=t.filter(x=>x.state==="stale"||x.state==="unreadable").length;
  document.body.className=bad?"error":(stale||!t.length?"":"ok");
  document.getElementById("summary").textContent=!t.length?"No status files yet":bad?bad+" failing":stale?stale+" without recent data":"All checks running";
  document.getElementById("tiles").innerHTML=t.length?t.map(x=>{
    if(x.state==="unreadable")return `<div class="tile"><div class="name">${esc(x.rule_name)}</div><div class="msg">Status file unreadable</div></div>`;
    const rows=Object.entries(x.by_status).map(([k,v])=>`<div class="row"><span>${esc(k)}</span><b>${v}</b></div>`).join("")||`<div class="row"><span>No exams</span><b>0</b></div>`;
    const note=x.state==="stale"?`<div class="msg" style="color:var(--mute)">No new check since ${ago(x.age_seconds)}</div>`:x.state==="error"?`<div class="msg">${esc(x.error||"Query failed")}</div>`:"";
    return `<div class="tile ${x.state}"><div class="name">${esc(x.rule_name)}</div><div class="meta">Checked ${ago(x.age_seconds)} · ${x.total} exams</div>${x.state==="error"?"":rows}${note}</div>`}).join(""):`<div class="empty">Waiting for the Dicom Router to write a status file.</div>`;
}
load();setInterval(load,10000);
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Dicommunication Lite status dashboard")
    parser.add_argument("--dir", required=True, help="Folder the Router rules export to")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(create_app(Path(args.dir).expanduser()), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
