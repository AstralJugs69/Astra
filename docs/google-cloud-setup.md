# Google Cloud setup

This is a production-style setup guide, not an automatic provisioner. Review
each IAM and deployment operation independently. Never use a service-account
JSON key.

## Ordinary user sign-in — browser-based authentication

These commands change local credential state only. They open browser-based
Google sign-in; do not provide a password to a script or repository file.

```text
gcloud auth login
gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly
```

The second command grants ordinary local ADC the narrow scopes used by the
read-only Drive adapter and private Cloud Run flow. Run `braille-relay doctor`
afterward.

## Existing private deployment

The repository assumes an existing private Cloud Run service and existing
Firestore/GCS resources. Do not make it public. The deployment must retain:

- the private service invoker policy;
- a paused recurring outbox scheduler outside an explicitly supervised demo;
- exact Liblouis profile/table readiness checks;
- separate source, telemetry, scheduler, demonstrator, and endpoint-evidence
  principals;
- no default project-level Token Creator grant.

The presentation server uses a short-lived audience-bound ID token minted from
ordinary user ADC only while a human performs a narrowly scoped, temporary
service-account Token Creator grant. The detailed cleanup pattern remains in
[active-professional-review-demo.md](active-professional-review-demo.md).

## Deploy — cloud mutation, human-reviewed

Build and deploy only after the frozen test suite is green. The project’s
existing deployment runbook and service configuration are authoritative. A
safe post-deploy smoke is read-only: authenticated `GET /health`, `GET /readyz`,
incident list, and incident detail. Do not invoke Drive reconcile, scheduler,
telemetry ingestion, or a human-record POST as a smoke test.

## Region

Use `europe-west3` by default unless the evaluator’s existing private service
uses another approved region. Store the region in `.env`, not source code.
