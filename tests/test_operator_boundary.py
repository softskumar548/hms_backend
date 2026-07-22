"""OPERATOR BOUNDARY TEST GATE (N3-X1).

Verifies that platform administration and tenant control endpoints (/tenants) are strictly
isolated from tenant-scoped roles (physician, receptionist, nurse, billing_clerk).
Standard tenant tokens can NEVER access operator provisioning or tenant management routes.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def test_operator_can_provision_and_manage_tenant():
    """Operator role successfully provisions, lists, retrieves, and updates tenant status."""
    headers = {"Authorization": "Bearer dev.apollo.operator"}

    # 1. Provision new tenant
    tenant_id = "test_hospital_n3"
    payload = {
        "id": tenant_id,
        "name": "Test Hospital N3",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": False}
    }
    resp = client.post("/tenants", json=payload, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == tenant_id
    assert data["status"] == "provisioned"

    # 2. List tenants
    resp = client.get("/tenants", headers=headers)
    assert resp.status_code == 200
    tenants = resp.json()
    assert any(t["id"] == tenant_id for t in tenants)

    # 3. Retrieve tenant detail
    resp = client.get(f"/tenants/{tenant_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == tenant_id

    # 4. Update status
    resp = client.patch(f"/tenants/{tenant_id}/status", json={"status": "configured", "reason": "Setup completed"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "configured"

    # 5. Invite staff member
    invite_payload = {
        "email": "dr.smith@testhospital.com",
        "role": "physician",
        "given_name": "John",
        "family_name": "Smith"
    }
    resp = client.post(f"/tenants/{tenant_id}/invitations", json=invite_payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "invited"


@pytest.mark.parametrize("role", ["physician", "receptionist", "nurse", "billing_clerk"])
def test_tenant_roles_blocked_from_operator_endpoints(role: str):
    """BOUNDARY GATE ASSERTION (N3-X1): Non-operator roles MUST receive 403 Forbidden on operator endpoints."""
    headers = {"Authorization": f"Bearer dev.apollo.{role}"}

    # 1. Provisioning attempt blocked
    resp = client.post("/tenants", json={"id": "forbidden_t", "name": "Forbidden"}, headers=headers)
    assert resp.status_code == 403

    # 2. List attempt blocked
    resp = client.get("/tenants", headers=headers)
    assert resp.status_code == 403

    # 3. Detail fetch blocked
    resp = client.get("/tenants/apollo", headers=headers)
    assert resp.status_code == 403

    # 4. Status update blocked
    resp = client.patch("/tenants/apollo/status", json={"status": "suspended"}, headers=headers)
    assert resp.status_code == 403

    # 5. Staff invite blocked
    resp = client.post("/tenants/apollo/invitations", json={"email": "a@b.com", "role": "physician", "given_name": "A", "family_name": "B"}, headers=headers)
    assert resp.status_code == 403
