"""PLT service entrypoint.

Foundation service: platform/tenancy plus a demo patients module that shows the
tenant-isolation + audit pattern every clinical module must follow. Auto-generates
the OpenAPI spec (INT-007) at /docs and /openapi.json.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .routers import patients, scheduling, emr, rx, ord, bil, por, rpt, integration, tenants

app = FastAPI(
    title="HMS Platform — PLT service",
    version="0.1.0",
    description="Foundation: multi-tenant, audited. Sprint-Zero skeleton.",
)

app.include_router(patients.router)
app.include_router(scheduling.router)
app.include_router(emr.router)
app.include_router(rx.router)
app.include_router(ord.router)
app.include_router(bil.router)
app.include_router(por.router)
app.include_router(rpt.router)
app.include_router(integration.router)
app.include_router(tenants.router)


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError):
    return JSONResponse(
        status_code=403,
        content={"detail": str(exc)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )


@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe used by Docker/compose and later by k8s."""
    return {"status": "ok"}

