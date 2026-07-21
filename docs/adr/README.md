# Architecture Decision Records

Each ADR captures **context**, **decision**, and **consequences** for a significant
choice. Prefer amending or superseding an ADR over silent changes to the codebase.

| # | Title | Status |
|---|-------|--------|
| [0001](0001-python-fastapi-stack.md) | Python 3.12 + FastAPI + Postgres RLS | Accepted |
| [0002](0002-fhir-store-jsonb.md) | FHIR resources as validated JSONB in Postgres (flag F2) | Accepted |
| [0003](0003-path-based-tenancy.md) | Path-based tenancy under `hms.zensynq.com` | Accepted |

Numbering is monotonically increasing. Never renumber.

Template:

```markdown
# ADR-NNNN — <short title>

- Status: Proposed | Accepted | Superseded by ADR-XXXX
- Date: YYYY-MM-DD
- Deciders: <people/roles>

## Context
<forces at play, constraints, why this decision is being made now>

## Decision
<the choice, stated plainly>

## Consequences
<what becomes easier, what becomes harder, what we now must not do>
```
