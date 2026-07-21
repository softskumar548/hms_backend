# ADR-0001 — Python 3.12 + FastAPI + Postgres (with RLS)

- Status: Accepted
- Date: 2026-07-20
- Deciders: Platform team

## Context

The HMS platform is a multi-tenant SaaS serving hospitals in Andhra Pradesh, India,
with strict patient-data isolation, audit, and residency requirements. We need a
stack the team can move quickly on, that is proven for healthcare workloads, and
whose tenancy story we can prove end-to-end from day one.

Constraints:
- Team fluency: Python is the strongest shared language.
- Isolation must be provable at the database layer, not just in application code
  (PLT-002).
- ABDM interoperability requires FHIR R4; the FHIR Python ecosystem is mature.
- Deployment target is a Docker Compose stack on an India-region Ubuntu VPS,
  moving to Kubernetes for production. The stack must run identically in both.

## Decision

- **Language / API framework:** Python 3.12, FastAPI, Pydantic v2, Uvicorn/Gunicorn.
- **Database:** PostgreSQL 16 with Row-Level Security for tenant isolation.
- **ORM / migrations:** SQLAlchemy 2.0 (async) + Alembic.
- **Async / tasks:** asyncio; Celery or Arq on Redis for background jobs.
- **Events:** Postgres/Redis behind an internal publisher interface (see
  `libs/hms_events`); Kafka is a later swap.
- **Identity:** Keycloak / OIDC + MFA; `libs/hms_auth` currently ships a dev stub
  and will be replaced with real OIDC validation (IAM-001, IAM-006).
- **FHIR:** `fhir.resources` (Pydantic FHIR R4). Storage decision in ADR-0002.
- **Infra:** Docker + Docker Compose today (India VPS); Kubernetes for production.

## Consequences

Easier:
- Auto-generated OpenAPI at `/docs` (INT-007) with no extra work.
- Tenant isolation is enforced by the database, not application discipline.
- Common Python libraries for FHIR, background jobs, and testing.

Harder / constraints we now accept:
- The app must always connect as the non-superuser `hms_app` role for RLS to
  apply. Any admin path (tenant provisioning, schema migration) uses a separate
  privileged role.
- We do not substitute stack components without a superseding ADR. In particular,
  brokers are only reached via `libs/hms_events`, never directly.
