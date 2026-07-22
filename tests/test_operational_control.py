"""OPERATIONAL CONTROL & BILLING/CLAIMS TEST SUITE (TEN-301 .. TEN-305 / Gate N5-X1).

Verifies multi-tenant usage metrics, SaaS subscription invoicing, Aarogyasri cashless pre-auth,
tenant suspension on billing default, and operator emergency override.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.main import app

client = TestClient(app)


def test_multi_tenant_metrics_and_invoicing():
    headers = {"Authorization": "Bearer dev.apollo.operator"}

    # 1. Fetch multi-tenant platform metrics (TEN-301)
    resp = client.get("/tenants/metrics", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tenants"] >= 2
    assert any(m["tenant_id"] == "apollo" for m in data["metrics"])

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
    headers = {"Authorization": "Bearer dev.apollo.operator"}
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

    # 3. Emergency override (TEN-305)
    override_payload = {"override_note": "Payment arrangement confirmed by operator"}
    resp = client.post(f"/tenants/{tenant_id}/override", json=override_payload, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
