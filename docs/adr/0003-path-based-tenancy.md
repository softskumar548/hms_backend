# ADR-0003 — Path-based tenancy under `hms.zensynq.com`

- Status: Accepted
- Date: 2026-07-20
- Deciders: Platform team

## Context

We ship one platform to many hospitals/clinics as SaaS. Every request must be
attributable to exactly one tenant, and cross-tenant access must be impossible
by construction. Three common tenancy addressing schemes exist:

1. **Subdomain-per-tenant** (`apollo.hms.zensynq.com`) — clean isolation in URLs
   and cookies, but requires wildcard TLS certificates, DNS churn per onboarding,
   and CORS complexity for the shared frontend.
2. **Path-based** (`hms.zensynq.com/api/...` with tenant from auth context) — a
   single certificate, no DNS work per tenant, simplest to operate for the
   India-region VPS starting posture.
3. **Header-based** — trivially spoofable when a client controls headers; unsafe
   as the sole tenancy source.

Regardless of scheme, **`tenant_id` is only ever read from the verified auth
context** (`RequestContext`), never from a header, query string, or request body.
The addressing scheme is orthogonal to the isolation mechanism (PLT-002 RLS).

## Decision

Serve the platform at `hms.zensynq.com` with **path-based routing under `/api/`**,
and derive `tenant_id` from the OIDC-verified auth claim. The DB layer enforces
isolation via RLS regardless of URL shape.

The `libs/hms_auth` boundary is the single place tenant identity is minted; the
rest of the codebase depends only on `RequestContext`, so switching to subdomain
routing later is a change to that one library plus nginx.

## Consequences

Easier:
- One TLS certificate, no DNS or wildcard-cert operations per tenant onboarding.
- One CORS policy for the shared frontend.
- Simpler nginx/ingress config.

Harder / constraints we now accept:
- URLs do not visually indicate the tenant — misleads no one because the auth
  claim is authoritative, but developers must remember not to look at the URL to
  reason about tenancy.
- If a customer contract later requires vanity subdomains, the `hms_auth`
  boundary must be updated to also parse the Host header — still the single
  place tenant identity is minted.
