"""Shared test fixtures.

Tenant provisioning for the isolation gate lives in test_tenant_isolation.py
(module-scoped autouse there), so the unit suites — which mock the DB session —
run without a live Postgres. Only the isolation tests require the database.
"""
from __future__ import annotations
