# Dicommunication

A low-code DICOM communication validator for PACS administrators.

Configure this workstation as a local Application Entity, register remote DICOM nodes, and run PING, C-ECHO, simulated C-STORE, Study Root C-FIND, and Modality Worklist C-FIND. New tools are Python plugins — drop in a file and they appear in the UI.

## Run it (Docker)

The image already contains Python, FastAPI, pynetdicom, and `ping`. You do not need a local virtualenv.

```bash
git clone https://github.com/arnoutpro/dicommunication.git
cd dicommunication
docker compose up --build
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Stop with Ctrl+C, or run detached:

```bash
docker compose up --build -d
```

Config and worklist data are stored in **`~/.dicommunication`** on the host (`config.json`, `results.json`, `worklist.json`). Rebuilding or replacing the Docker image does not wipe that folder.

If you already saved config under `./data` from an older run, that directory is still used until `~/.dicommunication/config.json` exists.

After `main` builds the published image, this also works:

```bash
docker compose pull
docker compose up -d
```

or without Compose:

```bash
mkdir -p ~/.dicommunication
docker run --rm -p 8080:8080 -p 11112:11112 -v "$HOME/.dicommunication:/app/data" ghcr.io/arnoutpro/dicommunication:latest
```

### Update

```bash
git pull origin main
docker compose up --build -d
```

### Local Python (optional)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
make run
```

## What you can do now

1. **Local DICOM config** — calling AE Title, bind host, listen port, association timeout, max PDU.
2. **Virtual local AE titles** — extra calling AEs so you can impersonate modalities (CT1, MR1, …) on C-ECHO, C-STORE, C-FIND, and MWL without extra listen ports. Pick **Present as** on Worklist, Testbench, and tool pages. The station AE Title is the worklist query filter; it defaults to the calling AE.
3. **Remote nodes** — name, called AE Title, host, port, notes. Add, edit, delete.
4. **Network PING** — DNS resolve, ICMP echo, TCP connect to the DICOM port. ICMP is often blocked on clinical networks; TCP is the more reliable layer-4 check.
5. **C-ECHO** — associate as the configured calling AE and send Verification (`1.2.840.10008.1.1`). This only proves connectivity, not Storage or Query/Retrieve.
6. **C-ECHO board** — run Verification against every configured node in one click (`/echo-board` or `POST /api/echo-board/run`).
7. **Testbench** — send a simulated **C-STORE** (tiny Secondary Capture), **Study Root C-FIND** (stored studies), or **MWL C-FIND** (scheduled procedures) to a remote node (`/testbench`). The result shows which SOP Classes the peer accepted or rejected. These are not interchangeable: Orthanc without the worklist plugin will accept Verification/Storage/Q/R and reject MWL.
8. **DMWL / worklist** — mark a remote as a Modality Worklist SCP, query it from the web Worklist page (MWL C-FIND), and optionally serve a local web worklist to modalities on the DICOM listen port.

There is also a JSON API under `/api` for the same operations (`/api/config`, `/api/remotes`, `/api/identities`, `/api/tools/{id}/run`, `/api/echo-board/run`, `/api/worklist/query`).

## Add a future tool

Create `app/tools/your_tool.py`:

```python
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register

class CMoveTool(BaseTool):
    id = "c-move"
    name = "C-MOVE"
    description = "Request a Query/Retrieve move from the remote AE."
    category = "dimse"

    def run(self, local: LocalAE, remote: RemoteNode | None, options=None) -> ToolResult:
        return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary="Not implemented yet")

register(CMoveTool())
```

Rebuild or restart the app. The tool shows up in the sidebar, on `/tools/c-move`, and at `POST /api/tools/c-move/run`.

## Tests

```bash
pip install -r requirements-dev.txt
make test
```

C-ECHO tests start an in-process Verification SCP; C-STORE and C-FIND tests start in-process Storage / Study Root FIND SCPs. PING tests use loopback ICMP and a local TCP listener.

## Notes for PACS admins

- Config, results, and the local worklist are saved in `~/.dicommunication` so a new Docker image does not reset your AE titles.
- AE Titles are 1–16 printable ASCII characters and must match what the remote node is configured to accept. Each **virtual local AE** is a different calling AE Title — add every one you impersonate to Orthanc `DicomModalities` (or the equivalent allow-list).
- Default unprivileged DICOM port is `11112`. `104` and Orthanc `4242` are also common. Docker publishes `8080` (web) and `11112` (optional local MWL SCP).
- From Docker, a PACS on the same Mac can be reached as `host.docker.internal`.
- Enable **Serve the web worklist over DICOM** in Configuration if a modality should C-FIND this workstation.
- This is a trusted-network admin tool. Do not expose it to the internet without an authenticating reverse proxy.
