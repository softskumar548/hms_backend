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


def test_get_operational_dashboard(db_session):
    """Test getting today's operational dashboard KPIs."""
    mock_appts = MagicMock()
    mock_appts.scalar.return_value = 10
    
    mock_arrivals = MagicMock()
    mock_arrivals.scalar.return_value = 4

    mock_rev = MagicMock()
    mock_rev.scalar.return_value = 5250.0

    mock_queue = MagicMock()
    mock_queue.scalar.return_value = 3

    # Executes: SET LOCAL, SELECT appts, SELECT arrivals, SELECT rev, SELECT queue
    db_session.execute.side_effect = [
        AsyncMock(), mock_appts, mock_arrivals, mock_rev, mock_queue
    ]

    response = client.get(
        "/rpt/dashboards/operational",
        headers={"Authorization": "Bearer dev.apollo.receptionist"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["appointments_count"] == 10
    assert data["arrivals_count"] == 4
    assert data["revenue_collected"] == 5250.0
    assert data["queue_length"] == 3


def test_get_visits_report(db_session):
    """Test visits aggregate counts report with date filters."""
    mock_visits = MagicMock()
    mock_visits.mappings.return_value.all.return_value = [
        {"practitioner_id": "physician1", "service_id": "consultation", "visits_count": 5}
    ]

    # Executes: SET LOCAL, SELECT visits
    db_session.execute.side_effect = [AsyncMock(), mock_visits]

    response = client.get(
        "/rpt/reports/visits?start_date=2026-07-01&end_date=2026-07-20",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["visits_count"] == 5


@patch("app.routers.rpt.audit_record", new_callable=AsyncMock)
def test_get_diagnoses_report_audited(mock_audit, db_session):
    """Test diagnosis frequency report records a read audit event."""
    mock_diag = MagicMock()
    mock_diag.mappings.return_value.all.return_value = [
        {"icd10_code": "I10", "display": "Essential hypertension", "patient_count": 12}
    ]

    # Executes: SET LOCAL, SELECT diagnoses
    db_session.execute.side_effect = [AsyncMock(), mock_diag]

    response = client.get(
        "/rpt/reports/diagnoses",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["icd10_code"] == "I10"
    assert data[0]["patient_count"] == 12
    
    # Verify audit trail logs query parameters access
    mock_audit.assert_called_once()
    assert mock_audit.call_args[1]["action"] == "read"
    assert mock_audit.call_args[1]["resource_type"] == "ClinicalDiagnosesReport"


def test_get_ar_aging_report(db_session):
    """Test outstanding claims aging splits report."""
    mock_claims = MagicMock()
    mock_claims.mappings.return_value.all.return_value = [
        {"bucket": "0-30", "outstanding_amount": 10500.0},
        {"bucket": "61-90", "outstanding_amount": 3200.0}
    ]

    # Executes: SET LOCAL, SELECT claims
    db_session.execute.side_effect = [AsyncMock(), mock_claims]

    response = client.get(
        "/rpt/reports/ar-aging",
        headers={"Authorization": "Bearer dev.apollo.billing_clerk"}
    )

    assert response.status_code == 200
    data = response.json()
    
    buckets = {item["bucket"]: item["outstanding_amount"] for item in data}
    assert buckets["0-30"] == 10500.0
    assert buckets["31-60"] == 0.0
    assert buckets["61-90"] == 3200.0
    assert buckets["90+"] == 0.0
