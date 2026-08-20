# Arnout.pro Dicommunication Tool

A low-code DICOM communication validator and PACS admin toolkit.

Configure this workstation as a DICOM Application Entity, register remote nodes (PACS, Orthanc, RIS/MWL, modalities), impersonate extra calling AE Titles, and run the checks a connectivity ticket actually needs: network PING, C-ECHO, simulated C-STORE, Study Root C-FIND, Modality Worklist C-FIND, and HL7 v2 send over MLLP.

The web UI is FastAPI + HTMX. DICOM uses pynetdicom/pydicom. New test tools are Python plugins: drop a file in `app/tools/` and it appears in the sidebar.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) after starting the stack.

## Contents

- [What this is (and is not)](#what-this-is-and-is-not)
- [DICOM services this tool distinguishes](#dicom-services-this-tool-distinguishes)
- [Run it](#run-it)
- [Windows MSI](#windows-msi)
- [macOS DMG](#macos-dmg)
- [Where data is stored](#where-data-is-stored)
- [Configuration](#configuration)
- [Logs](#logs)
- [Virtual local AE titles](#virtual-local-ae-titles)
- [Test tools](#test-tools)
- [Worklist](#worklist)
- [HL7 send](#hl7-send)
- [Talking to Orthanc on a LAN](#talking-to-orthanc-on-a-lan)
- [JSON API](#json-api)
- [Add a tool plugin](#add-a-tool-plugin)
- [Tests](#tests)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

## What this is (and is not)

This is a **trusted-network admin workstation**. Use it on the PACS VLAN, a lab, or a jumphost that can reach DICOM ports. It is not a PACS, not a viewer, and not a replacement for vendor modality simulators.

It **does**:

- Present a calling AE Title (the workstation, or a virtual identity such as `CT1`)
- Associate with a remote AE and show which SOP Classes were accepted or rejected
- Send Verification (C-ECHO), a tiny Secondary Capture (C-STORE), Study Root Query/Retrieve C-FIND, and Modality Worklist C-FIND
- Optionally serve a local web worklist as an MWL SCP on the listen port
- Send an HL7 v2 message over TCP (MLLP by default) to a host:port

It **does not**:

- Implement C-MOVE / C-GET (yet; those are plugin slots)
- Store a real archive of clinical images
- Speak DICOM TLS, or authenticate the web UI
- Parse, validate, or map HL7 fields — paste a message and send it

A successful C-ECHO only proves Verification. Orthanc (or any PACS) can accept C-ECHO and still reject Storage, Query/Retrieve, or Modality Worklist. That difference is the point of the Testbench.

## DICOM services this tool distinguishes

| UI name | DIMSE | SOP Class | What it actually tests |
| --- | --- | --- | --- |
| C-ECHO | C-ECHO | Verification `1.2.840.10008.1.1` | Association plus a DICOM ping. Connectivity only. |
| C-STORE | C-STORE | Secondary Capture Image Storage | Can the peer *receive* an instance? |
| C-FIND | C-FIND | Study Root Query/Retrieve FIND | Search *stored studies* in a PACS archive. Zero matches can still be a successful Q/R C-FIND. |
| MWL C-FIND / Worklist | C-FIND | Modality Worklist `1.2.840.10008.5.1.4.31` | Search *scheduled procedures*, not the archive. |

**MWL C-FIND and the Worklist page are the same SOP Class.** Study Root C-FIND is not. Orthanc without the worklist plugin typically accepts Verification, Storage, and Q/R, then rejects MWL. The Testbench and Worklist results show accepted vs rejected presentation contexts so that is visible.

Two AE Titles matter on a worklist query:

- **Calling AE Title** — who you are on the association. The remote must allow this AE (Orthanc `DicomModalities`, vendor “known AEs”, etc.).
- **Scheduled Station AE Title** — a query key in the MWL identifier. Modalities normally ask for procedures scheduled to their own station (`CT1`, `MR1`, …).

## Run it

### Docker Compose (recommended)

The image already contains Python 3.12, FastAPI, pynetdicom, pydicom, and `ping`. You do not need a local virtualenv.

```bash
git clone https://github.com/arnoutpro/dicommunication.git
cd dicommunication
docker compose up --build
```

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Stop with Ctrl+C, or run detached:

```bash
docker compose up --build -d
```

Compose publishes:

- `8080` — web UI and JSON API
- `11112` — optional local MWL SCP (only listens if you enable it in Configuration)

It also sets `host.docker.internal` so a PACS on the Docker host (typical on a Mac) is reachable from inside the container.

### Published image (GHCR)

Pushes to `main` build `ghcr.io/arnoutpro/dicommunication:latest`. After that image exists:

```bash
docker compose pull
docker compose up -d
```

or without Compose:

```bash
mkdir -p ~/.dicommunication
docker run --rm \
  -p 8080:8080 -p 11112:11112 \
  -v "$HOME/.dicommunication:/app/data" \
  -e DICOMM_DATA_DIR=/app/data \
  ghcr.io/arnoutpro/dicommunication:latest
```

### Update

```bash
git pull origin main
docker compose up --build -d
```

Rebuilding the image does not wipe AE titles if config lives in `~/.dicommunication` (see below).

### Local Python (optional)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
make run
```

That serves the UI on port 8080 with reload. Use this when developing plugins; Docker is the supported way to run it on a PACS admin laptop.

## Windows MSI

The browser does **not** need Python. Opening [http://127.0.0.1:8080](http://127.0.0.1:8080) is a normal web page.

Python **is** required to *serve* that page and to speak DICOM (FastAPI, pynetdicom, ping, disk config). You should not install Python yourself on a locked-down PACS PC. The MSI freezes a private runtime into `Program Files\Dicommunication`. Docker does the same thing inside the image. A local venv is only for developers.

**Those components cannot be installed from this app’s webpage.** The UI is served *by* the Python process, so the page only exists after the backend is already running. A “download Python from this screen” button would be a chicken-and-egg, and a web bootstrapper that fetches python.org at install time is a poor fit for hospital VLANs (often offline, SmartScreen/AV, no admin). The MSI is the offline installer IT can push with Intune or GPO. A public download page can *host* that MSI later; it still will not pip-install the server from inside the running UI.

CI builds `dicommunication-<version>-win64.msi` on `windows-latest` (workflow **Windows MSI**). A `v*` tag attaches that MSI to the GitHub Release and also publishes **`dicommunication.msi`** on [GitHub Packages](https://github.com/arnoutpro/dicommunication/pkgs/nuget/dicommunication.msi) (NuGet). GitHub has no MSI registry; the nupkg carries `tools/dicommunication-<version>-win64.msi`. GitHub Packages always needs a token, even for a public package — anonymous hospital downloads should keep using the [GitHub Release](https://github.com/arnoutpro/dicommunication/releases).

```text
dotnet nuget add source --name github-arnoutpro --username YOUR_GITHUB_USERNAME --password YOUR_PAT --store-password-in-clear-text https://nuget.pkg.github.com/arnoutpro/index.json
dotnet nuget install dicommunication.msi --version 0.2.0 --source github-arnoutpro
```

The already-cut `v0.2.0` MSI can be wrapped without rebuilding: **Actions → Windows MSI → Run workflow** and set `nuget_from_release` to `v0.2.0`.

Install the MSI, start **Dicommunication** from the Start menu. The UI opens in its own window (Edge WebView2 — not a browser tab). Close that window to stop the server. Config lives in `%LOCALAPPDATA%\dicommunication` and survives upgrades. Windows 10/11 already have WebView2; if it is missing the app falls back to the default browser.

Unsigned builds trigger SmartScreen until a code-signing certificate is used. If a modality must C-FIND this workstation, allow inbound TCP for `dicommunication.exe` (listen port 11112). Details and a local build script: [`packaging/windows/README.md`](packaging/windows/README.md).

## macOS DMG

Same idea as the MSI: the browser does **not** need Python. The DMG freezes a private runtime into `Dicommunication.app`. You should not install Python yourself on a locked-down PACS Mac. Docker does the same thing inside the image.

**Those components cannot be installed from this app’s webpage.** The UI is served *by* the Python process, so the page only exists after the backend is already running. Ship the DMG through IT (or a USB stick), not through a button on localhost.

CI builds `dicommunication-<version>-macos-arm64.dmg` on `macos-latest` (workflow **macOS DMG**). A `v*` tag attaches that DMG to the GitHub Release next to the Windows MSI.

The already-cut `v0.2.0` release can get a DMG without a new version tag: **Actions → macOS DMG → Run workflow** and set `release_tag` to `v0.2.0`.

Open the DMG, drag **Dicommunication** to Applications, then right-click the app and choose **Open** the first time (unsigned builds trip Gatekeeper). The Dock icon is the **Sansation Bold** A from the watermark. The UI opens in its own window (not a Safari tab). Close the window or quit from the Dock to stop the server. Config lives in `~/.dicommunication` and survives upgrades.

Apple Silicon only for now. Intel Macs keep using Docker Compose. If a modality must C-FIND this workstation, allow incoming TCP for **Dicommunication** (listen port 11112). Details: [`packaging/macos/README.md`](packaging/macos/README.md).

Linux keeps using Docker Compose.

## Where data is stored

| File | Contents |
| --- | --- |
| `config.json` | Local AE, virtual identities, remote nodes, logging settings |
| `results.json` | Recent tool runs (capped at 200) |
| `worklist.json` | Local web worklist entries |
| `hl7_messages.json` | Saved HL7 v2 drafts for the sender |
| `dicommunication.log` | Rotating application log (level and size set on **Logs**) |

Default directory:

1. `$DICOMM_DATA_DIR` if set (Docker Compose sets this to `/app/data`; the Windows launcher sets `%LOCALAPPDATA%\dicommunication` when unset)
2. else `%LOCALAPPDATA%\dicommunication` on Windows
3. else `~/.dicommunication` on the host (`/app/data` in the container, bind-mounted to `~/.dicommunication`)
4. else legacy `./data` if that folder already has `config.json` and `~/.dicommunication/config.json` does not exist (non-Windows only)

Writes are atomic (temp file + replace). Replacing the Docker image does not reset this folder.

## Configuration

Open **Configured nodes** in the left menu. Child links stay visible: Local DICOM AE, Virtual AEs, and Remote nodes. The overview (`/config`) lists everything at a glance. Add or edit on the child pages:

- `/config/local` — Local DICOM AE
- `/config/identities` — virtual local AE titles
- `/config/remotes` — remote DICOM nodes

On Local AE, **Advanced settings** hides PDU, timeouts, and MWL SCP options.

### Local DICOM AE (the workstation)

This is the real Application Entity of this software: listen address, listen port, timeouts, and the default calling AE Title when you are not impersonating a modality.

| Field | Default | Meaning |
| --- | --- | --- |
| AE Title | `DICOMM` | Calling AE Title on associations unless a virtual identity is selected. 1–16 printable ASCII characters, no backslash. Remote nodes must accept this AE. |
| IP address | `0.0.0.0` | Bind / listen address. `0.0.0.0` listens on all interfaces. DICOM connections *to* remotes use the remote’s host, not this field. |
| Hostname | empty | Documentation only. Listen still uses the IP address. |
| Port | `11112` | Listen port for the optional local MWL SCP. Common DICOM ports elsewhere: `104`, Orthanc `4242`. |
| Timeout | `10` s | ACSE, DIMSE, and network timeout (1–120). |
| Max PDU | `16382` | Association max PDU (4096–131072). |
| Implementation version | `DICOMM_1` | DICOM implementation version name (≤16 characters). |
| Station AE Title | empty | Default Scheduled Station AE Title for worklist queries when no virtual identity is selected. |
| Serve the web worklist over DICOM | off | Start an MWL SCP on the listen port, backed by the local web worklist. |

The listen port is **one** workstation AE. Virtual titles do not add extra SCP ports.

### Remote DICOM nodes

A remote is any peer you want to test: PACS, Orthanc, RIS/MWL, modality, VNA, or a vendor test SCP.

| Field | Meaning |
| --- | --- |
| Display name | Label in the UI |
| AE Title | Called AE Title (what you send as the called AE on associate) |
| Hostname / IP | Connection uses **IP when set**, otherwise hostname. Fill one or both. |
| Port | DICOM port (`104`, `11112`, `4242`, …) |
| Node type | PACS, DMWL, modality, VNA, other |
| Provides Modality Worklist | Marks the node as an MWL SCP. Choosing type **DMWL** also sets this. |
| Notes | VLAN, TLS front-end, vendor, contact — not sent on the wire |

From Docker, a PACS on the same Mac or Linux host is usually `host.docker.internal`. On Linux Compose this mapping is already added.

## Logs

Open **Logs** in the left menu (`/logs`).

The page has two parts:

- **Amount and file size** — log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`), maximum size of the current file (1–50 MB), and how many rotated files to keep. Changes apply immediately and are stored in `config.json`.
- **Log view** — the tail of `dicommunication.log`, refreshed every two seconds. Download the current file, or clear it (rotated files stay).

`INFO` records startup, configuration changes, tool runs, and MWL SCP start/stop. `DEBUG` also records HTTP requests (except health, static files, and the live tail poll). The console window on Windows shows the same stream.

## Virtual local AE titles

A modality does not query worklist as a generic workstation. It associates as `CT1` (calling AE) and asks for scheduled procedures for station `CT1`.

**Virtual local AE titles** are saved impersonation identities for outbound associations. Add as many as you need (`CT1`, `MR1`, `US1`, …). They do **not** listen.

| Field | Meaning |
| --- | --- |
| Display name | e.g. `CT scanner 1` |
| Calling AE Title | Who you are on C-ECHO, C-STORE, C-FIND, and MWL |
| Station AE Title | Worklist query filter. Empty means “same as calling AE”. |
| Default modality | Optional MWL/Q/R filter (`CT`, `MR`, …) filled when you pick this identity |
| Notes | Room, vendor, Orthanc modality key |

On **Worklist**, **Testbench**, and each tool page, use **Present as**:

- `(workstation)` — the Local DICOM AE Title
- a virtual identity — calling AE + station/modality defaults from that identity

The result header shows `as CT1` (or whatever calling AE was used).

Every virtual calling AE must be allowed on the remote, the same way the workstation AE is. For Orthanc, add each one to `DicomModalities`.

## Test tools

The left menu keeps **Configured nodes** and **Test tools** open. Under Test tools: Testbench, C-ECHO board, Worklist, then **Connectivity** (PING), **DIMSE** (C-ECHO, C-STORE, C-FIND, MWL C-FIND), and **HL7** (HL7 send). New plugin tools appear under their `category`.

### Testbench (`/testbench`)

One form to send C-ECHO, C-STORE, Study Root C-FIND, or MWL C-FIND to a selected remote, optionally as a virtual AE.

- **C-STORE** sends a 16×16 Secondary Capture: patient `ARNPRO^TESTBENCH` / `ARNPRO-TEST`, modality `OT`. Look that instance up on the PACS if Storage was accepted.
- **C-FIND** is Study Root, STUDY level. Leave filters empty for a broad query. Optional: patient name/ID, accession, study date, modality (`ModalitiesInStudy`).
- **MWL C-FIND** uses the worklist SOP Class and the identity’s station AE unless you override it.
- The result lists **SOP Classes negotiated** (accepted vs rejected) plus returned/stored records and the association log.

### C-ECHO board (`/echo-board`)

Runs Verification against **every** configured remote in one click (up to 8 in parallel). Uses the workstation calling AE, not a virtual identity. Also `POST /api/echo-board/run`.

### Network PING (`/tools/ping`)

DNS resolve, ICMP echo, then TCP connect to the DICOM port. ICMP is often blocked on clinical networks; the TCP check is the useful layer-4 result.

### Individual DIMSE tools

Same engines as the Testbench, one page each: `/tools/c-echo`, `/tools/c-store`, `/tools/c-find`, `/tools/mwl-find`. Each has **Present as**.

## Worklist

`/worklist` is the table view for **Modality Worklist C-FIND** (`1.2.840.10008.5.1.4.31`). It is not Study Root C-FIND.

**Query worklist**

- Source: local web worklist, or a remote node
- Present as: workstation or a virtual AE
- Filters: patient name (`DOE*` style), patient ID, accession, modality, station AE, scheduled date

Empty filters on a remote mean “return what the SCP is willing to send”. Presenting as `CT1` fills station (and modality, if the identity has one) unless you override the fields.

**Local web worklist**

Add scheduled procedures here. If **Serve the web worklist over DICOM** is on, a modality can C-FIND this workstation on the listen port (`11112` by default). The local MWL SCP also answers C-ECHO. It uses the workstation AE Title, not virtual identities.

## HL7 send

`/tools/hl7-send` sends an HL7 v2 message to a TCP endpoint. It is a sender, not an analyzer: paste (or save) the pipe-delimited text and ship it.

- **Host / port** — the HL7 engine, not the DICOM port. Common MLLP ports are `2575` and `6661`. You can copy a *host* from a saved DICOM remote; you still type the HL7 port.
- **Framing** — MLLP (`0x0B` … `0x1C 0x0D`) is the default. Raw TCP is there for engines that do not wrap.
- **Message** — HL7 v2 starting with `MSH`. The editor shows one segment per row (toggle **Raw** for the full paste). Long pipe-delimited lines wrap. Newlines become CR on the wire.
- **ACK** — if the peer replies, the result shows the raw ACK and MSA-1 (`AA` / `AE` / `AR`). An ACK is not a promise that PACS applied an order update.
- **New MSH-10** — on by default in the UI. The same Message Control ID is often ACKed and ignored. To change an existing ORM order, set **ORC-1** to `XO` (not `NW`) and put Reason for Study in **OBR-31** (count the pipes). The Send result repeats MSH-10, ORC-1, and OBR-31 as they went on the wire.
- Saved drafts live in `hl7_messages.json` next to config.

```bash
curl -s -X POST http://127.0.0.1:8080/api/tools/hl7-send/run \
  -H 'Content-Type: application/json' \
  -d '{"options":{"host":"10.0.0.20","port":2575,"message":"MSH|^~\\&|DICOMM|ARNPRO|RECVAPP|RECVFAC|20260101000000||ADT^A01|MSG00001|P|2.5\rPID|||ARNPRO-TEST||TEST^DICOMMUNICATE"}}'
```

## Talking to Orthanc on a LAN

Typical Orthanc DICOM port is `4242`. From Docker on a Mac, host is `host.docker.internal`.

1. Add a remote: AE Title `ORTHANC` (or whatever Orthanc uses), host `host.docker.internal` or the LAN IP, port `4242`.
2. In Orthanc `DicomModalities`, allow this tool’s calling AEs — the workstation (`DICOMM`) **and** every virtual title you will impersonate (`CT1`, …).
3. Testbench: C-ECHO should pass if the association is accepted.
4. C-STORE / Study Root C-FIND usually pass on a stock Orthanc Storage/Q/R setup.
5. MWL C-FIND passes only if the Orthanc **worklist plugin** (or equivalent) is enabled and that SOP Class is offered. Otherwise the Testbench shows MWL rejected and a “not an MWL SCP” message. That is expected, not a bug in this tool.

If the association is rejected entirely, the calling AE is not in Orthanc’s allow-list, or host/port/called AE are wrong.

## JSON API

Same operations as the UI. `GET /health` returns `{"status":"ok","version":"..."}`. Interactive docs: [http://127.0.0.1:8080/docs](http://127.0.0.1:8080/docs).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/config` | Full config |
| PUT | `/api/config/local` | Replace local AE |
| GET/PUT | `/api/logging` | Log level and rotation |
| GET | `/api/logs` | Tail of the application log |
| GET/POST/PUT/DELETE | `/api/remotes`, `/api/remotes/{id}` | Remote nodes |
| GET/POST/PUT/DELETE | `/api/identities`, `/api/identities/{id}` | Virtual local AEs |
| GET | `/api/tools` | Registered tools |
| POST | `/api/tools/{tool_id}/run` | Run a tool |
| GET | `/api/echo-board` | Last C-ECHO board snapshot |
| POST | `/api/echo-board/run` | C-ECHO every remote |
| GET/POST/DELETE | `/api/worklist`, `/api/worklist/{id}` | Local worklist items |
| POST | `/api/worklist/query` | MWL query (local or remote) |
| GET/POST/DELETE | `/api/hl7/messages`, `/api/hl7/messages/{id}` | Saved HL7 v2 drafts |

Tool ids: `ping`, `c-echo`, `c-store`, `c-find`, `mwl-find`, `hl7-send`. `hl7-send` does not need `remote_id`; pass `options.host`, `options.port`, and `options.message`.

Run a tool as a virtual AE:

```bash
curl -s -X POST http://127.0.0.1:8080/api/tools/c-echo/run \
  -H 'Content-Type: application/json' \
  -d '{"remote_id":"REPLACE","identity_id":"REPLACE"}'
```

Study Root C-FIND with filters:

```bash
curl -s -X POST http://127.0.0.1:8080/api/tools/c-find/run \
  -H 'Content-Type: application/json' \
  -d '{"remote_id":"REPLACE","options":{"patient_id":"1001","modality":"CT"}}'
```

Worklist query as `CT1`:

```bash
curl -s -X POST http://127.0.0.1:8080/api/worklist/query \
  -H 'Content-Type: application/json' \
  -d '{"source":"REMOTE_ID","identity_id":"IDENTITY_ID"}'
```

`source` is `"local"` or a remote id. If `identity_id` is set, calling AE and default station/modality come from that identity.

## Add a tool plugin

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
        return ToolResult(
            tool_id=self.id,
            tool_name=self.name,
            ok=False,
            summary="Not implemented yet",
        )

register(CMoveTool())
```

Rebuild or restart. The tool appears in the sidebar, at `/tools/c-move`, and at `POST /api/tools/c-move/run`. `local` is already the selected identity (workstation or virtual AE). `options` is the optional JSON object from the API (or Testbench form fields for built-in FIND tools).

## Tests

```bash
pip install -r requirements-dev.txt
make test
```

C-ECHO, C-STORE, Study Root C-FIND, and MWL tests start in-process SCPs. PING uses loopback ICMP and a local TCP listener. HL7 send uses a loopback MLLP listener. Identity tests check that a virtual AE is the calling AE Title and the worklist station filter.

## Security

This is a trusted-network admin tool. The web UI has no login. Do not publish port 8080 to the internet without an authenticating reverse proxy. Do not point it at production archives unless you intend to send the test C-STORE instance (`ARNPRO^TESTBENCH`). DICOM and HL7 are sent in the clear unless you terminate TLS elsewhere. HL7 send transmits whatever you paste.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Association rejected / aborted | Called AE, host, or port wrong; calling AE not in the peer’s allow-list |
| C-ECHO works, C-STORE fails | Peer is not a Storage SCP for Secondary Capture (or that SOP Class is disabled) |
| C-ECHO works, Study Root C-FIND fails | Peer is not a Q/R SCP. This is not MWL. |
| C-ECHO / C-STORE / Q/R work, MWL fails | Peer does not offer Modality Worklist FIND. Enable the worklist plugin (Orthanc) or query a RIS. |
| Worklist and MWL C-FIND look the same | They are the same SOP Class. Use Testbench Study Root C-FIND to search stored studies. |
| Empty MWL / C-FIND table but Pass | The SOP Class was accepted and the query succeeded with zero matches. Check station AE, date, and **Present as**. |
| HL7 send times out / no ACK | Peer is not listening, or that port is DICOM not MLLP. HL7 engines are often `2575` or `6661`, not `104`/`4242`. |
| HL7 ACK AA but PACS did not update | Same MSH-10 (duplicate) or ORC-1 still `NW` on an existing order. Use a new MSH-10 and `XO` to change. Check the Send transcript for OBR-31 — it must be the 31st OBR field. |
| PING ICMP fails, TCP succeeds | Normal on locked-down clinical networks. Trust TCP to the DICOM port. |
| `docker compose --build` fails at `apt-get` with exit 100 | Debian mirrors were unreachable from the Docker builder. Current images copy a static `ping` and do not run apt. Pull/rebuild from this change. |
| Cannot reach Orthanc from Docker | Use `host.docker.internal` (same Mac/host) or the LAN IP; publish/check `4242`. |
| Config vanished after image rebuild | Config should be in `~/.dicommunication` (Windows MSI: `%LOCALAPPDATA%\dicommunication`). Legacy `./data` is only used until the home folder exists. |
| Windows SmartScreen blocks the MSI | Unsigned first builds are expected. Use an Authenticode certificate for hospital rollout, or IT can allow the publisher. |
| MSI UI opens then nothing listens | Close the Dicommunication window only when you are done. The old black console is no longer the server. |
| macOS Gatekeeper blocks the app | Unsigned first builds are expected. Right-click **Open**, or run `xattr -dr com.apple.quarantine /Applications/Dicommunication.app`. |
| Mac Dock icon is live, no window | Quit from the Dock, then install a DMG built after the argv-emulation fix. Check `~/.dicommunication/launch.log`. Rebuild: **Actions → macOS DMG** with `release_tag=v0.2.0`. |
| UI opens in Safari/Chrome instead of an app window | Frozen builds should use a native window. Pass `--window`, or check WebView2 on Windows. `--browser` forces the system browser. |
| Modality cannot C-FIND this tool | Enable the MWL SCP, publish/allow `11112` (Windows: inbound TCP for `dicommunication.exe`; macOS: allow incoming for **Dicommunication**), and put this workstation AE on the modality’s worklist node list. |
