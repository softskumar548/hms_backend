"""TENANT ONBOARDING END-TO-END TEST (TEN-201 .. TEN-208).

Verifies the complete tenant onboarding lifecycle:
Provisioning -> Setup Wizard Config -> Legacy Migration Staging -> Clinician Reconciliation ->
Readiness Engine Evaluation -> Go-Live Transition (Active) -> Bulk FHIR Export.
"""

from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


import uuid

def test_end_to_end_tenant_onboarding_journey():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tenant_id = f"hosp_n4_{uuid.uuid4().hex[:6]}"

    # 1. Provision new tenant (TEN-101)
    provision_payload = {
        "id": tenant_id,
        "name": "KIMS Andhra Onboarding Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": False}
    }
    resp = client.post("/tenants", json=provision_payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["status"] == "provisioned"

    # 2. Setup Wizard configuration (TEN-104)
    site_id = f"site_{tenant_id}"
    room_id = f"room_{tenant_id}"
    svc_id = f"svc_{tenant_id}"
    config_payload = {
        "sites": [{"id": site_id, "name": "KIMS Vizag Site"}],
        "rooms": [{"id": room_id, "site_id": site_id, "name": "Room 101 OPD"}],
        "services": [{"id": svc_id, "name": "General OPD", "duration_minutes": 20}]
    }
    resp = client.post(f"/tenants/{tenant_id}/wizard/config", json=config_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["wizard_status"] == "configured"

    # 2b. Enroll genuine staff member via invitation (TEN-105)
    invite_payload = {
        "email": f"dr.verma.{tenant_id}@zensynq.com",
        "role": "physician",
        "given_name": "Suresh",
        "family_name": "Verma",
        "department": "Cardiology"
    }
    resp = client.post(f"/tenants/{tenant_id}/invitations", json=invite_payload, headers=headers)
    assert resp.status_code == 201

    # 3. Stage legacy CSV patient data (TEN-201)
    stage_payload = {
        "patients": [
            {"legacy_id": "LEG-001", "given_name": "Suresh", "family_name": "Kumar", "phone": "+919876543210"},
            {"legacy_id": "LEG-002", "given_name": "Padma", "family_name": "Devi", "phone": "+918765432109"}
        ]
    }
    resp = client.post(f"/tenants/{tenant_id}/migration/stage", json=stage_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["staged_count"] == 2

    # 4. Clinician reconciliation sign-off (TEN-202)
    reconcile_payload = {
        "staged_patient_ids": ["LEG-001", "LEG-002"],
        "reconciled_by": "dr.verma@zensynq.com",
        "notes": "Reviewed and verified legacy allergy and diagnostic history"
    }
    resp = client.post(f"/tenants/{tenant_id}/migration/reconcile", json=reconcile_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "reconciled"

    # 5. Evaluate tenant readiness checklist engine (TEN-203)
    resp = client.get(f"/tenants/{tenant_id}/readiness", headers=headers)
    assert resp.status_code == 200
    readiness_data = resp.json()
    assert readiness_data["ready_for_golive"] is True
    assert len(readiness_data["checks"]) == 6
    
    # Assert each specific code and passed state
    codes = {c["code"]: c["passed"] for c in readiness_data["checks"]}
    assert codes["SITES_CONFIGURED"] is True
    assert codes["ROOMS_CONFIGURED"] is True
    assert codes["SERVICES_CONFIGURED"] is True
    assert codes["STAFF_ENROLLED"] is True
    assert codes["MIGRATION_RECONCILED"] is True
    assert codes["ATTESTATION_SIGNED"] is True

    # 6. Flip tenant state to active Go-Live (TEN-204)
    resp = client.post(f"/tenants/{tenant_id}/go-live", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # 7. Bulk FHIR R4 dataset export (TEN-208)
    resp = client.get(f"/tenants/{tenant_id}/export/fhir", headers=headers)
    assert resp.status_code == 200
    fhir_data = resp.json()
    assert fhir_data["tenant_id"] == tenant_id
    assert fhir_data["fhir_bundle"]["resourceType"] == "Bundle"
    assert fhir_data["patient_count"] >= 2
    assert "2026-" in fhir_data["exported_at"]  # Dynamic ISO timestamp check

    # Teardown test tenant
    client.delete(f"/tenants/{tenant_id}", headers=headers)


def test_readiness_checklist_individual_check_gating_behavior():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}

    # Case A: Fresh provisioned tenant (zero staff, zero sites, un-reconciled)
    fresh_tid = f"test_hosp_{uuid.uuid4().hex[:6]}"
    client.post("/tenants", json={
        "id": fresh_tid,
        "name": "Test Hospital N3",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": False}
    }, headers=headers)

    resp = client.get(f"/tenants/{fresh_tid}/readiness", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready_for_golive"] is False

    check_map = {c["code"]: c for c in data["checks"]}
    assert check_map["STAFF_ENROLLED"]["passed"] is False
    assert check_map["STAFF_ENROLLED"]["details"] == "0 practitioner(s) & staff profile(s) enrolled"
    assert check_map["SITES_CONFIGURED"]["passed"] is False
    assert check_map["ATTESTATION_SIGNED"]["passed"] is True  # standard terms default

    # Case B: Commission-enabled tenant without counsel attestation -> ATTESTATION_SIGNED blocked
    comm_tid = f"test_comm_{uuid.uuid4().hex[:6]}"
    client.post("/tenants", json={
        "id": comm_tid,
        "name": "Commission Test Clinic",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": True, "ref_commission_attested": False}
    }, headers=headers)

    resp_comm = client.get(f"/tenants/{comm_tid}/readiness", headers=headers)
    assert resp_comm.status_code == 200
    comm_data = resp_comm.json()
    assert comm_data["ready_for_golive"] is False
    comm_attest_check = next(c for c in comm_data["checks"] if c["code"] == "ATTESTATION_SIGNED")
    assert comm_attest_check["passed"] is False
    assert "BLOCKED" in comm_attest_check["details"]

    # Teardown test tenants so no debris remains
    client.delete(f"/tenants/{fresh_tid}", headers=headers)
    client.delete(f"/tenants/{comm_tid}", headers=headers)


def test_tenant_admin_can_invite_doctor_with_password():
    """Verify that a Tenant Admin can onboard a doctor with custom initial password (TEN-105)."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"test_hosp_{uuid.uuid4().hex[:6]}"

    # 1. Provision tenant
    client.post("/tenants", json={
        "id": tid,
        "name": "Doctor Login Test Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    # 2. Tenant admin headers
    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}

    # 3. Tenant Admin invites a doctor with a temporary password
    doc_email = f"dr.ramesh.{tid}@zensynq.com"
    invite_resp = client.post(
        f"/tenants/{tid}/invitations",
        json={
            "email": doc_email,
            "role": "doctor",
            "given_name": "Ramesh",
            "family_name": "Naidu",
            "department": "Cardiology",
            "temporary_password": "ZenMed@Doctor2026",
        },
        headers=admin_headers,
    )
    assert invite_resp.status_code == 201
    res_json = invite_resp.json()
    assert res_json["status"] == "invited"
    assert res_json["email"] == doc_email
    assert res_json["tenant_id"] == tid

    # 4. Attempting to invite for a different tenant should fail with 403 Forbidden
    other_tid = f"other_{uuid.uuid4().hex[:6]}"
    forbidden_resp = client.post(
        f"/tenants/{other_tid}/invitations",
        json={
            "email": "intruder@other.com",
            "role": "doctor",
        },
        headers=admin_headers,
    )
    assert forbidden_resp.status_code == 403

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_tenant_screen_permissions_matrix_lifecycle():
    """Verify role-to-screen permissions matrix retrieval, persistence, and Admin lockout protection."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"hosp_perm_{uuid.uuid4().hex[:6]}"

    # 1. Provision tenant
    client.post("/tenants", json={
        "id": tid,
        "name": "Permission Matrix Test Hospital",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}
    other_admin_headers = {"Authorization": "Bearer dev.other_tenant.admin"}

    # 2. Get initial permissions
    get_resp = client.get(f"/tenants/{tid}/permissions", headers=admin_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["tenant_id"] == tid

    # 3. Update permissions for Doctor and attempt admin lockout on Administrator
    perm_payload = {
        "permissions": {
            "Doctor": {
                "dashboard_home": {
                    "isAccessible": True,
                    "canCreate": False,
                    "canRead": True,
                    "canUpdate": False,
                    "canDelete": False
                },
                "opd_appointments": {
                    "isAccessible": True,
                    "canCreate": True,
                    "canRead": True,
                    "canUpdate": True,
                    "canDelete": False
                }
            },
            "Administrator": {
                "admin_user_auth": {
                    # Attempt to lock out admin
                    "isAccessible": False,
                    "canCreate": False,
                    "canRead": False,
                    "canUpdate": False,
                    "canDelete": False
                }
            }
        }
    }

    put_resp = client.put(f"/tenants/{tid}/permissions", json=perm_payload, headers=admin_headers)
    assert put_resp.status_code == 200
    put_data = put_resp.json()
    assert put_data["tenant_id"] == tid
    
    # Assert Doctor permissions persisted
    assert put_data["permissions"]["Doctor"]["opd_appointments"]["canCreate"] is True
    
    # Assert Admin Lockout Prevention: Backend forced isAccessible=True and canRead=True for Administrator
    assert put_data["permissions"]["Administrator"]["admin_user_auth"]["isAccessible"] is True
    assert put_data["permissions"]["Administrator"]["admin_user_auth"]["canRead"] is True

    # 4. Cross-tenant access attempt should fail with 403 Forbidden
    cross_resp = client.put(f"/tenants/{tid}/permissions", json=perm_payload, headers=other_admin_headers)
    assert cross_resp.status_code == 403

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)


def test_tenant_permissions_edge_cases():
    """Verify edge cases for tenant screen permissions: non-admin rejection, operator override, idempotent upsert, and multiple role matrices."""
    op_headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tid = f"perm_edge_{uuid.uuid4().hex[:6]}"

    # 1. Provision tenant
    client.post("/tenants", json={
        "id": tid,
        "name": "Permission Edge Case Clinic",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
    }, headers=op_headers)

    admin_headers = {"Authorization": f"Bearer dev.{tid}.admin"}
    doctor_headers = {"Authorization": f"Bearer dev.{tid}.physician"}
    reception_headers = {"Authorization": f"Bearer dev.{tid}.receptionist"}
    billing_headers = {"Authorization": f"Bearer dev.{tid}.billing"}
    other_user_headers = {"Authorization": "Bearer dev.other_clinic.physician"}

    # Edge Case 1: Non-admin staff (Doctor, Receptionist, Biller) attempting to modify permissions should get 403
    for headers in [doctor_headers, reception_headers, billing_headers]:
        resp = client.put(f"/tenants/{tid}/permissions", json={"permissions": {}}, headers=headers)
        assert resp.status_code == 403, f"Expected 403 for non-admin headers, got {resp.status_code}"

    # Edge Case 2: Cross-tenant user attempting to read permissions should get 403
    cross_read = client.get(f"/tenants/{tid}/permissions", headers=other_user_headers)
    assert cross_read.status_code == 403

    # Edge Case 3: Operator break-glass access should succeed for both GET and PUT
    op_get = client.get(f"/tenants/{tid}/permissions", headers=op_headers)
    assert op_get.status_code == 200

    # Edge Case 4: Persisting 19 roles with partial and complete screens
    all_19_roles_payload = {
        "permissions": {
            "Administrator": {"admin_user_auth": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Super Administrator": {"admin_user_auth": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Doctor": {"opd_medical_records": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Nurse": {"ipd_bed_status": {"isAccessible": True, "canCreate": False, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Pharmacist": {"pharma_bill": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Pharmacy Incharge": {"pharma_stock": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Lab Assistant": {"lab_bill_history": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Lab Incharge": {"lab_rate_plan_master": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Radiographer": {"rad_usg_cases": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Receptionist": {"opd_appointments": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Billing": {"opd_bills": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "HR": {"hr_employees": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Accountant": {"hr_payroll_dashboard": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "MRD": {"opd_medical_records": {"isAccessible": True, "canCreate": False, "canRead": True, "canUpdate": False, "canDelete": False}},
            "MOD": {"emergency_triages": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Marketing Executive": {"crm_lead_mgmt": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Tele Caller": {"crm_contacts": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": False}},
            "Store Management": {"more_inventory_items": {"isAccessible": True, "canCreate": True, "canRead": True, "canUpdate": True, "canDelete": True}},
            "Staff": {"dashboard_home": {"isAccessible": True, "canCreate": False, "canRead": True, "canUpdate": False, "canDelete": False}},
        }
    }

    op_put = client.put(f"/tenants/{tid}/permissions", json=all_19_roles_payload, headers=op_headers)
    assert op_put.status_code == 200
    saved_perms = op_put.json()["permissions"]
    assert len(saved_perms) == 19
    assert saved_perms["Pharmacist"]["pharma_bill"]["canCreate"] is True
    assert saved_perms["HR"]["hr_employees"]["canDelete"] is True

    # Edge Case 5: Idempotent overwrite and update
    all_19_roles_payload["permissions"]["Doctor"]["opd_medical_records"]["canDelete"] = True
    update_resp = client.put(f"/tenants/{tid}/permissions", json=all_19_roles_payload, headers=admin_headers)
    assert update_resp.status_code == 200
    assert update_resp.json()["permissions"]["Doctor"]["opd_medical_records"]["canDelete"] is True

    # Teardown
    client.delete(f"/tenants/{tid}", headers=op_headers)




