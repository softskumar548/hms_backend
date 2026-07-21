from __future__ import annotations

from datetime import datetime, timedelta
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


def test_generate_invitation_staff(db_session):
    """Test generating a portal OTP invitation requires staff role."""
    mock_invite_insert = MagicMock()
    mock_invite_insert.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "email": "patient@zensynq.com",
        "phone": "+919999999999",
        "otp_code": "123456",
        "expires_at": datetime.now() + timedelta(minutes=15),
        "status": "pending"
    }

    # Executes: SET LOCAL, INSERT invitation
    db_session.execute.side_effect = [AsyncMock(), mock_invite_insert]

    response = client.post(
        "/por/invitations",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "email": "patient@zensynq.com",
            "phone": "+919999999999"
        }
    )

    assert response.status_code == 201
    assert response.json()["otp_code"] == "123456"


def test_activate_incorrect_otp(db_session):
    """Test activation fails with incorrect OTP code."""
    mock_invite_select = MagicMock()
    mock_invite_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "otp_code": "123456",
        "expires_at": datetime.now() + timedelta(minutes=15),
        "status": "pending"
    }

    # Executes: SET LOCAL, SELECT invitation
    db_session.execute.side_effect = [AsyncMock(), mock_invite_select]

    response = client.post(
        "/por/activate",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "invitation_id": "11111111-2222-3333-4444-555555555555",
            "otp_code": "999999",  # Incorrect code
            "username": "patient_user",
            "password": "securepassword"
        }
    )

    assert response.status_code == 400
    assert "Incorrect OTP" in response.text


def test_activate_success(db_session):
    """Test activation succeeds with correct OTP code and creates portal credentials."""
    mock_invite_select = MagicMock()
    mock_invite_select.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "otp_code": "123456",
        "expires_at": datetime.now() + timedelta(minutes=15),
        "status": "pending"
    }

    # Executes: SET LOCAL, SELECT invitation, INSERT portal_user, UPDATE invitation status
    db_session.execute.side_effect = [
        AsyncMock(), mock_invite_select, AsyncMock(), AsyncMock()
    ]

    response = client.post(
        "/por/activate",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "invitation_id": "11111111-2222-3333-4444-555555555555",
            "otp_code": "123456",
            "username": "patient_user",
            "password": "securepassword"
        }
    )

    assert response.status_code == 201
    assert "activated successfully" in response.json()["message"]


def test_list_portal_appointments_and_prerequisites(db_session):
    """Test portal list returns bookings and pre-visit checklists."""
    mock_app_select = MagicMock()
    mock_app_select.mappings.return_value.all.return_value = [
        {
            "id": "44444444-5555-6666-7777-888888888888",
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "practitioner_id": "physician1",
            "site_id": "site1",
            "room_id": "room1",
            "service_id": "serv1",
            "status": "booked",
            "start_time": datetime.now() + timedelta(days=2),
            "end_time": datetime.now() + timedelta(days=2, minutes=30)
        }
    ]

    mock_prereq_select = MagicMock()
    mock_prereq_select.mappings.return_value.all.return_value = [
        {
            "prerequisite_id": "fasting",
            "name": "Fasting 8 hours",
            "category": "lab",
            "satisfied": False
        }
    ]

    # Executes: SET LOCAL, SELECT appointments, SELECT prerequisites
    db_session.execute.side_effect = [
        AsyncMock(), mock_app_select, mock_prereq_select
    ]

    response = client.get(
        "/por/appointments?patient_id=22222222-3333-4444-5555-666666666666",
        headers={"Authorization": "Bearer dev.apollo.receptionist"}
    )

    assert response.status_code == 200
    apps = response.json()
    assert len(apps) == 1
    assert apps[0]["prerequisites"][0]["prerequisite_id"] == "fasting"
    assert apps[0]["prerequisites"][0]["satisfied"] is False


@patch("app.routers.por.audit_record", new_callable=AsyncMock)
def test_view_clinical_records_audited(mock_audit, db_session):
    """Test patient clinical timeline display queries items and writes a strict read audit log (POR-004, PLT-005)."""
    # 1. Mock conditions
    mock_cond = MagicMock()
    mock_cond.mappings.return_value.all.return_value = [
        {"code": "I10", "display": "Essential hypertension", "status": "active"}
    ]

    # 2. Mock allergies
    mock_allergies = MagicMock()
    mock_allergies.mappings.return_value.all.return_value = [
        {"substance_code": "penicillin", "substance_display": "Penicillin G", "criticality": "high"}
    ]

    # 3. Mock prescriptions
    mock_prescriptions = MagicMock()
    mock_prescriptions.mappings.return_value.all.return_value = [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "practitioner_id": "physician1",
            "status": "signed",
            "created_at": datetime.now(),
            "signed_at": datetime.now()
        }
    ]

    # 4. Mock prescription items
    mock_rx_items = MagicMock()
    mock_rx_items.mappings.return_value.all.return_value = [
        {
            "medication_id": "med1",
            "name": "Paracetamol 500mg",
            "dose": 1.0,
            "unit": "tablet",
            "route": "oral",
            "frequency": "twice daily",
            "duration_days": 5,
            "quantity": 10
        }
    ]

    # 5. Mock released results
    mock_results = MagicMock()
    mock_results.mappings.return_value.all.return_value = [
        {
            "id": "55555555-6666-7777-8888-999999999999",
            "test_name": "Hemoglobin",
            "value": 14.5,
            "unit": "g/dL",
            "reference_range": "12-16",
            "is_abnormal": False,
            "is_critical": False,
            "resulted_at": datetime.now()
        }
    ]

    # Executes:
    # 1. SET LOCAL
    # 2. SELECT conditions
    # 3. SELECT allergies
    # 4. SELECT prescriptions
    # 5. SELECT prescription items
    # 6. SELECT released lab results
    db_session.execute.side_effect = [
        AsyncMock(), mock_cond, mock_allergies, mock_prescriptions, mock_rx_items, mock_results
    ]

    response = client.get(
        "/por/clinical/records?patient_id=22222222-3333-4444-5555-666666666666",
        headers={"Authorization": "Bearer dev.apollo.receptionist"}
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["conditions"]) == 1
    assert data["conditions"][0]["code"] == "I10"
    assert len(data["prescriptions"]) == 1
    assert data["prescriptions"][0]["items"][0]["name"] == "Paracetamol 500mg"
    assert len(data["lab_results"]) == 1
    assert data["lab_results"][0]["test_name"] == "Hemoglobin"
    
    # Assert read action audit logged
    mock_audit.assert_called_once()
    assert mock_audit.call_args[1]["action"] == "read"
    assert mock_audit.call_args[1]["resource_type"] == "PatientPortalRecords"
