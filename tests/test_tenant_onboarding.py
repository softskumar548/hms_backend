"""TENANT ONBOARDING END-TO-END TEST (TEN-201 .. TEN-208).

Verifies the complete tenant onboarding lifecycle:
Provisioning -> Setup Wizard Config -> Legacy Migration Staging -> Clinician Reconciliation ->
Readiness Engine Evaluation -> Go-Live Transition (Active) -> Bulk FHIR Export.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def test_end_to_end_tenant_onboarding_journey():
    headers = {"Authorization": "Bearer dev.apollo.operator"}
    tenant_id = "hospital_n4_onboarding"

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
    config_payload = {
        "sites": [{"id": "site_n4_1", "name": "KIMS Vizag Site"}],
        "rooms": [{"id": "room_n4_1", "site_id": "site_n4_1", "name": "Room 101 OPD"}],
        "services": [{"id": "svc_n4_1", "name": "General OPD", "duration_minutes": 20}]
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
    assert len(readiness_data["checks"]) == 4

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
