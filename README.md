# Arnout.pro Dicommunication Tool

A low-code DICOM communication validator and PACS admin toolkit.

Configure this workstation as a DICOM Application Entity, register remote nodes (PACS, Orthanc, RIS/MWL, modalities), impersonate extra calling AE Titles, and run the checks a connectivity ticket actually needs: network PING, C-ECHO, simulated C-STORE, PDF to Encapsulated PDF Storage, Study Root C-FIND (including Study / Series / Image), Modality Worklist C-FIND, and HL7 v2 send over MLLP.

The Windows MSI installs **two Start-menu tools** that share this config: **Dicommunication** (network / DIMSE / HL7 workstation) and **Vue PACS Database Analytics** (Study Root C-FIND only, including Vue ELSCINT1 keys).

The web UI is FastAPI + HTMX. DICOM uses pynetdicom/pydicom. New test tools are Python plugins: drop a file in `app/tools/` and it appears in the Dicommunication sidebar. The sidebar **About** button shows the running version; **Help** is the in-app administrator guide.

Open [http://127.0.0.1:8080](http://127.0.0.1:8080) for Dicommunication, or [http://127.0.0.1:8080/vue/](http://127.0.0.1:8080/vue/) for Vue PACS Database Analytics.

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
- Send Verification (C-ECHO), a tiny Secondary Capture (C-STORE), Encapsulated PDF Storage (PDF to DICOM), Study Root Query/Retrieve C-FIND at Study, Series, or Image level, and Modality Worklist C-FIND
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
| PDF to DICOM | C-STORE | Encapsulated PDF Storage `1.2.840.10008.5.1.4.1.1.104.1` | Wrap a PDF as a DICOM document and store it. A peer that takes Secondary Capture may still reject Encapsulated PDF. |
| C-FIND | C-FIND | Study Root Query/Retrieve FIND | Search *stored studies* in a PACS archive at STUDY level. Zero matches can still be a successful Q/R C-FIND. |
| Vue PACS Database Analytics | C-FIND | Study Root Query/Retrieve FIND | Own Start-menu tool (`/vue/`). Same SOP Class at Study, Series, or Image plus optional Vue ELSCINT1 keys. Hierarchical: Series needs Study Instance UID; Image needs Study and Series Instance UID. Results copy / CSV / JSON. |
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

| Port | What | Published on | Override |
| --- | --- | --- | --- |
| `8080` | Web UI and JSON API | `127.0.0.1` — this machine only | `DICOMM_HTTP_BIND` |
| `11112` | Optional local MWL SCP (only listens if you enable it in Configuration) | every interface, so a modality can C-FIND this workstation | `DICOMM_DICOM_BIND` |

The UI has no login, so it is kept on loopback. To reach it from another machine, put an authenticating reverse proxy in front and publish it deliberately:

```bash
DICOMM_HTTP_BIND=0.0.0.0 docker compose up -d
```

`DICOMM_DICOM_BIND` pins the MWL SCP to one address (for example `DICOMM_DICOM_BIND=10.0.0.5`) when the host has several NICs and only one faces the modality VLAN.

Startup names the address the UI was published on, so `docker compose logs` answers "why can I not reach this from my laptop":

```
Dicommunication 0.3.0 started (data dir /app/data, log level INFO)
Web UI published on 127.0.0.1:8080 — reachable from the Docker host only. The UI has
no login, so this is the default. To reach it from another machine, restart with
DICOMM_HTTP_BIND=0.0.0.0 and put an authenticating reverse proxy in front of it.
```

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
  -p 127.0.0.1:8080:8080 -p 11112:11112 \
  -v "$HOME/.dicommunication:/app/data" \
  -e DICOMM_DATA_DIR=/app/data \
  ghcr.io/arnoutpro/dicommunication:latest
```

`-p 127.0.0.1:8080:8080` keeps the login-less UI on this machine. Drop the `127.0.0.1:` only behind an authenticating reverse proxy.

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
dotnet nuget install dicommunication.msi --version 0.3.0 --source github-arnoutpro
```

The already-cut `v0.2.0` MSI can be wrapped without rebuilding: **Actions → Windows MSI → Run workflow** and set `nuget_from_release` to `v0.2.0`.

Install the MSI, start **Dicommunication** or **Vue PACS Database Analytics** from the Start menu. Each UI opens in its own window (Edge WebView2 — not a browser tab). Close that window to stop the server if this shortcut started it. Config lives in `%LOCALAPPDATA%\dicommunication` and survives upgrades; both tools share it. Windows 10/11 already have WebView2; if it is missing the app falls back to the default browser.

Unsigned builds trigger SmartScreen until a code-signing certificate is used. If a modality must C-FIND this workstation, allow inbound TCP for `dicommunication.exe` (listen port 11112). Details and a local build script: [`packaging/windows/README.md`](packaging/windows/README.md).

## macOS DMG

Same idea as the MSI: the browser does **not** need Python. The DMG freezes a private runtime into `Dicommunication.app`. You should not install Python yourself on a locked-down PACS Mac. Docker does the same thing inside the image.

**Those components cannot be installed from this app’s webpage.** The UI is served *by* the Python process, so the page only exists after the backend is already running. Ship the DMG through IT (or a USB stick), not through a button on localhost.

CI builds `dicommunication-<version>-macos-arm64.dmg` on `macos-latest` (workflow **macOS DMG**). A `v*` tag attaches that DMG to the GitHub Release next to the Windows MSI.

The already-cut `v0.2.0` release can get a DMG without a new version tag: **Actions → macOS DMG → Run workflow** and set `release_tag` to `v0.2.0`.

Open the DMG, drag **Dicommunication** to Applications, then right-click the app and choose **Open** the first time (unsigned builds trip Gatekeeper). The Dock icon is the **Sansation Bold** A from the watermark. The UI opens in its own window (not a Safari tab). Close the window or quit from the Dock to stop the server. Config lives in `~/.dicommunication` and survives upgrades. Vue PACS Database Analytics: `open -n /Applications/Dicommunication.app --args --profile vue-analytics` (or open `/vue/` in the running UI).

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

The left menu is a two-level tree. **Configured nodes** and **Test tools** start folded; open the chevron to expand. Under Test tools: Testbench, C-ECHO board, Worklist, then **Connectivity** (PING), **DIMSE** (C-ECHO, C-STORE, PDF to DICOM, C-FIND, MWL C-FIND), and **HL7** (HL7 send). Study / Series / Image C-FIND with Vue keys is **Vue PACS Database Analytics** (`/vue/`), not this sidebar. The branch that contains the current page stays open. New plugin tools appear under their `category`.

### Testbench (`/testbench`)

One form to send C-ECHO, C-STORE, Study Root C-FIND, or MWL C-FIND to a selected remote, optionally as a virtual AE.

- **C-STORE** sends a 16×16 Secondary Capture: patient `ARNPRO^TESTBENCH` / `ARNPRO-TEST`, modality `OT`. Look that instance up on the PACS if Storage was accepted.
- **C-FIND** is Study Root, STUDY level. Leave filters empty for a broad query. Optional: patient name/ID, accession, study date, modality (`ModalitiesInStudy`).
- **MWL C-FIND** uses the worklist SOP Class and the identity’s station AE unless you override it.
- The result lists **SOP Classes negotiated** (accepted vs rejected) plus returned/stored records and the association log.

### C-ECHO board (`/echo-board`)

Runs Verification against **every** configured remote in one click (up to 8 in parallel). Uses the workstation calling AE, not a virtual identity. Also `POST /api/echo-board/run`.

### Network PING (`/tools/ping`)

DNS resolve, ICMP echo, then TCP connect to the DICOM port (the same kind of check as PowerShell `Test-NetConnection -Port`). ICMP is often blocked on clinical networks; the TCP check is the useful layer-4 result.

### Individual DIMSE tools

Same engines as the Testbench, one page each: `/tools/c-echo`, `/tools/c-store`, `/tools/c-find`, `/tools/mwl-find`. Each has **Present as**.

### Vue PACS Database Analytics (`/vue/`)

Own product in the Dicommunication installer. Start-menu **Vue PACS Database Analytics**, or `python -m app --profile vue-analytics`. Study Root Query/Retrieve FIND at **Study**, **Series**, or **Image**, with the searchable keys for that level. Checked keys are sent as return columns; a typed value is also a matching key.

Hierarchical FIND (no relational queries): Series keys unlock after **Study Instance UID** is present; Image keys unlock after **Study Instance UID** and **Series Instance UID**. MR-only keys such as Repetition Time stay locked unless Modality is `MR` or empty. Keys are grouped by Study / Series / Image as a list (not a grid). Collapsed **Vue PACS (ELSCINT1)** lists expose Tamar private study and series tags as optional return/match keys. Vue’s DCS does not list them. **Tamar Assign To Doctor** is a confirmed matching key on Vue 12.2.8 (used with Modalities in Study); other Vue tags may still come back empty. Grid Token sequences are not sent. Results are a column-aligned table. **Copy table** is tab-separated for Excel; **Download CSV** and **Download JSON** export the same rows. Click a result row to copy parent UIDs into the next level. Retrieve of those objects is C-MOVE, not C-GET.

The simple C-FIND tool and Testbench stay STUDY-level with a short filter list. This window has Query, Configured nodes, Logs, About, and Help only.

### PDF to DICOM (`/tools/pdf-store`)

Import PDF files, a ZIP of PDFs, a browser folder, or a directory path on this workstation. **…** opens a native folder dialog on this machine; **Scan** lists PDF files only. Each PDF is wrapped as Encapsulated PDF Storage (modality `DOC`). Checking **Generate Patient Name / ID** fills those fields. **Unique patient per PDF** derives identities from file names so a directory of reports does not stack as one person. **Store on PACS** C-STOREs those instances to the selected remote. Uncheck it to encapsulate only. The peer must accept Encapsulated PDF Storage — C-ECHO or the Secondary Capture test image is a different SOP Class.

Caps: 25 MB per PDF, 40 files, 40 MB ZIP. Only `.pdf` files are imported. ZIP entries with `..`, `__MACOSX`, or a non-PDF extension are skipped. The directory path is read by this process; the UI has no login.

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

- **Host / port** — the HL7 **listener**, not the DICOM port. On Philips Vue, **IS Link Configuration → Listeners** shows **Port Number** (often `10010`) and **Host IP**. That Listeners page is settings, not the inbound queue. Send to that Host IP:Port. **Control Port** (often `2112`) is not MLLP. Encoding **Cp1252** matches Latin-1 for ASCII HL7.
- **Framing** — MLLP (`0x0B` … `0x1C 0x0D`) is the default. Raw TCP is there for engines that do not wrap.
- **Message** — HL7 v2 starting with `MSH`. The editor shows one segment per row (toggle **Raw** for the full paste). Long pipe-delimited lines wrap. Newlines become CR on the wire.
- **ACK** — if the peer replies, the result shows the raw ACK, MSA-1 (`AA` / `AE` / `AR`), and ACK **MSH-3** (who answered). An ACK is not a promise that PACS applied an order update.
- **Advanced troubleshooting** — collapsed by default. Optional stamps applied to the paste before send (they do not invent missing ORC/OBR segments):
  - **New MSH-10** — on by default in the UI. The same Message Control ID is often ACKed and ignored.
  - **Change existing order** — on by default. Stamps **ORC-1** (see the control next to it) and **ORC-9**. Philips Vue / IS Link uses **SC** to update an order (`NW` new, `CA` cancel). **XO** is generic HL7 change; Vue often ignores it while Mirth still ACKs.
  - **ORC-1 on change** — UI default is **SC** (Vue). JSON API still stamps `XO` unless you pass `"orc_control": "SC"`.
  - **OBR-31 as CE text** — on by default in the UI. Reason for Study is `id^text`. A spaced identifier becomes `firstword^full text`. `^text` (empty id) is filled from the text. Vue maps OBR-31 to DICOM `(0040,2010)`, which may not be the study description you see in the Vue UI (that is often OBR-4.2 / C-STORE).
  - **Set OBR-25 to SC (in progress)** — on by default, test only. That is **result status**, not Vue’s ORC-1 SC. Also sets ORC-5 to `IP`.
- The Send result repeats MSH-10, MSH-5/MSH-6, ORC-1, OBR-25, and OBR-31 as they went on the wire, plus a Hint when the ACK is likely not a PACS update.
- Saved drafts live in `hl7_messages.json` next to config.

### Philips Vue PACS + Mirth Connect

The ACK you see is from **whoever answers on Host/Port**. **IS Link Configuration → Listeners** is the bind settings (Port Number, Host IP, Encoding, Control Port). It is not the message queue.

**If you cannot open Mirth, use IS Link.** Stop editing OBR-31 until a message shows under **Queues & Notifications**.

1. On **Listeners**, note **Port Number** (often 10010) and click **Edit Listener** for **Host IP**. Send Dicommunication to that Host IP:Port. Do not send to **Control Port** (often 2112).
2. After Add/Edit Listener, IS Link reminds you to **start the Listener process**. Config on 10010 does nothing if the process is stopped.
3. Look at **Queues & Notifications**, not the Listeners form:
   - **Message appears** — routing worked. Then ORC-1 **SC**, accession, OBR-31 can matter.
   - **Still empty, connection refused / timeout** — wrong Host IP, Listener down, or this workstation cannot reach that VLAN.
   - **Still empty, but you got an ACK** — ACK **MSH-3** is who answered. If it is Mirth/IBE, Host is not this IS Link. **MSH-5 / MSH-6** must match what IS Link accepts; Mirth often rewrites those. Encoding **Cp1252** is fine for ASCII ORM.
4. You do not need Mirth for that test. If you later get Mirth access: Message Browser received → transformed → **sent** to this IS Link Host IP:Port.
5. Vue IS Link order control is **NW** (new), **SC** (update), **CA** (cancel). **XO is not in that table.**
6. Match the accession Vue already has: **ORC-3 / OBR-3** (filler / order number) and often **OBR-18**.
7. Vue maps **OBR-31** to DICOM Reason for Requested Procedure `(0040,2010)`. The text you stare at in Vue is often **Study Description** from the images (C-STORE) or **OBR-4.2**, not OBR-31.
8. Updating an existing study may also need Vue’s **ZDS** Study Instance UID. **HL7-PACS Field Mapping** in this same tree decides which ORM fields actually overwrite.

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

Tool ids: `ping`, `c-echo`, `c-store`, `pdf-store`, `c-find`, `c-find-advanced`, `mwl-find`, `hl7-send`. `hl7-send` does not need `remote_id`; pass `options.host`, `options.port`, and `options.message`. `pdf-store` takes `options.patient_name`, `options.patient_id`, plus `options.pdfs` (`filename` + `content_b64`), `options.zip_b64`, or `options.directory`, and `options.send` (default true). `c-find-advanced` takes `options.level` (`STUDY`, `SERIES`, `IMAGE`), `options.values` (DICOM keywords to match), and optional `options.return_keys`. At `STUDY`, `StudyDate` is required.

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

Vue PACS Database Analytics at Series (hierarchical — Study Instance UID required):

```bash
curl -s -X POST http://127.0.0.1:8080/api/tools/c-find-advanced/run \
  -H 'Content-Type: application/json' \
  -d '{"remote_id":"REPLACE","options":{"level":"SERIES","values":{"StudyInstanceUID":"1.2.840…","BodyPartExamined":"CHEST"}}}'
```

Encapsulate a PDF from a directory without sending:

```bash
curl -s -X POST http://127.0.0.1:8080/api/tools/pdf-store/run \
  -H 'Content-Type: application/json' \
  -d '{"options":{"patient_name":"DOE^JANE","patient_id":"1001","directory":"/data/reports","send":false}}'
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

C-ECHO, C-STORE, PDF to DICOM, Study Root C-FIND (including Vue PACS Database Analytics at Series/Image), and MWL tests start in-process SCPs. PING uses loopback ICMP and a local TCP listener. HL7 send uses a loopback MLLP listener. Identity tests check that a virtual AE is the calling AE Title and the worklist station filter.

## Security

Full threat model, what has no protection by design, and how patient data is stored on disk: [`SECURITY.md`](SECURITY.md).

This is a trusted-network admin tool. The web UI has no login, so Compose publishes it on `127.0.0.1` only; `DICOMM_HTTP_BIND=0.0.0.0` opens it up and should only be used behind an authenticating reverse proxy. Do not publish port 8080 to the internet without one. Do not point it at production archives unless you intend to send the test C-STORE instance (`ARNPRO^TESTBENCH`) or Encapsulated PDF documents you import. DICOM and HL7 are sent in the clear unless you terminate TLS elsewhere. HL7 send transmits whatever you paste. PDF to DICOM reads uploaded files, ZIP contents, and any workstation path you type.

The data directory is not encrypted, and `results.json` keeps the last 200 tool results — including the body of any HL7 message you sent and the worklist rows a C-FIND returned. Against real systems that means patient identifiers on disk in cleartext. See [`SECURITY.md`](SECURITY.md#patient-data-on-disk).

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Association rejected / aborted | Called AE, host, or port wrong; calling AE not in the peer’s allow-list |
| C-ECHO works, C-STORE fails | Peer is not a Storage SCP for Secondary Capture (or that SOP Class is disabled) |
| C-ECHO / C-STORE work, PDF to DICOM fails | Peer does not accept Encapsulated PDF Storage. Enable that SOP Class; Secondary Capture is a different test. |
| C-ECHO works, Study Root C-FIND fails | Peer is not a Q/R SCP. This is not MWL. |
| Vue PACS Database Analytics Series/Image stays locked or fails | Hierarchical FIND. Fill Study Instance UID for Series; Study and Series Instance UID for Image. Empty Study UID cannot search body part archive-wide. |
| C-ECHO / C-STORE / Q/R work, MWL fails | Peer does not offer Modality Worklist FIND. Enable the worklist plugin (Orthanc) or query a RIS. |
| Worklist and MWL C-FIND look the same | They are the same SOP Class. Use Testbench Study Root C-FIND to search stored studies. |
| Empty MWL / C-FIND table but Pass | The SOP Class was accepted and the query succeeded with zero matches. Check station AE, date, and **Present as**. |
| HL7 send times out / no ACK | Peer is not listening, or that port is DICOM not MLLP. HL7 engines are often `2575` or `6661`, not `104`/`4242`. |
| HL7 ACK AA to 10010 but Vue IS Link “empty” | **Listeners** is settings, not the queue. Look at **Queues & Notifications**. Send to Listener **Host IP**:10010, not Control Port **2112**. Confirm the Listener process is started. ACK **MSH-3** is who answered. |
| HL7 ACK AA, IS Link queued, Vue UI unchanged | Vue updates with **ORC-1 SC**, not XO. Accession must match ORC-3/OBR-3 (and often OBR-18). OBR-31 is `(0040,2010)`; the Vue UI may show Study Description from C-STORE instead. |
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
| UI loads on the Docker host but not from another machine | Expected. Compose publishes `8080` on `127.0.0.1` because there is no login. `docker compose logs` names the address it published on and how to change it. Start with `DICOMM_HTTP_BIND=0.0.0.0` and front it with an authenticating reverse proxy. |
