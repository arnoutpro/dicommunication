# Dicommunication

A low-code DICOM communication validator for PACS administrators.

Configure this workstation as a local Application Entity, register remote DICOM nodes, and run the first two checks every connectivity ticket starts with: **network PING** and **DICOM C-ECHO**. New tools are Python plugins — drop in a file and they appear in the UI.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
make run
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080).

### Docker

```bash
docker compose up --build
```

Config is stored as JSON under `data/` (or the `DICOMM_DATA_DIR` volume in Compose).

## What you can do now

1. **Local DICOM config** — calling AE Title, bind host, listen port, association timeout, max PDU.
2. **Remote nodes** — name, called AE Title, host, port, notes. Add, edit, delete.
3. **Network PING** — DNS resolve, ICMP echo, TCP connect to the DICOM port. ICMP is often blocked on clinical networks; TCP is the more reliable layer-4 check.
4. **C-ECHO** — associate as the configured calling AE and send Verification (`1.2.840.10008.1.1`).

There is also a JSON API under `/api` for the same operations (`/api/config`, `/api/remotes`, `/api/tools/{id}/run`).

## Add a future tool

Create `app/tools/your_tool.py`:

```python
from app.models import LocalAE, RemoteNode, ToolResult, ToolStep
from app.tools.base import BaseTool
from app.tools.registry import register

class CStoreTool(BaseTool):
    id = "c-store"
    name = "C-STORE"
    description = "Send a DICOM instance to the remote AE."
    category = "dimse"

    def run(self, local: LocalAE, remote: RemoteNode | None, options=None) -> ToolResult:
        return ToolResult(tool_id=self.id, tool_name=self.name, ok=False, summary="Not implemented yet")

register(CStoreTool())
```

Restart the app. The tool shows up in the sidebar, on `/tools/c-store`, and at `POST /api/tools/c-store/run`.

## Tests

```bash
make test
```

C-ECHO tests start an in-process Verification SCP; PING tests use loopback ICMP and a local TCP listener.

## Notes for PACS admins

- AE Titles are 1–16 printable ASCII characters and must match what the remote node is configured to accept.
- Default unprivileged DICOM port is `11112`. `104` and Orthanc `4242` are also common.
- This is a trusted-network admin tool. Do not expose it to the internet without an authenticating reverse proxy.
