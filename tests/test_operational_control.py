from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def test_multi_tenant_metrics_and_invoicing():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}

    # 1. Fetch multi-tenant platform metrics (TEN-301)
    resp = client.get("/tenants/metrics", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tenants"] >= 1
    assert len(data["metrics"]) >= 1

    # 2. Issue SaaS subscription invoice (TEN-302)
    inv_payload = {
        "plan": "Enterprise SaaS",
        "amount_inr": 75000.0,
        "billing_period": "2026-07"
    }
    resp = client.post("/tenants/apollo/invoices", json=inv_payload, headers=headers)
    assert resp.status_code == 201
    inv_data = resp.json()
    assert inv_data["tenant_id"] == "apollo"
    assert inv_data["amount_inr"] == 75000.0
    assert inv_data["status"] == "issued"

    # 3. List SaaS subscription invoices (TEN-302)
    resp_list = client.get("/tenants/apollo/invoices", headers=headers)
    assert resp_list.status_code == 200
    invoices = resp_list.json()
    assert len(invoices) >= 1
    assert invoices[0]["tenant_id"] == "apollo"


def test_operator_support_access_impersonation():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}
    support_payload = {
        "reason": "Investigating payment reconciliation query from hospital admin",
        "duration_minutes": 60
    }
    resp = client.post("/tenants/apollo/support-access", json=support_payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["tenant_id"] == "apollo"
    assert data["status"] == "granted"
    assert data["token_id"].startswith("SUP-APOLLO-")


def test_aarogyasri_cashless_pre_auth_claim():
    headers = {"Authorization": "Bearer dev.apollo.physician"}
    claim_payload = {
        "patient_id": "c869fbbf-e61e-450e-b7ee-a4cf963a763a",
        "scheme": "aarogyasri",
        "card_number": "AARO-AP-99812",
        "treatment_code": "SURG-CARD-001",
        "estimated_amount_inr": 120000.0
    }
    resp = client.post("/tenants/apollo/claims/pre-auth", json=claim_payload, headers=headers)
    assert resp.status_code == 201
    claim_data = resp.json()
    assert claim_data["tenant_id"] == "apollo"
    assert claim_data["scheme"] == "aarogyasri"
    assert claim_data["status"] == "pre_authorized"
    assert claim_data["pre_auth_code"].startswith("PA-AP-")


import uuid

def test_tenant_suspension_and_emergency_override():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tenant_id = f"test_susp_{uuid.uuid4().hex[:6]}"

    # 1. Provision target tenant
    provision_payload = {
        "id": tenant_id,
        "name": "Suspension Test Clinic",
        "region": "india",
        "locale": "en-IN",
        "currency": "INR",
        "features": {"ref_commission": False}
    }
    resp = client.post("/tenants", json=provision_payload, headers=headers)
    assert resp.status_code == 201

    # 2. Suspend tenant (TEN-304)
    suspend_payload = {"reason": "Subscription invoice overdue by 60 days"}
    resp = client.post(f"/tenants/{tenant_id}/suspend", json=suspend_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    # 3. Non-operator attempt is blocked with 403 Forbidden
    phys_headers = {"Authorization": "Bearer dev.apollo.physician"}
    resp_blocked = client.post(f"/tenants/{tenant_id}/override", json={"override_note": "Unauth"}, headers=phys_headers)
    assert resp_blocked.status_code == 403

    # 4. Emergency override (TEN-305) by operator reinstates active status
    override_payload = {"override_note": "Payment arrangement confirmed by operator"}
    resp = client.post(f"/tenants/{tenant_id}/override", json=override_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # 5. Teardown: offboard test tenant so no debris remains
    client.delete(f"/tenants/{tenant_id}", headers=headers)


def test_saas_subscription_plan_and_quota_metering():
    headers = {"Authorization": "Bearer dev.__operator__.operator"}
    tenant_id = f"test_quota_{uuid.uuid4().hex[:6]}"

    try:
        # 1. Provision target tenant
        provision_payload = {
            "id": tenant_id,
            "name": "SaaS Quota Test Clinic",
            "region": "india",
            "locale": "en-IN",
            "currency": "INR",
            "features": {"subscription_plan": "starter"}
        }
        resp = client.post("/tenants", json=provision_payload, headers=headers)
        assert resp.status_code == 201

        # 2. Get initial quota usage
        resp_quota = client.get(f"/tenants/{tenant_id}/quotas", headers=headers)
        assert resp_quota.status_code == 200
        q_data = resp_quota.json()
        assert q_data["tenant_id"] == tenant_id
        assert "HMS Basic" in q_data["plan"] or "Starter" in q_data["plan"]
        assert q_data["read_only_mode"] is False
        assert q_data["admins_limit"] == 1
        assert q_data["staff_limit"] == 3
        assert q_data["beds_limit"] == 0 or q_data["beds_limit"] == 15
        assert len(q_data["quotas"]) == 3

        # 3. Upgrade subscription plan to growth
        upgrade_payload = {
            "plan": "growth",
            "billing_cycle": "annual"
        }
        resp_up = client.put(f"/tenants/{tenant_id}/subscription/plan", json=upgrade_payload, headers=headers)
        assert resp_up.status_code == 200
        up_data = resp_up.json()
        assert "Growth" in up_data["plan"]
        assert up_data["doctors_limit"] == 10
    finally:
        # 4. Teardown guaranteed
        client.delete(f"/tenants/{tenant_id}", headers=headers)



