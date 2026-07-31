# Setup Checklist — dev/demo/staging on the India-region Ubuntu VPS

Do these in order. Items 1–7 are the day-one setup from the kickoff pack.

## Bring the stack up
1. Install Docker + Docker Compose on the Ubuntu VPS.
2. `git clone` the repo; `cp .env.example .env` and change every secret.
3. `docker compose up --build`. Postgres applies `infra/postgres/init.sql`
   (schema + RLS) automatically on first start.
4. `docker compose exec plt python -m app.seed` to create demo tenants.
5. Verify isolation by hand (two tenants, different bearer tokens) — see README.
6. `docker compose exec plt pytest -q tests` — the isolation test must pass.
7. Confirm auto OpenAPI at `/api/docs` through nginx.

## Harden the box (staging stakes; production non-negotiable)
- `ufw` firewall: allow 22, 80, 443 only; Postgres port never exposed.
- SSH key-only, disable password auth and root login; install `fail2ban`.
- `unattended-upgrades` for automatic security patches.
- TLS via Let's Encrypt; enable the nginx 443 block; force HTTPS + HSTS.
- Full-disk encryption on the volume holding Postgres data.
- **Synthetic data only** on dev/demo/staging. Never a real hospital's records here.

## Backups (get the habit now)
- Schedule `scripts/backup.sh` via cron; set `BACKUP_GPG_RECIPIENT`.
- Configure off-box upload to India-region object storage.
- **Test a restore** — a backup you haven't restored is a hope, not a backup.

## Record these ADRs (docs/adr/)
- ADR-0001 Python/FastAPI stack (confirmed).
- ADR-0002 FHIR store approach (flag F2) — recommend JSONB-in-Postgres for MVP.
- ADR-0003 Path-based tenancy under hms.zensynq.com (confirmed).

## Production (later, upgraded servers)
- Separate/managed Postgres (India region) with automated failover + PITR.
- Multiple app nodes behind a load balancer for the 99.95% SLA.
- Same containers as staging; only scale + managed services differ.
- Kafka in place of Postgres/Redis events when volume justifies it.

## Known Open Items & Bug Tickets
- **`BUG-POR-001` (Portal Invitation RLS Violation)**: Unauthenticated `/por/activate` endpoint updates `portal_invitation` status without explicit target tenant session binding when caller has no `ctx.tenant_id`. Needs `tenant_session(session, ctx, tenant_id=invite.tenant_id)` context binding prior to update.
- **`TASK-TEN-105-HARDENING` (Practitioner System Placeholder Flag)**: Add explicit `is_system_placeholder BOOLEAN DEFAULT FALSE` column to `practitioner` table instead of relying on ID prefix filtering (`id NOT LIKE 'prac_%_1'`) in `STAFF_ENROLLED` readiness check.
