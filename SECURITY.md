# Security

Dicommunication is a **trusted-network admin workstation tool**. It talks to PACS,
RIS, and modalities on a clinical VLAN so an administrator can prove a link works.
It is not a PACS, not a viewer, and not an archive.

Read this before pointing it at anything that holds real patient data.

## Threat model

**Designed for:** one operator on one machine, or a small team on a hospital
network they are already trusted on — a PACS VLAN, a lab bench, or a jumphost that
can reach DICOM ports.

**Not designed for:** exposure to the internet, or a network where other users are
untrusted. It has its own sign-in, roles and audit log (next section), but it is not
hardened for hostile traffic.

When it is reachable from more than the computer it runs on, sign-in is **always
on** and cannot be turned off.

## Sign-in, roles, HTTPS and the audit log

Added in 0.11.0, for running the app on a network with patient data. None of it
needs another system (no Entra ID, no certificate authority).

**When sign-in applies.** It is required as soon as the app can be reached from
another computer (`--host 0.0.0.0`, the Windows installer's network option,
`DICOMM_HTTP_BIND`) or as soon as any account exists. On one computer with no
accounts the app opens as before. While no account exists on a network-reachable
app, every page answers *Setup needed*; the first administrator is created from the
computer itself (`/setup`, only from the loopback address), or from a command line:

```bash
dicommunication.exe --create-admin NAME --password-file password.txt
# the Windows service, run as an administrator: add --service-data
```

It reads the first line of the file, creates the administrator (or, for a name that
exists, resets it to an enabled administrator with that password), and prints the
result. Delete the file afterwards. This is also the way back in after losing the
last administrator's password.

**Accounts.** Passwords are at least 12 characters and stored only as scrypt hashes
in `users.json`. Five wrong passwords for a name, or from an address, lock that name
or address for five minutes; the answer is the same for a wrong name and a wrong
password. A session ends after 30 minutes idle or 12 hours in all, is kept in memory
(restarting the app signs everyone out), and uses an `HttpOnly`, `SameSite=Strict`
cookie that is `Secure` on HTTPS. Changing a person's role, disabling them or
resetting their password signs them out. The last active administrator cannot be
disabled, demoted or deleted, and nobody can delete their own account.

**Roles.**

| Role | May |
| --- | --- |
| **Viewer** | Read the status dashboard, the C-ECHO board and the simplified `/lite` page; change their own password |
| **Operator** | Everything a viewer may, plus run the tools, start and stop Router rules and send. Limited as below. |
| **Administrator** | Everything, including configuration, users, security settings and the audit log |

Every route is listed in a permission table (`app/auth.py`); a route nobody listed
needs an administrator, and a test fails if one is missing.

**Operator limits.** Without them an operator could make the server read any folder
it can, or send to any address. So an operator may only use **saved nodes** (no typed
C-ECHO address, and an HL7 host and port must match a saved node) and **folders an
administrator listed** under Configuration > Security: the PDF directory, the
Anonymizer's output folder, and Dicom Import / PDF scans. The folder Browse dialog
is for administrators.

**Dicom Import and archives.** A `.zip` is read in place, nothing is unpacked. A `.7z` has to be unpacked, so its files (real patient images) are written to `import_tmp` in the data folder while a scan is open. That folder is deleted when you start over, when a scan is discarded or idle for 30 minutes, and when the app starts, but it is not encrypted and a crash or power cut can leave files there until the next start. Links inside a 7z are never followed, a 7z that would unpack to more than 8 GB is refused, and an operator can only use folders an administrator listed.

**HTTPS.** When the app listens on a network address it serves **HTTPS
automatically**, with a certificate it makes for itself (EC P-256, valid for this
computer's names and addresses, renewed before it expires, private key readable only
by the owner, kept in `tls/` in the data folder). Browsers warn once about a
self-signed certificate. Configuration > Security shows its **fingerprint**: compare
it with what the browser shows, then install the downloadable certificate on the
computers that use the app (or accept the warning once) to keep the warning away.
Use `--no-https` only behind a proxy that terminates TLS. The desktop window talks to
`127.0.0.1` and stays plain HTTP. DICOM and HL7 traffic to the PACS is still not
encrypted.

**Audit log.** `audit.log` in the data folder records who did what and when: every
sign-in and failed sign-in, every denial, every change and every run, with the
person, role, address, action and target names. It never holds form values, patient
data or passwords. Each line carries a hash of the line before it, so editing or
deleting a line is detected: Configuration > Audit log checks the chain and says
where it breaks. It rolls over at 5 MB and keeps ten files. It is for review, not
tamper-proof against someone who can replace the whole folder; copy it elsewhere if
you must keep it. This is meant to support NEN 7510 / 7513 and the AVG; whether it
is enough is a decision for your information security officer.

**Retention.** The Router's run history keeps **counts only** (matched, new,
forwarded, failed, and how often each failure message occurred); the studies it found,
with names, IDs and accession numbers, are never written to disk. The tool results
keep the rows they fetched for **30 minutes** (Configuration > Security: 0 keeps none,
up to 1440), then the rows are emptied and the summary stays, so the dashboard and
echo board keep working. Runs and results older than **30 days** are deleted
altogether (adjustable, 0 to 3650; 0 keeps them until the fixed limits of 500 runs and
200 results push them out). An import review lives in memory and is dropped after 30
minutes idle. The rules themselves remember the Study Instance UIDs they have already
handled, so they do not forward a study twice; a UID is an identifier, not a name.

**What this does not do.** It does not encrypt files at rest (use BitLocker / an
encrypted volume), does not protect DICOM or HL7 on the wire, does not do two-factor
login or single sign-on, and cannot stop an administrator or someone with access to
the computer itself.

## Windows service

`dicommunication-<version>-win64.msi` runs the same program as a background
Windows service (see `packaging/windows/README.md`). The threat model above is
unchanged, and two things matter more because nobody is watching a window:

- **Sign-in is always on.** Since 0.11.2 the service asks for sign-in even when only
  this computer can reach it, because anyone logged in to the server (for example
  over Remote Desktop) could otherwise use the app and see patient data. The first
  visit to `http://localhost:<port>/` on the server goes straight to **Setup**, where
  you create the first administrator; nobody else can do that, and until it is done
  every page says *Setup needed*. Or use `--create-admin ... --service-data`.
- **Local only by default.** The service listens on `127.0.0.1`. The installer's
  "allow other computers" option makes it listen on every interface, turns on HTTPS
  and opens the Windows Firewall for that port **to the local subnet only**. The
  Start-menu shortcut then opens `https://localhost:<port>/`. (A plain `make run` or Docker
  still opens without a login until you create the first user.)
- **macOS is the same idea.** The DMG's installer package registers a launchd service
  that starts with the Mac, runs as the hidden account `_dicommunication`, always asks
  for sign-in, and keeps its data in `/Library/Application Support/Dicommunication`
  (mode 0700, that account only). `sudo dicommunication service network on` turns on
  HTTPS and listening on the network and adds the macOS firewall exception. Unlike the
  Windows rule, that exception cannot be limited to the local subnet, so on a shared
  network rely on sign-in and HTTPS, the Mac's own firewall and network segmentation.
  Create the first administrator at `http://localhost:<port>/` on the Mac or with
  `sudo dicommunication service create-admin NAME`.
- **Its data folder.** The service keeps settings, results and logs in
  `C:\ProgramData\Dicommunication`, which can contain patient data in cleartext.
  The installer limits that folder to administrators, SYSTEM and the service
  account (`NT AUTHORITY\LocalService`). It is not deleted on uninstall unless you
  agree to when asked.

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
| On one computer with **no accounts** the UI and JSON API need no login | It is a diagnostic console for one operator, like a serial terminal | Sign-in is always on when the app is reachable from the network ([above](#sign-in-roles-https-and-the-audit-log)). Other websites in the same browser are refused ([below](#protection-against-other-websites)); software already running on the machine is not. |
| DICOM and HL7 are sent **in the clear** | The protocols are used as the peers speak them; this tool is for reproducing what a modality does | Terminate TLS elsewhere, or stay on the clinical VLAN |
| **PDF to DICOM** reads any path an **administrator** types, and `/api/tools/pdf-store/scan` will list any directory | It is a local file picker for the person at the machine | Operators are limited to the folders listed under Configuration > Security. A page on another site cannot call it ([above](#protection-against-other-websites)). |
| `/api/logs` returns absolute host paths | Diagnostic output for the person at the keyboard | Same as above |
| The MWL SCP listens on all interfaces | A modality has to be able to C-FIND this workstation or the feature is pointless | It only listens once enabled in Configuration. `DICOMM_DICOM_BIND` pins it to one NIC. |
| **Tag Editor** writes patient study data back to the configured PACS, with no confirmation beyond the browser prompt | It exists specifically to correct stuck report-workflow metadata on a real archive | Only overwrites a tag already present, never invents one. Against a real Vue archive, Push has been observed to report success without the stored object actually changing — Fetch and check the current values before *and after* every Push. Use **Seed test studies** (synthetic `ARNPRO^TESTBENCH` patient, not a real one) to try Push before ever running it against a real study. |
| **Dicom Router PDF rules** read every top-level `.pdf` in a folder and move it to `Sent` or `Failed` inside that folder, as the app's own (service) account, then send it to the configured node as a DICOM document | An unattended schedule does this without confirmation | The folder is exactly the one on the rule (administrators only), hidden files, symlinks and subfolders are ignored, nothing is overwritten (a clash gets `name (2).pdf`), and file names are never stored or logged, only counts. Point it at a folder meant for this, with only the PDFs you intend to send. |
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
- **`route_runs.json` keeps the last 500 Dicom Router run records as counts
  only.** Before 0.11.1 it also held the patient name, ID, accession number and
  study date of every matched study; the first sweep after upgrading removes
  those from an existing file. `route_rules.json` (the rule definitions
  themselves — source PACS, filters, schedule, destination AE titles) holds
  configuration, plus the Study Instance UIDs a rule has already handled.
- `dicommunication.log` records what you ran, against which AE titles and
  endpoints.

Since 0.11.1 `route_runs.json` holds no patient rows, the rows in `results.json` are
emptied after 30 minutes by default and old entries are deleted after 30 days (see
*Retention* above), and `users.json` and `audit.log`
hold accounts and actions but no patient data. Nothing is encrypted at rest. On a
machine that touches production data, treat the data directory as containing PHI: put it on encrypted
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
| `8080` web UI | `127.0.0.1`. Any other address turns sign-in and HTTPS on | `DICOMM_HTTP_BIND` |
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
pip-audit --local --strict       # known advisories in what is installed (run it in a venv)
bandit -r app                    # static analysis
```

`bandit` reports four medium findings that are expected: three
`hardcoded_bind_all_interfaces` hits (`0.0.0.0` defaults an SCP needs in order to
accept associations) and one `urllib.urlopen` (the launcher polling its own
`/health` on loopback to know when the server is up).

`pytest` skips one ICMP test where the host does not allow raw sockets, which is
normal in a container without `CAP_NET_RAW`.

## Automatic dependency checks

`.github/workflows/dependency-check.yml` runs every Monday, on request (Actions >
Dependency check > Run workflow), and on any pull request that changes a
`requirements*.txt` file:

- **Advisories.** It installs what a build would install (including the desktop
  extras) on Linux and on Windows and runs `pip-audit` on the result. The
  requirement files only set minimum versions, so this audits the newest versions a
  new build would really get, not the minimums.
- **Breakage.** It runs the whole test suite against those newest versions, so a
  release that breaks the app shows up here and not in the next installer build.

A failed scheduled run is emailed by GitHub to the repository owner (check
Settings > Notifications > Actions if it is not). Dependabot (`dependabot.yml`)
proposes the version bumps as pull requests, and now gets the same two checks. Two
limits: it checks Python packages only (not the WinSW service wrapper, which is
pinned by checksum, nor the base Docker image), and it finds *published* advisories,
so it cannot warn about a flaw nobody has reported yet.

## Supported versions

The latest release only. Fixes go on `main` and into the next tag; there are no
maintenance branches.
