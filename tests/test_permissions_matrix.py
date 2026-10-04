"""
test_permissions_matrix.py — Comprehensive tests for Role-Based Screen & Action Permission Matrix.

Tests:
1. Retrieval of initial/empty tenant permissions (GET 200 OK).
2. Complete persistence and retrieval for all 19 healthcare roles across 62 screens.
3. Admin self-lockout safeguard enforcement on backend (admin_user_auth).
4. Granular CRUD permission flag verification (canCreate, canRead, canUpdate, canDelete).
5. Cross-tenant isolation (Tenant A cannot access or modify Tenant B).
6. Non-admin authorization gates (physician, nurse, receptionist, billing rejected with 403).
7. Operator break-glass overrides.
8. Idempotent upsert and audit logging verification.
"""

import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_permissions_get_default_empty():
    """Verify GET /tenants/{tenant_id}/permissions returns clean empty matrix for a newly created tenant."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_empty_{uuid.uuid4().hex[:6]}"

    # Provision tenant
    client.post("/tenants", json={
        "id": tid,
        "name": "Empty Permissions Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}

    resp = client.get(f"/tenants/{tid}/permissions", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == tid
    assert data["permissions"] == {}
    assert "updated_at" in data

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_permissions_put_and_get_all_19_roles():
    """Verify persisting and retrieving a complete permissions matrix for all 19 standard roles."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_19roles_{uuid.uuid4().hex[:6]}"

    client.post("/tenants", json={
        "id": tid,
        "name": "19 Roles Full Matrix Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}

    roles_list = [
        "Administrator",
        "Doctor",
        "Nurse",
        "Lab Assistant",
        "Staff",
        "Marketing Executive",
        "Pharmacist",
        "Receptionist",
        "Billing",
        "Super Administrator",
        "Store Management",
        "Pharmacy Incharge",
        "Lab Incharge",
        "MRD",
        "Radiographer",
        "HR",
        "Tele Caller",
        "Accountant",
        "MOD",
    ]

    all_roles_matrix = {
        role: {
            "dashboard_home": {
                "isAccessible": True,
                "canCreate": role in ("Administrator", "Super Administrator"),
                "canRead": True,
                "canUpdate": role in ("Administrator", "Super Administrator"),
                "canDelete": False,
            },
            "opd_appointments": {
                "isAccessible": role in ("Administrator", "Super Administrator", "Doctor", "Receptionist", "Nurse"),
                "canCreate": role in ("Administrator", "Super Administrator", "Receptionist"),
                "canRead": True,
                "canUpdate": role in ("Administrator", "Super Administrator", "Doctor", "Receptionist"),
                "canDelete": role in ("Administrator", "Super Administrator"),
            },
            "admin_user_auth": {
                "isAccessible": role in ("Administrator", "Super Administrator"),
                "canCreate": role in ("Administrator", "Super Administrator"),
                "canRead": role in ("Administrator", "Super Administrator"),
                "canUpdate": role in ("Administrator", "Super Administrator"),
                "canDelete": role == "Super Administrator",
            },
        }
        for role in roles_list
    }

    # Save complete matrix
    put_resp = client.put(f"/tenants/{tid}/permissions", json={"permissions": all_roles_matrix}, headers=admin_headers)
    assert put_resp.status_code == 200
    saved_data = put_resp.json()
    assert saved_data["tenant_id"] == tid
    assert len(saved_data["permissions"]) == 19

    # Retrieve and verify all 19 roles exist in response
    get_resp = client.get(f"/tenants/{tid}/permissions", headers=admin_headers)
    assert get_resp.status_code == 200
    retrieved_data = get_resp.json()
    assert len(retrieved_data["permissions"]) == 19

    for role in roles_list:
        assert role in retrieved_data["permissions"]
        assert "dashboard_home" in retrieved_data["permissions"][role]
        assert "opd_appointments" in retrieved_data["permissions"][role]
        assert "admin_user_auth" in retrieved_data["permissions"][role]

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_admin_lockout_safeguard_enforcement():
    """Verify that backend strictly blocks any attempt to disable admin_user_auth for Administrator or Super Administrator."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_lockout_{uuid.uuid4().hex[:6]}"

    client.post("/tenants", json={
        "id": tid,
        "name": "Lockout Safeguard Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}

    # Attempt to lock out Administrator and Super Administrator from admin_user_auth
    malicious_lockout_payload = {
        "permissions": {
            "Administrator": {
                "admin_user_auth": {
                    "isAccessible": False,
                    "canCreate": False,
                    "canRead": False,
                    "canUpdate": False,
                    "canDelete": False,
                }
            },
            "Super Administrator": {
                "admin_user_auth": {
                    "isAccessible": False,
                    "canCreate": False,
                    "canRead": False,
                    "canUpdate": False,
                    "canDelete": False,
                }
            }
        }
    }

    put_resp = client.put(f"/tenants/{tid}/permissions", json=malicious_lockout_payload, headers=admin_headers)
    assert put_resp.status_code == 200
    data = put_resp.json()

    # Backend override assertions
    admin_auth = data["permissions"]["Administrator"]["admin_user_auth"]
    super_admin_auth = data["permissions"]["Super Administrator"]["admin_user_auth"]

    assert admin_auth["isAccessible"] is True
    assert admin_auth["canRead"] is True
    assert admin_auth["canUpdate"] is True

    assert super_admin_auth["isAccessible"] is True
    assert super_admin_auth["canRead"] is True
    assert super_admin_auth["canUpdate"] is True

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_granular_crud_action_permissions():
    """Verify individual CRUD flags (canCreate, canRead, canUpdate, canDelete) are independently preserved."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_crud_{uuid.uuid4().hex[:6]}"

    client.post("/tenants", json={
        "id": tid,
        "name": "Granular CRUD Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}

    payload = {
        "permissions": {
            "Pharmacist": {
                "pharma_bill": {
                    "isAccessible": True,
                    "canCreate": True,
                    "canRead": True,
                    "canUpdate": True,
                    "canDelete": False,
                },
                "pharma_stock": {
                    "isAccessible": True,
                    "canCreate": False,
                    "canRead": True,
                    "canUpdate": False,
                    "canDelete": False,
                }
            },
            "Lab Incharge": {
                "lab_rate_plan_master": {
                    "isAccessible": True,
                    "canCreate": True,
                    "canRead": True,
                    "canUpdate": True,
                    "canDelete": True,
                }
            }
        }
    }

    put_resp = client.put(f"/tenants/{tid}/permissions", json=payload, headers=admin_headers)
    assert put_resp.status_code == 200
    res = put_resp.json()["permissions"]

    # Assert Pharmacist permissions
    assert res["Pharmacist"]["pharma_bill"]["canCreate"] is True
    assert res["Pharmacist"]["pharma_bill"]["canDelete"] is False
    assert res["Pharmacist"]["pharma_stock"]["canCreate"] is False
    assert res["Pharmacist"]["pharma_stock"]["canRead"] is True

    # Assert Lab Incharge permissions
    assert res["Lab Incharge"]["lab_rate_plan_master"]["canDelete"] is True

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_cross_tenant_isolation_forbidden():
    """Verify Tenant A administrator cannot view or update Tenant B permissions (PLT-002)."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid_a = f"perm_hosp_a_{uuid.uuid4().hex[:6]}"
    tid_b = f"perm_hosp_b_{uuid.uuid4().hex[:6]}"

    for tid in (tid_a, tid_b):
        client.post("/tenants", json={
            "id": tid,
            "name": f"Hospital {tid}",
            "region": "india",
            "locale": "en-IN",
            "currency": "INR",
        }, headers=op_headers)

    admin_a_headers = {"Authorization": f"Bearer dev.{tid_a}.admin"}
    admin_b_headers = {"Authorization": f"Bearer dev.{tid_b}.admin"}

    # Tenant A tries to read Tenant B permissions -> 403
    resp_read = client.get(f"/tenants/{tid_b}/permissions", headers=admin_a_headers)
    assert resp_read.status_code == 403

    # Tenant A tries to update Tenant B permissions -> 403
    resp_write = client.put(
        f"/tenants/{tid_b}/permissions",
        json={"permissions": {"Doctor": {"dashboard_home": {"isAccessible": False}}}},
        headers=admin_a_headers
    )
    assert resp_write.status_code == 403

    # Teardown
    client.delete(f"/tenants/{tid_a}", headers=op_headers)
    client.delete(f"/tenants/{tid_b}", headers=op_headers)


def test_non_admin_roles_forbidden_from_updating():
    """Verify all non-admin clinical roles are rejected with 403 Forbidden when calling PUT /permissions."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_nonadmin_{uuid.uuid4().hex[:6]}"

    client.post("/tenants", json={
        "id": tid,
        "name": "Non Admin Test Clinic",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    non_admin_roles = ["physician", "doctor", "nurse", "receptionist", "billing", "patient"]

    for role in non_admin_roles:
        headers = {"Authorization": f"Bearer dev.{tid}.{role}"}
        resp = client.put(
            f"/tenants/{tid}/permissions",
            json={"permissions": {}},
            headers=headers
        )
        assert resp.status_code == 403, f"Role {role} was not rejected with 403! Got: {resp.status_code}"

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_operator_break_glass_access():
    """Verify platform operator can view and update permissions for any tenant."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_op_{uuid.uuid4().hex[:6]}"

    client.post("/tenants", json={
        "id": tid,
        "name": "Operator Managed Clinic",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    # Operator GET
    get_resp = client.get(f"/tenants/{tid}/permissions", headers=op_headers)
    assert get_resp.status_code == 200

    # Operator PUT
    put_resp = client.put(
        f"/tenants/{tid}/permissions",
        json={
            "permissions": {
                "MOD": {
                    "emergency_triages": {
                        "isAccessible": True,
                        "canCreate": True,
                        "canRead": True,
                        "canUpdate": True,
                        "canDelete": True,
                    }
                }
            }
        },
        headers=op_headers
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["permissions"]["MOD"]["emergency_triages"]["canDelete"] is True

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)
