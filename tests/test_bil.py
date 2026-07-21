from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app

client = TestClient(app)


@pytest.fixture
def db_session():
    mock = AsyncMock(spec=AsyncSession)
    return mock


@pytest.fixture(autouse=True)
def override_db(db_session):
    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    yield
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _provision_test_tenants():
    """Override database provision fixtures to do nothing."""
    pass


def test_register_coverage_fails_missing_aadhaar(db_session):
    """Test public scheme registration blocks if patient Aadhaar linkage is missing."""
    mock_patient_select = MagicMock()
    mock_patient_select.mappings.return_value.one_or_none.return_value = {
        "aadhaar_last_four": None
    }

    # Executes: SET LOCAL, SELECT patient
    db_session.execute.side_effect = [AsyncMock(), mock_patient_select]

    response = client.post(
        "/bil/coverages",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "scheme_type": "aarogyasri",
            "plan_name": "Aarogyasri Cashless Standard",
            "member_id": "MEM1234",
            "validity_start": "2026-01-01",
            "validity_end": "2027-12-31",
            "patient_share_percent": 0.0
        }
    )

    assert response.status_code == 400
    assert "Aadhaar linkage is required" in response.text


def test_register_coverage_pmjay_success(db_session):
    """Test public scheme registration succeeds when Aadhaar is present."""
    mock_patient_select = MagicMock()
    mock_patient_select.mappings.return_value.one_or_none.return_value = {
        "aadhaar_last_four": "9876"
    }

    mock_coverage_insert = MagicMock()
    mock_coverage_insert.mappings.return_value.one.return_value = {
        "id": "77777777-8888-9999-0000-111111111111",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "scheme_type": "pmjay",
        "plan_name": "PMJAY Cashless Standard",
        "member_id": "MEM9876",
        "validity_start": date(2026, 1, 1),
        "validity_end": date(2027, 12, 31),
        "patient_share_percent": 0.0,
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, SELECT patient, INSERT coverage
    db_session.execute.side_effect = [AsyncMock(), mock_patient_select, mock_coverage_insert]

    response = client.post(
        "/bil/coverages",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "scheme_type": "pmjay",
            "plan_name": "PMJAY Cashless Standard",
            "member_id": "MEM9876",
            "validity_start": "2026-01-01",
            "validity_end": "2027-12-31",
            "patient_share_percent": 0.0
        }
    )

    assert response.status_code == 201
    assert response.json()["scheme_type"] == "pmjay"


@patch("app.routers.bil.audit_record", new_callable=AsyncMock)
def test_create_invoice_auto_captures_consult(mock_audit, db_session):
    """Test invoice draft auto-populates consultation charge master items."""
    mock_invoice_insert = MagicMock()
    mock_invoice_insert.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "draft",
        "coverage_id": None,
        "total_amount": 0.0,
        "payer_responsibility": 0.0,
        "patient_responsibility": 0.0,
        "created_at": datetime.now(),
        "updated_at": datetime.now()
    }

    # Consultation item exists in master catalog
    mock_consult_select = MagicMock()
    mock_consult_select.mappings.return_value.one_or_none.return_value = {
        "id": "chg-consult",
        "standard_price": 500.0,
        "tax_percent": 5.0
    }

    mock_item_insert = MagicMock()
    mock_item_insert.mappings.return_value.one.return_value = {
        "id": "44444444-5555-6666-7777-888888888888",
        "charge_item_id": "chg-consult",
        "quantity": 1,
        "unit_price": 500.0,
        "tax_amount": 25.0,
        "discount_amount": 0.0,
        "patient_share": 525.0,
        "payer_share": 0.0
    }

    # Executes: SET LOCAL, INSERT invoice, SELECT consultation item, INSERT invoice_item line
    db_session.execute.side_effect = [
        AsyncMock(), mock_invoice_insert, mock_consult_select, mock_item_insert
    ]

    response = client.post(
        "/bil/invoices",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "encounter_id": "33333333-4444-5555-6666-777777777777"
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert len(data["items"]) == 1
    assert data["items"][0]["charge_item_id"] == "chg-consult"
    mock_audit.assert_called_once()


@patch("app.routers.bil.event_publish", new_callable=AsyncMock)
@patch("app.routers.bil.audit_record", new_callable=AsyncMock)
def test_finalize_invoice_splits(mock_audit, mock_event, db_session):
    """Test invoice finalization sums totals and maps events."""
    mock_invoice_select = MagicMock()
    mock_invoice_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "status": "draft"
    }

    mock_sum_select = MagicMock()
    mock_sum_select.mappings.return_value.one.return_value = {
        "tot_amt": 525.0,
        "p_share": 105.0,
        "ins_share": 420.0
    }

    mock_update_invoice = MagicMock()
    mock_update_invoice.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "finalized",
        "coverage_id": "77777777-8888-9999-0000-111111111111",
        "total_amount": 525.0,
        "payer_responsibility": 420.0,
        "patient_responsibility": 105.0,
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, SELECT invoice, SELECT sum totals, UPDATE invoice to finalized
    db_session.execute.side_effect = [
        AsyncMock(), mock_invoice_select, mock_sum_select, mock_update_invoice
    ]

    response = client.post(
        "/bil/invoices/11111111-2222-3333-4444-555555555555/finalize",
        headers={"Authorization": "Bearer dev.apollo.billing_clerk"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "finalized"
    assert data["patient_responsibility"] == 105.0
    assert data["payer_responsibility"] == 420.0
    mock_event.assert_called_once()
    mock_audit.assert_called_once()


@patch("app.routers.bil.audit_record", new_callable=AsyncMock)
def test_reverse_invoice_credit_ledger(mock_audit, db_session):
    """Test invoice reversal workflow requires finance manager and transitions status."""
    mock_invoice_select = MagicMock()
    mock_invoice_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "status": "finalized"
    }

    mock_update_invoice = MagicMock()
    mock_update_invoice.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "reversed",
        "coverage_id": None,
        "total_amount": 525.0,
        "payer_responsibility": 0.0,
        "patient_responsibility": 525.0,
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, SELECT invoice, UPDATE invoice to reversed
    db_session.execute.side_effect = [
        AsyncMock(), mock_invoice_select, mock_update_invoice
    ]

    response = client.post(
        "/bil/invoices/11111111-2222-3333-4444-555555555555/reverse",
        headers={"Authorization": "Bearer dev.apollo.finance_manager"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "reversed"
    mock_audit.assert_called_once()
