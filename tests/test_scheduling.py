from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import app
from app.scheduling_service import (
    check_booking_conflicts,
    find_available_slots,
    verify_and_enforce_prerequisites
)

# Reuse TestClient
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


@pytest.mark.asyncio
async def test_find_available_slots_logic():
    """Test slots generation logic handles service durations, recurring templates and booked gaps."""
    mock_session = AsyncMock(spec=AsyncSession)

    # 1. Mock service query
    mock_service_res = MagicMock()
    mock_service_res.mappings.return_value.one_or_none.return_value = {"duration_minutes": 30}

    # 2. Mock availability query (9:00 AM to 10:30 AM)
    mock_avail_res = MagicMock()
    mock_avail_res.mappings.return_value.all.return_value = [
        {"site_id": "site1", "start_time": time(9, 0), "end_time": time(10, 30)}
    ]

    # 3. Mock existing bookings (9:30 AM to 10:00 AM is booked)
    mock_booking_res = MagicMock()
    mock_booking_res.mappings.return_value.all.return_value = [
        {"start_time": datetime(2026, 7, 21, 9, 30), "end_time": datetime(2026, 7, 21, 10, 0)}
    ]

    mock_session.execute.side_effect = [mock_service_res, mock_avail_res, mock_booking_res]

    slots = await find_available_slots(mock_session, "p1", "s1", date(2026, 7, 21))
    
    # Total window is 90 mins. Gaps are 30 mins each.
    # Expected slots: 09:00 - 09:30 (Available), 09:30 - 10:00 (Booked/Skip), 10:00 - 10:30 (Available)
    # Total available slots = 2
    assert len(slots) == 2
    assert slots[0]["start_time"].time() == time(9, 0)
    assert slots[1]["start_time"].time() == time(10, 0)


@pytest.mark.asyncio
async def test_booking_conflict_detector_blocks():
    """Test check_booking_conflicts raises HTTP 409 when practitioner, room or patient has overlaps."""
    mock_session = AsyncMock(spec=AsyncSession)

    # 1. Overlapping practitioner match
    mock_conflict_res = MagicMock()
    mock_conflict_res.mappings.return_value.all.return_value = [
        {"id": uuid4(), "practitioner_id": "doc1", "room_id": "room1", "patient_id": uuid4()}
    ]
    mock_session.execute.return_value = mock_conflict_res

    with pytest.raises(HTTPException) as exc:
        await check_booking_conflicts(
            mock_session, "doc1", "room2", uuid4(), datetime(2026, 7, 21, 9, 0), datetime(2026, 7, 21, 9, 30)
        )
    assert exc.value.status_code == 409
    assert "Practitioner doc1 is already booked" in exc.value.detail


@pytest.mark.asyncio
async def test_prerequisite_enforcement_rules():
    """Test verify_and_enforce_prerequisites blocks check-in on unsatisfied hard-stops."""
    mock_session = AsyncMock(spec=AsyncSession)

    # Unsatisfied hard-stop prerequisite
    mock_prereq_res = MagicMock()
    mock_prereq_res.mappings.return_value.all.return_value = [
        {"prerequisite_id": "fasting", "satisfied": False, "code": "FST", "description": "Fasting 8 hours", "enforcement_type": "hard-stop"},
        {"prerequisite_id": "consent", "satisfied": False, "code": "CNS", "description": "Advisory consent form", "enforcement_type": "advisory"}
    ]
    mock_session.execute.return_value = mock_prereq_res

    with pytest.raises(HTTPException) as exc:
        await verify_and_enforce_prerequisites(mock_session, uuid4())
    
    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert "Hard-stop prerequisites are unsatisfied" in detail["message"]
    assert len(detail["unsatisfied_hard_stops"]) == 1
    assert detail["unsatisfied_hard_stops"][0]["id"] == "fasting"
    assert len(detail["unsatisfied_advisories"]) == 1
    assert detail["unsatisfied_advisories"][0]["id"] == "consent"


@patch("app.routers.scheduling.event_publish", new_callable=AsyncMock)
@patch("app.routers.scheduling.audit_record", new_callable=AsyncMock)
@patch("app.routers.scheduling.check_booking_conflicts", new_callable=AsyncMock)
def test_create_appointment_success(mock_conflicts, mock_audit, mock_event, db_session):
    """Test booking an appointment inserts details and publishes events."""
    mock_conflicts.return_value = None

    # Mock DB insert result
    mock_insert_res = MagicMock()
    mock_insert_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "doc1",
        "site_id": "site1",
        "room_id": "room1",
        "service_id": "serv1",
        "status": "BOOKED",
        "start_time": datetime(2026, 7, 21, 10, 0),
        "end_time": datetime(2026, 7, 21, 10, 30),
        "referred_by_id": None,
        "referred_by_name": None
    }
    
    # 2 executes: SET LOCAL app.tenant_id, INSERT appointment
    db_session.execute.side_effect = [AsyncMock(), mock_insert_res]

    response = client.post(
        "/scheduling/appointments",
        headers={"Authorization": "Bearer dev.apollo.receptionist"},
        json={
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "practitioner_id": "doc1",
            "site_id": "site1",
            "room_id": "room1",
            "service_id": "serv1",
            "start_time": "2026-07-21T10:00:00",
            "end_time": "2026-07-21T10:30:00"
        }
    )

    assert response.status_code == 201
    assert response.json()["status"] == "BOOKED"
    
    # Assert side effects
    mock_audit.assert_called_once()
    mock_event.assert_called_once()


@patch("app.routers.scheduling.event_publish", new_callable=AsyncMock)
@patch("app.routers.scheduling.audit_record", new_callable=AsyncMock)
@patch("app.routers.scheduling.verify_and_enforce_prerequisites", new_callable=AsyncMock)
def test_check_in_appointment_success(mock_prereqs, mock_audit, mock_event, db_session):
    """Test successful check-in transitions appointment status to ARRIVED."""
    mock_prereqs.return_value = {"status": "allowed", "warnings": []}

    # Mock fetch app details
    mock_app_res = MagicMock()
    mock_app_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "status": "BOOKED"
    }

    # Mock update status returned row
    mock_update_res = MagicMock()
    mock_update_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "22222222-3333-4444-5555-666666666666",
        "practitioner_id": "doc1",
        "site_id": "site1",
        "room_id": "room1",
        "service_id": "serv1",
        "status": "ARRIVED",
        "start_time": datetime(2026, 7, 21, 10, 0),
        "end_time": datetime(2026, 7, 21, 10, 30),
        "referred_by_id": None,
        "referred_by_name": None
    }

    # Mock dues warning queries
    mock_resp_sum = MagicMock()
    mock_resp_sum.scalar.return_value = 1200.0

    mock_paid_sum = MagicMock()
    mock_paid_sum.scalar.return_value = 200.0

    # 5 executes:
    # 1. SET LOCAL app.tenant_id
    # 2. SELECT appointment details
    # 3. UPDATE status
    # 4. SELECT SUM(patient_responsibility)
    # 5. SELECT SUM(payment.amount)
    db_session.execute.side_effect = [
        AsyncMock(), mock_app_res, mock_update_res, mock_resp_sum, mock_paid_sum
    ]

    response = client.post(
        "/scheduling/appointments/11111111-2222-3333-4444-555555555555/check-in",
        headers={"Authorization": "Bearer dev.apollo.receptionist"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ARRIVED"
    assert data["dues_warning"] == "Patient has unpaid invoice dues of 1000.00 INR"
    mock_event.assert_called_once()


def test_queue_dashboard(db_session):
    """Test get_clinic_queue retrieves active check-ins for the dashboard display."""
    mock_queue_res = MagicMock()
    mock_queue_res.mappings.return_value.all.return_value = [
        {
            "appointment_id": "11111111-2222-3333-4444-555555555555",
            "patient_id": "22222222-3333-4444-5555-666666666666",
            "patient_name": "Ravi Kumar",
            "status": "ARRIVED",
            "start_time": datetime(2026, 7, 21, 10, 0),
            "service_name": "General Checkup",
            "practitioner_name": "Dr. Prasad",
            "site_name": "Vijayawada Clinic"
        }
    ]

    # 2 executes: SET LOCAL, SELECT queue
    db_session.execute.side_effect = [AsyncMock(), mock_queue_res]

    response = client.get(
        "/scheduling/queue?site_id=site1",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["patient_name"] == "Ravi Kumar"
    assert items[0]["status"] == "ARRIVED"
