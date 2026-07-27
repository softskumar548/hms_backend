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
    headers = {"Authorization": "Bearer dev.apollo.operator"}
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
    headers = {"Authorization": "Bearer dev.apollo.operator"}

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

