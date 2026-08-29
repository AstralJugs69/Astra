# Active professional-review demo

Run the print-only harness from WSL after the Gate 0 CUPS floor is healthy:

```text
bash infra/wsl/run_active_professional_review_demo.sh --help
```

It deliberately performs no CUPS, Drive, Cloud Run, or simulator-timing action.
It prints the bounded commands for the human holding each authority and waits
for typed confirmation between them. This keeps the active acceptance receipt,
human disposition, CUPS cancellation, later read-only observation, and operator
attestation as separate facts.

The required timing-profile backup is `/etc/cups/relay-capture.conf.active-professional-review.bak`.
If the walkthrough stops early, restore that backup using the printed final
step before trying again. Do not record passwords, tokens, raw BRF, CUPS spool
files, capture paths, or private Drive details in evidence.

For the local review screen, use:

```text
python -m braille_errata_relay.presentation.app
```

Set `RELAY_PRESENTATION_API_URL`, `RELAY_PRESENTATION_AUDIENCE`, and a fresh
`RELAY_PRESENTATION_SESSION_SECRET` in the current terminal only. The launcher
always binds `127.0.0.1`; it sends short-lived audience-bound identity tokens to
the private Relay API from the local server, never from browser JavaScript.
