# Local floor and CUPS simulator

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

## Restore and blockers

The setup and active-review scripts contain deliberate rollback and restoration
paths. If a CUPS service/system package/WSL prerequisite is unavailable, record
the exact command and error in sanitized evidence. Do not replace it with a
mocked passing result.
