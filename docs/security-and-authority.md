# Astra — security and authority

## Non-negotiable boundary

Relay is a report-first overlay. It never controls CUPS, an embosser, or any
production device. The cloud API, local bridge, presentation server, CLI, and
watch-floor JavaScript must never submit, hold, release, cancel, restart, pause,
or administer a queue.

## Browser and local presentation security

- The server binds to `127.0.0.1` only.
- Browser JavaScript comes from a same-origin `/assets/watch.js` route.
- CSP keeps `default-src 'none'`, permits only same-origin scripts and event
  connections, blocks frames, and uses `Cache-Control: no-store`.
- The server—not JavaScript—holds the short-lived Cloud Run credential.
- Signed sessions, CSRF, exact loopback origins, and same-origin forms protect
  the human-record routes.
- `/events` is sanitized SSE only. It excludes raw source, Drive IDs, private
  principals, model prompts/outputs, credentials, queue details, and unbounded
  evidence.

## Human records

Professional disposition, operator attestation, containment confirmation, proof
record, and replacement observation link are separate append-only records with
role, state-version, and idempotency checks. Local alert acknowledgement has no
cloud mutation and records no professional fact.

## Evidence boundaries

Read-only CUPS observation can establish an observed scheduler fact only.
Physical endpoint isolation, device stop, proof approval, replacement
submission, completion, and closure require their own attributable human
evidence. Candidate BRF remains `CANDIDATE_NOT_APPROVED_PRODUCTION_MASTER`
until appropriate human proof; it is never a production master by default.
