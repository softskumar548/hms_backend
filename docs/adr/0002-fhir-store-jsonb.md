# ADR-0002 — FHIR resources as validated JSONB in Postgres (flag F2)

- Status: Accepted (MVP; revisit before production scale-up)
- Date: 2026-07-20
- Deciders: Platform + Interop team

## Context

ABDM requires records to be exchangeable as FHIR R4. Two broad approaches:

1. **JSONB in Postgres.** Store FHIR resources as validated JSONB columns in the
   existing multi-tenant, RLS-scoped database. Validation happens via
   `fhir.resources` Pydantic models at the edge.
2. **Standalone FHIR server** (HAPI FHIR, Medplum, etc.). Delegate storage and
   query to a dedicated FHIR engine, sync with our relational data.

We are pre-launch, in a single region, with a small team. The dominant risks in
this phase are operational complexity and integration latency, not FHIR query
sophistication. We also need tenant isolation to remain a single, provable story.

## Decision

For MVP, **store FHIR resources as validated JSONB in Postgres, in the same
tenant-scoped tables under the same RLS policies.** Feature-flagged as `F2`.

- Validation via `fhir.resources` at write time; resources are rejected if they
  do not parse as valid FHIR R4.
- JSONB columns are indexed with `jsonb_path_ops` on the fields we actually query.
- Tenant isolation is the same PLT-002 mechanism — no separate isolation model.
- FHIR-shaped API endpoints are added under `/fhir/...` when ABDM integration
  requires them; internal endpoints stay in our own shape.

## Consequences

Easier:
- One database, one tenancy story, one backup path.
- No cross-system sync between our relational data and a FHIR engine.
- Operationally lean for the India-region VPS footprint.

Harder / constraints we now accept:
- Complex FHIR queries (search, chained references) must be implemented in SQL
  over JSONB rather than delegated to a FHIR engine. Revisit if we hit a query
  we cannot express reasonably.
- We commit to keeping resources **valid** FHIR R4 at the boundary — writes that
  bypass the Pydantic layer would poison the store.
- If ABDM scope grows or a customer demands a full FHIR server, this ADR is
  superseded and we migrate the JSONB store to a real FHIR engine.
