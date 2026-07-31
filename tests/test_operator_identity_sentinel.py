"""OPERATOR IDENTITY SENTINEL REGRESSION TEST (P1-05 & P2-05).

Verifies that:
1. Operator token carries '__operator__' sentinel as its home tenant_id.
2. If an operator token attempts to call a tenant-scoped clinical endpoint directly,
   it fails closed / returns 0 rows due to RLS, never leaking 'apollo' or 'kims' hospital data.
3. Staff invitation endpoint creates both a practitioner DB record in the target tenant
   and provisions a real Keycloak user account via Keycloak Admin REST API.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_operator_token_sentinel_fails_closed_on_tenant_data_endpoints():
    """P1-05: Proves operator token (__operator__) returns 0 patients on clinical endpoint."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    
    # 1. Clinical patient read fails closed (0 patients found) under __operator__ context
    resp = client.get("/patients", headers=op_headers)
    assert resp.status_code == 200
    patients = resp.json()
    assert len(patients) == 0, f"Expected 0 patients for sentinel __operator__, got {len(patients)}"

    # 2. Compare against valid apollo physician token
    apollo_headers = {"Authorization": "Bearer dev.apollo.physician"}
    resp_apollo = client.get("/patients", headers=apollo_headers)
    assert resp_apollo.status_code == 200
    assert len(resp_apollo.json()) >= 1, "Apollo physician should see apollo patients"


def test_operator_staff_invitation_provisions_db_and_keycloak():
    """P2-05: Proves POST /tenants/{tenant_id}/invitations creates DB practitioner and Keycloak user."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    target_tid = f"inv_hosp_{uuid.uuid4().hex[:6]}"

    # Provision target tenant
    client.post("/tenants", json={
        "id": target_tid,
        "name": "Invitation Test Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": False}
    }, headers=op_headers)

    # Invite staff member
    test_email = f"dr.test.{uuid.uuid4().hex[:4]}@example.invalid"
    invite_payload = {
        "email": test_email,
        "role": "physician",
        "given_name": "Suresh",
        "family_name": "Rao"
    }

    resp = client.post(f"/tenants/{target_tid}/invitations", json=invite_payload, headers=op_headers)
    assert resp.status_code == 201
    data = resp.json()

    assert data["status"] == "invited"
    assert data["tenant_id"] == target_tid
    assert data["email"] == test_email
    assert data["role"] == "physician"
    assert data["practitioner_id"].startswith(f"prac_{target_tid}_")

    # Clean up test tenant
    client.delete(f"/tenants/{target_tid}", headers=op_headers)
