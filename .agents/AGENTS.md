# AGENTS.md — HMS Backend Platform (FastAPI, PostgreSQL RLS, Keycloak, Celery/Redis, FHIR R4)

Project memory for AI AGENTS. This file is read at the start of every session.
Follow it for **every** change. When in doubt, prefer the rules here over habits
from other projects. If something here contradicts reality, fix this file in the
same PR — a stale AGENTS.md is worse than none, because it actively misleads.

Frontend: separate repo `hms-web` (React 18 + TypeScript / MediGo design system).
Staging deployment: `https://stage.zensynq.com` (VPS: `103.174.103.158`).

---

## 1. What this project is

A multi-tenant **Hospital Management System delivered as SaaS** — one platform many
hospitals/clinics subscribe to. Launch market: **Andhra Pradesh, India**.
Product domain: `hms.zensynq.com` (staging: `stage.zensynq.com` / `staging.hms.zensynq.com`).

Requirements live in `docs/`:
- FRD (`HMS-FRD-001`) — MVP functional requirements, all modules.
- REF increment (`HMS-FRD-002`) — referral, commission & follow-up.
- Tenant Management & Onboarding plan — `TEN-1xx/2xx` (onboarding), `TEN-3xx` (Control Center).
- IA & Role Navigation plan — landing views, nav trees, two-console structure.
- **`implementation_plan.md` — the LIVING checklist.** Current phase/status is
  tracked there, not here. Read it at session start alongside this file.

Every requirement has an ID (`PLT-002`, `REG-001`, `IAM-006`, `TEN-101`, `REF-061`).
**Always reference the relevant ID** in code comments, commit messages, and tests.

---

## 2. Where the project actually is (update this section as phases close)

- **Foundation, all 9 modules, tenant onboarding (TEN-101…205): built and passing**
  against real Postgres with RLS. Full test suite passing. Enhanced tenant
  provisioning API captures primary/secondary contacts with 12-digit Aadhaar ID,
  structured physical address (`door_no`, `address_line1`, `address_line2`, `city`, `state`, `pin_code`, `country`),
  landline extension telephony, validates contract signatory details via Aadhaar/Email/Phone matching,
  and auto-provisions designated Tenant Admin practitioners and Keycloak identities.
- **Sprint E4 — Platform Control Center & Billing Ops (TEN-301…304): BUILT & LIVE**
  - Subscription status tracking & invoicing (`TEN-301`).
  - Suspension lifecycle & reactivation (`TEN-303`).
  - Operator emergency overrides & break-glass audit logs (`TEN-304`).
- **SaaS Subscription Packages & Quota Metering (TEN-301 / Commercial Engine): BUILT & LIVE**
  - Multi-tier plan definitions (`starter`, `growth`, `enterprise`) and 9-dimensional quota tracking (`package_name`, `expiry_date`, `admins_limit`, `staff_limit`, `doctors_limit`, `beds_limit`, `sms_count_limit`, `email_count_limit`, `whatsapp_count_limit`).
  - Real-time quota usage and read-only mode calculation (`GET /tenants/{tenant_id}/quotas`) and operator tier upgrades (`PUT /tenants/{tenant_id}/subscription/plan`).
  - Soft-suspension safeguards ensuring clinical read safety while locking write operations on payment default.
- **Readiness Engine & Safe Offboarding (T1-03, T3-01): BUILT & LIVE**
  - Full 6-check setup readiness evaluation engine with badge status rendering.
  - Dynamic topological cascade engine for safe tenant offboarding with database safeguards.
- **Auth: real Keycloak/OIDC is LIVE** — RS256 validation with JWKS caching,
  `app.tenant_id` custom claim → `RequestContext`, roles from `realm_access.roles`.
  Declarative user profile enables `tenant_id` attribute propagation to tokens.
- **Staging deployment (`stage.zensynq.com` / `103.174.103.158`): LIVE, VERIFIED & ACTIVE** —
  Automated GitHub Actions CI/CD workflows active for backend (`hms_backend`) and frontend (`hms_web`).

---

## 3. Tech stack (confirmed — do not substitute without an ADR)

- **Language/API:** Python 3.12, FastAPI, Pydantic v2, Uvicorn/Gunicorn.
- **DB:** PostgreSQL 16 with **Row-Level Security** for tenant isolation.
- **ORM/migrations:** SQLAlchemy 2.0 (async) + Alembic.
- **Async/tasks:** asyncio; Celery or Arq on Redis for background jobs.
- **Events:** Postgres/Redis now, behind an event-publish interface; Kafka later.
- **FHIR:** `fhir.resources` (Pydantic FHIR R4 models); resources stored as validated
  JSONB in Postgres (ADR-0002 / flag F2). ABDM requires FHIR R4.
- **Identity:** **Keycloak / OIDC (live)** + MFA policy in the `hms` realm.
- **Infra:** Docker + Docker Compose now (India VPS); Kubernetes for production.

---

## 4. Non-negotiable rules (these protect patients and the business)

### 4.1 Tenant isolation (PLT-002) — the most important rule
- Every tenant-scoped table has a `tenant_id` column and a Postgres RLS policy
  with both `ENABLE` and `FORCE ROW LEVEL SECURITY`.
- All DB access for a request goes through `hms_tenancy.tenant_session(session, ctx)`,
  which sets `app.tenant_id`. **Never** open a raw session for tenant data.
- **Never read `tenant_id` from a header, query param, or request body.** It comes
  only from the verified auth context (`RequestContext`), which comes from the token.
- The app connects as the non-superuser role `hms_app` so RLS actually applies.
- `tests/test_tenant_isolation.py` is a permanent CI gate. **When you add a new
  tenant-scoped table, add a case to that test.**
- RLS must **fail closed**: no tenant context → zero rows. Never work around this.

### 4.2 Audit (PLT-005)
- Every create/read/update/export of patient-identifiable data calls
  `hms_audit.record(...)` in the **same transaction** as the action.
- The `audit_event` table is append-only. Never grant or use UPDATE/DELETE on it.

### 4.3 Consent & Privacy (PLT-010)
- Check consent before sharing patient data externally or sending non-essential comms.
- **Synthetic data only** in dev/demo/staging. Never seed or paste a real patient's data.
- All patient data and backups must stay in the **India region** (data residency compliance).

### 4.4 Reference Pattern
- `services/plt/app/routers/patients.py` is the canonical reference pattern for tenant session +
  audit + least-privilege role check. Copy this shape for all clinical and operational endpoints.

---

## 5. Andhra Pradesh / India Regional Specifics

- **NMC Prohibition on Doctor Commission:** Clinic/clinician referrer types are commission-*ineligible* by default. Never enable doctor-referral commissions as a default or one-click feature.
- **Referral & Follow-up Tracking:** Tracking, clinical prerequisites, and follow-up closure are fully active.
- **ABDM Integration:** Patients link with 14-digit ABHA IDs (`patient.national_id`), providers use HPR, and facilities use HFR. Records adhere to FHIR R4.
- **Aarogyasri / PMJAY:** Cashless billing eligibility captured via Aadhaar at check-in.