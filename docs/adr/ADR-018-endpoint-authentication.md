# ADR-018: Endpoint Authentication (Shared-Secret Bearer Token)

## Status
Accepted (Recommendation)

## Context
The Cloud Run backend service accepts code snippets, transcript slices, and commands. An unauthenticated public endpoint presents a security risk.

## Decision
Protect `POST /event` and `POST /reason` endpoints using a **shared-secret Bearer token** header (`Authorization: Bearer <ASTRA_AUTH_TOKEN>`), verified by FastAPI dependency in `api/auth.py`. The local hook script reads this token from its local configuration and attaches it to every request.

## Consequences
- **Positive**: Lightweight, zero external OAuth/IAM overhead for POC, protects endpoints against unauthorized access.
- **Negative**: Requires provisioning the secret in both Cloud Run and local hook configurations.
