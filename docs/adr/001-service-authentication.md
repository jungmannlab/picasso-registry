# 001 — Service authentication (the stack-wide pattern for registry + monet)

- Status: accepted (ratified 2026-09-07 — Open-Decisions **C18**, "was A9")
- Relates to: Open-Decisions **A9 → C18** (the ratified decision), **C11** (cluster
  SSH — a separate seam already decided), **A5** (agent external secrets)
- Implemented by: **WP-3b** (registry) and the auth extension to **WP-12a** (monet)

## Context

Authentication is only needed at a **trust boundary** — a point where traffic
crosses a *machine* or an *administrative/user domain*. Inside one trusted box the
right control is OS process isolation + a loopback bind, not bolted-on auth.

A boundary audit of the DNA-PAINT stack found that most seams are
single-machine/single-user (Hamilton/Arduino serial, pycromanager's localhost ZMQ
bridge, the Aria `localhost` TCP socket, the `mm_lock` file mutex, the WP-1 reader
subprocess) and must **not** grow auth. Two seams are genuinely networked and today
have **none**:

- **`picasso-registry`** — a FastAPI + SQLite service (`app.py`, currently
  `host="127.0.0.1"`, no middleware, no `Depends`-based auth) that is going
  **networked / multi-instrument soon**: multiple microscope PCs and cluster jobs
  write to it; the agent, recommender and dashboards read from it. It is
  **append-only** (no update/delete routes), so an unauthorized write permanently
  poisons the learning DB.
- **`monet`** — a *separate* FastAPI + SQLite per-microscope service that
  `picasso-registry` referenced by ID (`illumination.monet_calibration_id`) but
  does **not** subsume ("monet linkage, not duplication"). It is *worse off*:
  `monet/__main__.py` defaults `--host` to **`0.0.0.0`** (all interfaces) with no
  auth, and **WP-12a** is about to have the recommender/optimizer call its HTTP
  power API programmatically. A `write` here **actuates laser hardware**, so an
  unauthenticated write is a safety concern, not just data pollution.

Threat model: a **trusted lab LAN, not the public internet**. The risks worth
defending are unauthorized writes (DB poisoning / laser actuation), read
exfiltration of provenance, and accidental cross-instrument contamination — *not*
per-user identity, PII/GDPR, or a hostile internet.

Because both services are FastAPI + SQLite, one pattern can secure both.

## Decision

Adopt one **stack-wide service-auth pattern**, factored into a single shared helper
both services import (so there is one audited implementation, not two drifting
copies):

1. **Static bearer tokens with two capability scopes — `read` and `write`** —
   enforced by one FastAPI dependency (`Depends(require_scope(...))`): `read` on
   `GET`, `write` on `POST` (and on monet's power-set / calibration-edit routes).
   Append-only research data and a hardware actuator need *capability* separation,
   not full RBAC/identity.
2. **A token maps server-side to `(scope, label)`.** `label` is a human-readable
   owner — a machine role (`microscope-mercury`, `cluster`) or, later, a person.
   Per-writer tokens let one instrument be revoked/rotated without touching the
   others, and the label is logged as an *attributable writer identity* — a
   provenance bonus. The `(scope, label)` indirection means the mechanism never
   changes as holders change.
3. **Token storage.** Machine tokens live in the caller's per-machine config
   (the config `RegistryClient` already reads, an env var, or a gitignored secrets
   file) — **never committed, never in the append-only DB**. `RegistryClient` gains
   an optional `token=` that sets `Authorization: Bearer …`; absent ⇒ no header ⇒
   still works against a loopback dev instance and the in-memory test mock
   (backward-compatible).
4. **Human / browser access to a dashboard** (monet's, and any registry read UI)
   sits **behind the reverse proxy**, which does the human auth (HTTP Basic, or lab
   SSO if one exists); the API itself stays bearer-token. Two front doors, one
   scope model — a browser is not asked to hand-carry a bearer token on page loads.
   Dashboard **edit-vs-view is exactly `write` vs `read`**: a `view` (read) token
   is safe to share lab-wide; the `edit`/actuate (write) token is the sensitive one.
5. **TLS** — terminate at a reverse proxy (caddy/nginx) on the service host, or via
   uvicorn `--ssl-*`. An internal-CA / self-signed cert is acceptable on a trusted
   LAN; the point is to never send bearer tokens in cleartext.
6. **Fail-closed host guard** — a service refuses to start bound to a non-loopback
   host **unless** tokens are configured. This makes "networked but
   unauthenticated" impossible by construction and keeps the loopback dev path
   zero-config. For monet this also means flipping the `--host` default to
   `127.0.0.1`.

## Alternatives considered (rejected)

- **No auth / loopback-only forever.** Rejected: the registry is explicitly going
  multi-instrument, and monet already binds `0.0.0.0`.
- **OAuth2 / OpenID Connect / per-user accounts / JWT-with-claims.** Rejected for
  now: overkill for a trusted-LAN append-only provenance DB + a lab power actuator,
  and it adds an identity-provider dependency the lab does not run.
- **mTLS (client certs).** Rejected: stronger transport identity than needed on a
  trusted LAN, and certificate distribution/rotation is heavier than shared
  capability tokens for a handful of machines.

## Consequences

- One helper, one review surface, one invariant to enforce; monet and the registry
  cannot drift into two different auth stories.
- The loopback dev workflow and the in-memory test mock are unchanged (no token ⇒
  no header ⇒ works).
- The `label` gives attributable provenance for free and one-instrument revocation.
- **Escalation path** (recorded, not built): if either service ever faces the
  public internet or needs per-scientist authorization/audit, upgrade to OIDC +
  short-lived tokens. Because a token already maps to `(scope, label)`, flipping
  `label` from a role to a person turns the same mechanism into personal tokens
  with no redesign.
- **Standing invariant** (added to the service repos' `CLAUDE.md`): *never bind a
  non-loopback host without the shared auth helper configured.*
