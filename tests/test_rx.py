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


def test_search_drugs(db_session):
    """Test partial name searches on medication catalog."""
    mock_search_res = MagicMock()
    mock_search_res.mappings.return_value.all.return_value = [
        {"id": "med1", "name": "Paracetamol 500mg", "generic_name": "Acetaminophen", "form": "tablet", "strength": "500 mg"}
    ]

    # Executes: SET LOCAL, SELECT
    db_session.execute.side_effect = [AsyncMock(), mock_search_res]

    response = client.get(
        "/rx/drugs?q=para",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 200
    drugs = response.json()
    assert len(drugs) == 1
    assert drugs[0]["name"] == "Paracetamol 500mg"


@patch("app.routers.rx.audit_record", new_callable=AsyncMock)
def test_create_prescription_draft(mock_audit, db_session):
    """Test composing a draft prescription inserts header and items."""
    mock_rx_insert = MagicMock()
    mock_rx_insert.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "physician1",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "draft",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "signed_at": None,
        "signed_by": None
    }

    mock_item_insert = MagicMock()
    mock_item_insert.mappings.return_value.one.return_value = {
        "id": "44444444-5555-6666-7777-888888888888",
        "medication_id": "med1",
        "dose": 500.0,
        "unit": "mg",
        "route": "oral",
        "frequency": "twice daily",
        "duration_days": 5,
        "prn": False,
        "quantity": 10,
        "refills": 0,
        "free_text_sig": "Take after meals"
    }

    # Executes: SET LOCAL, INSERT prescription, INSERT prescription_item
    db_session.execute.side_effect = [AsyncMock(), mock_rx_insert, mock_item_insert]

    response = client.post(
        "/rx/prescriptions",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "encounter_id": "33333333-4444-5555-6666-777777777777",
            "items": [
                {
                    "medication_id": "med1",
                    "dose": 500.0,
                    "unit": "mg",
                    "route": "oral",
                    "frequency": "twice daily",
                    "duration_days": 5,
                    "quantity": 10
                }
            ]
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "draft"
    assert len(data["items"]) == 1
    assert data["items"][0]["medication_id"] == "med1"
    mock_audit.assert_called_once()


@patch("app.routers.rx.audit_record", new_callable=AsyncMock)
def test_sign_prescription_blocks_interaction(mock_audit, db_session):
    """Test signing blocks with HTTP 409 when drug allergy overlap is found (RX-003)."""
    # 1. Fetch prescription status
    mock_rx_res = MagicMock()
    mock_rx_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "draft"
    }

    # 2. Mock items (Aspirin prescribed, generic_name: Aspirin)
    mock_items_res = MagicMock()
    mock_items_res.mappings.return_value.all.return_value = [
        {"medication_id": "med-aspirin", "generic_name": "Aspirin"}
    ]

    # 3. Mock patient allergies list (Allergy to Aspirin exists!)
    mock_allergies_res = MagicMock()
    mock_allergies_res.mappings.return_value.all.return_value = [
        {"substance_display": "Aspirin"}
    ]

    # Executes: SET LOCAL, SELECT prescription, SELECT items, SELECT allergies
    db_session.execute.side_effect = [AsyncMock(), mock_rx_res, mock_items_res, mock_allergies_res]

    response = client.post(
        "/rx/prescriptions/11111111-2222-3333-4444-555555555555/sign",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={}
    )

    assert response.status_code == 409
    assert "Drug-allergy interaction alert" in response.text


@patch("app.routers.rx.event_publish", new_callable=AsyncMock)
@patch("app.routers.rx.audit_record", new_callable=AsyncMock)
def test_sign_prescription_override_success(mock_audit, mock_event, db_session):
    """Test signing succeeds with override_reason when alerts are present."""
    mock_rx_res = MagicMock()
    mock_rx_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "draft"
    }

    mock_items_res = MagicMock()
    mock_items_res.mappings.return_value.all.return_value = [
        {"medication_id": "med-aspirin", "generic_name": "Aspirin"}
    ]

    mock_allergies_res = MagicMock()
    mock_allergies_res.mappings.return_value.all.return_value = [
        {"substance_display": "Aspirin"}
    ]

    mock_update_res = MagicMock()
    mock_update_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "physician1",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "signed",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "signed_at": datetime.now(),
        "signed_by": "physician1"
    }

    # Executes:
    # 1. SET LOCAL
    # 2. SELECT prescription
    # 3. SELECT items
    # 4. SELECT allergies
    # 5. INSERT override reason
    # 6. UPDATE prescription to signed
    db_session.execute.side_effect = [
        AsyncMock(), mock_rx_res, mock_items_res, mock_allergies_res, AsyncMock(), mock_update_res
    ]

    response = client.post(
        "/rx/prescriptions/11111111-2222-3333-4444-555555555555/sign",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={"override_reason": "No other options. Will monitor patient closely."}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "signed"
    mock_event.assert_called_once()
    mock_audit.assert_called_once()


@patch("app.routers.rx.event_publish", new_callable=AsyncMock)
@patch("app.routers.rx.audit_record", new_callable=AsyncMock)
def test_sign_prescription_with_followup(mock_audit, mock_event, db_session):
    """Test follow-up scheduling triggers a DRAFT appointment (Flag F1)."""
    mock_rx_res = MagicMock()
    mock_rx_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "draft"
    }

    # No allergy overlaps
    mock_items_res = MagicMock()
    mock_items_res.mappings.return_value.all.return_value = [
        {"medication_id": "med-aspirin", "generic_name": "Aspirin"}
    ]
    mock_allergies_res = MagicMock()
    mock_allergies_res.mappings.return_value.all.return_value = []

    # Mock sign return row
    mock_update_res = MagicMock()
    mock_update_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "physician1",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "signed",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "signed_at": datetime.now(),
        "signed_by": "physician1"
    }

    # Mock Room query for site
    mock_room_res = MagicMock()
    mock_room_res.mappings.return_value.one_or_none.return_value = {"id": "room1"}

    # Mock DRAFT appointment insert return id
    mock_draft_app_insert = MagicMock()
    mock_draft_app_insert.mappings.return_value.one.return_value = {"id": uuid4()}

    # Executes:
    # 1. SET LOCAL
    # 2. SELECT prescription
    # 3. SELECT items
    # 4. SELECT allergies
    # 5. UPDATE prescription
    # 6. SELECT room details
    # 7. INSERT DRAFT appointment
    # 8. INSERT follow-up prerequisite
    db_session.execute.side_effect = [
        AsyncMock(), mock_rx_res, mock_items_res, mock_allergies_res, mock_update_res,
        mock_room_res, mock_draft_app_insert, AsyncMock()
    ]

    response = client.post(
        "/rx/prescriptions/11111111-2222-3333-4444-555555555555/sign",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={
            "follow_up_date": "2026-07-28",
            "follow_up_service_id": "serv1",
            "follow_up_site_id": "site1",
            "follow_up_prerequisites": ["fasting"]
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "signed"
    mock_event.assert_called_once()
    mock_audit.assert_called_once()
