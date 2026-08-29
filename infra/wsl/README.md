# WSL2 CUPS Gate 0 runbook

This runbook keeps CUPS, its capture backend, and the physical-endpoint
simulator in WSL2. Windows hosts browser and operator surfaces. The physical
endpoint is the only simulated component. The Relay never exposes a CUPS
command endpoint and never submits, holds, releases, cancels, restarts, or
otherwise controls a production job.

## Pinned Liblouis 3.38.0

The local production-floor verifier must use the exact Liblouis release and
table hashes bound by this repository. The following user-run WSL command
installs build prerequisites, builds the pinned commit, installs its upstream
Python binding outside the repository, validates both table hashes, and runs a
Unicode six-dot translation smoke test:

~~~text
sudo bash infra/wsl/setup_liblouis_3_38.sh
source infra/wsl/liblouis_env.sh
~~~

It requires sudo and network access to the Liblouis source repository. Do not
replace it with an unpinned distro package. It never contacts CUPS.

## Preconditions and non-mutating inspection
Use Ubuntu 24.04 under WSL2 with CUPS 2.x and systemd enabled. From the
repository root inside WSL, validate the policy before installing it:

~~~text
bash infra/wsl/validate_cups_policy.sh
bash infra/wsl/setup_cups_gate0.sh --inspect
~~~

The policy validation is non-mutating. `--inspect` is also non-mutating; a
normal WSL user receiving `Forbidden` from CUPS is expected and must not be
weakened. Use the privileged setup below to repair the simulator state.

## Transactional local simulator setup

The setup changes only the local WSL simulator. It creates a candidate
`cupsd.conf`, validates it before replacement, saves a root-only backup, and
rolls back CUPS configuration, policy, backend, and queue data if installation
or post-install checks fail. Repeated runs repair the fixed queue rather than
adding duplicate policies.

~~~text
sudo bash infra/wsl/setup_cups_gate0.sh
~~~

It configures the fixed raw queue `Braille-Embosser-Sim` with
`relay-capture://demo-embosser`, assigns
`printer-op-policy=relay-observer`, and creates:

- `relay-observer`: CUPS read-only identity with no `lp`, spool, or capture access;
- `relay-operator`: explicit human CUPS mutation identity with no capture access;
- `relay-endpoint-auditor`: fixed-root, read-only simulator evidence identity;
- `relay-audit`: capture-evidence group containing only `relay-endpoint-auditor`.

The installed backend reads only /etc/cups/relay-capture.conf. It fixes the
capture root and BRF geometry and permits only the documented five-second page
delay. CUPS does not receive arbitrary Relay environment configuration.

The root-owned setup also creates `/var/lib/braille-relay/captures` as
`lp:relay-audit` mode `2750`. The backend preserves that set-group-ID
inheritance so each captured file remains readable to the human operator’s
audit group without granting the observer direct file access.

When CUPS invokes the backend, it places the queue name in the executable's
first argument. Because the backend is a Python `#!` script, Linux consumes
that executable argument before Python receives the CUPS job arguments. The
backend therefore reads the numeric job ID first and accepts the device URI
only from CUPS's scheduler-provided `DEVICE_URI` environment variable. It
checks that URI is exactly `relay-capture://demo-embosser` and does not accept
one supplied by a Relay process.

The script never sets or records a password. If Basic authentication is
enabled, set the two local CUPS passwords only in an interactive terminal:

~~~text
sudo passwd relay-operator
sudo passwd relay-observer
~~~

Keep passwords out of shell history, repository files, evidence, and chat.

## Independent human/operator CUPS actions

All job mutations use an explicit, independent operator shell. Do not run them
as the ordinary WSL account, Relay observer, bridge, or cloud application.

~~~text
sudo -iu relay-operator
id -un
cd /path/to/Astra
lp -d Braille-Embosser-Sim -o raw -t 'BER|INCIDENT|demo|BASELINE' candidate.brf
lpstat -W not-completed -o Braille-Embosser-Sim
lp -i BRAILLE_EMBOSSER_SIM-JOB_ID -H hold
lp -i BRAILLE_EMBOSSER_SIM-JOB_ID -H resume
cancel BRAILLE_EMBOSSER_SIM-JOB_ID
exit
~~~

Replace the job identifier with the actual identifier returned by CUPS. Hold,
release, and cancellation are human facts. CUPS cancellation, device stop,
physical-output isolation, proof approval, and replacement submission remain
separate facts.

## Observer authorization and filesystem checks

The bridge must use the installed pycups host/port form:

~~~python
cups.Connection(host="localhost", port=631)
~~~

The authorization harness is a human-run verification tool, not a Relay or
bridge endpoint. It prompts for the observer password without storing it and
only prints operation status:

~~~text
python3 infra/wsl/verify_cups_gate0.py \
  --queue Braille-Embosser-Sim \
  --job-id HELD_JOB_ID \
  --send-document-job-id OPEN_JOB_ID \
  --restart-job-id TERMINAL_JOB_ID \
  --brf candidate.brf \
  --probe-admin-mutation
~~~

Before that command, an independent `relay-operator` shell must create a held
raw BRF job for `HELD_JOB_ID`, a completed or cancelled job for
`TERMINAL_JOB_ID`, and the empty `OPEN_JOB_ID` used only for the
`Send-Document` denial probe:

~~~text
lp -d Braille-Embosser-Sim -o raw -H hold -t 'BER|GATE0|held-auth-probe' candidate.brf
python3 infra/wsl/create_open_cups_job.py --queue Braille-Embosser-Sim
~~~

The operator cancels the held and empty probe jobs after the verifier finishes.
The Relay, bridge, and verifier expose no production-control endpoint.

### Single-session local floor exercise

After the simulator setup has passed, a human can execute all required local
test actions in one WSL session:

~~~text
bash infra/wsl/run_gate0_local_floor.sh
~~~

If capture verification already passed and the authorization verifier stopped
before sending an IPP request, resume with the explicit capture and probe job
IDs:

~~~text
bash infra/wsl/run_gate0_local_floor.sh \
  --resume-captures COMPLETED_JOB_ID TERMINATED_JOB_ID \
  --resume-auth-probes HELD_JOB_ID OPEN_JOB_ID
~~~

The resume form revalidates every explicit job and never discovers lineage
from the queue.

With no arguments, the runner accepts no passwords. It checks the fixed
local `relay-capture://demo-embosser` queue before every test, prompts locally
for each human-authorized test-job action, uses the separate operator and
observer identities, and stops without evidence on any failure. On success it
writes only the allowlisted, sanitized
`demo/evidence/gate0-local-floor.json`; it never records job IDs, paths,
credentials, raw BRF, spool content, or capture files.

After a completed capture, root verifies that `relay-observer` cannot traverse
the CUPS spool or capture tree and cannot read input/output BRF, journals, or
manifests:

~~~text
sudo bash infra/wsl/verify_observer_filesystem_access.sh --job-id JOB_ID
~~~

`relay-observer` owns only its private observation-journal directory. It does
not receive a capture-root setting or human audit-group membership.

## Exact-byte and recovery evidence

For Gate 0 completed/terminated checks, run the read-only capture verifier as
the dedicated endpoint auditor. The newer production-lineage receipt utility
accepts no capture path and reads only the numeric job directory under the
fixed simulator root:

~~~text
sudo -u relay-endpoint-auditor python3 infra/wsl/verify_capture_evidence.py --job-id JOB_ID --candidate candidate.brf --expected-state COMPLETED

python3 infra/wsl/audit_endpoint_receipt.py \
  --baseline-id BASELINE_SHA256 \
  --production-link-id LINK_SHA256 \
  --job-id JOB_ID \
  --expected-title 'BER|WORK_ORDER|HASH_PREFIX|BASELINE' \
  --approved-brf-sha256 APPROVED_BRF_SHA256 \
  --expected-state-version VERSION
~~~

For a deliberately cancelled slow job, use `--expected-state TERMINATED`. The
verifiers validate the capture-manifest schema, terminal event hash, journal
chain, completion timestamp when applicable, and candidate/backend/capture
SHA-256 equality. It emits hashes and status only; it never emits raw BRF.

Then exercise bridge restart/journal reopening, transactional-outbox recovery,
stale observations, missing lineage, and ambiguous jobs. A captured endpoint
file is not proof of device stop, physical isolation, proof approval,
replacement submission, or incident closure.

If a command requires a sudo password, run it interactively and record only a
sanitized result under `demo/evidence/`. Never store passwords, tokens,
machine-private paths, raw spool content, or capture files in the repository.
