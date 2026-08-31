# Guided authoritative-source and baseline onboarding

The loopback dashboard provides a bounded three-step onboarding path at
`http://127.0.0.1:8765/setup/source`. It is for a first-time operator who
already has a private Astra Cloud Run deployment.

This is not a general publishing platform. It accepts one configured source,
generates one deterministic demo-fixture baseline, and shows a monitor-safe
record. It does not create a Google Doc, change Drive sharing, update Cloud Run
configuration, enable the scheduler, approve a real production master, or
operate CUPS or an embosser.

## Before opening the wizard

1. Complete [fresh-project deployment](fresh-project-deployment.md).
2. Create the ignored local `.env` and confirm `braille-relay doctor` passes.
3. Start the loopback presentation with `infra/demo/start_demo.ps1` or the
   rehearsal wrapper.

The browser talks only to `127.0.0.1`. The local server uses a short-lived,
audience-bound credential and the dedicated demonstrator identity. No token or
private Cloud Run URL is placed in the page.

## Step 1 — choose, share, and verify the source

Choose either a native Google Doc containing simple headings and paragraphs,
or a UTF-8 Markdown file uploaded to Drive with MIME type `text/markdown`.
For a native Doc, Astra uses read-only Drive export and treats the exported
Markdown bytes as authoritative. For Markdown, it reads the file bytes.

Share only that file with the runtime service account shown on the page as
**Viewer**. Do not make the file public. Paste its Drive/Docs URL into the
wizard and select the correct type.

Verification performs a metadata read, authoritative byte read/export, a
second metadata read to detect a concurrent edit, strict normalization, and a
source-block parse. The response contains hashes and counts, not source text
or the raw Drive file ID. A heading or paragraph may contain up to 4,000
characters, which accommodates ordinary Google Docs paragraphs while keeping
the semantic evidence passed to the model explicitly bounded.

If the candidate is not currently configured in Cloud Run, the page shows a
copyable `gcloud run services update` command. A human must review and run it,
wait for the private revision, and verify again. The dashboard never applies
cloud configuration.

## Step 2 — initialize and register the baseline

After the exact configured source verifies, enter an external production
reference, site identifier, and observed queue name. The registration action:

1. obtains or reuses the Drive change cursor;
2. authoritatively refetches and commits the configured source revision;
3. normalizes and parses the source;
4. renders BRF with the pinned Liblouis profile;
5. stores the normalized source, BRF, source map, manifest, and profile as
   immutable artifacts; and
6. registers the `DEMO_FIXTURE_APPROVED` baseline through the idempotent ledger.

The server derives the configured file ID and accepted revision. The browser
never supplies either to baseline registration. A session-bound idempotency key
makes a same-session retry converge.

This does **not** submit or link a production job. The baseline begins in
`AWAITING_PRODUCTION_LINK`; any production-floor submission is a separate,
explicit human action through the existing surface.

## Step 3 — monitor the baseline

On success the dashboard redirects to `/baselines/<baseline-id>`. It refreshes
every 15 seconds and shows the external reference, status, state version, site,
queue, creation time, and abbreviated hashes. It omits the Drive file ID and
artifact URIs.

The next optional step is human submission followed by fresh, unambiguous,
read-only observation linking. Source-change automation is enabled separately
and remains paused by default.

### Time-bounded simulated-endpoint rehearsal

For a live hackathon rehearsal with no physical embosser, the human operator
may arm the registered baseline through the fixed local simulator with one
explicit command. Copy the complete baseline ID and BRF SHA-256 from the
monitor page, then run:

```powershell
.\infra\demo\arm_simulated_baseline.ps1 `
  -BaselineId '<complete baseline ID>' `
  -ApprovedBrfSha256 '<complete BRF SHA-256>' `
  -ProductionId 'BIOLOGY-VOLUME-2-DEMO' `
  -ObserverPassword (Read-Host 'relay-observer password' -AsSecureString) `
  -Confirmation ARM-SIMULATED-BASELINE
```

The password prompt happens before the helper starts its work. PowerShell passes
the resulting `SecureString` to the WSL bridge over standard input; it is not
placed in shell history, process arguments, logs, evidence, or configuration.
The helper verifies read-only CUPS access before submitting a simulator job, so
an incorrect password fails without adding another job to the queue history.

This is not a database status override. The helper refuses any destination
except `Braille-Embosser-Sim` bound to
`relay-capture://demo-embosser`, downloads and re-hashes the immutable BRF,
submits it under the separate local `relay-operator` identity, records a fresh
read-only observation, and admits exact received-byte evidence with truth basis
`SIMULATED_DEMO`. It never uses a physical embosser. A successful final line
reports `PRODUCTION_LINK_VERIFIED` and `physical_embosser: NOT_USED`.

## Recovery

- **403/404 while verifying:** confirm the runtime account is a Viewer on the
  exact file and organization policy permits the share.
- **Unsupported source:** select the correct type and use simple UTF-8
  Markdown-compatible content.
- **Configuration update required:** run the displayed command deliberately;
  never paste credentials into the dashboard.
- **Registration did not complete:** no production action occurred. Reopen
  source setup, verify, and retry.
- **Monitor unavailable:** the durable record remains in Firestore. Restore
  local authentication and reload.

See [authoritative Drive source](authoritative-drive-source.md) for the source
and retry model and [live demo runbook](live-demo-runbook.md) for the demo.
