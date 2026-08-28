# WSL2 CUPS Gate 0 runbook

This runbook keeps CUPS, the read-only bridge, and the physical-endpoint
simulator in WSL2. The endpoint is the only simulated component. The Relay
does not expose a CUPS command endpoint and does not submit, hold, release, or
cancel jobs.

## Preconditions

Use Ubuntu 24.04 under WSL2 with CUPS 2.x and systemd enabled. From the
repository root inside WSL:

~~~text
bash infra/wsl/validate_cups_policy.sh
~~~

The validation is non-mutating and must print PASS before the policy is
installed.

## One-time local setup

The setup changes only the local WSL simulator. It installs the policy and the
capture backend, creates the separate relay-operator and relay-observer
Linux identities, configures the raw queue, validates cupsd -t, and reloads
CUPS only after validation:

~~~text
sudo bash infra/wsl/setup_cups_gate0.sh
~~~

The script never sets or records a password. In an interactive terminal, set
the two passwords if Basic authentication is enabled:

~~~text
sudo passwd relay-operator
sudo passwd relay-observer
~~~

Keep those values out of shell history, repository files, logs, and chat.

## Independent human/operator test

The operator must use the independent CUPS surface:

~~~text
lp -d Braille-Embosser-Sim -t 'BER|INCIDENT|demo|BASELINE' candidate.brf
lpstat -W not-completed -o Braille-Embosser-Sim
lp -i BRAILLE_EMBOSSER_SIM-1 -H hold
lp -i BRAILLE_EMBOSSER_SIM-1 -H resume
cancel BRAILLE_EMBOSSER_SIM-1
~~~

Replace the job identifier with the actual scheduler job identifier returned by
CUPS. Hold, release, and cancellation are human facts; neither the bridge nor
the agent performs them.

## Observer test surface

The bridge must connect with the actual pycups syntax:

~~~python
cups.Connection(host="localhost", port=631)
~~~

The exact-operation authorization harness is a human-run verification tool, not a bridge endpoint. It prompts for the observer password without storing it, reads queue state through pycups, and probes the listed IPP operations directly:

~~~text
python3 infra/wsl/verify_cups_gate0.py --queue Braille-Embosser-Sim --job-id JOB_ID --brf candidate.brf
~~~

Use `--probe-admin-mutation` only with the reserved non-production probe name when an operator explicitly accepts that negative test.

Only getJobs, getJobAttributes, and getPrinterAttributes are permitted in the
bridge. The observer identity must be able to read those facts but must
receive authorization failures for Print-Job, Create-Job, Send-Document,
Hold-Job, Release-Job, Cancel-Job, Restart-Job, CUPS-Get-Document,
CUPS-Get-Devices, and
CUPS-Add-Modify-Printer when `--probe-admin-mutation` is used. Record status and response codes without
recording passwords or raw spool document bytes.

## Exact-byte and recovery evidence

Submit a known BRF through the operator surface. Compare:

1. the candidate artifact SHA-256;
2. bytes received by relay-capture://demo-embosser;
3. bytes written to the capture output.

Then verify the capture manifest's terminal event hash and completion timestamp,
the event hash chain, bridge restart/reopen behavior, stale observation blocking,
missing lineage, ambiguous jobs, and transactional outbox recovery. A captured
endpoint file is not proof of human device stop, physical isolation, proof
approval, replacement submission, or closure.

If a command requires a sudo password, run it interactively and report the
command plus sanitized result; never store the password or machine-private
identifiers in demo/evidence/.