from __future__ import annotations

from datetime import datetime
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


@patch("app.routers.ord.audit_record", new_callable=AsyncMock)
def test_create_lab_order(mock_audit, db_session):
    """Test creating a lab order inserts records and logs audits."""
    mock_order_insert = MagicMock()
    mock_order_insert.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "physician1",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "ordered",
        "priority": "routine",
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, INSERT order, INSERT order_item
    db_session.execute.side_effect = [AsyncMock(), mock_order_insert, AsyncMock()]

    response = client.post(
        "/ord/orders",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "encounter_id": "33333333-4444-5555-6666-777777777777",
            "priority": "routine",
            "test_ids": ["cbc"]
        }
    )

    assert response.status_code == 201
    assert response.json()["status"] == "ordered"
    mock_audit.assert_called_once()


@patch("app.routers.ord.audit_record", new_callable=AsyncMock)
def test_collect_specimen(mock_audit, db_session):
    """Test specimen collection transitions lab order status."""
    mock_order_select = MagicMock()
    mock_order_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "status": "ordered"
    }

    mock_update_res = MagicMock()
    mock_update_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "physician1",
        "encounter_id": "33333333-4444-5555-6666-777777777777",
        "status": "specimen_collected",
        "priority": "routine",
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, SELECT order, UPDATE status
    db_session.execute.side_effect = [AsyncMock(), mock_order_select, mock_update_res]

    response = client.post(
        "/ord/orders/11111111-2222-3333-4444-555555555555/specimen",
        headers={"Authorization": "Bearer dev.apollo.nurse"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "specimen_collected"
    mock_audit.assert_called_once()


@patch("app.routers.ord.event_publish", new_callable=AsyncMock)
@patch("app.routers.ord.audit_record", new_callable=AsyncMock)
def test_ingest_result_matched(mock_audit, mock_event, db_session):
    """Test resulting a matched order closes referral loop if patient was referred."""
    # Mock order select to find patient ID
    mock_order_select = MagicMock()
    mock_order_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666"
    }

    # Mock result insert
    mock_result_insert = MagicMock()
    mock_result_insert.mappings.return_value.one.return_value = {
        "id": "55555555-6666-7777-8888-999999999999",
        "order_id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "test_id": "cbc",
        "value": 14.5,
        "unit": "g/dL",
        "reference_range": "12-16",
        "is_abnormal": False,
        "is_critical": False,
        "resulted_at": datetime.now()
    }

    # Mock patient search showing referral parameters
    mock_patient_select = MagicMock()
    mock_patient_select.mappings.return_value.one_or_none.return_value = {
        "referred_by_id": "ref-doctor-1",
        "referred_by_name": "Dr. Ramesh"
    }

    # Executes:
    # 1. SET LOCAL
    # 2. SELECT order (find patient_id)
    # 3. INSERT result
    # 4. UPDATE order to resulted
    # 5. SELECT patient (check referred)
    db_session.execute.side_effect = [
        AsyncMock(), mock_order_select, mock_result_insert, AsyncMock(), mock_patient_select
    ]

    response = client.post(
        "/ord/results/ingest",
        headers={"Authorization": "Bearer dev.apollo.nurse"},
        json={
            "order_id": "11111111-2222-3333-4444-555555555555",
            "test_id": "cbc",
            "value": 14.5,
            "unit": "g/dL"
        }
    )

    assert response.status_code == 201
    assert response.json()["value"] == 14.5
    
    # Assert loop closed
    mock_event.assert_called_once_with("referral.closed", {
        "tenant_id": "apollo",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "referrer_id": "ref-doctor-1",
        "referrer_name": "Dr. Ramesh",
        "order_id": "11111111-2222-3333-4444-555555555555",
        "action": "lab_resulted"
    })
    mock_audit.assert_called_once()


def test_ingest_result_unmatched(db_session):
    """Test resulting an unmatched order routes to unresolved database queue."""
    mock_unmatched_insert = MagicMock()
    mock_unmatched_insert.mappings.return_value.one.return_value = {
        "id": "88888888-9999-0000-1111-222222222222"
    }

    mock_order_find_empty = MagicMock()
    mock_order_find_empty.mappings.return_value.one_or_none.return_value = None

    # Executes: SET LOCAL, SELECT order, INSERT unmatched
    db_session.execute.side_effect = [AsyncMock(), mock_order_find_empty, mock_unmatched_insert]

    response = client.post(
        "/ord/results/ingest",
        headers={"Authorization": "Bearer dev.apollo.nurse"},
        json={
            "order_id": "00000000-0000-0000-0000-000000000000",  # Mismatch ID
            "test_id": "cbc",
            "value": 14.5,
            "unit": "g/dL"
        }
    )

    assert response.status_code == 202
    assert "Routed to unmatched resolution queue" in response.text


@patch("app.routers.ord.audit_record", new_callable=AsyncMock)
def test_resolve_unmatched_result(mock_audit, db_session):
    """Test manually resolving unmatched queue matching order."""
    mock_unmatched_select = MagicMock()
    mock_unmatched_select.mappings.return_value.one_or_none.return_value = {
        "status": "pending",
        "payload": {
            "test_id": "cbc",
            "value": 14.5,
            "unit": "g/dL",
            "is_abnormal": False
        }
    }

    mock_result_insert = MagicMock()
    mock_result_insert.mappings.return_value.one.return_value = {
        "id": "55555555-6666-7777-8888-999999999999",
        "order_id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "test_id": "cbc",
        "value": 14.5,
        "unit": "g/dL",
        "reference_range": None,
        "is_abnormal": False,
        "is_critical": False,
        "resulted_at": datetime.now()
    }

    # Executes:
    # 1. SET LOCAL
    # 2. SELECT unmatched
    # 3. INSERT result
    # 4. UPDATE unmatched status to resolved
    # 5. UPDATE order status to resulted
    db_session.execute.side_effect = [
        AsyncMock(), mock_unmatched_select, mock_result_insert, AsyncMock(), AsyncMock()
    ]

    response = client.post(
        "/ord/results/unmatched/88888888-9999-0000-1111-222222222222/resolve",
        headers={"Authorization": "Bearer dev.apollo.nurse"},
        json={
            "order_id": "11111111-2222-3333-4444-555555555555",
            "patient_id": "22222222-3333-4444-5555-666666666666"
        }
    )

    assert response.status_code == 200
    assert response.json()["value"] == 14.5
    mock_audit.assert_called_once()
