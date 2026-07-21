# HMS Platform — Starter Skeleton

Next-Generation Hospital Management System (SaaS) — foundation scaffold.
Python 3.12 · FastAPI · PostgreSQL (Row-Level Security) · Docker Compose.

This is the **Sprint-Zero / Foundation** skeleton. It gives the team a running,
multi-tenant, audited FastAPI service so module work (REG, SCH, EMR, …) starts
against a real foundation instead of a blank repo.

## What's already wired in (the things that must exist from commit one)

- **Multi-tenant isolation (PLT-002)** — PostgreSQL Row-Level Security keyed to
  `tenant_id`, set per-request from the authenticated context. Cross-tenant reads
  are impossible at the database layer, not just in application code.
- **Immutable audit (PLT-005)** — every patient-data access can emit an append-only
  audit event via the `hms_audit` library.
- **Consent enforcement (PLT-010)** — `hms_consent` checks per-patient, per-purpose
  grants against a tenant-scoped `patient_consent` table. Absent consent →
  suppress/skip, never proceed.
- **Per-tenant feature flags** — `hms_config.feature(...)` reads `tenant.features`
  JSONB. Licensable capabilities are config, never a per-customer code branch.
- **Event publisher interface** — `hms_events.Publisher` is the single contract
  for emitting cross-service events. `NoopPublisher` for dev; transactional-outbox
  impl lands with the first real event. Nothing may import a broker client directly.
- **Tenant resolution (path-based)** — tenant taken from the auth context; ready to
  switch to subdomain-per-tenant later without touching module code (ADR-0003).
- **Auth stub (IAM)** — a dev-only bearer-token dependency that yields a
  `RequestContext` (tenant_id, user, role). Replace with OIDC/Keycloak in Sprint 1–2.
- **Alembic** — baseline migration in `services/plt/alembic/versions/`. Compose
  bootstraps schema from `init.sql`; `alembic stamp 0001_baseline` marks that state
  applied and future changes ship as diffs from #0002 onwards.
- **The isolation test (the heartbeat)** — `tests/test_tenant_isolation.py` seeds two
  tenants and asserts neither can see the other's rows across `patient`, `audit_event`,
  and `patient_consent`. **This test must stay green in CI forever.**

## Quickstart (local or on the Ubuntu VPS)

```bash
cp .env.example .env            # edit secrets
docker compose up --build       # starts postgres + plt service
# API docs (auto-generated OpenAPI) at http://localhost:8000/docs
```

Seed two demo tenants and try isolation by hand:

```bash
docker compose exec plt python -m app.seed
curl -H "Authorization: Bearer dev.apollo.admin"  http://localhost:8000/patients
curl -H "Authorization: Bearer dev.kims.admin"    http://localhost:8000/patients
# each tenant sees only its own patients
```

Run the tests (this is what CI runs):

```bash
docker compose exec plt pytest -q
```

## Repository layout

```
services/plt/    FastAPI app + Alembic migrations (platform + patients reference)
services/iam/    identity service (empty — dev auth stub lives in libs/hms_auth)
libs/            shared packages: hms_tenancy, hms_audit, hms_auth, hms_consent,
                 hms_config, hms_events
infra/           docker-compose, nginx, postgres init.sql (schema + RLS)
docs/adr/        architecture decision records (0001–0003)
docs/            setup checklist and future FRD/story sources
tests/           isolation + smoke tests (CI gate) + conftest for provisioning
scripts/         backup, deploy helpers
pyproject.toml   workspace-level ruff/mypy/pytest config
```

## What to build next

Per CLAUDE.md §7 the build order is PLT/IAM → REG → SCH → EMR → RX → ORD → BIL →
POR → RPT → INT. Current focus is **Foundation (Set A)**, Sprint-1 stories
S1-01…S1-05, which this scaffold covers. The immediate next step after Sprint 1
is replacing the `libs/hms_auth` dev stub with real OIDC/Keycloak validation
(IAM-001, IAM-006) — the rest of the codebase already depends only on the
returned `RequestContext`, so the swap touches one file.

## Environments (per client decision)

- **dev / demo / staging** → the current India-region Ubuntu VPS (8 GB / 2 core),
  Docker Compose, **synthetic data only**.
- **production** → upgraded India-region servers, separated/managed Postgres,
  redundancy for the SLA. Same containers; scale + managed services differ.

## Day-one decisions to record as ADRs

- ADR-0001 — Python/FastAPI stack (confirmed).
- ADR-0002 — FHIR store approach (flag F2): FHIR resources as validated JSONB in
  Postgres with RLS (recommended) vs standalone FHIR server. See `docs/adr/`.
- ADR-0003 — Path-based tenancy under `hms.zensynq.com` (confirmed).

## Andhra Pradesh launch flags (build-aware from the start)

- ABDM (ABHA/HPR/HFR, FHIR R4) — interoperability workstream, sandbox in Sprint 4.
- Aarogyasri / PMJAY cashless — billing module priority.
- REF commission engine — **locked OFF for India**; referral tracking + follow-up ON.
- Data residency — production data stays in India.

See `docs/SETUP_CHECKLIST.md` before first deploy.
