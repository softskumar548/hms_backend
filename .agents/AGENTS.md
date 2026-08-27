# AGENTS.md — HMS Platform (SaaS, backend, frontend, keycloak, redis, postgres)

Project memory for AI AGENTS. This file is read at the start of every session.
Follow it for **every** change. When in doubt, prefer the rules here over habits
from other projects. If something here contradicts reality, fix this file in the
same PR — a stale AGENTS.md is worse than none, because it actively misleads.

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
  against real Postgres with RLS. Full suite currently 79 passing. Enhanced tenant
  provisioning API captures primary/secondary contacts with 12-digit Aadhaar ID,
  structured physical address (`door_no`, `address_line1`, `address_line2`, `city`, `state`, `pin_code`, `country`),
  landline extension telephony, validates contract signatory details via Aadhaar/Email/Phone matching,
  and auto-provisions designated Tenant Admin practitioners and Keycloak identities.
- **Sprint E4 — Platform Control Center & Billing Ops (TEN-301…304): BUILT & LIVE**
  - Subscription status tracking & invoicing (`TEN-301`).
  - Suspension lifecycle & reactivation (`TEN-303`).
  - Operator emergency overrides & break-glass audit logs (`TEN-304`).
  - Operator Console UI (`hms-web`) with tenant roster, KPI cards, override controls, and `/operator/profile` management.
- **Readiness Engine & Safe Offboarding (T1-03, T3-01): BUILT & LIVE**
  - Full 6-check setup readiness evaluation engine with badge rendering in UI.
  - Dynamic topological cascade engine for safe tenant offboarding with UI modal safeguards.
- **Frontend Architecture & UX Revamp (Client Redesign — LIVE & ACTIVE):**
  - **Operator Tenant Onboarding Wizard (`/onboarding`)**: Streamlined 2-stage pipeline with real-time slug availability check, vertical single-column layout, sequential `Tab` and `Enter` key progression, searchable & creatable `DesignationCombobox`, 12-digit Aadhaar validation, error auto-scroll engine, and minimalist styling.
  - **Header Right Controls**: Live ticking clock (`DD MMM YYYY - HH:MM AM/PM`), `EN / TE` language toggle, and circular Profile Avatar dropdown menu (Dean/Admin details, account links, logout).
  - **Collapsible Sidebar Navigation (`AppSidebar`)**: 240px expanded / 72px collapsed modes with smooth transitions.
  - **Tenant Admin Role IA**: Clean, executive navigation with 📊 **Dashboard** (Welcome Banner, site selector, live refresh, KPI cards without tour checklist) and 👥 **Admin** collapsible menu.
  - **Master Configuration Management**: Dropdown-driven configuration manager (`Payment Type`, `Visit Type`, `Order Status`, `Clinic Type`, `Specialization`, `Room Type`, `Floor Type`, `Bed Category`, `Expense Category`) with add/edit/toggle modal workflows.
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
- **Frontend:** React 18 + TypeScript, MediGo design system with collapsible side navigation.
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
- RLS must **fail closed**: no tenant context → zero rows. Never work around this.

---

# AGENTS.md — hms-web (MediGo HMS Frontend)

Project memory for AI AGENTS and the source of truth for how this UI is built.

---

# PART 1 — THEME & VISUAL LANGUAGE (the MediGo design system)

## 1.1 Header & Sidebar Layout
- **Sticky Header**: Displays brand logo left, and right-aligned live clock (`DD MMM YYYY - HH:MM AM/PM`), tenant·role badge, language selector (`EN / TE`), and interactive Profile Avatar (`👤`) dropdown.
- **Collapsible Sidebar (`AppSidebar`)**: Replaces flat top navigation. Supports expanded (240px) and collapsed icon-only (72px) states with tooltips and active left indicator.
- **Universal ESC & Click-Outside Dismissals**: Applied across profile dropdown, modal dialogs, and drawer menus.

## 1.2 Platform Operator Console & Tenant Onboarding Pipeline (TEN-101 / TEN-301)
- 📋 **Tenant Fleet**: Live search, status filters, setup readiness badges, and safe cascade offboarding safeguards.
- ⚡ **Onboarding Wizard (`/onboarding`)**: Streamlined 2-stage onboarding pipeline with real-time slug availability check, vertical single-column layout, sequential `Tab` and `Enter` key progression, searchable & creatable `DesignationCombobox`, 12-digit Aadhaar validation, error auto-scroll engine, and minimalist styling.
- 👤 **Operator Profile & Security (`/operator/profile`)**: Dedicated operator details and password reset tabs.

## 1.3 Tenant Admin Navigation Architecture
- 📊 **Dashboard** (`/dashboard`): Executive Welcome Banner with Dean details & clinic name, facility site filter, live refresh counter, and KPI metric cards (`Today's Consultations`, `Avg Wait Time`, `No-Shows`, `Cashier Till Revenue`). Seeded tour checklist is omitted.
- 👥 **Admin (Expandable Accordion)**:
  - ⚙️ **Configuration** (`/settings?tab=config`): Master dropdown-driven configuration view (Payment Type, Visit Type, Order Status, Clinic Type, Specialization, Room Type, Floor Type, Bed Category, Expense Category) with dynamic item table & modal forms.
  - 🏢 **Account Settings** (`/settings?tab=account`): Subscription profile, signatory details (`DR K R MURALI`), and compliance documents.
  - 🔐 **User Authentication** (`/settings?tab=auth`): Keycloak OIDC issuer, client parameters, token scopes, and MFA status.
  - 👥 **Users** (`/settings?tab=users`): Staff directory with role badges and **+ Invite Staff** modal.
  - 💳 **Payment** (`/settings?tab=payment`): Payment collection rails, daily till reconciliation limits, PMJAY 100% cashless rules.
  - 🌐 **Online Services** (`/settings?tab=online`): ABDM ABHA milestones, Telehealth switches, and SMS/WhatsApp gateway.

## 1.4 Clinical Staff Navigation
- **Receptionist**: Live Queue / Check-in board (`/queue`), Patients (`/patients`), Scheduling (`/scheduling`).
- **Physician / Nurse**: My Schedule (`/my-schedule`), Live Queue (`/queue`), EMR / Notes (`/emr`).
- **Biller**: Invoices (`/billing`), Payment Till, Referral Analytics (`/reports/referrals`).