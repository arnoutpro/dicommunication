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

## No protection by design

These are properties of the tool, not defects. Please do not file them as
vulnerabilities — but do factor them into where you deploy it.

| Property | Why | What to do about it |
| --- | --- | --- |
| The web UI and JSON API have **no login** | It is a diagnostic console for one operator, like a serial terminal | Docker publishes it on `127.0.0.1` only. Front it with an authenticating proxy to go wider. |
| DICOM and HL7 are sent **in the clear** | The protocols are used as the peers speak them; this tool is for reproducing what a modality does | Terminate TLS elsewhere, or stay on the clinical VLAN |
| **PDF to DICOM** reads any path you type, and `/api/tools/pdf-store/scan` will list any directory | It is a local file picker for the operator's own machine | Do not expose the UI beyond the workstation |
| `/api/logs` returns absolute host paths | Diagnostic output for the person at the keyboard | Same as above |
| The MWL SCP listens on all interfaces | A modality has to be able to C-FIND this workstation or the feature is pointless | It only listens once enabled in Configuration. `DICOMM_DICOM_BIND` pins it to one NIC. |

## Patient data on disk

Everything lives unencrypted under the data directory (`~/.dicommunication`,
`%LOCALAPPDATA%\dicommunication` on Windows, `/app/data` in Docker):

- **`results.json` keeps the last 200 tool results, including the full body of
  any HL7 message you sent and the worklist rows a C-FIND returned.** If you send
  a real ADT or query a real worklist, patient identifiers are written to this
  file in cleartext.
- `worklist.json` holds whatever you typed into the local worklist.
- `dicommunication.log` records what you ran, against which AE titles and
  endpoints.

There is no retention policy and no encryption at rest. On a machine that touches
production data, treat the data directory as containing PHI: put it on encrypted
storage, and clear it when you are done. **Logs → Clear** empties the log file;
deleting `results.json` clears the result history.

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

Open an issue on this repository. If the repository is public when you read this,
prefer **Security → Report a vulnerability** so the report stays private until
there is a fix.

Please include the version from **About** (or `GET /health`), what you pointed
the tool at, and the smallest reproduction you have. There is no bounty and no
SLA — this is a single-maintainer tool.

## Running the checks yourself

Nothing here is privileged; you can reproduce the whole security review:

```bash
pip install -r requirements-dev.txt
python -m pytest                 # full suite
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
