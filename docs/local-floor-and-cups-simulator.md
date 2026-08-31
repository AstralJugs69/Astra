# Astra — local floor and CUPS simulator

The single-PC demo keeps Windows browser/operator surfaces separate from WSL2
where CUPS, the read-only bridge, and the physical-endpoint simulator run. Only
the physical endpoint is simulated.

## Safety model

- `relay-observer` is a read-only identity.
- A separate human `relay-operator` identity uses the existing CUPS/vendor
  surface for any lifecycle action.
- Relay has no CUPS mutation endpoint, generic shell tool, or device-control
  route.
- CUPS cancellation, device stop, physical-output isolation, proof, and
  resubmission remain separate facts.

## Human-run Gate 0 setup — local system mutation

Run only from a WSL distribution where the human can authenticate locally:

```bash
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
sudo bash infra/wsl/setup_cups_gate0.sh
```

The script validates CUPS configuration before restart and installs the fixed
raw simulator queue. Do not put passwords in commands, source files, evidence,
or chat. Follow the exact human pauses in:

```bash
bash infra/wsl/run_gate0_local_floor.sh --help
bash infra/wsl/run_active_professional_review_demo.sh --help
```

## What Gate 0 proves

When fully run with a human operator, Gate 0 can prove real CUPS scheduling,
read-only observer authorization denials, exact submitted/backend/captured BRF
hash equality, journal continuity, and simulated endpoint capture. It cannot
turn a scheduler event into a physical-output or professional-approval fact.

## Bounded fresh-observation session for the live demo

For a short, human-owned live demonstration only, use
[`arm_fresh_observation.ps1`](../infra/demo/arm_fresh_observation.ps1) after an
operator independently creates the exact CUPS job. The script starts the
`relay-observer` read-only loop with a locally entered CUPS password and a
separate telemetry-only publisher. It observes only the supplied numeric job
ID, appends to the existing canonical journal, and acknowledges each entry only
after telemetry admission accepts it. It contains no CUPS lifecycle command.

The session is bounded to 15 minutes, uses a five-second default observation
cadence, and stores only sanitized local monitor status under ignored `work/`.
The publisher status distinguishes a fresh local read from the exact latest
observation that private telemetry accepted; demo preflight requires both and
the unchanged 15-second cloud-evidence limit. It does not create a new journal,
relax the freshness limit, or provide any device-control capability. See
[demo preparation](demo-preparation.md) for the explicit human preparation and
stop procedure.

## Restore and blockers

The setup and active-review scripts contain deliberate rollback and restoration
paths. If a CUPS service/system package/WSL prerequisite is unavailable, record
the exact command and error in sanitized evidence. Do not replace it with a
mocked passing result.
