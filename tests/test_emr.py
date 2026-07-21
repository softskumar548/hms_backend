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


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_create_encounter(mock_audit, db_session):
    """Test creating an encounter succeeds with correct details."""
    mock_insert_res = MagicMock()
    mock_insert_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "appointment_id": "22222222-3333-4444-5555-666666666666",
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "practitioner_id": "doc1",
        "site_id": "site1",
        "status": "open",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "signed_at": None,
        "signed_by": None
    }
    # Executes: SET LOCAL, INSERT
    db_session.execute.side_effect = [AsyncMock(), mock_insert_res]

    response = client.post(
        "/emr/encounters",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={
            "appointment_id": "22222222-3333-4444-5555-666666666666",
            "patient_id": "33333333-4444-5555-6666-777777777777",
            "practitioner_id": "doc1",
            "site_id": "site1"
        }
    )

    assert response.status_code == 201
    assert response.json()["status"] == "open"
    mock_audit.assert_called_once()


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_save_notes_draft_success(mock_audit, db_session):
    """Test saving notes drafts upserts properly and increments version."""
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "open"
    }

    # Simulate note insert first time
    mock_note_find_empty = MagicMock()
    mock_note_find_empty.mappings.return_value.one_or_none.return_value = None

    mock_insert_res = MagicMock()
    mock_insert_res.mappings.return_value.one.return_value = {
        "id": "44444444-5555-6666-7777-888888888888",
        "encounter_id": "11111111-2222-3333-4444-555555555555",
        "template_type": "soap",
        "structured_content": {"symptom": "fever"},
        "rich_text_content": "SOAP Note text",
        "version": 1
    }

    # Executes: SET LOCAL, SELECT encounter, SELECT note, INSERT note
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res, mock_note_find_empty, mock_insert_res]

    response = client.put(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/notes",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={
            "template_type": "soap",
            "structured_content": {"symptom": "fever"},
            "rich_text_content": "SOAP Note text"
        }
    )

    assert response.status_code == 200
    assert response.json()["version"] == 1
    mock_audit.assert_called_once()


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_sign_off_blocks_missing_allergies(mock_audit, db_session):
    """Test sign-off fails when allergy status has not been explicitly asserted (EMR-005)."""
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "open"
    }

    # Simulate no allergy records
    mock_allergy_res = MagicMock()
    mock_allergy_res.mappings.return_value.one.return_value = {"val": 0}

    # Executes: SET LOCAL, SELECT encounter, SELECT allergy count
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res, mock_allergy_res]

    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/sign-off",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 400
    assert "Allergy status must be explicitly asserted" in response.text


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_sign_off_blocks_missing_diagnoses(mock_audit, db_session):
    """Test sign-off fails when active diagnosis list is empty (EMR-008)."""
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "open"
    }

    # Simulate allergy record exists
    mock_allergy_res = MagicMock()
    mock_allergy_res.mappings.return_value.one.return_value = {"val": 1}

    # Simulate no active diagnoses
    mock_diag_res = MagicMock()
    mock_diag_res.mappings.return_value.one.return_value = {"val": 0}

    # Executes: SET LOCAL, SELECT encounter, SELECT allergy count, SELECT diagnoses count
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res, mock_allergy_res, mock_diag_res]

    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/sign-off",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 400
    assert "active coded diagnosis (ICD-10) is required" in response.text


@patch("app.routers.emr.event_publish", new_callable=AsyncMock)
@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_sign_off_success(mock_audit, mock_event, db_session):
    """Test sign-off completes when allergies and diagnoses are present, locking clinical note."""
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "open"
    }

    mock_allergy_res = MagicMock()
    mock_allergy_res.mappings.return_value.one.return_value = {"val": 1}

    mock_diag_res = MagicMock()
    mock_diag_res.mappings.return_value.one.return_value = {"val": 1}

    mock_update_res = MagicMock()
    mock_update_res.mappings.return_value.one.return_value = {
        "id": "11111111-2222-3333-4444-555555555555",
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "practitioner_id": "doc1",
        "site_id": "site1",
        "status": "signed",
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "signed_at": datetime.now(),
        "signed_by": "physician1"
    }

    # Executes: SET LOCAL, SELECT encounter, SELECT allergy count, SELECT diagnoses count, UPDATE encounter
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res, mock_allergy_res, mock_diag_res, mock_update_res]

    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/sign-off",
        headers={"Authorization": "Bearer dev.apollo.physician"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "signed"
    mock_event.assert_called_once()
    mock_audit.assert_called_once()


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_addenda_post_signoff(mock_audit, db_session):
    """Test adding comments to finalized charts via addenda updates status to amended."""
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "signed"
    }

    mock_addendum_insert = MagicMock()
    mock_addendum_insert.mappings.return_value.one.return_value = {
        "id": "55555555-6666-7777-8888-999999999999",
        "encounter_id": "11111111-2222-3333-4444-555555555555",
        "author_id": "physician1",
        "content": "Follow-up notes appended",
        "created_at": datetime.now()
    }

    # Executes: SET LOCAL, SELECT encounter, INSERT addendum, UPDATE status to amended
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res, mock_addendum_insert, AsyncMock()]

    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/addenda",
        headers={"Authorization": "Bearer dev.apollo.physician"},
        json={"content": "Follow-up notes appended"}
    )

    assert response.status_code == 201
    assert response.json()["content"] == "Follow-up notes appended"
    mock_audit.assert_called_once()


@patch("app.routers.emr.audit_record", new_callable=AsyncMock)
def test_vitals_limits_validation(mock_audit, db_session):
    """Test vitals capture raises validation exceptions on extreme inputs (EMR-007)."""
    # Define encounter mock response
    mock_enc_res = MagicMock()
    mock_enc_res.mappings.return_value.one_or_none.return_value = {
        "patient_id": "33333333-4444-5555-6666-777777777777",
        "status": "open"
    }

    # 1. Invalid SpO2 (>100)
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res]
    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/vitals",
        headers={"Authorization": "Bearer dev.apollo.nurse"},
        json={"type": "spo2", "value": 110.0, "unit": "%"}
    )
    assert response.status_code == 422
    assert "SpO2 percentage must be between 0 and 100" in response.text

    # 2. Invalid Temperature (<25)
    db_session.execute.side_effect = [AsyncMock(), mock_enc_res]
    response = client.post(
        "/emr/encounters/11111111-2222-3333-4444-555555555555/vitals",
        headers={"Authorization": "Bearer dev.apollo.nurse"},
        json={"type": "temperature", "value": 15.0, "unit": "C"}
    )
    assert response.status_code == 422
    assert "temperature in Celsius must be within reasonable physiological limits" in response.text
