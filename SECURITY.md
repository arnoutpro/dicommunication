# Security

Dicommunication is a **trusted-network admin workstation tool**. It talks to PACS,
RIS, and modalities on a clinical VLAN so an administrator can prove a link works.
It is not a PACS, not a viewer, not an archive, and not a multi-user service.

Read this before pointing it at anything that holds real patient data.

## Threat model

**Designed for:** one operator, on one machine, on a network they are already
trusted on — a PACS VLAN, a lab bench, or a jumphost that can reach DICOM ports.

**Not designed for:** exposure to the internet, shared/multi-tenant use, or a
network where other users are untrusted. There is no login, no user model, and
no audit trail of who did what.

If you need it reachable from more than the machine it runs on, put an
authenticating reverse proxy in front of it and treat that proxy as the security
boundary.

## Protection against other websites

No login does not mean *anyone* can drive it. The person most likely to reach
the UI by accident is the operator's own browser, with other sites open in
other tabs. The app refuses both ways such a page could use it:

| Attack | What a hostile page would do | What stops it |
| --- | --- | --- |
| **Cross-site request forgery** | Auto-submit a form to `http://127.0.0.1:8080` to add a remote node, push tags, clean pixel data, send HL7, or clear the log | Every request that changes something (POST, PUT, DELETE) must come from the app's own pages. Browsers label where a request came from (`Sec-Fetch-Site`, `Origin`, `Referer`) and a page cannot forge that; a request from another site gets `403`. Tools without those headers (curl, scripts) are not browsers and keep working. |
| **DNS rebinding** | Point a host name it controls at `127.0.0.1`, then read the JSON API (remote nodes, results, log paths) as if it were same-origin | The app only answers to `localhost`, `*.localhost` and IP addresses. Any other `Host` gets `400`. Behind a reverse proxy that uses a name, list it in `DICOMM_ALLOWED_HOSTS` (comma-separated; `*` turns the check off). |
| **Injected script** | Get markup from a PACS, HL7 reply or report text into a page and run it | Every response carries a Content-Security-Policy with `script-src 'self'`: only the app's own `/static` scripts run, never inline or `eval`'d code. Pages cannot be framed (`frame-ancestors 'none'`, `X-Frame-Options: DENY`), and forms and requests can only go back to the app. Templates escape everything by default. |

FastAPI's `/docs`, `/redoc` and `/openapi.json` are switched off: nothing uses
them, and they would map every endpoint for whoever can reach the port.

`tests/test_security.py` replays each of these attacks against the app.

## Security review (0.5.1, September 2026)

Before 0.5.1 the whole project was reviewed with one question in mind: what
could someone who downloads it find? Everything below was fixed in 0.5.1 and
each fix has a test.

### What was found and fixed

| Finding | What could happen | Fix | Test |
| --- | --- | --- | --- |
| **Cross-site request forgery** | A page open in another tab of the operator's browser could post forms to the app. Shown in the review: it added a remote node called "attacker" and cleared the log. | Requests that change something must come from the app's own pages; others get `403` | `tests/test_security.py` |
| **DNS rebinding** | A site could point a name it controls at `127.0.0.1` and read the JSON API. Shown in the review: it read the remote nodes and the log path. | The app answers only to `localhost`, `*.localhost`, IP addresses and names in `DICOMM_ALLOWED_HOSTS`; others get `400` | `tests/test_security.py` |
| **Script from PACS data** (Dicom Cleaner) | The image picker built a JavaScript expression from the StudyInstanceUID the PACS returned, and htmx ran it. A crafted UID could run script in the app. | The UID is passed as JSON data; htmx `js:` expressions are gone | `test_cleaner_preview_study_uid_is_data_not_code` |
| **Server text as HTML** (Dicomtag Analytics) | A re-query error could put server-supplied text into the page as markup | Inserted as plain text | covered by the CSP test |
| **No Content-Security-Policy or framing protection** | Any injected script would have run, and the pages could be framed (clickjacking) | Strict CSP (`script-src 'self'`, `frame-ancestors 'none'`) and security headers on every response; inline scripts and `onclick` handlers moved into `/static/js` | `test_security_headers_…`, `test_templates_have_no_inline_script_…` |
| **API docs exposed** | `/docs`, `/redoc` and `/openapi.json` mapped every endpoint | Switched off | `test_api_docs_are_not_served` |
| **Option injection in Network PING** | A remote host such as `-f` would have been read by `ping` as an option | Hosts starting with `-` are rejected when a node is saved | `test_remote_host_cannot_look_like_a_ping_option` |

### What was checked and found clean

- **Dependencies:** `pip-audit` found no known advisories in `requirements.txt` or `requirements-desktop.txt`.
- **Static analysis:** `bandit` reports only the four expected findings described under [Running the checks yourself](#running-the-checks-yourself).
- **Secrets:** the full Git history was searched for tokens, keys, passwords and private keys. None were found.
- **Escaping:** templates escape all output by default and use no `|safe` or `Markup`; the JavaScript builds HTML only from fixed text.
- **Files:** ZIP uploads are read in memory with size limits and never extracted; the log download serves one fixed file.
- **Leftovers:** no debug code, TODO notes, internal hostnames, hospital names or real IP addresses in the code or its history.
- **Builds:** the Docker image contains only `app/` (no tests, notes or local data), and the Windows and macOS installers only the app, its templates and static files.

### Personal data in the Git history

The review also found the maintainer's personal email address, local machine
names and links to private AI coding sessions in commit metadata. On
2026-09-29 the history was rewritten to remove them; file contents did not
change. **Every commit ID from before that date changed**, including those of
the v0.2.0, v0.3.0 and v0.5.0 tags. A clone made before then still has the old
history: clone again rather than pulling.

## No protection by design

These are properties of the tool, not defects. Please do not file them as
vulnerabilities — but do factor them into where you deploy it.

| Property | Why | What to do about it |
| --- | --- | --- |
| The web UI and JSON API have **no login** | It is a diagnostic console for one operator, like a serial terminal | Docker publishes it on `127.0.0.1` only. Front it with an authenticating proxy to go wider. Other websites in the same browser are refused ([above](#protection-against-other-websites)); software already running on the machine is not. |
| DICOM and HL7 are sent **in the clear** | The protocols are used as the peers speak them; this tool is for reproducing what a modality does | Terminate TLS elsewhere, or stay on the clinical VLAN |
| **PDF to DICOM** reads any path you type, and `/api/tools/pdf-store/scan` will list any directory | It is a local file picker for the operator's own machine | Do not expose the UI beyond the workstation. A page on another site cannot call it ([above](#protection-against-other-websites)). |
| `/api/logs` returns absolute host paths | Diagnostic output for the person at the keyboard | Same as above |
| The MWL SCP listens on all interfaces | A modality has to be able to C-FIND this workstation or the feature is pointless | It only listens once enabled in Configuration. `DICOMM_DICOM_BIND` pins it to one NIC. |
| **Tag Editor** writes patient study data back to the configured PACS, with no confirmation beyond the browser prompt | It exists specifically to correct stuck report-workflow metadata on a real archive | Only overwrites a tag already present, never invents one. Against a real Vue archive, Push has been observed to report success without the stored object actually changing — Fetch and check the current values before *and after* every Push. Use **Seed test studies** (synthetic `ARNPRO^TESTBENCH` patient, not a real one) to try Push before ever running it against a real study. |
| **Dicom Router** moves real patient studies (C-MOVE then C-STORE) to configured destination nodes on an unattended schedule, with no per-study confirmation | That's the point of a router — it forwards without anyone watching | A route rule's destinations are exactly what you configured, nothing else; there is no discovery or fan-out beyond the node list on the rule. Review a rule's destination nodes and filters before enabling it, same care as any AE you register as a C-MOVE destination elsewhere. |
| **Dicom Cleaner** permanently overwrites pixel data (a black rectangle) on real patient studies and C-STOREs the result back to a PACS, with only a browser confirmation prompt in front of it | It exists specifically to redact burned-in identifiers on a real archive | It only redacts the one rectangle you configure — verify the region against the actual image layout before running it on a real study, not just the synthetic default. **New UID** (the default) leaves the original instance on the PACS untouched, so a wrong region is recoverable; **Same UID** relies on the destination PACS overwriting on a duplicate SOP Instance UID C-STORE, which is not guaranteed — confirm that behavior on a test study first. Dicom Cleaner does not touch PatientName/PatientID/other tags; it is not a substitute for Dicom Anonymizer. |

## Patient data on disk

Everything lives unencrypted under the data directory (`~/.dicommunication`,
`%LOCALAPPDATA%\dicommunication` on Windows, `/app/data` in Docker):

- **`results.json` keeps the last 200 tool results, including the full body of
  any HL7 message you sent, the worklist rows a C-FIND returned, and the parsed
  text of Structured Reports you retrieved.** If you send a real ADT, query a
  real worklist, or retrieve real reports, patient identifiers and report text
  are written to this file in cleartext.
- `worklist.json` holds whatever you typed into the local worklist.
- **`route_runs.json` keeps the last 500 Dicom Router run records, including
  the patient name, patient ID, accession number, and study date of every
  study a rule matched.** A rule that runs against real patient data writes
  those identifiers to this file in cleartext on every run, whether or not
  the rule forwards anywhere. `route_rules.json` (the rule definitions
  themselves — source PACS, filters, schedule, destination AE titles) holds
  configuration, not patient data.
- `dicommunication.log` records what you ran, against which AE titles and
  endpoints.

There is no retention policy and no encryption at rest. On a machine that touches
production data, treat the data directory as containing PHI: put it on encrypted
storage, and clear it when you are done. **Logs → Clear** empties the log file;
deleting `results.json` clears the result history, and deleting `route_runs.json`
clears Dicom Router's. Uninstalling the Windows MSI
asks once whether to also delete the whole data directory; answering No (the
default) leaves it in place, same as before this prompt existed — MSI uninstall
only removes what it installed under Program Files, not files the running app
wrote to `%LOCALAPPDATA%` afterward.

The built-in Testbench C-STORE sends a synthetic patient (`ARNPRO^TESTBENCH` /
`ARNPRO-TEST`). Everything else sends exactly what you give it.

## Deliberate defaults

Two bind addresses look like findings and are not. Both are asserted by tests in
`tests/test_packaging.py`, so changing either is a conscious act:

| Port | Default | Override |
| --- | --- | --- |
| `8080` web UI | `127.0.0.1` — the UI has no login | `DICOMM_HTTP_BIND` |
| `11112` MWL SCP | all interfaces — modalities must reach it | `DICOMM_DICOM_BIND` |

Startup logs which address the UI was published on, so `docker compose logs`
explains a UI that will not load from another machine.

## Reporting something

Do not open a public issue. This repository does not accept bug reports or patches.

If you found a security problem, use **Security → Report a vulnerability** so the report stays private. Include the version from **About** (or `GET /health`), what you pointed the tool at, and the smallest reproduction you have. There is no bounty and no SLA — this is a single-maintainer tool.

## Running the checks yourself

Nothing here is privileged; you can reproduce the whole security review:

```bash
pip install -r requirements-dev.txt
python -m pytest                 # full suite
python -m pytest tests/test_security.py   # the attacks from the security review
pip install pip-audit bandit
pip-audit -r requirements.txt    # known advisories in declared dependencies
bandit -r app                    # static analysis
```

`bandit` reports four medium findings that are expected: three
`hardcoded_bind_all_interfaces` hits (`0.0.0.0` defaults an SCP needs in order to
accept associations) and one `urllib.urlopen` (the launcher polling its own
`/health` on loopback to know when the server is up).

`pytest` skips one ICMP test where the host does not allow raw sockets, which is
normal in a container without `CAP_NET_RAW`.

## Supported versions

The latest release only. Fixes go on `main` and into the next tag; there are no
maintenance branches.
